# -*- coding: utf-8 -*-
"""mouflon.py

Biblioteca del protocolo MOUFLON v2 de doppiocdn (stripchat): descifrado de
URLs de segmento, lectura del master .m3u8 y eleccion de la mejor rendicion,
y los pedidos HTTP minimos para hablar con el sitio sin navegador. Ver
specs\\grabador_directo.md y pruebas\\RESULTADOS.md para la evidencia medida
que respalda cada decision de este archivo.

Diferencia clave con detectar_m3u8.py: ESTE modulo es importable desde
cualquier parte sin efecto alguno. detectar_m3u8.py tiene, al final,
"else: addons = [Detector()]" -- cualquier import que no sea __main__
construye un Detector entero y VIVO (con su Escritor, su _Tunel, sus
hilos). mouflon.py no define nada parecido: no abre hilos, no escribe en
disco, no tiene estado de modulo mutable. Por eso lo importan por igual
detectar_m3u8.py (el master-parsing que antes tenia duplicado) y
grabador_directo.py (todo el protocolo), sin que ninguno de los dos corra
el riesgo de levantar un Detector de mas.

Sobre KEY_MAP: guarda pkey -> KEYSTREAM (16 bytes), no pkey -> pdkey. Son
equivalentes para descifrar (keystream = sha256(pdkey)[:16], ver
RESULTADOS.md S1.1: los tokens cifrados son siempre de 16 bytes, así que
sha256 nunca cicla), pero el keystream es lo UNICO recuperable sin
ingenieria inversa de terceros si stripchat rota la tabla -- el
procedimiento de RESULTADOS.md S1.2 deriva el keystream directo de un par
cifrado/claro en disco, nunca necesita el pdkey. Por eso esta tabla no
depende de tenerlo.

verificar_canario() es el chequeo sin red: si deja de dar True, la tabla
rotó y hay que seguir RESULTADOS.md S1 para derivar una nueva.
"""

import base64
import hashlib
import json
import re
import struct
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# Constantes del sitio / CDN
# --------------------------------------------------------------------------

SITIO = "https://es.stripchat.com"
TUNEL_DEFECTO = "127.0.0.1:25345"      # mismo default que M3U8_TUNEL
                                        # (detectar_m3u8.py:166)

# Sin esto el sitio contesta 406 a lo que no parece un navegador. Portado
# de nucleo\scripts\supervisor.py:121-125.
CABECERAS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

# Mismo criterio que supervisor.py:134,137-148 -- usado por grabador_directo
# para decidir si una modelo sigue en vivo sin abrir ninguna pagina.
ESTADOS_VIVOS = {"public", "live", "groupShow", "p2p", "private", "ticketShow"}


def estado_incierto(estado: str) -> bool:
    """La consulta no pudo contestar: no dice nada de si emite o no.

    Portado de supervisor.py:137-148. Un 5xx o un 429 no es "dejo de
    emitir": es que la consulta fallo. Confundir las dos cosas cierra una
    sesion que seguia viva.
    """
    return (estado == "?" or estado.startswith("error:")
            or estado.startswith("http_5")
            or estado in {"http_408", "http_429"})


# --------------------------------------------------------------------------
# Excepciones propias (nada de reventar con KeyError/ValueError pelados)
# --------------------------------------------------------------------------

class ErrorMouflon(Exception):
    """Base de los errores de este modulo."""


class ErrorRed(ErrorMouflon):
    """Un pedido HTTP no dio lo esperado (status, timeout, JSON roto)."""


class ErrorPublicidad(ErrorMouflon):
    """La compuerta dura: esto es la playlist de publicidad, no la emision."""


# --------------------------------------------------------------------------
# HTTP minimo, con proxy opcional al tunel de la VPN (sin requests, sin
# Playwright: solo libreria estandar, igual que detectar_m3u8.py y puente.py)
# --------------------------------------------------------------------------

