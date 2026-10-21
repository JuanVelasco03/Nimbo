"""
detectar_m3u8.py

Addon de mitmproxy que detecta streams HLS/CMAF de doppiocdn (stripchat),
identifica el modelo, guarda init + segmentos y los une en un .mp4 final.

    Addon:  mitmdump -s detectar_m3u8.py --set flow_detail=0
    CLI:    python detectar_m3u8.py --listar
            python detectar_m3u8.py --unir [ID ...] [--forzar]

Como se usa
    1. Arranca el proxy y abre la pagina del modelo en el navegador.
    2. El script lista lo que detecta en consola y en activos.txt:
           ID           MODELO        VARIANTES           ESTADO
           105277105    fulana        1080p_h264          detectado
    3. Pega ese ID (o el nombre del modelo) en grabar.txt -> empieza a grabar.
    4. Borra la linea de grabar.txt -> cierra esa grabacion y genera el .mp4
       en el acto, sin esperar a que termine el proxy.
    Se pueden grabar varios modelos a la vez: una linea por cada uno.

De cada stream se graba UNA sola variante. Con @calidad=max (por defecto) es
siempre la mejor que ofrezca esa modelo: una emite en 1080p, otra en 720p y
otra en 480p, y cada una se graba a su maximo. Quien dice cual es la mejor es
el master .m3u8 de la modelo, no lo que pida el reproductor.

Con @forzar=1 (por defecto) el master .m3u8 de los streams que se estan
grabando se reescribe al vuelo dejando una sola variante: el reproductor ya
no tiene a donde bajar cuando la pestana pasa a segundo plano. Hay que
recargar la pagina despues de agregar el ID para que el player pida el
manifiesto de nuevo.

Si aun asi el reproductor cambia de calidad a mitad de la emision (ABR, pestana
en segundo plano, red lenta) la grabacion sigue al cambio: se cierra el archivo
de la calidad anterior y se abre otro con la nueva, sin perder segmentos
(quedan en un buffer en memoria mientras se confirma el cambio). Resoluciones
distintas no se pueden mezclar en un mismo mp4, por eso son archivos
consecutivos. Cuando vuelve la calidad buena, vuelve a cambiar.

Recuperacion
    Si el proceso se cae o se mata, al arrancar de nuevo une todas las
    sesiones que quedaron a medias (las que no tienen unido.json).

Control en caliente desde grabar.txt (se recarga solo al cambiar):
    105277105          -> graba por ID de stream
    nombreModelo       -> graba por nombre de modelo
    *                  -> graba todo lo detectado
    #                  -> comentario
    @calidad=max       -> la mejor que ofrezca cada modelo (tambien vale
                          fijarla: 1080p,720p -- por prefijo, 1080p acepta
                          1080p60)
    @codec=max         -> cualquier codec (o fijar: av1,h264)
    @variantes=1       -> una sola variante por stream (todas = todas)
    @espera=6          -> segundos para descubrir variantes antes de elegir
    @cambio=8          -> segundos antes de seguir un cambio de calidad del
                          reproductor (0 = seguirlo al instante)
    @fallback=0        -> no grabar nada si no hay una variante que coincida
                          con @calidad/@codec (por defecto graba la mejor)
    @forzar=0          -> no recortar el master .m3u8 (por defecto se recorta
                          para que el reproductor no baje de calidad solo)
    @borrosas=1        -> grabar tambien el stream censurado de los shows
                          privados (por defecto no: no es "menos calidad",
                          es otro video)
    @privado=160p      -> a esa altura (o menos) se da por hecho que la
                          modelo se fue a privado: corta y guarda el mp4, y
                          reanuda sola cuando vuelve la calidad buena
                          (@privado=0 lo desactiva)
    @vpn=fulana,mengana-> modelos geobloqueadas: su pagina y su API salen
                          por el tunel de la VPN (wireproxy), y solo eso. El
                          video y los assets del reproductor siguen saliendo
                          directos, y la ruta del PC no se toca, asi que las
                          demas grabaciones no se enteran. Ver _Tunel.

La emision original ("source") no lleva la calidad en la URL de sus segmentos,
asi que se detecta como auto_na; su altura real se lee del master. Ojo: en los
streams verticales el master las nombra por el ALTO (1080x1440 -> "1440p"), por
eso @calidad=max es lo sensato.
    @unir=0            -> no unir automaticamente
    @inactividad=120   -> cerrar y unir tras N segundos sin segmentos (0 = off)

Disco
    streams/<modelo>_<id>/<calidad_codec>_<fecha-hora>/   <- una sesion
        init_*.mp4
        segmentos/*.mp4
        info.json
        unido.json        <- marca que ya se genero el video
    grabaciones/<modelo>_<id>_<variante>_<fecha-hora>.mp4
    detector.log          <- historial (incluye el motivo de cada cierre)
"""

import json
import logging
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

try:                      # solo existe corriendo como addon de mitmdump;
    from mitmproxy.connection import Server as _ConexionServidor
except ImportError:       # de CLI (--unir) no hace falta
    _ConexionServidor = None

# Los scripts viven en <proyecto>\scripts; los datos (streams, grabaciones,
# perfil del navegador) en <proyecto>. Si el .py se mueve a una subcarpeta
# llamada scripts/bin, la base sigue siendo la carpeta de arriba: asi las
# grabaciones no se parten en dos sitios al mover los scripts.
AQUI = os.path.dirname(os.path.abspath(__file__))
SUBCARPETAS_SCRIPT = {"scripts", "script", "bin", "src"}


def _raiz_proyecto() -> str:
    if os.path.basename(AQUI).lower() in SUBCARPETAS_SCRIPT:
        return os.path.dirname(AQUI)
    return AQUI


def _al_lado(nombre: str) -> str:
    """Primero junto al script, si no en la raiz del proyecto."""
    aqui = os.path.join(AQUI, nombre)
    if os.path.exists(aqui):
        return aqui
    arriba = os.path.join(BASE, nombre)
    return arriba if os.path.exists(arriba) else aqui


def _unico_conf() -> str:
    """El .conf de WireGuard que haya junto al script, si hay uno solo.

    Misma idea que vpn_wg.py: no hacer que el usuario repita la ruta. Si hay
    varios, no se adivina: lo dice M3U8_TUNEL_CONF.
    """
    for carpeta in (AQUI, _raiz_proyecto()):
        try:
            hallados = [n for n in os.listdir(carpeta)
                        if n.lower().endswith(".conf") and n != CONF_GENERADA]
        except OSError:
            continue
        if len(hallados) == 1:
            return os.path.join(carpeta, hallados[0])
        if hallados:
            return ""
    return ""


BASE = os.environ.get("M3U8_BASE") or _raiz_proyecto()
FFMPEG = os.environ.get("M3U8_FFMPEG") or _al_lado("ffmpeg.exe")
STREAMS_DIR = os.path.join(BASE, "streams")
GRABACIONES_DIR = os.path.join(BASE, "grabaciones")
SELECCION = os.environ.get("M3U8_GRABAR") or _al_lado("grabar.txt")
# activos.txt vive junto a grabar.txt: el supervisor lo busca ahi
ACTIVOS = os.environ.get("M3U8_ACTIVOS") or os.path.join(
    os.path.dirname(SELECCION), "activos.txt"
)
REGISTRO = os.path.join(BASE, "detector.log")

# --- Tunel VPN por pestana (ver la clase _Tunel) --------------------------
# host:puerto donde wireproxy deja el tunel como proxy HTTP local. Vacio =
# desactivado: todo sale directo, como antes de que existiera el tunel.
TUNEL = os.environ.get("M3U8_TUNEL", "127.0.0.1:25345").strip()
WIREPROXY = os.environ.get("M3U8_WIREPROXY") or _al_lado("wireproxy.exe")
# el .conf que se genera (el del usuario + la seccion [http]); no se busca
# a si mismo cuando hay que encontrar el del usuario
CONF_GENERADA = "tunel_wireproxy.conf"
TUNEL_CONF = os.environ.get("M3U8_TUNEL_CONF") or _unico_conf()
# Por el tunel va SOLO lo que el sitio esconde por pais: su pagina y su API.
# Los assets del reproductor (assets.chapturist.com y demas) y el video de
# doppiocdn salen directos. Medido el 2026-09-27 con todo por el tunel: las
# marcadas perdieron 138-1530 s por sesion (y la sin marcar, 0), porque cada
# recarga bajaba los MB de JS de la pagina por un servidor gratis de Proton,
# no llegaba a pintar el reproductor, y el supervisor recargaba otra vez.
TUNEL_HOSTS = tuple(
    h.strip().lower() for h in os.environ.get(
        "M3U8_TUNEL_HOSTS", "stripchat.com,xhamsterlive.com,xlivesex.com"
    ).split(",") if h.strip()
)
# 1 = tambien los playlists (cualquier host). Si al abrir una geobloqueada el
# master llega recortado o de publicidad, es esto lo que hay que encender.
TUNEL_PLAYLIST = os.environ.get("M3U8_TUNEL_PLAYLIST", "") not in {
    "", "0", "no", "false"}
# 1 = absolutamente todo lo de esa pestana, video incluido. Solo si doppiocdn
# llegara a bloquear por pais: el .conf suele ser de un servidor gratis.
TUNEL_TODO = os.environ.get("M3U8_TUNEL_TODO", "") not in {"", "0", "no", "false"}
# con esta cabecera marca el navegador los pedidos de una pestana @vpn.
# El mismo nombre esta en supervisor.py: si se cambia, cambiarlo en los dos.
CABECERA_TUNEL = "x-grab-tunel"

MARCADOR = "unido.json"
CARPETA_SEGS = "segmentos"
CARPETA_PLAYLISTS = "playlists"

INTERVALO_VIGILANTE = 2.0      # cada cuanto se revisa grabar.txt / inactividad
INTERVALO_TABLA = 60.0         # cada cuanto se reimprime la tabla de activos
VENTANA_ACTIVO = 45.0          # una variante "sigue viva" si se vio hace menos
OLVIDAR_STREAM = 180.0         # sacar de la lista lo no visto en 3 min
CICLOS_PARA_CERRAR = 2         # ciclos seguidos fuera de grabar.txt antes de cerrar
FALLOS_PARA_VACIAR = 5         # lecturas fallidas de grabar.txt antes de darlo por vacio
MAX_INITS = 400                # cache de init por (id, variante)
MAX_BUFFER_SEGS = 60           # segmentos en memoria por variante mientras se elige
MAX_BUFFER_BYTES = 192 * 1024 * 1024
MAX_REGISTRO = 20 * 1024 * 1024
# La emision "fuente" (la mejor que sube la modelo) NO lleva calidad en la URL:
#   .../90060036/90060036_3323_<hash>.mp4     <- fuente
#   .../90060036/90060036_480p_h264_3323_.mp4 <- rendicion recodificada
# Mientras no se sepa su altura real (la dice el master) se le da este valor
# para que gane a cualquier rendicion: la fuente es, por definicion, la mejor.
ALTO_FUENTE = 99999
# Cuando la modelo se va a show privado, stripchat sigue sirviendo segmentos
# pero de un video de relleno de 160p. No es una calidad mas baja: no hay nada
# que grabar. Por debajo (o igual) de esta altura se corta y se guarda.
ALTO_PRIVADO = 160

log = logging.getLogger("detector")

# --------------------------------------------------------------------------
# Expresiones regulares (precompiladas: se evaluan en cada respuesta)
# --------------------------------------------------------------------------

RE_API_MODELO = re.compile(r"/api/front/models/(\d{4,})(?:[/?#]|$)", re.I)
RE_USERNAME = re.compile(r'"username"\s*:\s*"([A-Za-z0-9_.\-]{2,40})"')
RE_REFERER = re.compile(
    r"stripchat\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?([A-Za-z0-9_.\-]{2,40})",
    re.I,
)
RE_NOMBRE_VALIDO = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")
RE_CALIDAD = re.compile(r"(\d{3,4}p(?:\d{2})?)", re.I)
# el sufijo de calidad de doppiocdn va siempre entre separadores:
#   40920948_480p_h264_933_....mp4   ->  480p
# sin anclar, un hash como "abc123pXY" se colaba como si fuera una calidad
RE_CALIDAD_TOKEN = re.compile(r"[_/.-](\d{3,4}p(?:\d{2})?)(?=[_/.-]|$)", re.I)
RE_CODEC = re.compile(r"(h264|h265|hevc|av1|vp9)", re.I)
RE_ALTURA = re.compile(r"^(\d{3,4})p")
RE_ULTIMO_NUM = re.compile(r"(\d+)(?!.*\d)", re.S)
# Nombre de un segmento de doppiocdn:
#   <id>[_<calidad>_<codec>]_<secuencia>_<hash>_<epoch>[_part<n>].mp4
# Antes se ordenaba por 'el ultimo numero del nombre', que en .mp4 siempre
# es el 4 de la extension: todas las claves salian iguales y acababa
# ordenando por texto, con lo que el segmento 1000 iba antes que el 999 y
# el video quedaba hecho un lio.
RE_SEGMENTO = re.compile(
    r"_(\d+)_[A-Za-z0-9]+_(\d+)(?:_part(\d+))?\.[A-Za-z0-9]+$")
# el segmento entero va detras de sus partes, si acaso llegan los dos
PARTE_ENTERA = 1 << 30
# miniaturas, clips de portada y demas: no son el stream en vivo
RE_IGNORAR = re.compile(
    r"(preview|thumb|snapshot|poster|teaser|sprite|avatar|/img/|/static/)", re.I
)

