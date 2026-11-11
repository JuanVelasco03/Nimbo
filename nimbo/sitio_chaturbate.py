"""Consultas al sitio chaturbate: estado online, miniatura y (desde
SPEC 21, con cuenta) busqueda. Subclase de sitio.SitioBase, que es donde
vive lo que de verdad se comparte con stripchat (transporte, las dos
caches con TTL, la LRU de miniaturas). Ver sitio.py para esas piezas;
aca solo lo especifico del sitio nuevo.

Busqueda con autocompletado (SPEC 21): sin cuenta (medido en SPEC 18)
chaturbate no expone nada parecido a
"/api/front/v4/models/search/suggestion" de stripchat. CON cuenta SI --
medido en vivo el 2026-11-10 (ver sesion_chaturbate.py): el mismo
endpoint que da las seguidas (ENDPOINT_ROOM_LIST) acepta "keywords=" y
busca por nombre/tema en el CATALOGO ENTERO, no solo en las seguidas
(confirmado: una busqueda devolvio resultados con "is_following": false
mezclados con los propios seguidos). buscar() reusa
SesionChaturbate._normalizar_modelos -- misma forma de fila que las
seguidas. Sin sesion guardada sigue devolviendo [] -- el mismo "se
degrada a nada" de siempre (Decisiones de SPEC 18: "si no lo expone,
'agregar modelo' es un campo de texto simple, sin autocompletado"; acá
la salvedad es "sin cuenta", no "nunca").

Sin VPN: chaturbate no pasa por el proxy de grabacion (fuera de alcance,
SPEC 18), asi que estado()/imagen() de aca nunca reciben vpn=True -- a
diferencia de Sitio, que sí lo necesita para las modelos marcadas @vpn=.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from sitio import SitioBase, CACHE_BUSQUEDA_SEGUNDOS, MIN_CONSULTA
from sesion import _quitar_header
import sesion_chaturbate as sesion_chaturbate_mod

_chaturbate_cache = {}


def _chaturbate(ruta_scripts):
    """Importa nucleo\\scripts\\chaturbate.py -- protocolo puro, igual de
    seguro de importar que mouflon.py (ver su docstring): sin
    'addons = [...]', sin estado de modulo mutable."""
    mod = _chaturbate_cache.get(ruta_scripts)
    if mod is None:
        if ruta_scripts not in sys.path:
            sys.path.insert(0, ruta_scripts)
        import chaturbate as mod
        _chaturbate_cache[ruta_scripts] = mod
    return mod


class Chaturbate(SitioBase):
    CLAVE_SITIO = "sitio_chaturbate"
    # jpeg.live.mmcdn.com: snapshot en vivo (chaturbate.url_snapshot).
    # thumb.live.mmcdn.com: miniatura de catalogo, mas chica -- tambien
    # confirmada en vivo (ver pruebas\sonda_chaturbate.py).
    HOSTS_IMAGEN_PERMITIDOS = frozenset({"jpeg.live.mmcdn.com", "thumb.live.mmcdn.com"})

    CACHE_ESTADO_SEGUNDOS = 20.0

    def __init__(self, ajustes):
        super().__init__(ajustes)

    # -- estado online -----------------------------------------------------

    def estado(self, nombre: str) -> dict:
        """-> {"estado": "public"/"?"/"http_..."/"error:...", "id": str|None}.
        Mismo shape que Sitio.estado (sin "vpn": chaturbate no lo usa)."""
        cb = _chaturbate(self.ajustes.scripts)
        info = cb.consultar_estado(nombre)
        return {"estado": info["estado"], "id": info["id"]}

    def estado_cacheado(self, nombre: str) -> dict:
        clave = nombre.strip().lower()
        ahora = time.time()
        with self._lock:
            visto = self._cache_estado.get(clave)
        if visto and ahora - visto[0] < self.CACHE_ESTADO_SEGUNDOS:
            return visto[1]
        resultado = self.estado(nombre)
        with self._lock:
            self._cache_estado[clave] = (ahora, resultado)
        return resultado

    # -- busqueda ------------------------------------------------------------

    def buscar(self, consulta: str, limite: int = 8) -> list:
        """-> [] si no hay sesion de chaturbate guardada, la consulta es
        muy corta, o el pedido fallo -- nunca lanza, misma filosofia que
        Sitio.buscar(). Mismo endpoint y misma forma de fila que las
        seguidas (ver sesion_chaturbate.py), con "keywords=" en vez de
        "follow=true": busca en el catalogo entero, no solo en lo
        seguido."""
        consulta = (consulta or "").strip()
        if len(consulta) < MIN_CONSULTA:
            return []
        sesion_obj = sesion_chaturbate_mod.SesionChaturbate(self.ajustes.estado_sesion_chaturbate)
        datos = sesion_obj.leer()
        cookies = (datos or {}).get("cookies") or {}
        if not cookies:
            return []   # sin sesion guardada: se degrada a nada, como siempre

        clave = (consulta.lower(), limite)
        ahora = time.time()
        with self._lock:
            visto = self._cache_busqueda.get(clave)
        if visto and ahora - visto[0] < CACHE_BUSQUEDA_SEGUNDOS:
            return visto[1]

        cabeceras = dict(datos.get("headers") or {})
        # Sin esto el pedido sale con el "Cookie" viejo (el que habia AL
        # MOMENTO de loguearse) duplicado junto al nuevo, y con el
        # "Accept-Encoding" pegado (gzip/br) el sitio comprime la
        # respuesta -- urllib no descomprime solo, asi que json.loads()
        # se rompia en silencio sobre los bytes crudos (mismo bug que
        # describe sesion.py:_pedir_pagina).
        _quitar_header(cabeceras, "Cookie")
        _quitar_header(cabeceras, "Accept-Encoding")
        cabeceras["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
        cabeceras["X-Requested-With"] = "XMLHttpRequest"
        cabeceras.setdefault("Accept", "application/json, text/*;q=0.8, */*;q=0.5")

        resultados = []
        qs = urllib.parse.urlencode({"keywords": consulta, "offset": 0, "limit": limite})
        url = sesion_chaturbate_mod.ENDPOINT_ROOM_LIST + "?" + qs
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=cabeceras), timeout=10.0) as r:
                obj = json.loads(r.read())
            resultados = sesion_obj._normalizar_modelos(obj)[:limite]
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError, ValueError):
            pass   # el endpoint no es oficial: si falla, se degrada a []

        with self._lock:
            self._cache_busqueda[clave] = (ahora, resultados)
        return resultados

    # -- miniaturas ------------------------------------------------------

    def url_snapshot(self, nombre: str) -> str:
        """La URL publica y estable del snapshot en vivo (sin cookies) --
        /api/sitio/imagen la pide con el Referer que hace falta (ver
        SitioBase.imagen), igual que con las de stripchat."""
        cb = _chaturbate(self.ajustes.scripts)
        return cb.url_snapshot(nombre)