def _opener(por_tunel: bool, tunel: str):
    if not por_tunel:
        return urllib.request.build_opener()
    proxy = urllib.request.ProxyHandler({
        "http": "http://" + tunel, "https": "http://" + tunel,
    })
    return urllib.request.build_opener(proxy)


def pedir(url: str, por_tunel: bool = False, tunel: str = TUNEL_DEFECTO,
          cabeceras: dict = None, timeout: float = 15.0):
    """GET pelado. -> (status, url_final, cuerpo_bytes).

    Nunca lanza por un status de error HTTP (eso se interpreta aparte,
    como en el resto del protocolo): HTTPError se captura y se devuelve
    igual que una respuesta cualquiera. Solo se propagan los errores de
    red de verdad (timeout, DNS, conexion rechazada) como OSError/URLError.
    """
    todas = dict(CABECERAS)
    if cabeceras:
        todas.update(cabeceras)
    req = urllib.request.Request(url, headers=todas)
    opener = _opener(por_tunel, tunel)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url, e.read()


# --------------------------------------------------------------------------
# Descubrimiento: id por nombre, estado (cam)
# --------------------------------------------------------------------------

def resolver_id(nombre: str, por_tunel: bool = False,
                 tunel: str = TUNEL_DEFECTO, timeout: float = 15.0) -> int:
    """-> el id numerico de la modelo. Lanza ErrorRed si la API no contesta
    200 o no trae 'id' (p.ej. Cloudflare bloqueando el pedido anonimo --
    en ese caso el id conocido de antes, el de la carpeta en disco, sigue
    siendo valido para pedir el master directo)."""
    url = "%s/api/front/users/user-ids/%s?%d" % (
        SITIO, nombre, int(time.time() * 1000))
    status, _, cuerpo = pedir(url, por_tunel, tunel, timeout=timeout)
    if status != 200:
        raise ErrorRed("user-ids dio %s" % status)
    try:
        datos = json.loads(cuerpo)
    except ValueError as e:
        raise ErrorRed("user-ids: JSON invalido (%s)" % e)
    id_modelo = datos.get("id")
    if not id_modelo:
        raise ErrorRed("user-ids 200 pero sin 'id': %r" % (datos,))
    return int(id_modelo)


def consultar_cam(id_modelo, por_tunel: bool = False,
                   tunel: str = TUNEL_DEFECTO, timeout: float = 15.0) -> dict:
    """-> {"estado","disponible","show","geobaneada","crudo"}.

    "estado" es el mismo valor que supervisor.RE_ESTADO sacaba del HTML
    (ESTADOS_VIVOS de arriba lo interpreta). Lanza ErrorRed si la API no
    contesta 200 o el JSON no se puede leer.
    """
    url = ("%s/api/front/v2/models/%s/cam?timezoneOffset=0&"
           "triggerRequest=loadCam&uniq=%d"
           % (SITIO, id_modelo, int(time.time() * 1000)))
    status, _, cuerpo = pedir(url, por_tunel, tunel, timeout=timeout)
    if status != 200:
        raise ErrorRed("cam dio %s" % status)
    try:
        datos = json.loads(cuerpo)
    except ValueError as e:
        raise ErrorRed("cam: JSON invalido (%s)" % e)
    user = (datos.get("user") or {}).get("user") or {}
    cam = datos.get("cam") or {}
    return {
        "estado": user.get("status") or "?",
        "disponible": bool(cam.get("isCamAvailable")),
        "show": cam.get("show"),
        "geobaneada": bool(user.get("isGeoBanned")),
        "crudo": datos,
    }


# --------------------------------------------------------------------------
# Master playlist: variantes que ofrece la modelo, y cual es "la mejor"
#
# Esto es lo que se MOVIO desde detectar_m3u8.py (ahi quedo importando de
# aca) -- ver specs\grabador_directo.md S4.1. RE_CALIDAD/RE_ALTURA/RE_CODEC/
# RE_CALIDAD_TOKEN/nombre_archivo/altura_de son copias de detectar_m3u8.py
# (lineas 232-239,329-332,718-719), necesarias para que leer_master()
# funcione sola aca: se duplican a proposito, mismo patron que ya usan
# vivo.py/mp4.py/sonda_directo.py con otras constantes de ese modulo.
# --------------------------------------------------------------------------