# doppiocdn cambia de TLD (.com/.net/.media/.live), por eso no se fija uno solo
RE_SID = [
    re.compile(r"doppiocdn\.[a-z]+/(?:b-)?(?:hls|dash)[^/]*/(\d{4,})[/_]", re.I),
    re.compile(r"doppiocdn\.[a-z]+/[^/]+/(\d{4,})[/_]", re.I),
    re.compile(r"doppiocdn\.[a-z]+/(\d{4,})[/_]", re.I),
    re.compile(r"/(\d{7,})[/_]", re.I),
]

NO_MODELOS = {
    "login", "signup", "search", "tags", "categories", "discover", "models",
    "home", "api", "static", "assets", "favorites", "girls", "guys", "trans",
    "couples", "cam", "cams", "top", "new", "popular", "feed", "wallet",
    "settings", "profile", "security", "privacy", "terms", "support", "www",
}

EXT_SEGMENTO = (".mp4", ".m4s", ".ts", ".cmfv", ".cmfa")


# --------------------------------------------------------------------------
# Log: consola (aunque mitmdump corra con -q) + archivo detector.log
# --------------------------------------------------------------------------

class FiltroRuido(logging.Filter):
    """Silencia los 'Unhandled error in task' por conexiones cortadas."""

    RUIDO = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def __init__(self):
        super().__init__()
        self.contados = 0

    def filter(self, record) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, self.RUIDO):
            self.contados += 1
            return False
        return True


filtro_ruido = FiltroRuido()


