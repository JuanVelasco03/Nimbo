"""Consultas al sitio: estado online, miniatura y busqueda. Todo a traves
del proxy de grabacion (para que las modelos marcadas con @vpn= salgan
por el tunel igual que sus paginas) y SIN tocar el perfil de Chromium,
que es un recurso exclusivo del supervisor (no se puede abrir una
segunda sesion de Playwright sobre el mismo perfil mientras el
supervisor esta corriendo).

Dos caminos, con usos distintos:

  - estado(): el MISMO metodo que ya usa supervisor.py desde hace meses
    (pedir la pagina de la modelo y leer el "status" embebido con un
    regex -- se reusa su RE_ESTADO, no se copia, para no duplicar el
    patron en dos sitios que se puedan desincronizar). Es el que de
    verdad importa: la decision real de "esta online o no" la toma el
    supervisor con este mismo mecanismo.

  - buscar(): un endpoint de busqueda del propio sitio
    (/api/front/v4/models/search/suggestion), descubierto una sola vez
    observando su trafico real con un proxy (no esta documentado por
    stripchat, puede cambiar sin aviso). Si deja de funcionar, buscar()
    simplemente devuelve [] -- no es un error fatal: la vista de
    Modelos sigue siendo util escribiendo el nombre exacto y usando
    estado(). La respuesta ya trae avatarUrl, asi que no hace falta un
    endpoint aparte para la miniatura.
"""
import collections
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# El sitio rechaza con 406 un pedido sin pinta de navegador (probado: la
# pagina de un perfil da 406 con solo el User-Agent, y 200 con esto). El
# endpoint de busqueda no es tan estricto, pero se manda igual por si
# cambia.
CABECERAS_NAVEGADOR = {
    "User-Agent": AGENTE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Las miniaturas (previewUrlThumbSmall, y sobre todo el snapshot en vivo
# de img.doppiocdn.media) exigen un Referer de verdad -- confirmado a
# mano: sin el, 404; con "https://<sitio>/", 200. El navegador NUNCA va
# a mandar ese Referer solo (manda el de Nimbo, "http://127.0.0.1:..."),
# asi que estas imagenes no se pueden poner directo en un <img src>: las
# pide Nimbo mismo (ver Sitio.imagen) y se las pasa al navegador ya
# resueltas -- por eso la lista blanca de hosts de abajo, para no
# convertir esto en un proxy abierto.
CABECERAS_IMAGEN = {
    "User-Agent": AGENTE,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
HOSTS_IMAGEN_PERMITIDOS = {"img.doppiocdn.media", "static-proxy.strpst.com"}

CACHE_ESTADO_SEGUNDOS = 20.0
CACHE_BUSQUEDA_SEGUNDOS = 10.0
MIN_CONSULTA = 2     # el propio sitio rechaza consultas mas cortas (400)

# Cache de miniaturas (ver specs\congelado_interfaz.md). Hasta el
# 25/10/2026 cada <img> que recreaba la interfaz era un HTTPS saliente al
# CDN ocupando un hilo del pool de servidor.py: con 8 modelos en la grilla
# y un repintado por segundo, 480 pedidos salientes por minuto (medido).
# La defensa de verdad es del lado del cliente (Cache-Control inmutable +
# no recrear el <img>), pero esto es la red de abajo: aunque el navegador
# pida igual -- DevTools con "Disable cache", una ventana recien abierta,
# dos pestanas -- el pedido se responde de RAM y no sale a la red.
#
# TIENE que estar acotada: el snapshot del modal de previa
# (vista_previa.js:pintarImagen) arma una URL nueva por segundo a
# proposito, asi que un dict indexado por URL sin tope es una fuga.
CACHE_IMAGEN_ENTRADAS = 64
CACHE_IMAGEN_BYTES = 8 * 1024 * 1024
CACHE_IMAGEN_SEGUNDOS = 300.0
# El fallo tambien se cachea, mas corto: una URL muerta (o una modelo que
# ya no existe) se reintentaba a 1 Hz, un hilo del pool por vez.
CACHE_IMAGEN_FALLO_SEGUNDOS = 30.0
# Un snapshot son ~7 KB: 4 s ya es patologico. Bajado de 10 s porque el
# timeout es lo unico que acota cuanto se queda un hilo del pool tomado
# -- y ni eso alcanza: getaddrinfo() NO esta cubierto por el timeout= de
# urllib (hay 2391 "getaddrinfo failed" en estado\eventos_directo.jsonl,
# en esta maquina el DNS si falla), asi que el tope duro de cuantos hilos
# puede comerse esto lo pone CUPO_IMAGEN en servidor.py.
TOPE_IMAGEN = 4.0

_CONTEXTO_SSL = ssl.create_default_context()
_CONTEXTO_SSL.check_hostname = False
_CONTEXTO_SSL.verify_mode = ssl.CERT_NONE   # el certificado es el de mitmproxy

_supervisor_cache = {}

# Centinela de "no hay nada en la cache": None no sirve, porque es un
# resultado valido y cacheable (la miniatura que no se pudo bajar).
_AUSENTE = object()


def _supervisor(ruta_scripts):
    """Importa supervisor.py solo para reusar RE_ESTADO y CABECERA_TUNEL
    -- las mismas constantes que ya usa el supervisor real, sin
    retipearlas aqui (ver CLAUDE.md, seccion "El nucleo copiado": el nombre de la
    cabecera del tunel esta a proposito en un solo lugar de cada lado).
    Importar el modulo no tiene efectos secundarios: todo lo que hace
    algo esta detras de 'if __name__ == "__main__"', y playwright se
    importa perezoso adentro de sus corrutinas, no al nivel del modulo.
    """
    mod = _supervisor_cache.get(ruta_scripts)
    if mod is None:
        if ruta_scripts not in sys.path:
            sys.path.insert(0, ruta_scripts)
        import supervisor as mod
        _supervisor_cache[ruta_scripts] = mod
    return mod


class Sitio:
    def __init__(self, ajustes):
        self.ajustes = ajustes
        self._cache_estado = {}
        self._cache_busqueda = {}
        # LRU acotada: url -> (guardado, resultado). OrderedDict y no dict
        # porque hace falta desalojar el mas viejo, ver _imagen_cacheada.
        self._cache_imagen = collections.OrderedDict()
        self._bytes_imagen = 0
        self._lock = threading.Lock()

    # -- transporte ---------------------------------------------------

    def _abridor(self, vpn: bool):
        # El proxy de grabacion solo hace falta para las de @vpn=: es el
        # unico sitio donde existe el enrutado hacia el tunel de wireproxy
        # (lo hace el detector al ver la cabecera x-grab-tunel). Para el
        # resto, el sitio es publico y conviene ir directo: asi la busqueda
        # funciona aunque el motor todavia no este arrancado.
        handlers = []
        if vpn:
            proxy = self.ajustes["proxy"]
            if proxy:
                url_proxy = "http://" + proxy
                handlers.append(urllib.request.ProxyHandler(
                    {"http": url_proxy, "https": url_proxy}))
        handlers.append(urllib.request.HTTPSHandler(context=_CONTEXTO_SSL))
        return urllib.request.build_opener(*handlers)

    def _pedir(self, url, vpn=False, tope=10):
        sup = _supervisor(self.ajustes.scripts)
        req = urllib.request.Request(url, headers=dict(CABECERAS_NAVEGADOR))
        if vpn:
            req.add_header(sup.CABECERA_TUNEL, "1")
        with self._abridor(vpn).open(req, timeout=tope) as r:
            return r.status, r.read()

    # -- estado online (el metodo de supervisor.py) -----------------------

    def estado(self, nombre: str, vpn: bool = False) -> dict:
        """-> {"estado": "public"/"off"/"?"/"http_..."/"error:...", "id": str|None}"""
        sup = _supervisor(self.ajustes.scripts)
        url = self.ajustes["sitio"].rstrip("/") + "/" + nombre
        try:
            status, datos = self._pedir(url, vpn=vpn)
        except urllib.error.HTTPError as e:
            return {"estado": "http_%d" % e.code, "id": None}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"estado": "error:%s" % type(e).__name__, "id": None}
        if status != 200:
            return {"estado": "http_%d" % status, "id": None}
        texto = datos.decode("utf-8", errors="replace")
        m = sup.RE_ESTADO.search(texto)
        if not m:
            return {"estado": "?", "id": None}
        # el id va al principio del mismo fragmento que matcheo RE_ESTADO:
        # '"model":{"id":NNNN....."status":"..."'
        m_id = re.search(r'"id":(\d+)', m.group(0))
        return {"estado": m.group(1), "id": m_id.group(1) if m_id else None}

    def estado_cacheado(self, nombre: str, vpn: bool = False) -> dict:
        clave = nombre.strip().lower()
        ahora = time.time()
        with self._lock:
            visto = self._cache_estado.get(clave)
        if visto and ahora - visto[0] < CACHE_ESTADO_SEGUNDOS:
            return visto[1]
        resultado = self.estado(nombre, vpn=vpn)
        with self._lock:
            self._cache_estado[clave] = (ahora, resultado)
        return resultado

    # -- busqueda (endpoint no documentado; con fallback a "nada") --------

    def buscar(self, consulta: str, limite: int = 8, vpn: bool = False) -> list:
        """-> lista de dicts (username, id, avatar, online, genero).
        [] si la consulta es muy corta, si el sitio no respondio, o si
        cambio el endpoint -- nunca lanza, para no romper la vista de
        Modelos por esto."""
        consulta = (consulta or "").strip()
        if len(consulta) < MIN_CONSULTA:
            return []
        clave = (consulta.lower(), limite)
        ahora = time.time()
        with self._lock:
            visto = self._cache_busqueda.get(clave)
        if visto and ahora - visto[0] < CACHE_BUSQUEDA_SEGUNDOS:
            return visto[1]

        url = (self.ajustes["sitio"].rstrip("/") +
               "/api/front/v4/models/search/suggestion?query=" +
               urllib.parse.quote(consulta) + "&limit=%d&primaryTag=girls" % limite)
        resultados = []
        try:
            status, datos = self._pedir(url, vpn=vpn)
            if status == 200:
                obj = json.loads(datos.decode("utf-8", errors="replace"))
                for m in obj.get("models", []):
                    resultados.append({
                        "username": m.get("username", ""),
                        "id": m.get("id"),
                        "avatar": m.get("avatarUrl") or "",
                        "online": bool(m.get("isOnline") or m.get("isLive")),
                        "genero": m.get("gender", ""),
                    })
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError, ValueError):
            pass    # el endpoint no es oficial: si falla, se degrada a []

        with self._lock:
            self._cache_busqueda[clave] = (ahora, resultados)
        return resultados

    # -- miniaturas (proxy de imagen) --------------------------------------

    def imagen(self, url: str, vpn: bool = False):
        """-> (content_type, bytes) | None. Pide la imagen con el
        Referer que el sitio exige (el navegador no puede mandarlo
        solo, ver CABECERAS_IMAGEN mas arriba) y la devuelve tal cual,
        para que servidor.py se la pase al navegador. Solo pide a los
        hosts de HOSTS_IMAGEN_PERMITIDOS -- cualquier otra cosa se
        descarta sin ni intentar el pedido. Nunca lanza: si la
        miniatura no carga, la tarjeta se queda sin ella (el cliente
        ya tiene onerror="this.remove()").

        Cacheada en RAM (ver CACHE_IMAGEN_* arriba): un hit no sale a la
        red ni se queda con el hilo del pool."""
        try:
            partes = urllib.parse.urlsplit(url)
        except ValueError:
            return None
        if partes.scheme not in ("http", "https") or partes.netloc not in HOSTS_IMAGEN_PERMITIDOS:
            return None

        guardado = self._imagen_cacheada(url)
        if guardado is not _AUSENTE:
            return guardado

        cabeceras = dict(CABECERAS_IMAGEN)
        cabeceras["Referer"] = self.ajustes["sitio"].rstrip("/") + "/"
        req = urllib.request.Request(url, headers=cabeceras)
        resultado = None
        # El lock NUNCA se tiene tomado durante el open(), igual que en
        # estado_cacheado()/buscar(): es un pedido de red de hasta
        # TOPE_IMAGEN segundos, y adentro del lock serializaria a todos
        # los hilos del pool que esten sirviendo miniaturas.
        try:
            with self._abridor(vpn).open(req, timeout=TOPE_IMAGEN) as r:
                if r.status == 200:
                    resultado = (r.headers.get("Content-Type", "image/jpeg"),
                                  r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            resultado = None
        self._guardar_imagen(url, resultado)
        return resultado

    def _imagen_cacheada(self, url: str):
        """-> el resultado guardado (puede ser None, un fallo cacheado), o
        _AUSENTE si no hay nada vigente. None es un valor valido de cache,
        asi que no sirve de centinela."""
        ahora = time.time()
        with self._lock:
            visto = self._cache_imagen.get(url)
            if visto is None:
                return _AUSENTE
            guardado, resultado = visto
            tope = (CACHE_IMAGEN_SEGUNDOS if resultado is not None
                    else CACHE_IMAGEN_FALLO_SEGUNDOS)
            if ahora - guardado >= tope:
                del self._cache_imagen[url]
                self._bytes_imagen -= len(resultado[1]) if resultado else 0
                return _AUSENTE
            self._cache_imagen.move_to_end(url)   # LRU: recien usada
            return resultado

    def _guardar_imagen(self, url: str, resultado) -> None:
        with self._lock:
            viejo = self._cache_imagen.pop(url, None)
            if viejo is not None and viejo[1] is not None:
                self._bytes_imagen -= len(viejo[1][1])
            self._cache_imagen[url] = (time.time(), resultado)
            if resultado is not None:
                self._bytes_imagen += len(resultado[1])
            while (len(self._cache_imagen) > CACHE_IMAGEN_ENTRADAS
                    or self._bytes_imagen > CACHE_IMAGEN_BYTES):
                _, (_, fuera) = self._cache_imagen.popitem(last=False)
                if fuera is not None:
                    self._bytes_imagen -= len(fuera[1])