RE_CALIDAD = re.compile(r"(\d{3,4}p(?:\d{2})?)", re.I)
RE_CALIDAD_TOKEN = re.compile(r"[_/.-](\d{3,4}p(?:\d{2})?)(?=[_/.-]|$)", re.I)
RE_CODEC = re.compile(r"(h264|h265|hevc|av1|vp9)", re.I)
RE_ALTURA = re.compile(r"^(\d{3,4})p")

RE_ATTR_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
RE_ATTR_BW = re.compile(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", re.I)
RE_ATTR_CODECS = re.compile(r'CODECS="([^"]*)"', re.I)

CODECS_HLS = (
    ("av01", "av1"), ("avc1", "h264"), ("avc3", "h264"),
    ("hvc1", "hevc"), ("hev1", "hevc"), ("dvh", "hevc"), ("vp09", "vp9"),
)

# la fuente ("source") no lleva calidad en su URL: sin altura real conocida
# le gana a cualquier rendicion con calidad explicita (ver altura(), mas abajo)
ALTO_FUENTE = 99999


def nombre_archivo(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]


def altura_de(calidad: str) -> int:
    """'1080p60' -> 1080. 'auto' (la fuente) -> 0: no la dice el nombre."""
    m = RE_ALTURA.match(calidad or "")
    return int(m.group(1)) if m else 0


def _codec_hls(atributos: str, uri: str) -> str:
    m = RE_ATTR_CODECS.search(atributos)
    if m:
        bajo = m.group(1).lower()
        for prefijo, nombre in CODECS_HLS:
            if prefijo in bajo:
                return nombre
    m = RE_CODEC.search(uri.lower()) or RE_CODEC.search(atributos.lower())
    if not m:
        return "na"
    codec = m.group(1).lower()
    return "hevc" if codec == "h265" else codec


class _Puntuable:
    """Lo minimo que necesita puntaje() para desempatar: altura, codec y
    un numero para romper empates (la banda del master, aca). No es la
    Deteccion de detectar_m3u8.py -- puntaje() no le pide el tipo, solo
    estos tres atributos (duck typing), asi que sirve igual para una
    Deteccion real (la del ABR en vivo) que para esto (una fila del
    master que todavia no se bajo nada)."""

    __slots__ = ("altura", "codec", "veces")

    def __init__(self, altura, codec, veces):
        self.altura = altura
        self.codec = codec
        self.veces = veces


class Rendicion:
    """Una linea EXT-X-STREAM-INF del master: lo que la modelo ofrece."""

    __slots__ = ("nombre", "calidad", "codec", "alto", "banda", "attrs",
                 "uri", "fuente")

    def __init__(self, nombre, calidad, codec, alto, banda, attrs, uri,
                 fuente=False):
        self.nombre = nombre
        self.calidad = calidad
        self.codec = codec
        self.alto = alto
        self.banda = banda
        self.attrs = attrs
        self.uri = uri
        self.fuente = fuente

    @property
    def borrosa(self) -> bool:
        return self.nombre.endswith("_blurred")

    def como_deteccion(self) -> _Puntuable:
        altura = self.alto if self.fuente else altura_de(self.calidad)
        if self.fuente and not altura:
            altura = ALTO_FUENTE
        return _Puntuable(altura=altura, codec=self.codec, veces=self.banda)


def leer_master(texto: str):
    """Lee las variantes de un master playlist. -> (cabecera, rendiciones, pos).

    Portado literal de detectar_m3u8.py (antes lineas 847-897).
    """
    if not texto or "#EXT-X-STREAM-INF" not in texto.upper():
        return [], [], 0

    lineas = texto.splitlines()
    cabecera, rendiciones = [], []
    pos = None
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if linea.upper().startswith("#EXT-X-STREAM-INF"):
            j = i + 1
            while j < len(lineas) and (not lineas[j].strip()
                                       or lineas[j].startswith("#")):
                j += 1
            if j >= len(lineas):
                break
            uri = lineas[j].strip()
            alto = 0
            m = RE_ATTR_RES.search(linea)
            if m:
                alto = int(m.group(2))
            else:
                q = RE_CALIDAD.search(uri)
                if q:
                    alto = int(RE_ALTURA.match(q.group(1)).group(1))
            m = RE_ATTR_BW.search(linea)
            banda = int(m.group(1)) if m else 0
            codec = _codec_hls(linea, uri)
            calidad = ("%dp" % alto) if alto else "auto"
            nombre = calidad + "_" + codec
            if "blur" in uri.lower():
                nombre += "_blurred"
            es_fuente = (
                'NAME="source"' in linea
                or RE_CALIDAD_TOKEN.search(nombre_archivo(uri)) is None
            ) and "blur" not in uri.lower()
            rendiciones.append(Rendicion(nombre=nombre, calidad=calidad,
                                         codec=codec, alto=alto, banda=banda,
                                         attrs=linea, uri=uri,
                                         fuente=es_fuente))
            if pos is None:
                pos = len(cabecera)
            i = j + 1
            continue
        cabecera.append(linea)
        i += 1

    return cabecera, rendiciones, pos or 0


def puntaje(variante: str, det, cfg) -> tuple:
    """Para elegir 'la mejor': nitida > borrosa, mas alto mejor, codec
    preferido. 'det' solo necesita .altura/.codec/.veces (ver _Puntuable):
    sirve igual con una Deteccion real de detectar_m3u8.py. Portado
    literal de detectar_m3u8.py (antes lineas 783-790)."""
    blur = variante.endswith("_blurred")
    if cfg.codec and det.codec in cfg.codec:
        pref = len(cfg.codec) - cfg.codec.index(det.codec)
    else:
        pref = 0
    return (0 if blur else 1, det.altura, pref, det.veces)


def mejor_rendicion(rendiciones, cfg):
    """La mejor del master segun los filtros de cfg; si ninguna coincide y
    cfg.fallback, la mejor a secas. cfg necesita .acepta_variante(calidad,
    codec, fuente, borrosa) y .fallback (duck typing: sirve el Config de
    detectar_m3u8.py o el de grabador_directo.py). Portado literal de
    detectar_m3u8.py (antes lineas 900-911)."""
    if not rendiciones:
        return None
    coinciden = [r for r in rendiciones
                 if cfg.acepta_variante(r.calidad, r.codec, r.fuente,
                                        r.borrosa)]
    if not coinciden:
        if not cfg.fallback:
            return None
        coinciden = rendiciones
    return max(coinciden, key=lambda r: puntaje(r.nombre, r.como_deteccion(), cfg))


# --------------------------------------------------------------------------
# Descifrado MOUFLON v2
#
# El master trae varios tokens #EXT-X-MOUFLON:PSCH:v2:<pkey> en claro; la
# media playlist real se pide agregando uno de esos a la query
# (psch=v2&pkey=<pkey>). Cada URI real de segmento viaja en una linea
# "#EXT-X-MOUFLON:URI:<url>" INMEDIATAMENTE ANTES de la linea HLS estandar
# (que siempre apunta a un senuelo fijo, .../media.mp4); esa URL real tiene
# el campo de hash cifrado: 22 caracteres base64 en vez de los 16
# alfanumericos del nombre real.
#
# Verificado en vivo (ver RESULTADOS.md S2.5): el CDN acepta cualquiera de
# los pkey que el propio master ofrece y cifra la respuesta con ESE pkey
# -- no hace falta que sea "el que le tocaba". Por eso alcanza con forzar
# siempre el unico pkey del que se tiene keystream.
# --------------------------------------------------------------------------

PKEY_VERIFICADO = "NTK9aqcLmNFMWrpQ"

# pkey -> keystream (16 bytes). Ver el docstring del modulo: por que
# keystream y no pdkey, y como derivar uno nuevo si esto deja de
# descifrar (verificar_canario(), mas abajo, es el chequeo).
KEY_MAP = {
    PKEY_VERIFICADO: bytes.fromhex("1838c78f543606e6de5fc81167f36e5a"),
}

# (pkey, cifrado_b64, claro_esperado) -- de datos\streams\_gaby2_171961503\
# playlists\171961503_1080p.m3u8 (msn 1849-1851) contra los nombres reales
# que grabo el detector en esa misma sesion. Ver RESULTADOS.md S2.1.
PARES_CANARIO = [
    (PKEY_VERIFICADO, "gNmEqXlhpNtSoM7FD7my0L", "7taceM4bsiPt9RHl"),
    (PKEY_VERIFICADO, "wOWpYF7NKauDddERS/E+UU", "IwCrprs607kjry8a"),
    (PKEY_VERIFICADO, "AHjkaLXhbG6OoaaFj1qqHc", "hBmYelledFpFJZMF"),
]

# anclado: el epoch tiene 10 digitos y puede matchear antes que el hash si
# no se ancla el patron completo (RESULTADOS.md S2.3 / grabador_directo.md S4.1)
RE_TOKEN_SEGMENTO = re.compile(r"_(\d+)_([A-Za-z0-9+/=]{22})_(\d{10})")

# nombre de un segmento de doppiocdn YA DESCIFRADO:
#   <id>[_<calidad>_<codec>]_<secuencia>_<hash>_<epoch>[_part<n>].mp4
# Copia de detectar_m3u8.py:246-249 (RE_SEGMENTO/PARTE_ENTERA).
RE_SEGMENTO = re.compile(
    r"_(\d+)_[A-Za-z0-9]+_(\d+)(?:_part(\d+))?\.[A-Za-z0-9]+$")
PARTE_ENTERA = 1 << 30

RE_TARGETDURATION = re.compile(r"#EXT-X-TARGETDURATION:(\d+(?:\.\d+)?)")
RE_MAP_URI = re.compile(r'#EXT-X-MAP:URI="([^"]+)"')


def descifrar_token(token_b64: str, keystream: bytes) -> str:
    """token invertido + base64 -> XOR contra el keystream, ciclado (nunca
    cicla en la practica: los tokens son de 16 bytes y el keystream
    tambien, ver RESULTADOS.md S1.1)."""
    invertido = token_b64[::-1]
    invertido += "=" * (-len(invertido) % 4)
    crudo = base64.b64decode(invertido)
    return bytes(b ^ keystream[i % len(keystream)]
                 for i, b in enumerate(crudo)).decode("utf-8", "replace")


def es_token_valido(claro: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{16}", claro))


def verificar_canario(key_map=None) -> bool:
    """Sin red, instantaneo: True si KEY_MAP sigue descifrando los pares
    conocidos. False = la tabla de claves roto; seguir RESULTADOS.md S1
    para derivar una nueva desde una sesion grabada por el navegador."""
    key_map = key_map if key_map is not None else KEY_MAP
    for pkey, cifrado, esperado in PARES_CANARIO:
        keystream = key_map.get(pkey)
        if not keystream:
            return False
        if descifrar_token(cifrado, keystream) != esperado:
            return False
    return True


def clave_segmento(nombre: str):
    """-> (epoch, secuencia, parte) o None. Copia de
    detectar_m3u8.py:950-963 -- manda el epoch, no la secuencia (un
    reinicio de emision la vuelve a cero)."""
    m = RE_SEGMENTO.search(nombre)
    if not m:
        return None
    parte = m.group(3)
    return (int(m.group(2)), int(m.group(1)),
            int(parte) if parte is not None else PARTE_ENTERA)


def es_publicidad(texto: str, url: str) -> bool:
    """Compuerta dura: NUNCA grabar esto como si fuera la emision. Pedir
    la variante sin pkey (o con uno sin keystream) redirige aca."""
    return ("/cpa/" in url or "MOUFLON-ADVERT" in texto
            or "PLAYLIST-TYPE:VOD" in texto or "#EXT-X-ENDLIST" in texto)


def descifrar_playlist(texto: str, key_map=None):
    """Descifra en el lugar cada "#EXT-X-MOUFLON:URI:<cifrado>" de una
    media playlist real. -> (texto_descifrado, fallos), con fallos =
    [(secuencia, token_b64), ...] de lo que no se pudo descifrar (3
    fallos seguidos en grabador_directo.py es la senal de 'degradado',
    ver specs\\grabador_directo.md S4.2 paso 8)."""
    key_map = key_map if key_map is not None else KEY_MAP
    tokens_vistos = {}
    fallos = []
    terminador = "\r\n" if "\r\n" in texto else "\n"
    salida = []
    for ln in texto.splitlines():
        if ln.startswith("#EXT-X-MOUFLON:URI:"):
            m = RE_TOKEN_SEGMENTO.search(ln)
            if m:
                secuencia, token_b64 = m.group(1), m.group(2)
                claro = tokens_vistos.get(token_b64)
                if not claro:
                    for keystream in key_map.values():
                        candidato = descifrar_token(token_b64, keystream)
                        if es_token_valido(candidato):
                            claro = candidato
                            break
                    tokens_vistos[token_b64] = claro
                if claro:
                    ln = ln.replace(token_b64, claro)
                else:
                    fallos.append((secuencia, token_b64))
        salida.append(ln)
    nuevo = terminador.join(salida)
    if texto.endswith(("\n", "\r")):
        nuevo += terminador
    return nuevo, fallos


def partir_fragmentos(datos: bytes):
    """Si 'datos' trae mas de un 'moof' (un .mp4 de playlistType=standard
    suele venir con varios sub-fragmentos CMAF de 0.5s pegados en un solo
    archivo, uno por cada #EXT-X-PART que tendria la variante lowLatency),
    lo separa en una lista de bytes con UN 'moof' cada una.

    Hace falta porque detectar_m3u8.Reencadenador (compartido con la union,
    invocada por subproceso) asume un 'moof' por archivo -- es lo que ve
    normalmente intercepando al navegador, que pide los PARTS sueltos. Con
    varios 'moof' en un mismo archivo, su deteccion de huecos compara cada
    sub-fragmento contra el 'fin' del segmento ANTERIOR entero (no se
    actualiza hasta terminar de procesar todo el archivo) y los confunde
    con saltos hacia adelante: medido en vivo, truncaba un video de 5
    minutos a 75 segundos (298s -> 74.87s, descartando 3 de cada 4
    sub-fragmentos). Partirlos aca, antes de escribir a disco, hace que
    cada llamada a Reencadenador.arreglar() vea un solo 'moof' por pista,
    igual que con el camino del navegador -- sin tocar ese modulo.

    Los nombres resultantes llevan "_partN" antes de la extension
    (clave_segmento()/detectar_m3u8.clave_segmento ya entienden ese sufijo:
    es el mismo que usan los PARTS de LL-HLS)."""
    n = len(datos)
    pos = 0
    moof_inicios = []
    while pos + 8 <= n:
        largo = struct.unpack(">I", datos[pos:pos + 4])[0]
        tipo = datos[pos + 4:pos + 8]
        cabecera = 8
        if largo == 1:
            if pos + 16 > n:
                break
            largo = struct.unpack(">Q", datos[pos + 8:pos + 16])[0]
            cabecera = 16
        if largo < 8 or pos + largo > n:
            break
        if tipo == b"moof":
            moof_inicios.append(pos)
        pos += largo
    if len(moof_inicios) <= 1:
        return [datos]
    # el primer fragmento se queda con cualquier styp/sidx que lo preceda;
    # cada corte siguiente arranca justo en el proximo 'moof'
    bordes = [0] + moof_inicios[1:] + [n]
    return [datos[bordes[i]:bordes[i + 1]] for i in range(len(bordes) - 1)]


def extraer_segmentos(texto_descifrado: str):
    """-> (init_url, target_duration, [(msn, epoch, url), ...]) de una
    media playlist YA DESCIFRADA (ver descifrar_playlist). El orden es el
    de aparicion en la playlist. init_url es None si no hubo
    #EXT-X-MAP en este ciclo (no deberia pasar en una playlist real)."""
    target = 2.0
    m = RE_TARGETDURATION.search(texto_descifrado)
    if m:
        target = float(m.group(1))
    m = RE_MAP_URI.search(texto_descifrado)
    init_url = m.group(1) if m else None

    segmentos = []
    for ln in texto_descifrado.splitlines():
        if not ln.startswith("#EXT-X-MOUFLON:URI:"):
            continue
        url = ln.split(":URI:", 1)[1].strip()
        nombre = nombre_archivo(url)
        clave = clave_segmento(nombre)
        if clave is None:
            continue
        segmentos.append((clave[1], clave[0], url))      # (msn, epoch, url)
    return init_url, target, segmentos


# --------------------------------------------------------------------------
# Master + media: el camino completo, de un tiron
# --------------------------------------------------------------------------

def url_master(id_modelo) -> str:
    return "https://edge-hls.doppiocdn.com/hls/%s/master/%s_auto.m3u8" % (
        id_modelo, id_modelo)


def pedir_master(id_modelo, por_tunel: bool = False,
                  tunel: str = TUNEL_DEFECTO, timeout: float = 15.0) -> str:
    """-> el texto crudo del master (sin descifrar: el master no lleva
    nada cifrado, solo la media playlist). Lanza ErrorRed si no da 200 o
    no parece un master valido."""
    status, url_final, cuerpo = pedir(url_master(id_modelo), por_tunel,
                                       tunel, timeout=timeout)
    if status == 404:
        raise ErrorRed("master 404: la modelo no esta emitiendo (o cambio de id)")
    if status != 200:
        raise ErrorRed("master dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in texto:
        raise ErrorMouflon("master 200 pero sin #EXT-X-STREAM-INF")
    return texto


def pedir_media(uri_variante: str, pkey: str, por_tunel: bool = False,
                 tunel: str = TUNEL_DEFECTO, timeout: float = 15.0) -> str:
    """Pide la media playlist real (forzando psch=v2&pkey=<pkey>,
    playlistType=standard: ver specs\\grabador_directo.md S2.1/S3) y la
    devuelve YA DESCIFRADA (ver descifrar_playlist). Lanza ErrorPublicidad
    si el pkey no tiene keystream valido (el CDN cae al senuelo de
    publicidad) o ErrorRed/ErrorMouflon ante cualquier otra respuesta
    rara."""
    base = uri_variante.split("?", 1)[0]
    url = base + "?playlistType=standard&preferredVideoCodec=h264&psch=v2&pkey=" + pkey
    status, url_final, cuerpo = pedir(url, por_tunel, tunel, timeout=timeout)
    if status != 200:
        raise ErrorRed("media playlist dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if es_publicidad(texto, url_final):
        raise ErrorPublicidad(
            "la variante con pkey=%s dio publicidad (url final: %s)"
            % (pkey, url_final))
    if "#EXT-X-MAP:URI=" not in texto or "#EXT-X-MOUFLON:URI:" not in texto:
        raise ErrorMouflon("200 pero no es la playlist real (sin MAP/MOUFLON)")
    texto_claro, _fallos = descifrar_playlist(texto)
    return texto_claro


if __name__ == "__main__":
    # Canario sin red: python mouflon.py
    if verificar_canario():
        print("OK: %d/%d pares canario descifran correcto -- la tabla de "
              "claves sigue vigente" % (len(PARES_CANARIO), len(PARES_CANARIO)))
        raise SystemExit(0)
    print("FALLA: la tabla de claves rotó (ver pruebas\\RESULTADOS.md S1 "
          "para derivar una nueva)")
    raise SystemExit(1)
