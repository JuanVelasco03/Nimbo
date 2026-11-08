# -*- coding: utf-8 -*-
"""chaturbate.py

Biblioteca del protocolo de chaturbate: estado de una sala, lectura del
master LL-HLS y eleccion de la mejor rendicion, y los pedidos HTTP minimos
para hablar con el sitio sin navegador. Mismo criterio que hace seguro a
mouflon.py (ver su docstring): este modulo es importable desde cualquier
lado sin efecto alguno -- sin "addons = [...]", sin estado de modulo
mutable, solo libreria estandar. Por eso lo importa
grabador_chaturbate.py sin peligro de levantar nada de mas.

Toda la evidencia de abajo sale de medir contra el sitio real el
2026-11-08 (ver pruebas\\sonda_chaturbate.py, que corrio las fases 0 a 7 sin
fallar -- 0 huecos en 1.5 min sostenidos). A diferencia de MOUFLON (SPEC 03,
mouflon.py), chaturbate **no envuelve nada en un token propio que haya que
descifrar**: el master trae un JWT de corta vida en su propia URL (lo da
/api/chatvideocontext/) y la media playlist (la "chunklist") trae las URLs
de segmento en texto plano. Por eso este modulo no tiene ningun
verificar_canario(): no hay tabla de claves que pueda rotar.

Diferencia de identidad con stripchat: chaturbate **no tiene id numerico**.
"broadcaster_uid"/"room_uid" es un token alfanumerico de 7 caracteres (ej.
"H349Y5V"), nunca un numero -- la identidad estable de una sala es su
propio username. Por eso resolver_id() de aca abajo devuelve ese token
alfanumerico (decision tomada en la implementacion de SPEC 18: usar el uid
real en vez de inventar un id sintetico, y aflojar
nimbo\\grabaciones.RE_NOMBRE de \\d+ a [A-Za-z0-9]+ para que lo siga
aceptando).
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# Constantes del sitio / CDN
# --------------------------------------------------------------------------

SITIO = "https://chaturbate.com"

# Sin un User-Agent de navegador real, varios endpoints de chaturbate dan
# 403. Portado del mismo criterio que mouflon.CABECERAS.
CABECERAS = {
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

# Unico valor de room_status visto en vivo que significa "transmitiendo en
# publico, hay algo que grabar" (sonda_chaturbate.py fase 1). Otros valores
# posibles del sitio (privado, away, offline, etc.) no se midieron a
# proposito: SPEC 18 no agrega equivalentes de @privado= para este sitio
# ("chaturbate graba lo que haya, sin seleccion de calidad" -- Decisiones),
# asi que todo lo que no sea "public" se trata por igual como "no hay nada
# que grabar ahora".
ESTADO_PUBLICO = "public"


def estado_incierto(estado: str) -> bool:
    """La consulta no pudo contestar: no dice nada de si la sala sigue en
    vivo o no. Mismo criterio que mouflon.estado_incierto -- un 5xx/429 no
    es "se fue", es que la consulta fallo."""
    return (estado in ("?", None) or estado.startswith("error:")
            or estado.startswith("http_5")
            or estado in {"http_408", "http_429"})


# --------------------------------------------------------------------------
# Excepciones propias
# --------------------------------------------------------------------------

class ErrorChaturbate(Exception):
    """Base de los errores de este modulo."""


class ErrorRed(ErrorChaturbate):
    """Un pedido HTTP no dio lo esperado (status, timeout, JSON roto)."""


# --------------------------------------------------------------------------
# HTTP minimo (solo libreria estandar). Chaturbate queda FUERA del proxy de
# grabacion a proposito (SPEC 18, fuera de alcance: VPN/tunel para el sitio
# nuevo no pasa por GRAB_PROXY) -- por eso, a diferencia de mouflon.pedir,
# esto no tiene parametro "por_tunel".
# --------------------------------------------------------------------------

def pedir(url: str, cabeceras: dict = None, timeout: float = 15.0):
    """GET pelado. -> (status, url_final, cuerpo_bytes).

    Nunca lanza por un status de error HTTP (se interpreta aparte, como en
    el resto del protocolo): HTTPError se captura y se devuelve igual que
    una respuesta cualquiera. Solo se propagan los errores de red de
    verdad (timeout, DNS, conexion rechazada) como OSError/URLError."""
    todas = dict(CABECERAS)
    if cabeceras:
        todas.update(cabeceras)
    req = urllib.request.Request(url, headers=todas)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url, e.read()


# --------------------------------------------------------------------------
# Estado de la sala (reemplaza a resolver_id()+consultar_cam() de mouflon:
# chatvideocontext da las dos cosas en un solo pedido)
# --------------------------------------------------------------------------

def consultar_estado(nombre: str, timeout: float = 15.0) -> dict:
    """-> {"estado", "id", "hls_source", "espectadores", "crudo"}.

    "estado" es "public"/"?"/"http_NNN"/"error:TipoDeExcepcion" (ver
    estado_incierto() arriba). "id" es el token alfanumerico de la sala
    (broadcaster_uid/room_uid) o None si la respuesta no lo trajo. Nunca
    lanza: una sala que no existe o un pedido que falla da
    estado="http_404"/"error:...", igual de inerte para el llamador que
    "no esta en vivo"."""
    url = "%s/api/chatvideocontext/%s/" % (SITIO, nombre)
    try:
        status, _, cuerpo = pedir(url, timeout=timeout)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"estado": "error:%s" % type(e).__name__, "id": None,
                "hls_source": "", "espectadores": None, "crudo": {}}
    if status != 200:
        return {"estado": "http_%d" % status, "id": None, "hls_source": "",
                "espectadores": None, "crudo": {}}
    try:
        datos = json.loads(cuerpo)
    except ValueError:
        return {"estado": "?", "id": None, "hls_source": "",
                "espectadores": None, "crudo": {}}
    uid = datos.get("broadcaster_uid") or datos.get("room_uid")
    return {
        "estado": datos.get("room_status") or "?",
        "id": str(uid) if uid else None,
        "hls_source": datos.get("hls_source") or "",
        "espectadores": datos.get("num_viewers"),
        "crudo": datos,
    }


def resolver_id(nombre: str, timeout: float = 15.0) -> str:
    """-> el token alfanumerico de la sala. Lanza ErrorRed si
    chatvideocontext no contesta 200 o no trae ningun uid -- en la
    practica, solo pasa si la sala no existe o el sitio esta caido."""
    info = consultar_estado(nombre, timeout=timeout)
    if not info["id"]:
        raise ErrorRed("chatvideocontext sin id para %r (estado=%s)"
                        % (nombre, info["estado"]))
    return info["id"]


# --------------------------------------------------------------------------
# Master LL-HLS: que rendiciones ofrece la sala, y cual es "la mejor"
#
# Nunca hay que elegir por @calidad/@codec (fuera de alcance, SPEC 18:
# "chaturbate graba lo que haya, sin seleccion de calidad") -- mejor_rendicion
# de aca abajo siempre es "la de mayor bandwidth", sin ningun Config de por
# medio (a diferencia de mouflon.mejor_rendicion).
# --------------------------------------------------------------------------

RE_ATTR_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
RE_ATTR_BW = re.compile(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", re.I)
RE_ATTR_CODECS = re.compile(r'CODECS="([^"]*)"', re.I)

# Mismo mapeo que mouflon.CODECS_HLS: el prefijo RFC6381 del atributo
# CODECS -> la etiqueta corta que ya usa el resto de Nimbo en nombres de
# archivo/carpeta.
_CODECS_HLS = (
    ("av01", "av1"), ("avc1", "h264"), ("avc3", "h264"),
    ("hvc1", "hevc"), ("hev1", "hevc"), ("vp09", "vp9"),
)


def _codec_de(codecs_attr: str) -> str:
    bajo = (codecs_attr or "").lower()
    for prefijo, nombre in _CODECS_HLS:
        if prefijo in bajo:
            return nombre
    return "na"


class Rendicion:
    __slots__ = ("resolucion", "bandwidth", "codecs", "uri", "audio_group")

    def __init__(self, resolucion, bandwidth, codecs, uri, audio_group=None):
        self.resolucion = resolucion
        self.bandwidth = bandwidth
        self.codecs = codecs
        self.uri = uri
        # SPEC 18 (hallazgo posterior): chaturbate NO muxa audio+video en
        # un solo stream como stripchat -- el audio es una rendicion HLS
        # aparte (#EXT-X-MEDIA:TYPE=AUDIO), asociada por GROUP-ID via el
        # atributo AUDIO="..." de la linea #EXT-X-STREAM-INF. Sin esto,
        # el grabador solo pedia el chunklist de video: grababa mudo.
        # None si la rendicion no declara ningun grupo de audio (pasa en
        # algunas calidades/modelos -- ahi no hay audio que grabar).
        self.audio_group = audio_group

    @property
    def calidad(self) -> str:
        """'1920x1080' -> '1080p'. Sin resolucion declarada: 'auto'."""
        if "x" in (self.resolucion or ""):
            return self.resolucion.split("x", 1)[1] + "p"
        return "auto"

    @property
    def codec(self) -> str:
        return _codec_de(self.codecs)

    @property
    def nombre(self) -> str:
        """Para fila_estado()/el nombre de carpeta de sesion -- mismo
        formato "<calidad>_<codec>" que mouflon.Rendicion.nombre (ej.
        "1080p_h264"). Necesario para que el .mp4 final matchee
        nimbo\\grabaciones.RE_NOMBRE (<modelo>_<id>_<calidad>_<codec>_
        <fecha>.mp4) -- con un solo token el nombre final quedaba corto y
        caia al grupo "sin-convencion" (medido en la verificacion de SPEC
        18, paso 7)."""
        return self.calidad + "_" + self.codec


def pedir_master(hls_source: str, timeout: float = 15.0):
    """hls_source YA es la URL completa (con el JWT de corta vida que dio
    chatvideocontext) -- a diferencia de mouflon.pedir_master, aca no hay
    un id_modelo con el que construir la URL del master: el JWT es de un
    solo uso efimero y hay que pedirlo de nuevo cada vez que hace falta un
    master fresco (ver docstring del modulo). -> (texto, url_final). Lanza
    ErrorRed si no da 200 o no es un master valido."""
    status, url_final, cuerpo = pedir(hls_source, timeout=timeout)
    if status != 200:
        raise ErrorRed("master dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in texto:
        raise ErrorChaturbate("master 200 pero sin #EXT-X-STREAM-INF")
    return texto, url_final


RE_ATTR_AUDIO = re.compile(r'AUDIO="([^"]*)"')
RE_ATTR_GROUP_ID = re.compile(r'GROUP-ID="([^"]*)"')


def leer_master(texto: str, url_final: str) -> list:
    """-> [Rendicion, ...], con uri ya absoluta (urljoin contra
    url_final -- el master puede traer URIs relativas)."""
    rendiciones = []
    lineas = texto.splitlines()
    for i, ln in enumerate(lineas):
        if not ln.startswith("#EXT-X-STREAM-INF"):
            continue
        uri = lineas[i + 1].strip() if i + 1 < len(lineas) else None
        if not uri:
            continue
        m_res = RE_ATTR_RES.search(ln)
        m_bw = RE_ATTR_BW.search(ln)
        m_codecs = RE_ATTR_CODECS.search(ln)
        m_audio = RE_ATTR_AUDIO.search(ln)
        rendiciones.append(Rendicion(
            resolucion=(m_res.group(1) + "x" + m_res.group(2)) if m_res else "",
            bandwidth=int(m_bw.group(1)) if m_bw else 0,
            codecs=m_codecs.group(1) if m_codecs else "",
            uri=urllib.parse.urljoin(url_final, uri),
            audio_group=m_audio.group(1) if m_audio else None,
        ))
    return rendiciones


def audio_uris(texto: str, url_final: str) -> dict:
    """-> {group_id: uri_absoluta} de cada "#EXT-X-MEDIA:TYPE=AUDIO" del
    master -- la rendicion de audio asociada a Rendicion.audio_group (ver
    leer_master). Las lineas #EXT-X-MEDIA van SUELTAS en el master (no
    llevan una URI en la linea siguiente como #EXT-X-STREAM-INF: la URI
    es un atributo mas, URI="...")."""
    salida = {}
    for ln in texto.splitlines():
        if not ln.startswith("#EXT-X-MEDIA:") or "TYPE=AUDIO" not in ln:
            continue
        m_grupo = RE_ATTR_GROUP_ID.search(ln)
        m_uri = re.search(r'URI="([^"]+)"', ln)
        if m_grupo and m_uri:
            salida[m_grupo.group(1)] = urllib.parse.urljoin(url_final, m_uri.group(1))
    return salida


def mejor_rendicion(rendiciones: list):
    """-> la Rendicion de mayor bandwidth, o None si la lista esta vacia.
    Sin Config: chaturbate no selecciona calidad (ver docstring de la
    seccion)."""
    return max(rendiciones, key=lambda r: r.bandwidth) if rendiciones else None


# --------------------------------------------------------------------------
# Chunklist (media playlist): init + segmentos, en claro
# --------------------------------------------------------------------------

RE_TARGETDURATION = re.compile(r"#EXT-X-TARGETDURATION:(\d+(?:\.\d+)?)")
RE_MAP_URI = re.compile(r'#EXT-X-MAP:URI="([^"]+)"')
# segmento COMPLETO (no una EXT-X-PART de LL-HLS): "seg_<rendicion>_<msn>_..."
# Ver sonda_chaturbate.py:RE_NOMBRE_SEG / fase_3_chunklist.
RE_LINEA_SEGMENTO = re.compile(r"^(/[^\s#][^\s]*\.m4s[^\s]*)$", re.M)
RE_MSN_SEGMENTO = re.compile(r"seg_\d+_(\d+)_")


def es_publicidad(texto: str) -> bool:
    """Compuerta dura, mismo espiritu que mouflon.es_publicidad: nunca
    grabar un VOD/placeholder como si fuera la emision en vivo."""
    return "PLAYLIST-TYPE:VOD" in texto or "#EXT-X-ENDLIST" in texto


def pedir_variante(uri_variante: str, timeout: float = 10.0):
    """-> (init_url, target_duration, [(msn, url), ...]).

    Las URLs ya vienen absolutas (urljoin contra la URL final del pedido).
    Solo se listan segmentos COMPLETOS (seg_*), nunca las EXT-X-PART de
    LL-HLS (preview de baja latencia, de menor duracion -- mezclarlas con
    los completos duplicaria cobertura y rompe el supuesto de "un segmento,
    un archivo" que ya tiene vivo.py). Lanza ErrorRed/ErrorChaturbate si la
    respuesta no es una chunklist fMP4 real."""
    status, url_final, cuerpo = pedir(uri_variante, timeout=timeout)
    if status != 200:
        raise ErrorRed("chunklist dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if es_publicidad(texto):
        raise ErrorChaturbate("la chunklist es un VOD/placeholder, no la emision")
    if "#EXT-X-MAP:URI=" not in texto:
        raise ErrorChaturbate("200 pero sin #EXT-X-MAP, no es una chunklist fMP4")

    target = 2.0
    m = RE_TARGETDURATION.search(texto)
    if m:
        target = float(m.group(1))

    m = RE_MAP_URI.search(texto)
    init_url = urllib.parse.urljoin(url_final, m.group(1)) if m else None

    segmentos = []
    for rel in RE_LINEA_SEGMENTO.findall(texto):
        if "seg_" not in rel:
            continue
        m_msn = RE_MSN_SEGMENTO.search(rel)
        if not m_msn:
            continue
        segmentos.append((int(m_msn.group(1)), urllib.parse.urljoin(url_final, rel)))
    return init_url, target, segmentos


def nombre_archivo(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]


# --------------------------------------------------------------------------
# Snapshot publico para miniatura (sin cookies, sin cuenta)
# --------------------------------------------------------------------------

def url_snapshot(nombre: str) -> str:
    """https://jpeg.live.mmcdn.com/stream?room=<nombre> -- JPEG real,
    confirmado en vivo que cambia de un pedido al siguiente (no es un
    placeholder cacheado). Usado por nimbo\\sitio_chaturbate.py."""
    return "https://jpeg.live.mmcdn.com/stream?room=" + urllib.parse.quote(nombre)


if __name__ == "__main__":
    import sys
    nombre = sys.argv[1] if len(sys.argv) > 1 else None
    if not nombre:
        print("uso: python chaturbate.py <nombre_de_sala>")
        raise SystemExit(2)
    info = consultar_estado(nombre)
    print("estado=%(estado)s id=%(id)s espectadores=%(espectadores)s" % info)
    if info["estado"] == ESTADO_PUBLICO and info["hls_source"]:
        texto, url_final = pedir_master(info["hls_source"])
        rendiciones = leer_master(texto, url_final)
        print("%d rendiciones" % len(rendiciones))
        mejor = mejor_rendicion(rendiciones)
        if mejor:
            init_url, target, segmentos = pedir_variante(mejor.uri)
            print("variante=%s target=%.1f init=%s segmentos=%d"
                  % (mejor.nombre, target, nombre_archivo(init_url or ""),
                     len(segmentos)))