def configurar_log() -> None:
    if not log.handlers:
        consola = logging.StreamHandler(sys.stderr)
        consola.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(consola)
        try:
            if os.path.exists(REGISTRO) and os.path.getsize(REGISTRO) > MAX_REGISTRO:
                os.replace(REGISTRO, REGISTRO + ".1")
            archivo = logging.FileHandler(REGISTRO, encoding="utf-8")
            archivo.setFormatter(
                logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            log.addHandler(archivo)
        except OSError:
            pass
        log.setLevel(logging.INFO)
        log.propagate = False  # no depende del verbosity de mitmproxy

    if os.environ.get("M3U8_RUIDO"):
        return
    raiz = logging.getLogger()
    if filtro_ruido not in raiz.filters:
        raiz.addFilter(filtro_ruido)
    for h in raiz.handlers:
        if filtro_ruido not in h.filters:
            h.addFilter(filtro_ruido)


# --------------------------------------------------------------------------
# Configuracion viva (grabar.txt)
# --------------------------------------------------------------------------

def altura_de(calidad: str) -> int:
    """'1080p60' -> 1080. 'auto' (la fuente) -> 0: no la dice la URL."""
    m = RE_ALTURA.match(calidad or "")
    return int(m.group(1)) if m else 0


@dataclass
class Config:
    entradas: set = field(default_factory=set)
    todo: bool = False
    calidad: tuple = ()
    codec: tuple = ()
    unir: bool = True
    inactividad: float = 120.0
    una_variante: bool = True
    espera: float = 6.0
    cambio: float = 8.0
    fallback: bool = True
    forzar: bool = True
    borrosas: bool = False    # el stream censurado (show privado): no es video
    privado: int = ALTO_PRIVADO   # altura del relleno del show privado
    vpn: frozenset = frozenset()  # modelos geobloqueadas: salen por el tunel

    def quiere(self, sid: str, modelo: str) -> bool:
        if self.todo:
            return True
        if sid in self.entradas:
            return True
        return bool(modelo) and modelo.lower() in self.entradas

    def acepta_variante(self, calidad: str, codec: str, fuente: bool = False,
                        borrosa: bool = False) -> bool:
        # La fuente no lleva calidad ni codec en la URL: descartarla por no
        # "coincidir" con el filtro era tirar justo la mejor emision. Con
        # @calidad=max / @codec=max (por defecto) siempre pasa.
        # el stream censurado que manda stripchat en los shows privados no es
        # una calidad mas baja: es otro video. Por defecto no se graba.
        if borrosa and not self.borrosas:
            return False
        if self.es_privado(altura_de(calidad)):
            return False
        if fuente and not self.calidad and not self.codec:
            return True
        # por prefijo: "1080p" acepta tambien "1080p60"
        if self.calidad and not calidad.lower().startswith(self.calidad):
            return False
        # "na" = no se pudo leer el codec en la URL; no es motivo para tirarla
        if self.codec and codec.lower() != "na" and codec.lower() not in self.codec:
            return False
        return True

    def es_privado(self, altura: int) -> bool:
        """160p no es 'peor calidad': es lo que sirve stripchat cuando la
        modelo se fue a show privado. No hay video que grabar ahi.

        altura 0 = no se sabe (la fuente no la lleva en la URL): no se toca.
        """
        return bool(self.privado) and 0 < altura <= self.privado


class Seleccion:
    """Lee grabar.txt solo cuando cambia (mtime), no en cada segmento.

    Si el archivo no se puede leer (el editor lo esta guardando, antivirus,
    bloqueo de Windows) NO se borra la configuracion: se conserva la ultima
    buena. Perderla cerraba todas las grabaciones en curso.
    """

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._firma = None
        self._cfg = Config()
        self._fallos = 0
        self._lock = threading.Lock()

    def actual(self) -> Config:
        with self._lock:
            try:
                st = os.stat(self.ruta)
                firma = (st.st_mtime, st.st_size)
            except OSError as e:
                self._fallos += 1
                if self._fallos == 1:
                    log.warning("[CONFIG] no se pudo leer %s (%s), mantengo lo anterior",
                                os.path.basename(self.ruta), e)
                elif self._fallos == FALLOS_PARA_VACIAR and self._firma is not None:
                    log.warning("[CONFIG] %s sigue sin leerse, me quedo sin objetivos",
                                os.path.basename(self.ruta))
                    self._firma = None
                    self._cfg = Config()
                return self._cfg

            self._fallos = 0
            if firma == self._firma:
                return self._cfg
            if st.st_size == 0:
                # guardado a medias: no lo tomo como "borra todo"
                return self._cfg

            cfg = self._parsear()
            if cfg is None:
                return self._cfg
            self._firma = firma
            self._cfg = cfg
            log.info(
                "[CONFIG] objetivos=%s todo=%s calidad=%s codec=%s variantes=%s "
                "unir=%s inactividad=%s espera=%ss cambio=%ss fallback=%s "
                "forzar=%s privado=%s vpn=%s",
                sorted(cfg.entradas) or "-",
                cfg.todo,
                ", ".join(cfg.calidad) or "*",
                ", ".join(cfg.codec) or "*",
                "1" if cfg.una_variante else "todas",
                cfg.unir,
                int(cfg.inactividad) or "off",
                int(cfg.espera),
                int(cfg.cambio),
                cfg.fallback,
                cfg.forzar,
                ("%dp" % cfg.privado) if cfg.privado else "off",
                ", ".join(sorted(cfg.vpn)) or "-",
            )
            return self._cfg

    def _parsear(self):
        cfg = Config()
        try:
            with open(self.ruta, "r", encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except OSError as e:
            log.warning("[CONFIG] %s: %s", self.ruta, e)
            return None

        for linea in lineas:
            linea = linea.split("#", 1)[0].strip()
            if not linea:
                continue

            if linea.startswith("@"):
                clave, _, valor = linea[1:].partition("=")
                clave, valor = clave.strip().lower(), valor.strip().lower()
                if clave == "calidad":
                    cfg.calidad = _lista(valor)
                elif clave == "codec":
                    cfg.codec = tuple(
                        "hevc" if v == "h265" else v for v in _lista(valor)
                    )
                elif clave == "unir":
                    cfg.unir = valor not in {"0", "no", "false", "off"}
                elif clave == "variantes":
                    cfg.una_variante = valor not in {"todas", "all", "*", "0"}
                elif clave in {"inactividad", "timeout"}:
                    cfg.inactividad = _num(valor, cfg.inactividad, "@inactividad")
                elif clave == "espera":
                    cfg.espera = _num(valor, cfg.espera, "@espera")
                elif clave == "cambio":
                    cfg.cambio = _num(valor, cfg.cambio, "@cambio")
                elif clave == "fallback":
                    cfg.fallback = valor not in {"0", "no", "false", "off"}
                elif clave in {"borrosas", "borrosa", "blur"}:
                    cfg.borrosas = valor not in {"0", "no", "false", "off"}
                elif clave in {"privado", "privados", "minimo"}:
                    cfg.privado = _alto(valor, cfg.privado, "@privado")
                elif clave in {"forzar", "forzar_calidad"}:
                    cfg.forzar = valor not in {"0", "no", "false", "off"}
                elif clave == "vpn":
                    # el supervisor marca sus pestanas y aqui se enrutan por
                    # el tunel. Se suman: dos lineas @vpn= no se pisan.
                    cfg.vpn |= {v.strip() for v in valor.split(",") if v.strip()}
                elif clave in {"todo", "all"}:
                    cfg.todo = valor not in {"0", "no", "false", "off"}
                else:
                    log.warning("[CONFIG] directiva desconocida: @%s", clave)
                continue

            if linea in {"*", "all", "todo", "todos"}:
                cfg.todo = True
            else:
                cfg.entradas.add(linea.lower())

        return cfg


SIN_FILTRO = {"max", "maxima", "mejor", "best", "auto", "*", "todas", "todos", ""}


def _lista(valor: str) -> tuple:
    """'1080p,720p' -> ('1080p','720p'); 'max' / vacio -> () = sin filtro."""
    partes = tuple(v for v in valor.replace(" ", "").split(",") if v)
    if not partes or any(v in SIN_FILTRO for v in partes):
        return ()
    return partes


def _alto(valor: str, por_defecto: int, nombre: str) -> int:
    """'160p' / '160' -> 160; '0' / 'no' -> 0 (desactivado)."""
    if valor in {"0", "no", "false", "off", "nunca"}:
        return 0
    m = re.match(r"^(\d{2,4})p?$", valor)
    if not m:
        log.warning("[CONFIG] %s invalido: %s (esperaba algo como 160p)",
                    nombre, valor)
        return por_defecto
    return int(m.group(1))


def _num(valor: str, por_defecto: float, nombre: str) -> float:
    try:
        return max(0.0, float(valor))
    except ValueError:
        log.warning("[CONFIG] %s invalido: %s", nombre, valor)
        return por_defecto


# --------------------------------------------------------------------------
# Escritor en segundo plano (no bloquea el event loop del proxy)
# --------------------------------------------------------------------------

class Escritor(threading.Thread):
    def __init__(self, maxsize: int = 4000):
        super().__init__(daemon=True, name="escritor")
        self.cola: queue.Queue = queue.Queue(maxsize=maxsize)
        self.descartados = 0
        self.errores = 0

    def encolar(self, destino: str, datos: bytes) -> bool:
        try:
            self.cola.put_nowait(("w", destino, datos))
            return True
        except queue.Full:
            self.descartados += 1
            log.error("[DISCO] cola llena, segmento descartado (%d)", self.descartados)
            return False

    def barrera(self) -> threading.Event:
        """Evento que se activa cuando todo lo encolado hasta ahora esta escrito."""
        ev = threading.Event()
        self.cola.put(("b", ev, None))
        return ev

    def run(self) -> None:
        while True:
            item = self.cola.get()
            try:
                if item is None:
                    return
                tipo, a, b = item
                if tipo == "b":
                    a.set()
                    continue
                destino, datos = a, b
                try:
                    os.makedirs(os.path.dirname(destino), exist_ok=True)
                    tmp = destino + ".part"
                    with open(tmp, "wb") as f:
                        f.write(datos)
                    os.replace(tmp, destino)
                except OSError as e:
                    self.errores += 1
                    log.error("[DISCO] %s: %s", os.path.basename(destino), e)
            finally:
                self.cola.task_done()

    def detener(self, timeout: float = 30.0) -> None:
        self.cola.put(None)
        self.join(timeout)


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------

@dataclass
class Deteccion:
    calidad: str
    codec: str
    ultimo: float
    desde: float = 0.0        # inicio de la racha actual (para el ABR)
    veces: int = 0
    borrosa: bool = False     # el stream censurado de los shows privados
    fuente: bool = False      # emision sin recodificar (la mejor que hay)
    alto_fuente: int = 0      # altura real de la fuente, si el master la dijo

    @property
    def altura(self) -> int:
        if self.fuente:
            return self.alto_fuente or ALTO_FUENTE
        return altura_de(self.calidad)


@dataclass
class Variante:
    calidad: str
    codec: str
    carpeta: str          # .../<modelo>_<id>/<variante>_<sesion>
    variante: str         # 1080p_h264
    sesion: str           # 20260922-181500
    init: bool = False
    segmentos: int = 0
    bytes: int = 0
    inicio: float = field(default_factory=time.time)
    vistos: set = field(default_factory=set)

    @property
    def etiqueta(self) -> str:
        return os.path.basename(self.carpeta)


@dataclass
class Stream:
    sid: str
    modelo: str = "desconocido"
    primera: float = field(default_factory=time.time)
    inicio: float = 0.0
    grabando: bool = False
    ultimo_visto: float = field(default_factory=time.time)
    ultimo_segmento: float = 0.0
    elegida: str = ""
    forzada: str = ""
    master: dict = field(default_factory=dict)   # nombre -> Rendicion
    master_ts: float = 0.0
    fuente_nombre: str = ""       # como llama el master a la fuente (1080p_h264)
    alto_fuente: int = 0          # altura real de la fuente
    aviso_master: str = ""
    aviso_fallback: bool = False
    privado: bool = False         # se fue a show privado: cerrado y esperando
    privado_desde: float = 0.0
    fuera: int = 0                                   # ciclos seguidos sin permiso
    detectadas: dict = field(default_factory=dict)   # variante -> Deteccion
    variantes: dict = field(default_factory=dict)    # variante -> Variante

    @property
    def segmentos(self) -> int:
        return sum(v.segmentos for v in self.variantes.values())

    @property
    def bytes(self) -> int:
        return sum(v.bytes for v in self.variantes.values())

    @property
    def carpeta(self) -> str:
        nombre = self.modelo + "_" + self.sid if self.modelo != "desconocido" else self.sid
        return os.path.join(STREAMS_DIR, limpiar(nombre))

    def nombre_master(self, variante: str) -> str:
        """El nombre que el master le da a lo que estamos grabando.

        Los segmentos de la fuente no llevan calidad en la URL, asi que se
        detectan como 'auto_na'; en el master esa misma emision aparece como
        '1080p_h264'. Sin esta traduccion no se podian comparar.
        """
        if variante.startswith("auto_") and self.fuente_nombre:
            return self.fuente_nombre
        return variante

    def mejor_posible(self, cfg) -> str:
        """La mejor variante que ofrece el master (lo que deberiamos grabar)."""
        r = mejor_rendicion(list(self.master.values()), cfg)
        return r.nombre if r else ""

    def vivas(self, ahora: float) -> dict:
        return {
            v: d for v, d in self.detectadas.items()
            if ahora - d.ultimo <= VENTANA_ACTIVO
        }

    @property
    def es_preview(self) -> bool:
        """Miniatura del grid: solo variantes borrosas o minusculas."""
        if not self.detectadas:
            return False
        return all(
            v.endswith("_blurred") or d.altura and d.altura <= 240
            for v, d in self.detectadas.items()
        )


# --------------------------------------------------------------------------
# Utilidades de URL
# --------------------------------------------------------------------------

def extraer_stream_id(url: str):
    for patron in RE_SID:
        m = patron.search(url)
        if m:
            return m.group(1)
    return None


def nombre_archivo(url: str) -> str:
    return os.path.basename(url.split("?", 1)[0].split("#", 1)[0])


def clasificar(url: str):
    """-> (tipo, calidad, codec, variante, fuente) o None si no interesa."""
    nombre = nombre_archivo(url).lower()
    if not nombre:
        return None

    if nombre.endswith(".m3u8") or nombre.endswith(".mpd"):
        tipo = "playlist"
    elif "init" in nombre:
        tipo = "init"
    elif nombre.endswith(EXT_SEGMENTO):
        tipo = "segmento"
    else:
        return None

    ruta = url.split("?", 1)[0].lower()
    if RE_IGNORAR.search(ruta):
        return None

    # La calidad va como token entre separadores (..._480p_...). Se mira solo
    # el nombre y la ruta: la query trae cosas como preferredVideoCodec=h264 y
    # hashes que no son calidades.
    q = RE_CALIDAD_TOKEN.search(nombre) or RE_CALIDAD_TOKEN.search(ruta)
    c = RE_CODEC.search(nombre) or RE_CODEC.search(ruta)

    calidad = q.group(1).lower() if q else "auto"
    codec = c.group(1).lower() if c else "na"
    if codec == "h265":
        codec = "hevc"

    borrosa = "blur" in nombre or "blur" in ruta
    variante = calidad + "_" + codec
    if borrosa:
        variante += "_blurred"

    # sin sufijo de calidad y nitida = la emision original, la mejor que hay
    fuente = calidad == "auto" and not borrosa

    return tipo, calidad, codec, variante, fuente


def modelo_desde_referer(referer: str):
    if not referer:
        return None
    m = RE_REFERER.search(referer)
    if not m:
        return None
    nombre = m.group(1).strip()
    if nombre.lower() in NO_MODELOS or not RE_NOMBRE_VALIDO.match(nombre):
        return None
    return nombre


def limpiar(nombre: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", nombre)[:60] or "x"


def sello() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def puntaje(variante: str, det: Deteccion, cfg: Config) -> tuple:
    """Para elegir 'la mejor': nitida > borrosa, mas alto mejor, codec preferido."""
    blur = variante.endswith("_blurred")
    if cfg.codec and det.codec in cfg.codec:
        pref = len(cfg.codec) - cfg.codec.index(det.codec)
    else:
        pref = 0
    return (0 if blur else 1, det.altura, pref, det.veces)


# --------------------------------------------------------------------------
# Forzar calidad: dejar una sola variante en el master .m3u8
#
# El reproductor baja de calidad solo (pestana en segundo plano, ABR). Si el
# manifiesto que le llega ofrece una sola calidad, no tiene a donde bajar.
# --------------------------------------------------------------------------

RE_ATTR_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
RE_ATTR_BW = re.compile(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", re.I)
RE_ATTR_CODECS = re.compile(r'CODECS="([^"]*)"', re.I)

CODECS_HLS = (
    ("av01", "av1"), ("avc1", "h264"), ("avc3", "h264"),
    ("hvc1", "hevc"), ("hev1", "hevc"), ("dvh", "hevc"), ("vp09", "vp9"),
)


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


@dataclass
class Rendicion:
    """Una linea EXT-X-STREAM-INF del master: lo que la modelo ofrece."""
    nombre: str           # 1080p_av1
    calidad: str          # 1080p
    codec: str            # av1
    alto: int
    banda: int
    attrs: str            # la linea #EXT-X-STREAM-INF completa
    uri: str
    fuente: bool = False  # NAME="source": la emision tal cual la sube la modelo

    @property
    def borrosa(self) -> bool:
        return self.nombre.endswith("_blurred")

    def como_deteccion(self) -> Deteccion:
        # la banda entra como desempate en puntaje()
        return Deteccion(calidad=self.calidad, codec=self.codec, ultimo=0.0,
                         veces=self.banda, fuente=self.fuente,
                         borrosa=self.borrosa, alto_fuente=self.alto)


def leer_master(texto: str):
    """Lee las variantes de un master playlist. -> (cabecera, rendiciones, pos)."""
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
            # la fuente: NAME="source", o la URI sin sufijo de calidad
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


def mejor_rendicion(rendiciones, cfg: Config):
    """La mejor del master segun los filtros; si ninguna coincide, la mejor a secas."""
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


# por debajo de esto es miniatura del grid, no el reproductor grande
ALTO_MINIMO_REAL = 360


def master_es_preview(rendiciones) -> bool:
    """True si el master solo ofrece miniaturas (borrosas o minusculas)."""
    if not rendiciones:
        return True
    return all(r.borrosa or r.alto < ALTO_MINIMO_REAL for r in rendiciones)


def reducir_master(texto: str, cfg: Config):
    """Deja una sola variante en un master playlist.

    -> (texto_nuevo, elegida, todas) o None si no hay nada que tocar.
    """
    cabecera, rendiciones, pos = leer_master(texto)
    if len(rendiciones) <= 1:
        return None

    elegida = mejor_rendicion(rendiciones, cfg)
    if elegida is None:
        return None              # no tocar: que el player haga lo que quiera

    nuevas = cabecera[:pos] + [elegida.attrs, elegida.uri] + cabecera[pos:]
    fin = "\r\n" if "\r\n" in texto else "\n"
    nuevo = fin.join(nuevas)
    if texto.endswith(("\n", "\r")):
        nuevo += fin
    return nuevo, elegida.nombre, [r.nombre for r in rendiciones]


# --------------------------------------------------------------------------
# Union de segmentos (CMAF: init + segmentos concatenados = mp4 fragmentado)
# --------------------------------------------------------------------------

def clave_segmento(nombre: str):
    """-> (epoch, secuencia, parte) o None si el nombre no sigue el patron.

    Manda el EPOCH, no la secuencia: cuando la modelo reinicia la emision el
    contador de secuencia vuelve a empezar, y ordenar por el metia el trozo
    nuevo en medio del viejo. Con el reloj del nombre eso no pasa. Medido en
    una grabacion real: solapes 5292 -> 6, huecos 5480 -> 198.
    """
    m = RE_SEGMENTO.search(nombre)
    if not m:
        return None
    parte = m.group(3)
    return (int(m.group(2)), int(m.group(1)),
            int(parte) if parte is not None else PARTE_ENTERA)


def _orden_segmentos(carpeta: str):
    """Los segmentos en el orden en que se emitieron.

    El orden lo da la secuencia del nombre, no la fecha del archivo: con
    varias descargas en paralelo las fechas se pisan y no son fiables.
    """
    archivos = [
        os.path.join(carpeta, n)
        for n in os.listdir(carpeta)
        if n.lower().endswith(EXT_SEGMENTO)
    ]
    claves, sin_clave = {}, []
    for ruta in archivos:
        k = clave_segmento(os.path.basename(ruta))
        if k is None:
            sin_clave.append(ruta)
        else:
            claves[ruta] = k

    if sin_clave and claves:
        log.warning("[UNIR] %d de %d segmentos no siguen el patron de nombre; "
                    "esos van por fecha al final", len(sin_clave), len(archivos))
    if not claves:
        archivos.sort(key=lambda r: (os.path.getmtime(r), r))
        return archivos

    ordenados = sorted(claves, key=lambda r: claves[r])
    sin_clave.sort(key=lambda r: (os.path.getmtime(r), r))
    return ordenados + sin_clave


# --------------------------------------------------------------------------
# Reencadenar los tiempos (cuando la modelo reinicia la emision)
#
# Cada fragmento lleva en su caja tfdt el momento en que va. Si la modelo
# corta y vuelve a emitir, ese reloj se pone otra vez a cero: los fragmentos
# nuevos dicen "voy en el segundo 3" cuando ya llevabamos dos horas. Al
# pegarlo todo, el reproductor ve el tiempo ir hacia atras, ffmpeg empieza a
# amontonar fotogramas de tick en tick ("non monotonically increasing dts") y
# el resultado es un mp4 con todas las imagenes dentro pero con media hora de
# duracion declarada: no se puede buscar ni adelantar.
#
# Aqui se detecta ese salto y se le suma a los fragmentos siguientes lo que
# haga falta para que el reloj siga donde estaba.
#
# El mismo arreglo vale para el caso contrario: cuando un trozo no se llego a
# bajar, el siguiente dice "voy en el segundo 120" pero solo teniamos hasta el
# 90. Ese hueco se conserva en la linea de tiempo y el reproductor congela el
# ultimo fotograma 30 s y luego salta. Se cierra igual, reanclando el reloj
# donde acabo lo anterior: la grabacion dura menos pero corre seguida.
# --------------------------------------------------------------------------

# En segundos. Cuanto tiene que retroceder el reloj para ser un reinicio, y
# cuanto tiene que adelantarse para ser un hueco. Medio trozo son 0.5 s, asi
# que 0.25 s los coge todos sin confundirse con el redondeo normal entre
# fragmentos, que no pasa de unos milisegundos (medido: +-20 ms).
TOLERANCIA_SALTO = 1.0
TOLERANCIA_HUECO = 0.25
# si el init no dice la escala de una pista, se supone la de video
ESCALA_POR_DEFECTO = 90000


def _cajas(datos, ini, fin):
    """Recorre las cajas MP4 de datos[ini:fin] -> (tipo, pos, largo, cabecera)."""
    pos = ini
    while pos + 8 <= fin:
        largo = struct.unpack(">I", datos[pos:pos + 4])[0]
        tipo = datos[pos + 4:pos + 8]
        cabecera = 8
        if largo == 1:
            if pos + 16 > fin:
                return
            largo = struct.unpack(">Q", datos[pos + 8:pos + 16])[0]
            cabecera = 16
        if largo < 8 or pos + largo > fin:
            return
        yield tipo, pos, largo, cabecera
        pos += largo


def _duracion_trun(datos, pos, largo, por_defecto):
    """Suma de las duraciones de las muestras de un trun."""
    banderas = struct.unpack(">I", datos[pos + 8:pos + 12])[0] & 0xFFFFFF
    n = struct.unpack(">I", datos[pos + 12:pos + 16])[0]
    q = pos + 16
    if banderas & 0x01:
        q += 4
    if banderas & 0x04:
        q += 4
    if not banderas & 0x100:            # no trae duraciones: la de tfhd
        return n * por_defecto
    paso = 4 * bin(banderas & 0xF00).count("1")
    total = 0
    for i in range(n):
        total += struct.unpack(">I", datos[q + i * paso:q + i * paso + 4])[0]
    return total


def _pistas_del_fragmento(datos):
    """-> [(pos_tfdt, version, pista, tiempo, duracion)] de cada traf."""
    salida = []
    for tipo, pos, largo, _ in _cajas(datos, 0, len(datos)):
        if tipo != b"moof":
            continue
        for t2, p2, l2, _ in _cajas(datos, pos + 8, pos + largo):
            if t2 != b"traf":
                continue
            pista = tiempo = pos_tfdt = version = None
            dur_defecto = 0
            duracion = 0
            for t3, p3, l3, _ in _cajas(datos, p2 + 8, p2 + l2):
                if t3 == b"tfhd":
                    banderas = struct.unpack(">I", datos[p3 + 8:p3 + 12])[0] & 0xFFFFFF
                    pista = struct.unpack(">I", datos[p3 + 12:p3 + 16])[0]
                    q = p3 + 16
                    if banderas & 0x01:
                        q += 8
                    if banderas & 0x02:
                        q += 4
                    if banderas & 0x08:
                        dur_defecto = struct.unpack(">I", datos[q:q + 4])[0]
                elif t3 == b"tfdt":
                    version = datos[p3 + 8]
                    pos_tfdt = p3 + 12
                    tiempo = (struct.unpack(">Q", datos[pos_tfdt:pos_tfdt + 8])[0]
                              if version == 1
                              else struct.unpack(">I", datos[pos_tfdt:pos_tfdt + 4])[0])
                elif t3 == b"trun":
                    duracion += _duracion_trun(datos, p3, l3, dur_defecto)
            if pista is not None and tiempo is not None:
                salida.append((pos_tfdt, version, pista, tiempo, duracion))
    return salida


def _escalas_del_init(datos):
    """-> {pista: unidades por segundo}, de moov/trak/mdhd del init.

    Hace falta para medir un corte en segundos: el video va a 90 kHz y el
    audio a 48 kHz, asi que el mismo hueco son numeros muy distintos en cada
    pista y un unico umbral en unidades no sirve para las dos.
    """
    escalas = {}
    for tipo, pos, largo, _ in _cajas(datos, 0, len(datos)):
        if tipo != b"moov":
            continue
        for t2, p2, l2, _ in _cajas(datos, pos + 8, pos + largo):
            if t2 != b"trak":
                continue
            pista = escala = None
            for t3, p3, l3, _ in _cajas(datos, p2 + 8, p2 + l2):
                if t3 == b"tkhd":
                    q = p3 + (28 if datos[p3 + 8] == 1 else 20)
                    pista = struct.unpack(">I", datos[q:q + 4])[0]
                elif t3 == b"mdia":
                    for t4, p4, l4, _ in _cajas(datos, p3 + 8, p3 + l3):
                        if t4 == b"mdhd":
                            q = p4 + (28 if datos[p4 + 8] == 1 else 20)
                            escala = struct.unpack(">I", datos[q:q + 4])[0]
            if pista and escala:
                escalas[pista] = escala
    return escalas


def _escalas_de(rutas):
    """Las escalas del init del plan, o {} si el plan no trae init."""
    for ruta, es_segmento in rutas:
        if es_segmento:
            break                      # ya empezaron los segmentos: no hay init
        try:
            with open(ruta, "rb") as f:
                return _escalas_del_init(f.read())
        except (OSError, struct.error, IndexError):
            break
    return {}


class Reencadenador:
    """Deja el reloj corriendo seguido: ni hacia atras ni con huecos."""

    def __init__(self, escalas=None):
        self.escalas = escalas or {}
        self.desfase = {}        # pista -> cuanto hay que sumarle
        self.fin = {}            # pista -> donde acabo el fragmento anterior
        self.reinicios = 0
        self.huecos = 0          # trozos que no se bajaron y se han cerrado
        self.segundos_huecos = 0.0

    def escala(self, pista):
        return self.escalas.get(pista) or ESCALA_POR_DEFECTO

    def arreglar(self, datos: bytes) -> bytes:
        try:
            pistas = _pistas_del_fragmento(datos)
        except Exception:
            return datos                      # no se entiende: pasar tal cual
        if not pistas:
            return datos

        # Un corte afecta a todas las pistas a la vez, asi que primero se mide
        # en segundos y despues se corrigen todas juntas. Comprobado que cada
        # fragmento de este CDN trae siempre video y audio, y que los 14 cortes
        # de una grabacion de una hora salian en las dos pistas a la vez.
        retroceso = adelanto = 0.0
        for _, _, pista, tiempo, _ in pistas:
            if pista not in self.fin:
                continue
            fuera = ((tiempo + self.desfase.get(pista, 0) - self.fin[pista])
                     / self.escala(pista))
            if fuera < -TOLERANCIA_SALTO:
                retroceso = min(retroceso, fuera)
            elif fuera > TOLERANCIA_HUECO:
                adelanto = max(adelanto, fuera)

        if retroceso or adelanto:
            if retroceso:
                self.reinicios += 1
            else:
                self.huecos += 1
                self.segundos_huecos += adelanto
            # Cada pista se reancla donde acabo la anterior. El desfase entre
            # audio y video se mantiene en su banda normal (medido: -20 a 0 ms,
            # sin deriva), asi que esto no descuadra el sonido.
            for _, _, pista, tiempo, _ in pistas:
                if pista in self.fin:
                    self.desfase[pista] = self.fin[pista] - tiempo

        editable = None
        for pos_tfdt, version, pista, tiempo, duracion in pistas:
            desfase = self.desfase.get(pista, 0)
            nuevo = tiempo + desfase
            if desfase:
                if version == 0 and nuevo > 0xFFFFFFFF:
                    # no cabe en 32 bits; dejarlo como esta antes que romperlo
                    nuevo = tiempo
                else:
                    if editable is None:
                        editable = bytearray(datos)
                    if version == 1:
                        struct.pack_into(">Q", editable, pos_tfdt, nuevo)
                    else:
                        struct.pack_into(">I", editable, pos_tfdt, nuevo)
            self.fin[pista] = nuevo + duracion
        return bytes(editable) if editable is not None else datos


# Cuantas veces se intenta saltar un trozo atascado antes de rendirse
MAX_REPARACIONES = 6
# si el remux saca menos de esto, es que ffmpeg se planto a mitad
FRACCION_MINIMA = 0.90
# margen de disco que se exige antes de empezar, sobre el tamano de la
# grabacion: el mp4 final pesa mas o menos lo mismo que los segmentos
MARGEN_DISCO = 1.10


def _plan(inits, segmentos, saltar):
    """Que bytes se van a pegar y en que orden, sin leerlos todavia.

    -> (rutas, cortes, total). 'rutas' son pares (ruta, es_segmento); el init
    no pasa por el reencadenador. 'cortes' dice en que byte del pegote empieza
    cada segmento, para poder localizar luego donde se atasco ffmpeg. 'saltar'
    son numeros de secuencia que se dejan fuera.

    El total sale de los tamanos en disco, no de lo que se llegue a enviar:
    asi sigue siendo exacto aunque ffmpeg deje de leer a mitad y la tuberia se
    corte antes de mandarlo todo.
    """
    rutas, cortes, total = [], [], 0
    for parte in inits[:1]:
        rutas.append((parte, False))
        total += os.path.getsize(parte)
    for ruta in segmentos:
        clave = clave_segmento(os.path.basename(ruta))
        if clave and clave[1] in saltar:
            continue
        rutas.append((ruta, True))
        cortes.append((total, clave[1] if clave else None))
        total += os.path.getsize(ruta)
    return rutas, cortes, total


# Tope de ritmo de la union mientras haya emisiones grabandose, en bytes/s.
RITMO_UNION = 25 * 1048576
# A quien se le pregunta si hay emisiones en curso. Lo rellena el addon; por
# la CLI se queda en None y la union va a toda velocidad, que es lo que se
# quiere cuando no hay nada grabando.
hay_grabaciones = None


class _Freno:
    """Frena la union mientras haya emisiones grabandose.

    Unir es leer miles de trozos y mandarlos a ffmpeg tan rapido como de el
    disco. Con emisiones en curso eso les quita disco y CPU, el reproductor se
    queda sin buffer y el HLS en vivo salta al directo: los segundos que se
    salta no se piden nunca y se pierden para siempre. Medido: unir 2021 MB
    tardo 40s y en esos mismos 40s las otras cuatro modelos perdieron entre 12
    y 30 segundos cada una. Frenada, la union tarda el doble y no quita nada.
    """

    def __init__(self, ritmo: float = RITMO_UNION):
        self.ritmo = ritmo
        self.reloj = time.monotonic()
        self.enviado = 0
        self.mirado = 0.0
        self.frenar = False

    def paso(self, n: int) -> None:
        ahora = time.monotonic()
        if ahora - self.mirado > 2.0:   # preguntarlo por cada trozo seria caro
            self.mirado = ahora
            try:
                self.frenar = bool(hay_grabaciones and hay_grabaciones())
            except Exception:
                self.frenar = False
            if not self.frenar:
                self.reloj, self.enviado = ahora, 0
        if not self.frenar:
            return
        self.enviado += n
        de_mas = self.enviado / self.ritmo - (ahora - self.reloj)
        if de_mas > 0:
            time.sleep(min(de_mas, 0.5))


def _alimentar(rutas, escribir, cadena=None):
    """Manda init + segmentos a 'escribir' en orden. -> el reencadenador.

    El reencadenador puede reescribir los sellos de tiempo, pero nunca cambia
    el tamano, asi que los offsets del plan siguen valiendo. Quien llama puede
    pasar el suyo para quedarse con el recuento aunque la tuberia se corte a
    mitad (BrokenPipeError).
    """
    if cadena is None:
        cadena = Reencadenador(_escalas_de(rutas))
    freno = _Freno()
    for ruta, es_segmento in rutas:
        with open(ruta, "rb") as f:
            datos = f.read()
        escribir(cadena.arreglar(datos) if es_segmento else datos)
        freno.paso(len(datos))
    return cadena


def _hay_espacio(total, etiqueta, veces=MARGEN_DISCO):
    """Avisa y corta antes de llenar el disco a mitad de la union."""
    try:
        libre = shutil.disk_usage(GRABACIONES_DIR).free
    except OSError:
        return True   # si no se puede saber, mejor intentarlo
    if libre >= total * veces:
        return True
    log.error("[UNIR] %s: falta espacio en %s: hacen falta ~%.1f GB y hay "
              "%.1f GB. Los segmentos NO se han tocado; libera espacio y "
              "reintenta con python %s --unir",
              etiqueta, os.path.splitdrive(GRABACIONES_DIR)[0] or GRABACIONES_DIR,
              total * veces / 1073741824, libre / 1073741824,
              os.path.basename(__file__))
    return False


def _escribir_crudo(rutas, crudo):
    """Pega el plan en un archivo. -> reencadenador, o None si falla el disco."""
    try:
        with open(crudo, "wb") as salida:
            return _alimentar(rutas, salida.write)
    except OSError as e:
        log.error("[UNIR] %s", e)
        return None


def _secuencia_atascada(cortes, bytes_salida):
    """Que numero de secuencia estaba ffmpeg leyendo cuando se paro."""
    atascada = None
    for offset, secuencia in cortes:
        if offset > bytes_salida:
            break
        atascada = secuencia
    return atascada


def _init_de_hermana(carpeta_sesion: str) -> str:
    """El init de otra sesion del mismo stream y la misma variante, si hay.

    El init describe las pistas (codec, timescale) y es el mismo mientras la
    modelo no reinicie la emision, asi que el de una sesion hermana sirve. Es
    la red de seguridad para cuando el navegador tenia el init en cache y no
    lo volvio a pedir: sin esto, esos segmentos no se pueden convertir en
    video y la grabacion se pierde.
    """
    padre = os.path.dirname(carpeta_sesion)
    variante = os.path.basename(carpeta_sesion).rsplit("_", 1)[0]
    candidatos = []
    try:
        hermanas = os.listdir(padre)
    except OSError:
        return ""
    for nombre in hermanas:
        otra = os.path.join(padre, nombre)
        if otra == carpeta_sesion or not os.path.isdir(otra):
            continue
        if nombre.rsplit("_", 1)[0] != variante:
            continue
        try:
            for n in sorted(os.listdir(otra)):
                if n.lower().startswith("init"):
                    candidatos.append((os.path.getmtime(otra),
                                       os.path.join(otra, n)))
                    break
        except OSError:
            continue
    return max(candidatos)[1] if candidatos else ""


def unir_sesion(carpeta_sesion: str, etiqueta: str, remux: bool = True):
    """Concatena init + segmentos de UNA sesion y remuxea. Devuelve la ruta final."""
    carpeta_segs = os.path.join(carpeta_sesion, CARPETA_SEGS)
    if not os.path.isdir(carpeta_segs):
        return None

    segmentos = _orden_segmentos(carpeta_segs)
    if not segmentos:
        return None

    inits = sorted(
        os.path.join(carpeta_sesion, n)
        for n in os.listdir(carpeta_sesion)
        if n.lower().startswith("init")
    )
    if not inits:
        prestado = _init_de_hermana(carpeta_sesion)
        if prestado:
            inits = [prestado]
            log.warning(
                "[UNIR] %s no tiene init propio (el navegador lo tenia en "
                "cache): uso el de %s", etiqueta,
                os.path.basename(os.path.dirname(prestado)))
        else:
            log.warning("[UNIR] %s SIN INIT: el video puede no abrirse", etiqueta)

    os.makedirs(GRABACIONES_DIR, exist_ok=True)
    base = os.path.join(GRABACIONES_DIR, limpiar(etiqueta))
    crudo = base + ".raw.mp4"
    final = base + ".mp4"
    n = 1
    while os.path.exists(final):
        n += 1
        crudo = base + "_" + str(n) + ".raw.mp4"
        final = base + "_" + str(n) + ".mp4"

    ffmpeg = (FFMPEG if os.path.exists(FFMPEG) else shutil.which("ffmpeg")) \
        if remux else ""
    saltar = set()
    salida = None

    for intento in range(MAX_REPARACIONES + 1):
        rutas, cortes, total = _plan(inits, segmentos, saltar)
        if not intento:
            if not _hay_espacio(total, etiqueta):
                return None
            log.info("[UNIR] %s | %d segmentos | %.1f MB -> remuxeando...",
                     etiqueta, len(segmentos), total / 1048576)

        if not ffmpeg:
            # sin ffmpeg no hay mp4 posible: queda el pegote en crudo, que el
            # reproductor no abre pero conserva todo el contenido
            if not _hay_espacio(total, etiqueta, MARGEN_DISCO + 1):
                return None
            if _escribir_crudo(rutas, crudo) is None:
                return None
            if remux:
                log.warning("[UNIR] ffmpeg no encontrado, queda %s", crudo)
            salida = crudo
            break

        # Por tuberia: ffmpeg lee los fragmentos directamente y no se escribe
        # el pegote intermedio. Ahorra una copia entera de la grabacion en
        # disco por intento (el pico baja de 3x a 2x su tamano).
        hecho = _remuxear_tubo(ffmpeg, rutas, final, etiqueta)
        if hecho is None:
            # red de seguridad: si por tuberia no sale, se hace como antes
            hecho = _remuxear_disco(ffmpeg, rutas, crudo, final, etiqueta, total)
        if hecho is None:
            salida = crudo if os.path.exists(crudo) else None
            break
        salida, cadena = hecho
        if not intento and cadena.reinicios:
            log.info("[UNIR] %s: %d reinicio(s) de emision; los tiempos se "
                     "reencadenaron para que el video no se corte",
                     etiqueta, cadena.reinicios)
        if not intento and cadena.huecos:
            log.info("[UNIR] %s: %d corte(s) que no llegaron a bajarse "
                     "(%.0f s en total); cerrados, el video corre seguido y "
                     "dura eso menos", etiqueta, cadena.huecos,
                     cadena.segundos_huecos)

        # Si el mp4 sale mucho mas chico que el pegote, ffmpeg dejo de leer a
        # mitad: hay un segmento que no le gusta. Se busca cual, se deja fuera
        # y se vuelve a intentar; perder 2 segundos es mejor que perder horas.
        hechos = os.path.getsize(salida)
        if hechos >= total * FRACCION_MINIMA or intento == MAX_REPARACIONES:
            if saltar:
                log.warning("[UNIR] %s: se dejaron fuera %d segmento(s) que "
                            "atascaban a ffmpeg (%s); son unos pocos segundos",
                            etiqueta, len(saltar),
                            ", ".join(str(x) for x in sorted(saltar)))
            break

        atascada = _secuencia_atascada(cortes, hechos)
        if atascada is None or atascada in saltar:
            log.warning("[UNIR] %s: el remux sale corto (%.0f%%) y no logro "
                        "localizar el segmento culpable", etiqueta,
                        100.0 * hechos / total)
            break
        log.warning("[UNIR] %s: ffmpeg se paro en el %.0f%%; salto la "
                    "secuencia %s y reintento", etiqueta,
                    100.0 * hechos / total, atascada)
        saltar.add(atascada)
        try:
            os.remove(salida)
        except OSError:
            pass

    if not salida:
        return None
    _marcar_unida(carpeta_sesion, salida, len(segmentos))
    log.info("[LISTO] %s (%.1f MB)", salida, os.path.getsize(salida) / 1048576)
    return salida


# Cada trozo descargado trae su propio indice (cajas sidx) que apunta a
# posiciones dentro de ESE trozo. Al pegarlos todos, esos indices quedan
# apuntando a cualquier sitio y ffmpeg se niega a abrir el archivo con
# "error reading header". +ignidx le dice que pase de ellos y lea los
# fragmentos en orden, que es justo lo que queremos.
ARGS_REMUX = ["-fflags", "+ignidx"]
# La union es trabajo de fondo; las emisiones en vivo no. En Windows se le
# baja la prioridad a ffmpeg para que no le quite turno de CPU al proxy.
PRIORIDAD_FONDO = (
    {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    if hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS") else {}
)
# el video puede empezar en el minuto 116 (si la modelo llevaba rato
# emitiendo): make_zero lo baja a 0 para que la barra de tiempo sea util
ARGS_SALIDA = ["-c", "copy", "-avoid_negative_ts", "make_zero",
               "-movflags", "+faststart"]


# Dos pasadas: la normal y, si esa falla, una tolerante para el caso de que
# algun trozo haya llegado a medias.
INTENTOS_REMUX = [
    (ARGS_REMUX, ARGS_SALIDA, ""),
    (ARGS_REMUX + ["-err_detect", "ignore_err"],
     ARGS_SALIDA + ["-max_muxing_queue_size", "4096"], " (tolerante)"),
]


def _remuxear_tubo(ffmpeg: str, rutas, final: str, etiqueta: str):
    """Remuxea alimentando a ffmpeg por tuberia, sin pegote intermedio.

    Ahorra escribir (y luego borrar) una copia entera de la grabacion en cada
    intento. Comprobado contra la version con archivo: sale el mismo mp4 byte
    a byte, y un trozo malo en medio se detecta igual (el mp4 sale corto).
    -> (ruta, reencadenador) o None si ffmpeg no pudo con ninguna pasada.
    """
    ultimo = ""
    for args_in, args_out, nota in INTENTOS_REMUX:
        with tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 *args_in, "-i", "pipe:0", *args_out, final],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                **PRIORIDAD_FONDO,
            )
            # el reencadenador se crea aqui para conservar el recuento aunque
            # la tuberia se corte a mitad
            cadena = Reencadenador(_escalas_de(rutas))
            try:
                _alimentar(rutas, proc.stdin.write, cadena)
            except BrokenPipeError:
                # ffmpeg dejo de leer: se planto en algun trozo. No es fatal,
                # el tamano del mp4 lo delata y arriba se repara saltandolo.
                pass
            except OSError as e:
                log.error("[UNIR] %s: %s", etiqueta, e)
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.wait()
            if proc.returncode == 0 and os.path.exists(final):
                if nota:
                    log.warning("[UNIR] %s: hizo falta el modo tolerante", etiqueta)
                return final, cadena
            err.seek(0)
            ultimo = err.read().decode("utf-8", "replace").strip()[-300:]
    log.warning("[UNIR] %s: por tuberia no salio (%s); lo intento con el "
                "pegote en disco", etiqueta, ultimo)
    return None


def _remuxear_disco(ffmpeg: str, rutas, crudo: str, final: str, etiqueta: str,
                    total: int):
    """Como antes: escribe el pegote entero y luego lo remuxea.

    Solo se usa si la tuberia no funciono. Necesita el doble de disco, asi que
    se comprueba antes. -> (ruta, reencadenador) o None.
    """
    if not _hay_espacio(total, etiqueta, MARGEN_DISCO + 1):
        return None
    cadena = _escribir_crudo(rutas, crudo)
    if cadena is None:
        return None
    ultimo = ""
    for args_in, args_out, nota in INTENTOS_REMUX:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             *args_in, "-i", crudo, *args_out, final],
            capture_output=True, text=True, **PRIORIDAD_FONDO,
        )
        if proc.returncode == 0 and os.path.exists(final):
            if nota:
                log.warning("[UNIR] %s: hizo falta el modo tolerante", etiqueta)
            os.remove(crudo)
            return final, cadena
        ultimo = (proc.stderr or "").strip()[-300:]
    log.error("[UNIR] %s: ffmpeg no pudo remuxear: %s", etiqueta, ultimo)
    log.error("[UNIR] %s: queda el pegote en crudo; reintenta con "
              "python %s --unir --forzar", etiqueta,
              os.path.basename(__file__))
    return None


def _marcar_unida(carpeta_sesion: str, salida: str, n_segmentos: int) -> None:
    try:
        with open(os.path.join(carpeta_sesion, MARCADOR), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "salida": salida,
                    "segmentos": n_segmentos,
                    "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f, indent=2, ensure_ascii=False,
            )
    except OSError as e:
        log.warning("[UNIR] no se pudo marcar %s: %s", carpeta_sesion, e)


def escribir_info(carpeta_sesion: str, datos: dict) -> None:
    try:
        os.makedirs(carpeta_sesion, exist_ok=True)
        with open(os.path.join(carpeta_sesion, "info.json"), "w",
                  encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.warning("[INFO] %s", e)


# --------------------------------------------------------------------------
# Sesiones en disco (recuperacion y CLI)
# --------------------------------------------------------------------------

@dataclass
class SesionDisco:
    stream: str          # carpeta del stream: modelo_id
    sesion: str          # carpeta de la sesion: 1080p_h264_20260922-181500
    ruta: str
    archivos: int
    unida: bool

    @property
    def etiqueta(self) -> str:
        return self.stream + "_" + self.sesion


def sesiones_en_disco():
    if not os.path.isdir(STREAMS_DIR):
        return
    for stream in sorted(os.listdir(STREAMS_DIR)):
        ruta_stream = os.path.join(STREAMS_DIR, stream)
        if not os.path.isdir(ruta_stream):
            continue
        for sesion in sorted(os.listdir(ruta_stream)):
            if sesion == CARPETA_PLAYLISTS:
                continue
            ruta = os.path.join(ruta_stream, sesion)
            segs = os.path.join(ruta, CARPETA_SEGS)
            if not os.path.isdir(segs):
                continue
            try:
                archivos = sum(
                    1 for n in os.listdir(segs) if n.lower().endswith(EXT_SEGMENTO)
                )
            except OSError:
                continue
            yield SesionDisco(
                stream=stream,
                sesion=sesion,
                ruta=ruta,
                archivos=archivos,
                unida=os.path.exists(os.path.join(ruta, MARCADOR)),
            )


def recuperar_pendientes(remux: bool = True) -> int:
    """Une las sesiones que quedaron a medias (caida, kill, cierre forzado)."""
    pendientes = [s for s in sesiones_en_disco() if s.archivos and not s.unida]
    if not pendientes:
        return 0
    log.info("[RECUPERAR] %d sesion(es) a medio unir de la vez anterior",
             len(pendientes))
    hecho = 0
    for s in pendientes:
        try:
            if unir_sesion(s.ruta, s.etiqueta, remux=remux):
                hecho += 1
        except Exception as e:
            log.error("[RECUPERAR] %s: %s", s.etiqueta, e)
    return hecho


# --------------------------------------------------------------------------
# Addon
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# El tunel de la VPN, como proxy HTTP local (wireproxy)
# --------------------------------------------------------------------------
#
# Un tunel de WireGuard normal es de TODO el PC: al encenderlo o apagarlo se
# cortan las conexiones que este proxy tiene abiertas con doppiocdn y esos
# segundos no se piden nunca mas. Con 4 modelos en @vpn= cada una abria su
# propia ventana de VPN cada 5 minutos -- hasta 8 cambios de ruta por cada 5
# minutos, y en cada uno las otras grabaciones perdian segundos (los huecos de
# 18-64 s de las grabaciones del 24/09).
#
# wireproxy levanta el MISMO .conf de WireGuard en espacio de usuario y lo
# deja como un proxy HTTP local. No crea adaptador de red, no toca ninguna
# ruta y no pide administrador (comprobado: ipconfig no cambia, y una consulta
# por el proxy sale con la IP de Proton mientras la de al lado sale con la de
# casa). Asi el tunel se queda encendido siempre y se elige pedido por pedido
# quien sale por el: lo hace Detector._enrutar.
#
# Importante: no encender ADEMAS el tunel de vpn_wg.py. Los dos usan la misma
# clave de Proton y la sesion se pelea.

# wireproxy es un exe de consola: sin esto Windows le abre su ventana negra
SIN_VENTANA = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
)


class _Tunel(threading.Thread):
    """Mantiene wireproxy vivo mientras corra el proxy.

    direccion() dice si se puede usar ahora mismo. Si wireproxy no esta o el
    .conf no aparece, no pasa nada grave: todo sale directo, igual que antes
    de que existiera el tunel (las geobloqueadas no grabaran, como cuando la
    VPN estaba apagada).
    """

    ESPERA_PUERTO = 20.0        # a que acepte conexiones tras arrancarlo
    REINTENTO_MIN = 3.0
    REINTENTO_MAX = 60.0
    VIVIO_BASTANTE = 120.0      # si duro mas, el siguiente fallo no acumula

    def __init__(self, destino: str, exe: str, conf: str):
        threading.Thread.__init__(self, daemon=True, name="tunel")
        self.exe = exe
        self.conf = conf
        self.destino = None
        self.proc = None
        self.listo = False
        self._parar = threading.Event()
        host, _, puerto = destino.rpartition(":")
        try:
            self.destino = (host or "127.0.0.1", int(puerto))
        except ValueError:
            log.error("[TUNEL] M3U8_TUNEL=%r no es host:puerto", destino)

    def por_que_no(self) -> str:
        """'' si se puede usar; si no, que le falta, en una linea."""
        if not self.destino:
            return "M3U8_TUNEL no esta escrito como host:puerto"
        if not os.path.exists(self.exe):
            return ("falta wireproxy.exe (ponelo en %s; se baja de "
                    "github.com/whyvl/wireproxy/releases)" % AQUI)
        if not self.conf:
            return ("no encuentro el .conf de WireGuard junto al script, o hay "
                    "varios: eligelo con M3U8_TUNEL_CONF")
        if not os.path.exists(self.conf):
            return "no existe %s" % self.conf
        return ""

    def direccion(self):
        """(host, puerto) si el tunel esta arriba; None si no."""
        return self.destino if self.listo else None

    # -- vida del proceso --------------------------------------------------

    def run(self) -> None:
        if self._ya_escucha():
            # quedo uno vivo de una vez anterior: la ventana del proxy cerrada
            # sin Ctrl+C no llega a pararlo. Arrancar otro no serviria (no
            # podria coger el puerto y moriria), asi que se usa el que hay.
            self.listo = True
            log.info("[TUNEL] ya habia un tunel escuchando en %s:%d, uso ese "
                     "(quedo abierto de una vez anterior)",
                     self.destino[0], self.destino[1])
            return
        espera = self.REINTENTO_MIN
        while not self._parar.is_set():
            nacio = time.time()
            if self._arrancar():
                self._reflejar()            # vuelve cuando wireproxy muere
                self.listo = False
                if self._parar.is_set():
                    break
                log.warning("[TUNEL] wireproxy se cerro (codigo %s), lo reabro",
                            self.proc.poll() if self.proc else "?")
            if time.time() - nacio > self.VIVIO_BASTANTE:
                espera = self.REINTENTO_MIN
            else:
                espera = min(espera * 2, self.REINTENTO_MAX)
            if self._parar.wait(espera):
                break

    def parar(self) -> None:
        self._parar.set()
        self.listo = False
        self._matar()

    def _arrancar(self) -> bool:
        conf = self._generar_conf()
        if not conf:
            return False
        try:
            self.proc = subprocess.Popen(
                [self.exe, "-s", "-c", conf],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, **SIN_VENTANA,
            )
        except OSError as e:
            log.error("[TUNEL] no pude arrancar %s: %s",
                      os.path.basename(self.exe), e)
            return False
        if not self._esperar_puerto():
            log.error("[TUNEL] wireproxy no acepto conexiones en %s:%d en %ds. "
                      "Revisa el .conf (%s)", self.destino[0], self.destino[1],
                      int(self.ESPERA_PUERTO), os.path.basename(self.conf))
            self._matar()
            return False
        self.listo = True
        log.info("[TUNEL] listo en %s:%d con %s -- por aqui salen solo las "
                 "pestanas de @vpn", self.destino[0], self.destino[1],
                 os.path.basename(self.conf))
        return True

    def _ya_escucha(self) -> bool:
        try:
            socket.create_connection(self.destino, 1.0).close()
            return True
        except OSError:
            return False

    def _esperar_puerto(self) -> bool:
        fin = time.time() + self.ESPERA_PUERTO
        while time.time() < fin and not self._parar.is_set():
            if self.proc.poll() is not None:
                return False
            try:
                socket.create_connection(self.destino, 1.0).close()
                return True
            except OSError:
                time.sleep(0.3)
        return False

    # lo dice cada vez que alguien abre y cierra sin pedir nada: lo hace
    # _esperar_puerto al comprobar que ya escucha, y el navegador al cerrar
    # sus conexiones. No es un problema y llenaba el log.
    RUIDO = ("read request failed",)

    def _reflejar(self) -> None:
        """Pasa al log lo que se queje wireproxy, y espera a que muera."""
        salida = self.proc.stderr
        if salida is not None:
            for linea in salida:
                texto = linea.decode("utf-8", "replace").strip()
                if not texto or self._parar.is_set():
                    continue
                if any(x in texto for x in self.RUIDO):
                    continue
                log.warning("[TUNEL] %s", texto[:200])
        try:
            self.proc.wait()
        except OSError:
            pass

    def _matar(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass

    def _generar_conf(self) -> str:
        """El .conf del usuario + la seccion [http]. El original no se toca."""
        try:
            with open(self.conf, "r", encoding="utf-8", errors="replace") as f:
                texto = f.read()
        except OSError as e:
            log.error("[TUNEL] no pude leer %s: %s", self.conf, e)
            return ""
        if "[http]" in texto.lower():
            return self.conf              # ya lo trae puesto: se usa tal cual
        nuevo = (
            texto.rstrip() + "\n\n"
            "# Lo agrega detectar_m3u8.py y se regenera solo: es el .conf de\n"
            "# al lado mas la seccion que deja el tunel como proxy HTTP local.\n"
            "# Para cambiar de pais, edita el .conf original, no este.\n"
            "[http]\nBindAddress = %s:%d\n" % self.destino
        )
        ruta = os.path.join(os.path.dirname(self.conf) or AQUI, CONF_GENERADA)
        try:
            anterior = ""
            if os.path.exists(ruta):
                with open(ruta, "r", encoding="utf-8", errors="replace") as f:
                    anterior = f.read()
            if anterior != nuevo:
                with open(ruta, "w", encoding="utf-8") as f:
                    f.write(nuevo)
        except OSError as e:
            log.error("[TUNEL] no pude escribir %s: %s", ruta, e)
            return ""
        return ruta


class Detector:
    def __init__(self):
        self.streams: dict = {}
        self.modelos: dict = {}
        self.inits: dict = {}           # (sid, variante) -> (nombre, datos)
        self.buffer: dict = {}          # (sid, variante) -> [(nombre, datos)]
        self._bytes_buffer = 0
        self.lock = threading.RLock()
        self.seleccion = Seleccion(SELECCION)
        self.escritor = Escritor()
        self.trabajos: queue.Queue = queue.Queue()
        self._parar = threading.Event()
        self._activos_txt = ""
        self._ultima_tabla = 0.0
        self.tunel = None               # se arranca en running()
        self._por_tunel = 0             # pedidos enrutados, para la tabla

        configurar_log()
        os.makedirs(STREAMS_DIR, exist_ok=True)
        if not os.path.exists(SELECCION):
            with open(SELECCION, "w", encoding="utf-8") as f:
                f.write(
                    "# Un ID de stream o nombre de modelo por linea. '*' = todo.\n"
                    "# Los IDs detectados aparecen en activos.txt y en consola.\n"
                    "# Agregar linea = empieza a grabar. Borrarla = cierra y une.\n"
                    "\n"
                    "@calidad=max\n"
                    "@codec=max\n"
                    "@forzar=1\n"
                    "@borrosas=0\n"
                    "@privado=160p\n"
                    "@variantes=1\n"
                    "@unir=1\n"
                    "@inactividad=120\n"
                )

        global hay_grabaciones
        hay_grabaciones = self._grabando_alguna

        self.escritor.start()
        self.hilo_final = threading.Thread(
            target=self._bucle_finalizador, daemon=True, name="finalizador"
        )
        self.hilo_final.start()
        self.hilo_vigila = threading.Thread(
            target=self._bucle_vigilante, daemon=True, name="vigilante"
        )
        self.hilo_vigila.start()

    def _grabando_alguna(self) -> bool:
        """Hay alguna emision escribiendo segmentos ahora mismo.

        Lo consulta el freno de la union: mientras alguien grabe, unir va
        despacio para no robarle disco ni CPU.
        """
        limite = time.time() - 30
        with self.lock:
            return any(st.grabando and st.ultimo_segmento > limite
                       for st in self.streams.values())

    # -- ciclo de vida del addon -----------------------------------------

    def running(self) -> None:
        configurar_log()
        log.info("=" * 70)
        log.info(
            "[DETECTOR] base=%s | ffmpeg=%s",
            BASE, "ok" if os.path.exists(FFMPEG) else "NO ENCONTRADO",
        )
        log.info(
            "[DETECTOR] IDs detectados -> %s | edita %s para grabar/parar",
            os.path.basename(ACTIVOS), os.path.basename(SELECCION),
        )
        self._arrancar_tunel()
        log.info("=" * 70)
        self.seleccion.actual()
        threading.Thread(
            target=recuperar_pendientes, daemon=True, name="recuperador"
        ).start()

    def _arrancar_tunel(self) -> None:
        """Deja el tunel de la VPN listo, sin tocar las rutas del PC."""
        if not TUNEL:
            log.info("[TUNEL] desactivado (M3U8_TUNEL vacio): todo sale directo")
            return
        tunel = _Tunel(TUNEL, WIREPROXY, TUNEL_CONF)
        falta = tunel.por_que_no()
        if falta:
            log.warning("[TUNEL] sin tunel: %s", falta)
            log.warning("[TUNEL] las modelos de @vpn= solo grabaran si no estan "
                        "geobloqueadas desde aqui")
            return
        self.tunel = tunel
        tunel.start()

    def done(self) -> None:
        self._parar.set()
        if self.tunel:
            self.tunel.parar()
        with self.lock:
            for st in list(self.streams.values()):
                self._encolar_cierre(st, "cierre del proxy")

        if not self.trabajos.empty():
            log.info("[CIERRE] uniendo grabaciones en curso, no cierres la ventana...")
        self.trabajos.put(None)
        self.hilo_final.join(900)
        self.escritor.detener()

        if self.escritor.errores or self.escritor.descartados:
            log.warning(
                "[DISCO] errores=%d descartados=%d",
                self.escritor.errores, self.escritor.descartados,
            )
        if filtro_ruido.contados:
            log.info("[RED] %d conexiones cortadas ignoradas", filtro_ruido.contados)
        log.info("[FIN] listo")

    # -- modelos ----------------------------------------------------------

    def _asociar(self, sid: str, modelo: str) -> None:
        with self.lock:
            if not modelo or self.modelos.get(sid) == modelo:
                return
            self.modelos[sid] = modelo
            st = self.streams.get(sid)
            if st and st.modelo == "desconocido":
                st.modelo = modelo
        log.info("[MODELO] %s -> id %s", modelo, sid)

    def _modelos_desde_request(self, flow) -> None:
        url = flow.request.pretty_url
        modelo = modelo_desde_referer(flow.request.headers.get("referer", ""))
        if not modelo:
            return

        m = RE_API_MODELO.search(url)
        if m:
            self._asociar(m.group(1), modelo)
            return

        # el manifiesto/segmento lleva el id y el referer el nombre del modelo
        if "doppiocdn." in url.lower():
            sid = extraer_stream_id(url.lower())
            if sid:
                self._asociar(sid, modelo)

    def _modelo_desde_json(self, flow, sid: str) -> None:
        if self.modelos.get(sid) or not flow.response:
            return
        try:
            cuerpo = flow.response.get_text(strict=False) or ""
        except Exception:
            return
        m = RE_USERNAME.search(cuerpo[:20000])
        if m and m.group(1).lower() not in NO_MODELOS:
            self._asociar(sid, m.group(1))

    # -- hooks ------------------------------------------------------------

    def _va_por_tunel(self, flow) -> bool:
        """Este destino es de lo que el sitio esconde por pais?

        Solo su pagina y su API (unos KB por minuto). Los assets del
        reproductor viven en otros dominios y el video en doppiocdn: por ahi
        no hay geobloqueo y meterlos en el tunel es lo que ahogaba la
        grabacion.
        """
        if TUNEL_TODO:
            return True
        host = (flow.request.pretty_host or "").lower()
        for dominio in TUNEL_HOSTS:
            if host == dominio or host.endswith("." + dominio):
                return True
        if TUNEL_PLAYLIST:
            info = clasificar(flow.request.pretty_url.lower())
            return bool(info) and info[0] == "playlist"
        return False

    def _enrutar(self, flow) -> None:
        """Manda por el tunel de la VPN solo los pedidos de las pestanas @vpn.

        El navegador marca cada pedido de esas pestanas con CABECERA_TUNEL
        (supervisor.py, set_extra_http_headers). Se borra siempre, marcada o
        no: al sitio no tiene por que llegarle.
        """
        marcado = flow.request.headers.pop(CABECERA_TUNEL, None) is not None
        destino = self.tunel.direccion() if self.tunel else None
        if destino is None:
            return
        if not self._va_por_tunel(flow):
            return
        if not marcado:
            # red de seguridad: si el pedido no viene de la pestana (un worker
            # del reproductor, una precarga) el referer todavia dice de quien
            # es la pagina
            modelo = modelo_desde_referer(flow.request.headers.get("referer", ""))
            if not modelo or modelo.lower() not in self.seleccion.actual().vpn:
                return
        self._por_tunel += 1
        if flow.server_conn.timestamp_start is not None and _ConexionServidor:
            # ya hay una conexion abierta y directa: mitmproxy la reutilizaria
            # y el via no haria nada. Se cambia por una nueva (es lo que hace
            # el ejemplo oficial change_upstream_proxy.py).
            flow.server_conn = _ConexionServidor(address=flow.server_conn.address)
        flow.server_conn.via = ("http", destino)

    def _init_completo(self, flow) -> None:
        """Que el init venga siempre con sus bytes, no un 304 vacio.

        Si el navegador ya tiene el init en su cache lo revalida, el CDN
        contesta 304 sin cuerpo y el proxy nunca ve los bytes. Sin init no se
        puede armar el mp4: se pierde la grabacion entera (pasado el 27/09 con
        Ameellie, 393 MB de segmentos buenos y ningun video). Son 2 KB: se le
        quitan las cabeceras condicionales para que el CDN lo mande completo.
        """
        url = flow.request.pretty_url.lower()
        if "init" not in url or "doppiocdn." not in url:
            return                    # el 99% de los pedidos se va por aqui
        info = clasificar(url)
        if info and info[0] == "init":
            flow.request.headers.pop("if-none-match", None)
            flow.request.headers.pop("if-modified-since", None)

    def request(self, flow) -> None:
        try:
            self._init_completo(flow)
        except Exception as e:
            log.error("[REQUEST] init: %s", e)
        try:
            self._enrutar(flow)
        except Exception as e:
            log.error("[TUNEL] no pude enrutar %s: %s",
                      flow.request.pretty_host, e)
        try:
            self._modelos_desde_request(flow)
        except Exception as e:
            log.error("[REQUEST] %s", e)

    def response(self, flow) -> None:
        try:
            url = flow.request.pretty_url
            bajo = url.lower()

            m = RE_API_MODELO.search(bajo)
            if m:
                self._modelo_desde_json(flow, m.group(1))
                return

            if "doppiocdn." not in bajo:
                return

            sid = extraer_stream_id(bajo)
            if not sid:
                return

            info = clasificar(bajo)
            if not info:
                return
            tipo, calidad, codec, variante, fuente = info
            borrosa = variante.endswith("_blurred")

            resp = flow.response
            if not resp or resp.status_code != 200:
                return
            try:
                datos = resp.content
            except Exception:
                return
            if not datos:
                return

            ahora = time.time()
            with self.lock:
                st = self._stream(sid, ahora)
                cfg = self.seleccion.actual()

                if tipo != "playlist":
                    det = st.detectadas.get(variante)
                    if det is None:
                        det = Deteccion(calidad=calidad, codec=codec,
                                        ultimo=ahora, desde=ahora,
                                        fuente=fuente, borrosa=borrosa,
                                        alto_fuente=st.alto_fuente if fuente else 0)
                        st.detectadas[variante] = det
                        log.info("[VARIANTE] id %s | %s | %s", sid, st.modelo,
                                 st.nombre_master(variante))
                    elif ahora - det.ultimo > cfg.cambio:
                        # volvio despues de un corte: empieza otra racha, hay
                        # que sostenerla @cambio segundos para que se la elija
                        det.desde = ahora
                    det.ultimo = ahora
                    det.veces += 1

                # el init se guarda en memoria aunque todavia no se grabe:
                # si agregas el ID a mitad de reproduccion, ya no vuelve a pedirse
                if tipo == "init":
                    self._cachear_init(sid, variante, nombre_archivo(url), datos)
                    # y que no se lo guarde: la proxima vez que lo pida tiene
                    # que pasar por aqui otra vez (ver _init_completo)
                    resp.headers["cache-control"] = "no-store"

                if tipo == "playlist":
                    # saber que ofrece la modelo aunque no se este grabando
                    self._leer_master(st, resp)
                    # El master se pide UNA sola vez, al arrancar el video, y
                    # ahi todavia puede no saberse el nombre de la modelo (lo
                    # dice el referer o /api/front/models/<id>). Si se esperaba
                    # a cfg.quiere() el recorte no llegaba nunca y el
                    # reproductor se quedaba con todas las calidades para
                    # bajarse solo. Recortar de mas no cuesta nada: el
                    # navegador solo abre las paginas que pediste.
                    if cfg.forzar and not st.es_preview:
                        self._forzar_calidad(st, cfg, resp)

                if not cfg.quiere(sid, st.modelo):
                    return
                st.fuera = 0

                if tipo == "playlist":
                    # se guarda el manifiesto original, no el recortado
                    destino = os.path.join(
                        st.carpeta, CARPETA_PLAYLISTS, nombre_archivo(url)
                    )
                    self.escritor.encolar(destino, datos)
                    return

                if cfg.una_variante:
                    elegida = self._elegir(st, cfg, ahora)
                    # el relleno del show privado no se guarda ni en memoria:
                    # llegan segmentos toda la hora que dure y no sirven
                    det = st.detectadas.get(variante)
                    relleno = cfg.es_privado(det.altura if det else 0)
                    if not elegida:
                        # todavia decidiendo calidad: se guardan en memoria para
                        # no perder los primeros segundos de la grabacion
                        if tipo == "segmento" and not relleno:
                            self._bufferizar(sid, variante,
                                             nombre_archivo(url), datos)
                        return
                    if variante != elegida:
                        # puede que el reproductor este cambiando de calidad:
                        # se guarda un poco por si hay que pasarse a esta
                        # (el init no: ese ya quedo en self.inits)
                        if tipo == "segmento" and not relleno and (
                                cfg.fallback
                                or cfg.acepta_variante(calidad, codec, fuente,
                                                       borrosa)):
                            self._bufferizar(sid, variante,
                                             nombre_archivo(url), datos)
                        return
                elif not cfg.acepta_variante(calidad, codec, fuente, borrosa):
                    return

                var = st.variantes.get(variante)
                if var is None:
                    var = self._abrir_sesion(st, variante, calidad, codec)

                if not st.grabando:
                    st.grabando = True
                    st.inicio = ahora
                    log.info("[GRABANDO] id %s | modelo %s | %s",
                             sid, st.modelo, variante)
                self._avisar_calidad(st, cfg, variante)

                st.ultimo_segmento = ahora
                self._guardar(st, var, tipo, nombre_archivo(url), datos)

        except Exception as e:
            log.error("[RESPONSE] %s", e)

    def _leer_master(self, st: Stream, resp) -> None:
        """Guarda lo que ofrece el master.

        Es la unica fuente fiable de cual es la mejor calidad de esta modelo:
        el reproductor pide una sola variante y puede no ser la mejor.
        """
        try:
            texto = resp.get_text(strict=False) or ""
        except Exception:
            return
        _, rendiciones, _ = leer_master(texto)
        if not rendiciones:
            return
        nuevo = {}
        for r in rendiciones:
            clave = r.nombre
            n = 2
            while clave in nuevo:          # dos rendiciones con el mismo alto
                clave = "%s#%d" % (r.nombre, n)
                n += 1
            nuevo[clave] = r
        if set(nuevo) != set(st.master):
            log.info("[MASTER] id %s (%s) ofrece: %s", st.sid, st.modelo,
                     ", ".join(r.nombre for r in sorted(
                         rendiciones, key=lambda r: -r.alto)))
        st.master = nuevo
        st.master_ts = time.time()

        # El master es el unico sitio donde se ve la altura real de la fuente
        # (sus segmentos no la llevan en la URL). Sin esto la fuente valia 0 y
        # cualquier 240p le ganaba al elegir calidad.
        fuentes = [r for r in rendiciones if r.fuente]
        if fuentes:
            f = max(fuentes, key=lambda r: r.alto)
            if st.fuente_nombre != f.nombre:
                log.info("[FUENTE] id %s (%s): la emision original es %s",
                         st.sid, st.modelo, f.nombre)
            st.fuente_nombre = f.nombre
            st.alto_fuente = f.alto
            for det in st.detectadas.values():
                if det.fuente:
                    det.alto_fuente = f.alto

    def _avisar_calidad(self, st: Stream, cfg: Config, variante: str) -> None:
        """Avisa si se esta grabando peor calidad de la que la modelo ofrece."""
        variante = st.nombre_master(variante)
        if len(st.master) < 2 or variante not in st.master:
            # una sola variante, o el nombre del segmento no se corresponde
            # con ninguna del master: no hay nada que comparar
            return
        mejor = st.mejor_posible(cfg)
        if not mejor or mejor == variante:
            if st.aviso_master and mejor == variante:
                st.aviso_master = ""
                log.info("[CALIDAD] id %s (%s): ya esta en %s, la mejor que hay",
                         st.sid, st.modelo, variante)
            return
        if st.aviso_master == variante:
            return
        st.aviso_master = variante
        log.warning(
            "[CALIDAD] id %s (%s): grabando %s pero la modelo ofrece %s -- "
            "recarga la pagina (F5) para que el reproductor pida la mejor",
            st.sid, st.modelo, variante, mejor,
        )

    def _forzar_calidad(self, st: Stream, cfg: Config, resp) -> None:
        """Recorta el master .m3u8 para que el player solo vea la mejor calidad.

        Asi no baja de calidad al pasar la pestana a segundo plano.
        """
        # Las miniaturas del grid tambien traen master, y no hay que
        # tocarlas: subirles la calidad seria gastar banda en lo que no se
        # esta mirando. Se distinguen por tamano, no por tener "source":
        # hay modelos que emiten a 720p y su master no trae rendicion source.
        if master_es_preview(list(st.master.values())):
            return
        try:
            texto = resp.get_text(strict=False) or ""
        except Exception:
            return
        resultado = reducir_master(texto, cfg)
        if not resultado:
            return
        nuevo, elegida, todas = resultado
        try:
            resp.text = nuevo
        except Exception as e:
            log.warning("[FORZAR] id %s: no se pudo reescribir el master: %s",
                        st.sid, e)
            return
        if st.forzada != elegida:
            st.forzada = elegida
            log.info(
                "[FORZAR] id %s (%s): el reproductor solo vera %s (el master "
                "traia: %s)", st.sid, st.modelo, elegida, ", ".join(todas),
            )

    def _stream(self, sid: str, ahora: float) -> Stream:
        st = self.streams.get(sid)
        if st is None:
            st = Stream(sid=sid, modelo=self.modelos.get(sid, "desconocido"),
                        primera=ahora, ultimo_visto=ahora)
            self.streams[sid] = st
            log.info(
                "[DETECTADO] id %s | modelo %s -- ponlo en %s para grabar",
                sid, st.modelo, os.path.basename(SELECCION),
            )
        elif st.modelo == "desconocido":
            st.modelo = self.modelos.get(sid, "desconocido")
        st.ultimo_visto = ahora
        return st

    # -- eleccion de variante ---------------------------------------------

    def _elegir(self, st: Stream, cfg: Config, ahora: float):
        """Una sola variante por stream: la mejor que pase el filtro.

        Sigue los cambios de calidad del reproductor (ABR):
          - si la elegida deja de llegar durante @cambio segundos y esta
            entrando otra, se pasa a esa (lo que se acumulo en el buffer se
            vuelca, asi no queda hueco);
          - si mas tarde vuelve una mejor y se sostiene @cambio segundos, se
            vuelve a ella.
        Cada cambio cierra la sesion anterior y abre otra: no se pueden mezclar
        resoluciones distintas dentro del mismo mp4.
        """
        # una variante sigue "viva" si llego algo en los ultimos @cambio segundos
        vivas = {v: d for v, d in st.detectadas.items()
                 if ahora - d.ultimo <= cfg.cambio}

        if not vivas:
            return st.elegida       # pausa momentanea: conservar la elegida

        if not st.elegida:
            if ahora - st.primera < cfg.espera:
                # recien detectado: dar tiempo a que aparezcan las otras
                # calidades, si no se elige la primera que llegue (la mas baja)
                return ""
            return self._cambiar(st, cfg, vivas, ahora, "")

        if st.elegida not in vivas:
            return self._cambiar(
                st, cfg, vivas, ahora,
                "%s dejo de llegar (el reproductor bajo/subio la calidad)"
                % st.elegida,
            )

        # la elegida sigue entrando: solo cambiar si volvio una claramente mejor
        actual = st.detectadas[st.elegida]
        coinciden = {
            v: d for v, d in vivas.items()
            if cfg.acepta_variante(d.calidad, d.codec, d.fuente, d.borrosa)
        }
        if coinciden:
            mejor = max(coinciden, key=lambda v: puntaje(v, coinciden[v], cfg))
            if (mejor != st.elegida
                    and puntaje(mejor, coinciden[mejor], cfg)
                    > puntaje(st.elegida, actual, cfg)
                    and ahora - coinciden[mejor].desde >= cfg.cambio):
                return self._cambiar(st, cfg, {mejor: coinciden[mejor]}, ahora,
                                     "volvio %s" % mejor)
        return st.elegida

    def _cambiar(self, st: Stream, cfg: Config, vivas: dict, ahora: float,
                 motivo: str) -> str:
        """Elige la mejor de 'vivas' y cierra la sesion anterior si la habia."""
        # Si lo unico que llega es el relleno de 160p, la modelo se fue a show
        # privado: no es una calidad mas baja a la que pasarse.
        publicas = {v: d for v, d in vivas.items()
                    if not cfg.es_privado(d.altura)}
        if vivas and not publicas:
            return self._a_privado(st, vivas, ahora)
        vivas = publicas
        coinciden = {
            v: d for v, d in vivas.items()
            if cfg.acepta_variante(d.calidad, d.codec, d.fuente, d.borrosa)
        }
        # el fallback nunca cae en el stream censurado: eso no es "peor
        # calidad", es otro video (show privado). Mejor no grabar nada.
        nitidas = {v: d for v, d in vivas.items()
                   if cfg.borrosas or not d.borrosa}
        if coinciden:
            nueva = max(coinciden, key=lambda v: puntaje(v, coinciden[v], cfg))
        elif not cfg.fallback or not nitidas:
            if st.elegida and not st.aviso_fallback:
                st.aviso_fallback = True
                log.warning(
                    "[ESPERANDO] id %s (%s): solo hay %s y @fallback=0 -- "
                    "no grabo hasta que vuelva calidad=%s codec=%s",
                    st.sid, st.modelo, ", ".join(sorted(vivas)),
                    ",".join(cfg.calidad) or "*", ",".join(cfg.codec) or "*",
                )
            anterior, st.elegida = st.elegida, ""
            if anterior:
                self._cerrar_variante(st, anterior, motivo or "calidad no permitida")
            return ""
        else:
            nueva = max(nitidas, key=lambda v: puntaje(v, nitidas[v], cfg))
            if not st.aviso_fallback:
                st.aviso_fallback = True
                log.warning(
                    "[FALLBACK] id %s (%s): ninguna variante coincide con "
                    "calidad=%s codec=%s -- grabo %s (hay: %s)",
                    st.sid, st.modelo, ",".join(cfg.calidad) or "*",
                    ",".join(cfg.codec) or "*", nueva, ", ".join(sorted(vivas)),
                )

        if st.privado:
            st.privado = False
            log.info(
                "[PRIVADO] id %s (%s): volvio a %s tras %d min -- sigo "
                "grabando, en un archivo nuevo",
                st.sid, st.modelo, nueva,
                int((ahora - st.privado_desde) / 60),
            )

        if nueva == st.elegida:
            return nueva

        anterior = st.elegida
        st.elegida = nueva
        if anterior:
            st.aviso_fallback = False
            log.info(
                "[CAMBIO] id %s (%s) | %s -> %s | %s | se corta en otro archivo",
                st.sid, st.modelo, anterior, nueva, motivo,
            )
            self._cerrar_variante(st, anterior, "cambio de calidad a " + nueva)
        return nueva

    def _a_privado(self, st: Stream, vivas: dict, ahora: float) -> str:
        """Se fue a show privado: cerrar el mp4 y quedarse esperando.

        Lo que sirve stripchat mientras dura el privado es un video de relleno
        de 160p. Seguir grabandolo llenaba la sesion de horas de nada, y como
        es otra resolucion habia que cortar el archivo igual. Se corta aqui,
        se une (el mp4 queda listo en el acto) y se reanuda solo cuando vuelve
        una calidad de verdad: no hay que tocar grabar.txt.
        """
        anterior, st.elegida = st.elegida, ""
        if anterior:
            self._cerrar_variante(
                st, anterior,
                "show privado (solo llega %s)" % ", ".join(sorted(vivas)),
            )
        st.grabando = False
        # sin esto el vigilante lo volveria a cerrar por @inactividad
        st.ultimo_segmento = 0.0
        st.aviso_fallback = False
        st.aviso_master = ""
        # al volver, dar @espera segundos antes de elegir: si no, se graba la
        # primera calidad que llegue, que suele ser la mas baja
        st.primera = ahora
        if not st.privado:
            st.privado = True
            st.privado_desde = ahora
            log.warning(
                "[PRIVADO] id %s (%s): bajo a %s -- se fue a show privado. "
                "Cierro y guardo lo grabado; cuando salga vuelvo sola",
                st.sid, st.modelo, ", ".join(sorted(vivas)),
            )
        return ""

    def _abrir_sesion(self, st: Stream, variante: str, calidad: str,
                      codec: str) -> Variante:
        ses = sello()
        var = Variante(
            calidad=calidad, codec=codec, variante=variante, sesion=ses,
            carpeta=os.path.join(st.carpeta, variante + "_" + ses),
        )
        st.variantes[variante] = var
        log.info("[SESION] id %s | %s | %s", st.sid, st.modelo, var.etiqueta)

        guardado = self.inits.get((st.sid, variante))
        if guardado:
            nombre, datos = guardado
            if not nombre.lower().startswith("init"):
                nombre = "init_" + nombre
            self.escritor.encolar(os.path.join(var.carpeta, nombre), datos)
            var.init = True
            log.info("[INIT] id %s | %s | %s (de cache)", st.sid, var.etiqueta, nombre)

        # volcar lo que se acumulo mientras se decidia la calidad
        pendientes = self.buffer.pop((st.sid, variante), [])
        for clave in [k for k in self.buffer if k[0] == st.sid]:
            self._bytes_buffer -= sum(len(d) for _, d in self.buffer.pop(clave))
        if pendientes:
            self._bytes_buffer -= sum(len(d) for _, d in pendientes)
            log.info("[BUFFER] id %s | %s | %d segmentos recuperados",
                     st.sid, var.etiqueta, len(pendientes))
            for nombre, datos in pendientes:
                self._guardar(st, var, "segmento", nombre, datos)
        return var

    def _bufferizar(self, sid: str, variante: str, nombre: str,
                    datos: bytes) -> None:
        clave = (sid, variante)
        cola = self.buffer.setdefault(clave, [])
        cola.append((nombre, datos))
        self._bytes_buffer += len(datos)
        while len(cola) > MAX_BUFFER_SEGS:
            self._bytes_buffer -= len(cola.pop(0)[1])
        while self._bytes_buffer > MAX_BUFFER_BYTES and self.buffer:
            vieja = next(iter(self.buffer))
            self._bytes_buffer -= sum(len(d) for _, d in self.buffer.pop(vieja))

    def _cachear_init(self, sid: str, variante: str, nombre: str,
                      datos: bytes) -> None:
        if len(datos) > 2 * 1024 * 1024:
            return
        if len(self.inits) >= MAX_INITS:
            self.inits.pop(next(iter(self.inits)), None)
        self.inits[(sid, variante)] = (nombre, datos)

    # -- guardado ---------------------------------------------------------

    def _guardar(self, st: Stream, var: Variante, tipo: str,
                 nombre: str, datos: bytes) -> None:
        if tipo == "init":
            if var.init:
                return
            var.init = True
            if not nombre.lower().startswith("init"):
                nombre = "init_" + nombre
            self.escritor.encolar(os.path.join(var.carpeta, nombre), datos)
            log.info("[INIT] id %s | %s | %s", st.sid, var.etiqueta, nombre)
            return

        if nombre in var.vistos:
            return
        var.vistos.add(nombre)

        destino = os.path.join(var.carpeta, CARPETA_SEGS, nombre)
        if not self.escritor.encolar(destino, datos):
            var.vistos.discard(nombre)
            return

        var.segmentos += 1
        var.bytes += len(datos)

        if var.segmentos % 20 == 0:
            log.info(
                "[%s] %s | %s | %d segmentos | %.1f MB | %.1f min",
                st.sid, st.modelo, var.variante, var.segmentos,
                var.bytes / 1048576, (time.time() - var.inicio) / 60,
            )

    # -- vigilante (grabar.txt en caliente + inactividad) ------------------

    def _bucle_vigilante(self) -> None:
        while not self._parar.wait(INTERVALO_VIGILANTE):
            try:
                self._revisar()
            except Exception as e:
                log.error("[VIGILANTE] %s", e)

    def _revisar(self) -> None:
        cfg = self.seleccion.actual()
        ahora = time.time()
        cerrar = []

        with self.lock:
            for sid, st in list(self.streams.items()):
                for v, d in list(st.detectadas.items()):
                    if ahora - d.ultimo > OLVIDAR_STREAM:
                        del st.detectadas[v]

                if st.grabando:
                    if not cfg.quiere(sid, st.modelo):
                        # dos ciclos seguidos: un guardado a medias no corta nada
                        st.fuera += 1
                        if st.fuera >= CICLOS_PARA_CERRAR:
                            cerrar.append(
                                (st, "quitado de " + os.path.basename(SELECCION))
                            )
                    else:
                        st.fuera = 0
                        if cfg.inactividad and st.ultimo_segmento and \
                                ahora - st.ultimo_segmento > cfg.inactividad:
                            cerrar.append(
                                (st, "sin segmentos hace %ds" % int(cfg.inactividad))
                            )
                elif ahora - st.ultimo_visto > OLVIDAR_STREAM:
                    del self.streams[sid]
                    self.inits = {k: v for k, v in self.inits.items() if k[0] != sid}
                    for k in [k for k in self.buffer if k[0] == sid]:
                        self._bytes_buffer -= sum(len(d) for _, d in self.buffer.pop(k))

            for st, motivo in cerrar:
                self._encolar_cierre(st, motivo)

        self._publicar_activos(ahora)

    def _cerrar_variante(self, st: Stream, variante: str, motivo: str) -> None:
        """Cierra una sola sesion del stream (cambio de calidad); el resto sigue."""
        var = st.variantes.pop(variante, None)
        if var is None or not var.segmentos:
            return
        log.info("[CIERRE] id %s (%s) | %s -- %s | uniendo en segundo plano",
                 st.sid, st.modelo, var.etiqueta, motivo)
        self.trabajos.put((st.sid, st.modelo, motivo, {variante: var}))

    def _encolar_cierre(self, st: Stream, motivo: str) -> None:
        """Se llama con el lock tomado. Desprende la sesion y la manda a unir."""
        variantes, st.variantes = st.variantes, {}
        st.grabando = False
        st.ultimo_segmento = 0.0
        st.elegida = ""
        st.fuera = 0
        st.aviso_fallback = False
        st.privado = False
        if not any(v.segmentos for v in variantes.values()):
            return
        log.info(
            "[CIERRE] id %s (%s) -- %s | uniendo en segundo plano",
            st.sid, st.modelo, motivo,
        )
        self.trabajos.put((st.sid, st.modelo, motivo, variantes))

    # -- finalizador (union sin bloquear el proxy) -------------------------

    def _bucle_finalizador(self) -> None:
        while True:
            trabajo = self.trabajos.get()
            try:
                if trabajo is None:
                    return
                self._finalizar(*trabajo)
            except Exception as e:
                log.error("[UNIR] %s", e)
            finally:
                self.trabajos.task_done()

    def _finalizar(self, sid: str, modelo: str, motivo: str, variantes: dict) -> None:
        # esperar a que el escritor vacie lo encolado antes de concatenar
        if not self.escritor.barrera().wait(180):
            log.warning("[UNIR] id %s: escrituras pendientes, sigo igual", sid)

        cfg = self.seleccion.actual()
        for nombre, var in variantes.items():
            if not var.segmentos:
                continue
            escribir_info(var.carpeta, {
                "id": sid,
                "modelo": modelo,
                "variante": var.variante,
                "sesion": var.sesion,
                "calidad": var.calidad,
                "codec": var.codec,
                "segmentos": var.segmentos,
                "bytes": var.bytes,
                "init": var.init,
                "motivo_cierre": motivo,
                "inicio": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(var.inicio)),
                "fin": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            etiqueta = "%s_%s_%s_%s" % (modelo, sid, var.variante, var.sesion)
            if cfg.unir:
                unir_sesion(var.carpeta, etiqueta)
            else:
                log.info("[UNIR] @unir=0, queda sin unir: %s", var.carpeta)

    # -- listado de activos ------------------------------------------------

    def _publicar_activos(self, ahora: float) -> None:
        cfg = self.seleccion.actual()
        with self.lock:
            filas = []
            for sid, st in sorted(self.streams.items()):
                vivas = st.vivas(ahora)
                variantes = ", ".join(sorted(vivas)) or "-"
                if st.grabando:
                    mb = st.bytes / 1048576
                    estado = "GRABANDO %s (%d seg, %.0f MB)" % (
                        st.elegida or ",".join(sorted(st.variantes)),
                        st.segmentos, mb,
                    )
                elif not vivas:
                    estado = "inactivo (ultimo %s)" % time.strftime(
                        "%H:%M:%S", time.localtime(st.ultimo_visto)
                    )
                elif st.privado:
                    estado = "SHOW PRIVADO desde las %s (guardado; espero)" % (
                        time.strftime("%H:%M:%S",
                                      time.localtime(st.privado_desde))
                    )
                elif st.es_preview:
                    estado = "preview del grid"
                else:
                    estado = "detectado"
                filas.append((sid, st.modelo, st.mejor_posible(cfg) or "?",
                              variantes, estado))

        ancho = max([len(f[3]) for f in filas] + [20]) + 2
        formato = "%-12s %-20s %-14s %-" + str(ancho) + "s %s"
        lineas = [
            "# Streams detectados -- se regenera solo cada %ds" % int(INTERVALO_VIGILANTE),
            "# Copia el ID (o el modelo) a %s para grabar; borralo para cerrar."
            % os.path.basename(SELECCION),
            "# MEJOR = lo mejor que ofrece la modelo (segun su master .m3u8).",
            "# 'preview del grid' = miniatura de la portada, no una pestana abierta.",
            "# %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            formato % ("ID", "MODELO", "MEJOR", "PIDE EL PLAYER", "ESTADO"),
        ]
        for fila in filas:
            lineas.append(formato % fila)
        if not filas:
            lineas.append("(nada detectado todavia: abre la pagina del modelo)")
        texto = "\n".join(lineas) + "\n"

        cuerpo = texto.split("\n", 5)[5:]
        if cuerpo != self._activos_txt.split("\n", 5)[5:]:
            try:
                with open(ACTIVOS, "w", encoding="utf-8") as f:
                    f.write(texto)
            except OSError as e:
                log.warning("[ACTIVOS] %s", e)
            self._activos_txt = texto

        if filas and ahora - self._ultima_tabla >= INTERVALO_TABLA:
            self._ultima_tabla = ahora
            log.info("---- streams detectados ----")
            for fila in filas:
                log.info("  " + formato % fila)
            # si esto crece de unas decenas por minuto, algo que no deberia
            # (los assets o el video) se esta yendo por el tunel y la
            # grabacion de esa modelo se va a resentir
            if self._por_tunel:
                log.info("  (%d pedidos por el tunel de la VPN)", self._por_tunel)
                self._por_tunel = 0


# --------------------------------------------------------------------------
# Modo CLI (sin mitmproxy): listar y unir lo ya descargado
# --------------------------------------------------------------------------

def main(argv) -> int:
    configurar_log()
    forzar = "--forzar" in argv or "--rehacer" in argv or "-f" in argv

    if "--unir" in argv or "--recuperar" in argv:
        filtros = [a.lower() for a in argv if not a.startswith("-")]
        hecho = 0
        for s in sesiones_en_disco():
            if filtros and not any(
                f in s.stream.lower() or f in s.sesion.lower() for f in filtros
            ):
                continue
            if not s.archivos:
                continue
            if s.unida and not forzar:
                log.info("[SALTO] %s ya unida (--forzar para rehacer)", s.etiqueta)
                continue
            if unir_sesion(s.ruta, s.etiqueta):
                hecho += 1
        if not hecho:
            log.info("nada que unir en %s", STREAMS_DIR)
        return 0

    total = 0
    for s in sesiones_en_disco():
        total += 1
        log.info(
            "%-34s %-30s %5d segmentos  %s",
            s.stream, s.sesion, s.archivos, "unida" if s.unida else "PENDIENTE",
        )
    if not total:
        log.info("sin descargas en %s", STREAMS_DIR)
    else:
        log.info("")
        log.info("unir pendientes:  python %s --unir", os.path.basename(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
else:
    addons = [Detector()]
