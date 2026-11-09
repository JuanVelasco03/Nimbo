# -*- coding: utf-8 -*-
"""grabador_chaturbate.py

SPEC 18: quinto proceso de captura, hermano de grabador_directo.py (camino
directo de stripchat) pero para chaturbate. **Puerto deliberado** de la
parte agnostica del sitio de grabador_directo.py -- Escritor, LectorGrabar
+ _ConfigGeneral, el pool de descargas, la cola de union con su hilo
consumidor unico, fila_estado()/_publicar_estado(), y la clase Grabador
completa con sus dos eventos separados _parar/_fin -- citando en cada
bloque la linea de origen en grabador_directo.py. Las unicas piezas
especificas del sitio son las llamadas que en el original van a
mouflon.*; aca van a chaturbate.* (ver nucleo\\scripts\\chaturbate.py).

Diferencias deliberadas con grabador_directo.py (ver
specs\\18-segundo-sitio-chaturbate.md, Scope/Decisiones):

  - **Una sola sesion por modelo**, no un dict por variante: chaturbate no
    tiene equivalente de @calidad=/@codec=/@variantes= ("chaturbate graba
    lo que haya, sin seleccion de calidad" -- fuera de alcance a proposito).
    Siempre se abre la rendicion de mayor bandwidth.
  - **Sin tunel de VPN**: @vpn= no aplica (chaturbate no pasa por el proxy
    de grabacion, fuera de alcance).
  - **Sin "privado" como estado propio**: solo se mide si room_status ==
    "public" (lo unico confirmado en vivo, ver chaturbate.ESTADO_PUBLICO);
    cualquier otro valor se trata como "no hay nada que grabar ahora",
    igual de congelado que el caso privado/p2p de stripchat.
  - **El master es efimero**: el JWT de chatvideocontext vence: a
    diferencia de mouflon (id estable + pkey fijo), aca hay que
    re-derivar master+chunklist desde chatvideocontext cada vez que la
    chunklist actual empieza a fallar, no solo cada CHEQUEO_ESTADO.
  - **id_modelo es un token alfanumerico** (uid de 7 caracteres), no un
    numero -- cache propia en estado\\ids_chaturbate.json (nunca
    estado\\ids.json: ese es de stripchat, valores int).
  - **Video y audio son DOS streams HLS separados** (hallazgo posterior a
    la primera version de este archivo, verificado en vivo: el init de
    video no trae ninguna pista de audio -- ver chaturbate.py, seccion
    "#EXT-X-MEDIA:TYPE=AUDIO"). A diferencia de MOUFLON, donde un solo
    init+segmentos ya trae las dos pistas muxadas, aca cada modelo graba
    DOS "pistas" (`_Pista`, mas abajo) con su propio init/segmentos/
    carpeta -- la de video en la carpeta de sesion de siempre (compatible
    con vivo.py, que sigue viendo solo video ahi) y la de audio en una
    carpeta hermana con "_audio" al final del nombre. Cada una se une por
    separado con "detectar_m3u8.py --unir" (dos sesiones validas de una
    sola pista, ninguna sorpresa para el nucleo) y recien despues se
    mezclan con un ffmpeg propio -- ver _unir_doble()/_mezclar_audio().

Arrancado por nimbo\\lanzador_chaturbate.py (igual que
nimbo\\lanzador_directo.py arranca esto... digo, arranca
grabador_directo.py), que reenvia el logger "chaturbate" a
estado\\eventos_chaturbate.jsonl. Tambien corre solo para diagnostico:

    python grabador_chaturbate.py --una-vez <modelo> --minutos 5 [--salida DIR]

Parada por stdin ("parar\\n"), nunca por kill -- mismo contrato que los
otros cuatro procesos (ver CLAUDE.md).
"""

import argparse
import concurrent.futures as cf
import contextlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time

# -- rutas (mismo patron que grabador_directo.py:63-85) --------------------

AQUI = os.path.dirname(os.path.abspath(__file__))
SUBCARPETAS_SCRIPT = {"scripts", "script", "bin", "src"}


def _raiz_proyecto() -> str:
    if os.path.basename(AQUI).lower() in SUBCARPETAS_SCRIPT:
        return os.path.dirname(AQUI)
    return AQUI


def _al_lado(nombre: str) -> str:
    aqui = os.path.join(AQUI, nombre)
    if os.path.exists(aqui):
        return aqui
    arriba = os.path.join(BASE, nombre)
    return arriba if os.path.exists(arriba) else aqui


if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import chaturbate  # noqa: E402
# Solo para partir_fragmentos() (protocolo-agnostico, ver su docstring):
# importarlo no construye ni arranca nada, igual que lo usan mp4.py/vivo.py.
import mouflon  # noqa: E402
import cerrojo_union  # noqa: E402  -- turno unico de union (SPEC 19)
import progreso_union  # noqa: E402  -- progreso de la union (SPEC 20)

BASE = os.environ.get("M3U8_BASE") or _raiz_proyecto()
STREAMS_DIR = os.environ.get("M3U8_STREAMS") or os.path.join(BASE, "streams")
SELECCION = os.environ.get("M3U8_GRABAR") or _al_lado("grabar.txt")
REGISTRO = os.path.join(BASE, "chaturbate.log")

# detectar_m3u8.py, invocado como SUBPROCESO para la union (nunca
# importado -- ver grabador_directo.py:98-101 / CLAUDE.md "Los dos puentes").
PYTHON_EXE = sys.executable
DETECTAR_PY = os.path.join(AQUI, "detectar_m3u8.py")
_SIN_VENTANA = ({"creationflags": subprocess.CREATE_NO_WINDOW}
                if hasattr(subprocess, "CREATE_NO_WINDOW") else {})
# Mismo valor y mismo motivo que grabador_directo.py:104-110 (SPEC 13).
ESPERA_UNION = 3600
# Para _mezclar_audio(): mismo patron de busqueda que detectar_m3u8.py:164
# (M3U8_FFMPEG / _al_lado). No se le pide nada a detectar_m3u8.py para
# esto -- es un ffmpeg propio, aparte del que corre --unir.
FFMPEG = os.environ.get("M3U8_FFMPEG") or _al_lado("ffmpeg.exe")
ESPERA_MEZCLA = 600

DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_raiz_proyecto()), "estado")
ARCHIVO_ESTADO = os.path.join(DIR_ESTADO, "chaturbate.json")
# Cache separada de la de stripchat (estado\ids.json, valores int): el id
# de chaturbate es un token alfanumerico, no un numero.
RUTA_IDS_CACHE = os.path.join(DIR_ESTADO, "ids_chaturbate.json")

MARCADOR = "unido.json"              # detectar_m3u8.py:194
CARPETA_SEGS = "segmentos"           # detectar_m3u8.py:195
CARPETA_PLAYLISTS = "playlists"      # detectar_m3u8.py:196

RE_NOMBRE = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")
MAX_REGISTRO = 20 * 1024 * 1024

# Igual que grabador_directo.py:153-162 (ESTADOS_PUBLICOS/ESPERA_NO_PUBLICO/
# ESPERA_CIERRE_NO_PUBLICO/ESPERA_STATUS_MALO), adaptado a un solo estado
# "vivo-y-grabable" (chaturbate.ESTADO_PUBLICO) en vez del conjunto de
# stripchat.
ESPERA_NO_PUBLICO = 30.0
ESPERA_CIERRE_NO_PUBLICO = 120.0
ESPERA_STATUS_MALO = 5.0

log = logging.getLogger("chaturbate")


def configurar_log() -> None:
    # Puerto literal de grabador_directo.py:167-183.
    if log.handlers:
        return
    consola = logging.StreamHandler(sys.stderr)
    consola.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.addHandler(consola)
    try:
        if os.path.exists(REGISTRO) and os.path.getsize(REGISTRO) > MAX_REGISTRO:
            os.replace(REGISTRO, REGISTRO + ".1")
        archivo = logging.FileHandler(REGISTRO, encoding="utf-8")
        archivo.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(archivo)
    except OSError:
        pass
    log.setLevel(logging.INFO)
    log.propagate = False


# Solo para nombres de CARPETA (_carpeta_stream): el tope de 60 se queda
# como esta. El de 120 de detectar_m3u8.py:836 (TOPE_NOMBRE_MP4) es para
# el .mp4 final, cuya etiqueta completa lleva "_audio" al final y no
# pasa por aca.
def _limpiar(nombre: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", nombre)[:60] or "x"


def _escribir_atomico(ruta: str, texto: str) -> None:
    # Puerto literal de grabador_directo.py:190-208 (mismo reintento por el
    # mismo WinError 5 transitorio contra servidor.py:_leer_json).
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    for intento in range(5):
        try:
            os.replace(tmp, ruta)
            return
        except PermissionError:
            if intento == 4:
                raise
            time.sleep(0.05 * (intento + 1))


_lock_ids_cache = threading.Lock()


def _leer_ids_cache() -> dict:
    try:
        with open(RUTA_IDS_CACHE, encoding="utf-8") as f:
            datos = json.load(f)
        return {str(k).lower(): str(v) for k, v in datos.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _guardar_id_cache(nombre_lower: str, id_modelo: str) -> None:
    with _lock_ids_cache:
        datos = _leer_ids_cache()
        if datos.get(nombre_lower) == id_modelo:
            return
        datos[nombre_lower] = id_modelo
        try:
            _escribir_atomico(RUTA_IDS_CACHE, json.dumps(datos, ensure_ascii=False))
        except OSError as e:
            log.warning("[IDS] no se pudo guardar la cache: %s", e)


def _escribir_info_sesion(sesion, modelo: str, id_modelo, motivo: str) -> None:
    # Puerto de grabador_directo.py:265-287.
    try:
        os.makedirs(sesion.carpeta, exist_ok=True)
        with open(os.path.join(sesion.carpeta, "info.json"), "w",
                  encoding="utf-8") as f:
            json.dump({
                "id": id_modelo or "",
                "modelo": modelo,
                "variante": sesion.variante,
                "sesion": sesion.sesion,
                "segmentos": sesion.segmentos,
                "bytes": sesion.bytes,
                "init": sesion.init_nombre is not None,
                "motivo_cierre": motivo,
                "modo": "chaturbate",
                "inicio": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(sesion.inicio)),
                "fin": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.warning("[INFO] %s", e)


_LINEAS_UNION = ("[LISTO]", "[UNIR]", "[SALTO]", "[ERROR]", "[DISCO]")


def _resumen_union(proc, tope: int = 3) -> str:
    # Puerto literal de grabador_directo.py:293-306.
    texto = ((getattr(proc, "stderr", "") or "")
             + (getattr(proc, "stdout", "") or ""))
    utiles = [l.strip() for l in texto.splitlines()
              if any(m in l for m in _LINEAS_UNION)]
    return " | ".join(utiles[-tope:]) if utiles else "(el hijo no dijo nada)"


def _unir_en_fondo(escritor: "Escritor", sesion) -> str | None:
    # Puerto de grabador_directo.py:309-376 (mismas tres verificaciones de
    # exito: codigo de retorno, unido.json presente, y su mtime distinto
    # al de antes de correr el hijo). A diferencia del original, devuelve
    # la ruta del .mp4 final (leida del propio marcador -- "salida" en
    # unido.json, ver detectar_m3u8._marcar_unida) para que _unir_doble()
    # pueda encontrar los dos archivos (video y audio) y mezclarlos; None
    # si no se pudo unir.
    if not escritor.barrera().wait(180):
        log.warning("[UNIR] %s: escrituras pendientes, sigo igual", sesion.ref)
    filtro = sesion.ref.lower()
    cmd = [PYTHON_EXE, DETECTAR_PY, "--unir", filtro]
    marcador = os.path.join(sesion.carpeta, MARCADOR)
    try:
        antes = os.path.getmtime(marcador)
    except OSError:
        antes = None
    # SPEC 20: el hijo publica el progreso en estado\union.json desde
    # unir_sesion(), y sin esto no tendria forma de saber que la sesion es
    # de chaturbate (la pondria como "stripchat", el default). Calco de
    # como SPEC 19 le pasa NIMBO_UNION_TURNO por el mismo env.
    entorno = dict(os.environ)
    entorno[progreso_union.NOMBRE_ENTORNO_SITIO] = "chaturbate"
    try:
        proc = subprocess.run(cmd, cwd=AQUI, env=entorno,
                               capture_output=True, text=True,
                               timeout=ESPERA_UNION,
                               stdin=subprocess.DEVNULL, **_SIN_VENTANA)
        if proc.returncode != 0:
            log.error("[UNIR] %s: --unir fallo (codigo %s): %s", sesion.ref,
                      proc.returncode, (proc.stderr or proc.stdout or "")[-400:])
            return None
        if not os.path.isfile(marcador):
            log.error(
                "[UNIR] %s: --unir termino sin error pero no generó %s -- "
                "revisa M3U8_BASE (tiene que ser una ruta ABSOLUTA) -- "
                "salida: %s", sesion.ref, MARCADOR, _resumen_union(proc))
            return None
        try:
            ahora = os.path.getmtime(marcador)
        except OSError:
            ahora = None
        if antes is not None and ahora == antes:
            log.error(
                "[UNIR] %s: NO se unio -- ya tenia un %s de antes y "
                "--unir la salteo. Rehacela a mano: "
                "python %s --unir --forzar \"%s\" -- salida: %s",
                sesion.ref, MARCADOR, os.path.basename(DETECTAR_PY),
                sesion.ref, _resumen_union(proc))
            return None
        log.info("[UNIR] %s: listo | %s", sesion.ref, _resumen_union(proc))
        try:
            with open(marcador, encoding="utf-8") as f:
                return json.load(f).get("salida")
        except (OSError, ValueError) as e:
            log.error("[UNIR] %s: unido.json ilegible: %s", sesion.ref, e)
            return None
    except subprocess.TimeoutExpired:
        log.error("[UNIR] %s: --unir no termino en ESPERA_UNION (%d s); la "
                  "sesion queda sin %s y recuperar_pendientes() la puede "
                  "retomar", sesion.ref, ESPERA_UNION, MARCADOR)
        return None
    except OSError as e:
        log.error("[UNIR] %s: no se pudo lanzar --unir: %s", sesion.ref, e)
        return None


def _vigilar_mezcla(informe, tmp: str, fin: "threading.Event") -> None:
    """Late el progreso de la mezcla mirando crecer el .mp4 que escribe
    ffmpeg (SPEC 20).

    Un hilo que hace getsize cada INTERVALO_UNION es lo contrario de lo que
    se decidio para el remux (ahi el informe se cuelga de _Freno.paso, que
    ya corria), pero aca no hay ningun bucle del que colgarse:
    _mezclar_audio() es un subprocess.run que bloquea hasta el final. Sin
    latido el lector descartaria la fase por FRESCURA_UNION a los 15s y el
    banner desapareceria en medio de la mezcla. Un getsize no lee el
    archivo, solo su entrada de directorio: no le compite el disco a nadie.
    """
    while not fin.wait(progreso_union.INTERVALO_UNION):
        try:
            informe.avance(os.path.getsize(tmp))
        except OSError:
            pass          # todavia no existe, o ya se reemplazo: sin ruido


def _mezclar_audio(ruta_video: str, ruta_audio: str, informe=None) -> bool:
    """Mezcla el .mp4 de audio (una pista sola, ya unido por separado)
    adentro del .mp4 de video -- dos sesiones completas e independientes
    para detectar_m3u8.py (que nunca sabe que hay un audio aparte), un
    ffmpeg propio al final. -c copy: no se reencodea nada, solo se
    multiplexan los dos streams ya terminados. -> True si quedo mezclado
    (ruta_video termina con audio adentro); False si algo fallo (ruta_video
    queda como estaba, mudo -- mejor eso que nada)."""
    if not os.path.isfile(FFMPEG):
        log.error("[MUX] %s: no esta %s, el video queda mudo",
                  ruta_video, FFMPEG)
        return False
    tmp = ruta_video + ".conaudio.mp4"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-i", ruta_video, "-i", ruta_audio,
           "-c", "copy", "-map", "0:v:0", "-map", "1:a:0",
           "-movflags", "+faststart", tmp]
    fin_vigilante = threading.Event()
    vigilante = None
    if informe is not None:
        total = 0
        for p in (ruta_video, ruta_audio):
            with contextlib.suppress(OSError):
                total += os.path.getsize(p)
        informe.fase("mezcla", total)
        vigilante = threading.Thread(
            target=_vigilar_mezcla, args=(informe, tmp, fin_vigilante),
            daemon=True, name="chaturbate-mezcla-progreso")
        vigilante.start()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=ESPERA_MEZCLA,
                               stdin=subprocess.DEVNULL, **_SIN_VENTANA)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("[MUX] %s: no se pudo mezclar el audio: %s", ruta_video, e)
        with contextlib.suppress(OSError):
            os.remove(tmp)
        return False
    finally:
        fin_vigilante.set()
        if vigilante is not None:
            vigilante.join(timeout=progreso_union.INTERVALO_UNION * 2)
    if proc.returncode != 0 or not os.path.isfile(tmp):
        log.error("[MUX] %s: ffmpeg no pudo mezclar el audio: %s",
                  ruta_video, (proc.stderr or "")[-500:])
        with contextlib.suppress(OSError):
            os.remove(tmp)
        return False
    try:
        os.replace(tmp, ruta_video)
    except OSError as e:
        log.error("[MUX] %s: no se pudo reemplazar por la version con "
                  "audio: %s", ruta_video, e)
        return False
    log.info("[MUX] %s: audio mezclado", ruta_video)
    return True


def _unir_doble(escritor: "Escritor", pista_video, pista_audio, parar=None) -> None:
    """Une video y audio como dos sesiones separadas (cada una, para
    detectar_m3u8.py --unir, es una sesion cualquiera de una sola pista --
    nunca se toca ese codigo) y despues las mezcla. Si no hay pista de
    audio, o su union falla, el video queda solo -- mudo es mejor que
    nada.

    El turno de union (SPEC 19) se toma ACA, una sola vez alrededor de las
    dos uniones y del mux: _unir_en_fondo() no lo toca, para que el
    subproceso --unir que lanza (y que lo hereda por _HEREDADO) no se trabe
    con este mismo proceso."""
    with cerrojo_union.turno(pista_video.ref, parar=parar, raiz=STREAMS_DIR,
                              log=log):
        salida_video = _unir_en_fondo(escritor, pista_video)
        if salida_video is None:
            return
        if pista_audio is None or not pista_audio.segmentos:
            return
        salida_audio = _unir_en_fondo(escritor, pista_audio)
        if salida_audio is None:
            log.warning("[%s] no se pudo unir el audio -- el video queda mudo",
                       pista_video.ref)
            return
        # SPEC 20: las dos uniones de arriba publicaron su progreso solas
        # (lo hace unir_sesion() adentro del subproceso --unir y lo cierra
        # al terminar), pero la mezcla no pasa por detectar_m3u8.py: es la
        # UNICA fase que hay que publicar desde aca. Informe propio, con el
        # mismo .mp4 de destino que ya tenia la fase "remux" del video --
        # asi la tarjeta marcada en la grilla no cambia de una fase a otra.
        informe = progreso_union.Informe(
            DIR_ESTADO, pista_video.ref, sitio="chaturbate",
            archivo=os.path.basename(salida_video))
        try:
            mezclado = _mezclar_audio(salida_video, salida_audio,
                                       informe=informe)
        finally:
            informe.cerrar()
        if mezclado:
            with contextlib.suppress(OSError):
                os.remove(salida_audio)


def _descargar(url: str) -> bytes:
    status, _, datos = chaturbate.pedir(url, timeout=15.0)
    if status != 200:
        raise RuntimeError("status %s" % status)
    return datos


# --------------------------------------------------------------------------
# Escritor en segundo plano -- puerto literal de grabador_directo.py:391-437
# (Escritor), que a su vez porta detectar_m3u8.Escritor.
# --------------------------------------------------------------------------

class Escritor(threading.Thread):
    def __init__(self, maxsize: int = 4000):
        super().__init__(daemon=True, name="chaturbate-escritor")
        self.cola: queue.Queue = queue.Queue(maxsize=maxsize)
        self.descartados = 0
        self.errores = 0

    def encolar(self, destino: str, datos: bytes) -> bool:
        try:
            self.cola.put_nowait(("w", destino, datos))
            return True
        except queue.Full:
            self.descartados += 1
            log.error("[DISCO] cola llena, descartado (%d)", self.descartados)
            return False

    def barrera(self) -> threading.Event:
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
# grabar.txt: lo que este modulo entiende de el (lectura propia, portada --
# nunca se importa detectar_m3u8.py ni nimbo\lista.py). Subconjunto de
# grabador_directo._ConfigGeneral (ver grabador_directo.py:462-511): sin
# calidad/codec/fallback/una_variante/borrosas/privado/vpn/navegador/todo --
# "chaturbate graba lo que haya, sin seleccion de calidad" (fuera de
# alcance, ver Decisiones del spec). La marca "@chaturbate=n1,n2" es la que
# de verdad decide quien se graba -- calco de "@vpn=" (nimbo\lista.py:
# marcar_sitio).
# --------------------------------------------------------------------------

class _ConfigChaturbate:
    __slots__ = ("entradas", "chaturbate", "inactividad", "unir")

    def __init__(self):
        self.entradas = set()
        self.chaturbate = frozenset()
        self.inactividad = 120.0
        self.unir = True


class LectorGrabar:
    """Relee grabar.txt solo cuando cambia (mtime) -- mismo patron que
    grabador_directo.LectorGrabar (grabador_directo.py:514-537)."""

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._firma = None
        self._cfg = _ConfigChaturbate()

    def actual(self) -> _ConfigChaturbate:
        try:
            st = os.stat(self.ruta)
            firma = (st.st_mtime, st.st_size)
        except OSError:
            return self._cfg
        if firma == self._firma or st.st_size == 0:
            return self._cfg
        cfg = self._parsear()
        if cfg is None:
            return self._cfg
        self._firma = firma
        self._cfg = cfg
        return self._cfg

    def _parsear(self):
        cfg = _ConfigChaturbate()
        try:
            with open(self.ruta, "r", encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except OSError:
            return None

        for linea in lineas:
            linea = linea.split("#", 1)[0].strip()
            if not linea:
                continue
            if linea.startswith("@"):
                clave, _, valor = linea[1:].partition("=")
                clave, valor = clave.strip().lower(), valor.strip().lower()
                if clave == "chaturbate":
                    cfg.chaturbate |= frozenset(
                        v.strip() for v in valor.split(",") if v.strip())
                elif clave in {"inactividad", "timeout"}:
                    try:
                        cfg.inactividad = max(0.0, float(valor))
                    except ValueError:
                        pass
                elif clave == "unir":
                    cfg.unir = valor not in {"0", "no", "false", "off"}
                # cualquier otra directiva (@calidad=, @vpn=, @directo=,
                # etc.) es de stripchat o del detector -- se ignora en
                # silencio, igual que grabador_directo.py con las suyas.
                continue
            if linea in {"*", "all", "todo", "todos"}:
                continue    # "todo" no aplica a @chaturbate=: hay que marcar a mano
            cfg.entradas.add(linea.lower())
        return cfg


# --------------------------------------------------------------------------
# Una pista de grabacion -- UN stream HLS de UNA sola pista (video o
# audio: son streams separados en chaturbate, ver docstring del modulo).
# Cada modelo abre dos de estas por sesion: self.pista_video y
# self.pista_audio (ver _ModeloChaturbate).
# --------------------------------------------------------------------------

class _Pista:
    __slots__ = ("variante", "carpeta", "sesion", "init_nombre", "segmentos",
                 "bytes", "inicio", "vistos", "ultimo_segmento")

    def __init__(self, variante, carpeta, sesion):
        self.variante = variante
        self.carpeta = carpeta
        self.sesion = sesion
        self.init_nombre = None
        self.segmentos = 0
        self.bytes = 0
        self.inicio = time.time()
        self.vistos = set()
        # 0.0 a proposito: ver grabador_directo.py:620-624.
        self.ultimo_segmento = 0.0

    @property
    def etiqueta(self) -> str:
        return os.path.basename(self.carpeta)

    @property
    def ref(self) -> str:
        """"<modelo>_<id>/<variante>_<timestamp>" -- filtro EXACTO para
        "detectar_m3u8.py --unir" (ver grabador_directo.py:630-639 para el
        porque de que tenga que ser exacto, no un substring)."""
        return "%s/%s" % (os.path.basename(os.path.dirname(self.carpeta)),
                          os.path.basename(self.carpeta))


# nombre de un segmento ya bajado del CDN de chaturbate (ver
# pruebas\sonda_chaturbate.py:RE_NOMBRE_SEG): el streamid largo es lo unico
# de ese nombre que vale la pena conservar en el nombre que escribimos a
# disco (como "token" de vivo.RE_SEGMENTO) -- es estable durante toda la
# sesion de encoding, a diferencia del msn/rendicion que ya van aparte.
RE_STREAMID = re.compile(r"_(\d{6,})_llhls\.m4s$")


def _token_de(nombre_origen: str) -> str:
    m = RE_STREAMID.search(nombre_origen)
    return m.group(1) if m else "0"


# --------------------------------------------------------------------------
# Un hilo por modelo
# --------------------------------------------------------------------------

class _ModeloChaturbate(threading.Thread):
    CHEQUEO_ESTADO = 60.0
    FALLOS_PARA_DEGRADAR = 3

    def __init__(self, nombre: str, grabador: "Grabador"):
        super().__init__(daemon=True, name="chaturbate-" + nombre)
        self.nombre = nombre
        self.grabador = grabador
        self._parar = threading.Event()

        self.id_modelo = grabador.ids_cache.get(nombre.lower())
        self.estado_cam = "?"
        self.proximo_chequeo = 0.0
        self.target_duration = 2.0
        self.ultimo_visto = time.time()
        self.degradado = False

        self.pista_video = None       # _Pista | None
        self.pista_audio = None       # _Pista | None -- None si la rendicion
                                       # no declaro grupo de audio
        self.uri_variante = None      # chunklist de video de la rendicion abierta
        self.uri_audio = None         # chunklist de audio asociada (o None)
        self.nombre_variante = None   # "<calidad>_<codec>", ver chaturbate.Rendicion.nombre
        self.fallos = 0
        self._forzado = False         # ya se forzo un master nuevo en esta racha
        self._proximo_intento = 0.0   # backoff propio tras un status malo
        self._futuros = {}            # future -> (pista, es_init, url, msn)
        self._lock = threading.Lock()
        self.esperando_publico = 0.0  # ver grabador_directo.py:674-678

    # -- ciclo de vida -------------------------------------------------

    def parar(self) -> None:
        self._parar.set()

    def run(self) -> None:
        log.info("[%s] arranca", self.nombre)
        try:
            while not self._parar.is_set():
                try:
                    self._paso()
                except Exception as e:
                    log.error("[%s] %s", self.nombre, e)
                self._parar.wait(self._intervalo())
        finally:
            self._cerrar_sesion("hilo terminado")
        log.info("[%s] termina", self.nombre)

    def _intervalo(self) -> float:
        # Puerto de grabador_directo.py:698-701.
        if self.esperando_publico:
            return ESPERA_NO_PUBLICO
        return max(0.5, self.target_duration / 2.0) if self.pista_video else 5.0

    def _carpeta_stream(self) -> str:
        sufijo = self.id_modelo or "sinid"
        return os.path.join(STREAMS_DIR, _limpiar(self.nombre + "_" + sufijo))

    # -- paso principal --------------------------------------------------

    def _paso(self) -> None:
        # Estructura calcada de grabador_directo.py:722-778 (_paso), con la
        # rama de "es_privado"/multi-variante removida: chaturbate solo
        # distingue "public" de "todo lo demas" (ver docstring del modulo).
        if self.degradado:
            if self.pista_video:
                self._cerrar_sesion("degradado")
            return

        cfg = self.grabador.seleccion.actual()
        ahora = time.time()
        if ahora >= self.proximo_chequeo:
            self._chequear_estado()
            self.proximo_chequeo = ahora + (
                ESPERA_NO_PUBLICO if self.esperando_publico else self.CHEQUEO_ESTADO)

        # @inactividad cierra y une sesiones sin bytes recientes, tanto si
        # el motivo es que la chunklist dejo de responder como si es que
        # la sala dejo de estar en publico -- antes de decidir la rama de
        # abajo, igual que grabador_directo.py:752.
        self._cerrar_por_inactividad(cfg)

        if chaturbate.estado_incierto(self.estado_cam):
            # la consulta fallo: no dice nada de si sigue en publico. No se
            # decide nada, se sigue como se venia (grabador_directo.py:
            # 754-761).
            if self.pista_video:
                self._refrescar()
            return

        if self.estado_cam != chaturbate.ESTADO_PUBLICO:
            if not self.esperando_publico:
                self.esperando_publico = ahora
                log.info("[%s] no esta en publico (%s) -- congelo y "
                         "reintento cada %ds", self.nombre, self.estado_cam,
                         int(ESPERA_NO_PUBLICO))
            return

        if self.esperando_publico:
            log.info("[%s] volvio a estar en publico", self.nombre)
            self.esperando_publico = 0.0

        if self.pista_video is None:
            self._abrir_sesion()
        if self.pista_video is not None:
            self._refrescar()

    def _cerrar_por_inactividad(self, cfg: _ConfigChaturbate) -> None:
        # Puerto de grabador_directo.py:780-820, con la referencia de
        # inactividad medida siempre sobre la pista de VIDEO -- las dos
        # pistas se abren y cierran juntas, asi que basta con una.
        if not self.pista_video:
            return
        limite = cfg.inactividad
        if not limite and self.esperando_publico:
            limite = ESPERA_CIERRE_NO_PUBLICO
        if not limite:
            return
        ahora = time.time()
        pista = self.pista_video
        referencia = pista.ultimo_segmento or (
            pista.inicio if self.esperando_publico else 0.0)
        if referencia and ahora - referencia > limite:
            self._cerrar_sesion("sin segmentos hace %ds" % int(limite))

    # -- estado (sin abrir pagina) ---------------------------------------

    def _chequear_estado(self) -> None:
        info = chaturbate.consultar_estado(self.nombre)
        nuevo = info["estado"]
        if nuevo != self.estado_cam:
            log.info("[%s] estado: %s -> %s", self.nombre, self.estado_cam, nuevo)
        self.estado_cam = nuevo
        if info["id"] and info["id"] != self.id_modelo:
            self.id_modelo = info["id"]
            _guardar_id_cache(self.nombre.lower(), self.id_modelo)
            log.info("[%s] id: %s", self.nombre, self.id_modelo)
        if nuevo == chaturbate.ESTADO_PUBLICO:
            self.ultimo_visto = time.time()
            self._hls_source = info["hls_source"]

    # -- master -> chunklist: re-derivar la URI de la variante ------------

    def _reconciliar_uri(self) -> bool:
        """Chatvideocontext -> master -> mejor rendicion -> chunklist URI.
        A diferencia de mouflon (id estable, master siempre pedible con la
        misma URL), el master de chaturbate lleva un JWT de corta vida: no
        alcanza con guardar un id_modelo, hay que volver a pasar por
        chatvideocontext cada vez que hace falta un master fresco (ver
        docstring del modulo). -> True si consiguio una URI nueva."""
        info = chaturbate.consultar_estado(self.nombre)
        if info["estado"] != chaturbate.ESTADO_PUBLICO or not info["hls_source"]:
            return False
        try:
            texto, url_final = chaturbate.pedir_master(info["hls_source"])
        except chaturbate.ErrorChaturbate as e:
            log.warning("[%s] master: %s", self.nombre, e)
            return False
        rendiciones = chaturbate.leer_master(texto, url_final)
        mejor = chaturbate.mejor_rendicion(rendiciones)
        if not mejor:
            return False
        self.uri_variante = mejor.uri
        self.nombre_variante = mejor.nombre
        if mejor.audio_group:
            mapa_audio = chaturbate.audio_uris(texto, url_final)
            self.uri_audio = mapa_audio.get(mejor.audio_group)
            if not self.uri_audio:
                log.warning(
                    "[%s] la rendicion declara audio_group=%s pero el "
                    "master no trae esa URI -- se graba sin audio",
                    self.nombre, mejor.audio_group)
        else:
            self.uri_audio = None
        return True

    def _abrir_sesion(self) -> None:
        if not self._reconciliar_uri():
            return
        sello = time.strftime("%Y%m%d-%H%M%S")
        variante = self.nombre_variante or "auto_na"
        carpeta = os.path.join(self._carpeta_stream(), variante + "_" + sello)
        self.pista_video = _Pista(variante, carpeta, sello)
        self.pista_audio = _Pista(variante, carpeta + "_audio", sello) if self.uri_audio else None
        self.fallos = 0
        log.info("[%s] sesion nueva: %s (%s)%s", self.nombre, sello, variante,
                 " con audio" if self.pista_audio else " SIN audio")

    # -- chunklist: bajar init + segmentos ---------------------------------

    def _refrescar(self) -> None:
        # Puerto de grabador_directo.py:911-1019 (_refrescar_todas +
        # _refrescar_variante), aplanado a una sola variante -- mas el
        # agregado de SPEC 18 (hallazgo tardio): la chunklist de audio se
        # pide y se procesa igual que la de video, vía _procesar_chunklist,
        # pero nunca degrada ni fuerza un master nuevo por si misma (es
        # mejor-esfuerzo: mudo es mejor que sin grabar, ver docstring del
        # modulo).
        if not self.uri_variante or self.pista_video is None:
            self._recolectar()
            return
        ahora = time.time()
        if ahora < self._proximo_intento:
            self._recolectar()
            return
        try:
            init_url, target, segmentos = chaturbate.pedir_variante(self.uri_variante)
        except chaturbate.ErrorRed as e:
            # Un status malo en la chunklist (403/404) es casi siempre el
            # token de sesion venciendo -- se arregla con un master nuevo,
            # no reintentando la misma URI (grabador_directo.py:936-974).
            if self._forzado:
                log.warning(
                    "[%s] chunklist dio %s otra vez tras pedir un master "
                    "nuevo -- reintento cada %ds", self.nombre, e,
                    int(ESPERA_STATUS_MALO))
                self._proximo_intento = ahora + ESPERA_STATUS_MALO
                self.proximo_chequeo = 0.0
            else:
                log.warning("[%s] chunklist: %s -- pido un master nuevo",
                           self.nombre, e)
                self._forzado = True
                if not self._reconciliar_uri():
                    self.proximo_chequeo = 0.0
            self._recolectar()
            return
        except chaturbate.ErrorChaturbate as e:
            self._registrar_fallo(str(e))
            self._recolectar()
            return
        self._forzado = False
        self._proximo_intento = 0.0
        self.fallos = 0
        if target:
            self.target_duration = target

        self._procesar_chunklist(self.pista_video, init_url, segmentos)
        if self.pista_video is None:
            # _procesar_chunklist cerro toda la sesion (init de video
            # cambio -- reconexion del encoder).
            self._recolectar()
            return

        if self.uri_audio and self.pista_audio is not None:
            try:
                init_url_a, _, segmentos_a = chaturbate.pedir_variante(self.uri_audio)
            except chaturbate.ErrorChaturbate as e:
                log.warning("[%s] chunklist de audio: %s -- este tick "
                           "queda sin audio, el video sigue", self.nombre, e)
            else:
                self._procesar_chunklist(self.pista_audio, init_url_a, segmentos_a)

        self._recolectar()

    def _procesar_chunklist(self, pista: "_Pista", init_url, segmentos) -> None:
        """Aplica init+segmentos nuevos de una chunklist a UNA pista --
        comun a video y audio (llamado dos veces por tick desde
        _refrescar()). Si el init cambio (reconexion del encoder) cierra
        TODA la sesion, no solo esta pista: un init de video nuevo con uno
        de audio viejo (o viceversa) dejaria las dos pistas de la sesion
        siguiente desincronizadas entre si."""
        if init_url:
            init_nombre = chaturbate.nombre_archivo(init_url)
            if pista.init_nombre and init_nombre != pista.init_nombre:
                log.info("[%s] init nuevo en %s (%s): reconexion del "
                         "encoder -- cierro la sesion y abro otra",
                         self.nombre, os.path.basename(pista.carpeta),
                         init_nombre)
                self._cerrar_sesion("reconexion de encoder")
                return
            if pista.init_nombre is None:
                pista.init_nombre = init_nombre
                self._someter_descarga(pista, init_url, es_init=True, msn=None)

        for msn, url_seg in segmentos:
            nombre_origen = chaturbate.nombre_archivo(url_seg)
            if nombre_origen in pista.vistos:
                continue
            pista.vistos.add(nombre_origen)
            self._someter_descarga(pista, url_seg, es_init=False, msn=msn)

    def _registrar_fallo(self, motivo: str) -> None:
        # Puerto literal de grabador_directo.py:1021-1032.
        self.fallos += 1
        log.warning("[%s] %s (fallo %d/%d)", self.nombre, motivo,
                    self.fallos, self.FALLOS_PARA_DEGRADAR)
        if self.fallos >= self.FALLOS_PARA_DEGRADAR:
            self.degradado = True
            log.error(
                "[%s] DEGRADADO tras %d fallos seguidos -- el camino de "
                "chaturbate no sirve para esta modelo por ahora.",
                self.nombre, self.fallos)

    # -- descargas: pool compartido, nunca bloquea el refresco ------------

    def _someter_descarga(self, pista: "_Pista", url: str, es_init: bool, msn) -> None:
        fut = self.grabador.pool.submit(_descargar, url)
        with self._lock:
            self._futuros[fut] = (pista, es_init, url, msn)

    def _recolectar(self) -> None:
        # Puerto de grabador_directo.py:1041-1057, con la pista (video o
        # audio) viajando en la tupla en vez de asumir "la unica sesion".
        with self._lock:
            listos = [f for f in self._futuros if f.done()]
        for fut in listos:
            with self._lock:
                pista, es_init, url, msn = self._futuros.pop(fut)
            try:
                datos = fut.result()
            except Exception as e:
                nombre = chaturbate.nombre_archivo(url)
                log.warning("[%s] fallo bajando %s: %s", self.nombre, nombre, e)
                if not es_init:
                    pista.vistos.discard(nombre)
                continue
            self._guardar_descarga(pista, es_init, url, msn, datos)

    def _guardar_descarga(self, pista: "_Pista", es_init: bool, url: str,
                          msn, datos: bytes) -> None:
        if pista is not self.pista_video and pista is not self.pista_audio:
            return   # la sesion se cerro mientras la descarga estaba en vuelo
        es_audio = pista is self.pista_audio
        nombre_origen = chaturbate.nombre_archivo(url)
        if es_init:
            self.grabador.escritor.encolar(os.path.join(pista.carpeta, nombre_origen), datos)
            log.info("[%s] init %s | %s", self.nombre,
                     "audio" if es_audio else "video", nombre_origen)
            return

        # Un segmento "completo" de chaturbate junta dos EXT-X-PART de LL-HLS
        # (0.8s cada una) en un solo archivo fMP4 -- puede traer mas de un
        # 'moof' pegado, igual que el playlistType=standard de stripchat.
        # mouflon.partir_fragmentos es protocolo-agnostico (solo recorre
        # cajas MP4), asi que se reusa tal cual -- ver su docstring.
        fragmentos = mouflon.partir_fragmentos(datos)
        epoch = int(time.time())
        token = _token_de(nombre_origen)
        # El prefijo "seg" (cualquier cosa no vacia sirve) es lo que le da
        # a vivo.RE_SEGMENTO el "_" que su patron exige ANTES de la
        # secuencia -- sin el, "<msn>_<token>_<epoch>..." no tiene ningun
        # underscore que lo preceda y RE_SEGMENTO.search() no matchea nada
        # (medido en la verificacion de SPEC 18, paso 7: 0/117 segmentos
        # reconocidos hasta agregar este prefijo).
        base = "seg_%d_%s_%d" % (msn or 0, token, epoch)
        nombres = ([base + ".m4s"] if len(fragmentos) == 1
                   else ["%s_part%d.m4s" % (base, i) for i in range(len(fragmentos))])
        for nombre_frag, bytes_frag in zip(nombres, fragmentos):
            destino = os.path.join(pista.carpeta, CARPETA_SEGS, nombre_frag)
            if not self.grabador.escritor.encolar(destino, bytes_frag):
                continue
            pista.segmentos += 1
            pista.bytes += len(bytes_frag)
            pista.ultimo_segmento = time.time()
        if not es_audio and pista.segmentos % 20 == 0:
            log.info("[%s] %d segmentos | %.1f MB | %.1f min", self.nombre,
                     pista.segmentos, pista.bytes / 1048576,
                     (time.time() - pista.inicio) / 60)

    # -- cierre de sesion ---------------------------------------------------

    def _cerrar_sesion(self, motivo: str) -> None:
        # Puerto de grabador_directo.py:1095-1123 (_cerrar_variante +
        # _cerrar_todas, aplanados a una sola sesion), mas el agregado de
        # SPEC 18: la pista de audio (si existe) viaja junto a la de video
        # en la misma entrada de cola_union -- _unir_doble() une las dos
        # por separado y recien despues las mezcla (ver su docstring).
        pista_video = self.pista_video
        pista_audio = self.pista_audio
        self.pista_video = None
        self.pista_audio = None
        self.uri_variante = None
        self.uri_audio = None
        self.nombre_variante = None
        self.fallos = 0
        self._forzado = False
        self._proximo_intento = 0.0
        if pista_video is None:
            return
        if not pista_video.segmentos:
            log.info("[%s] %s sin segmentos, no se une", self.nombre, pista_video.etiqueta)
            return
        _escribir_info_sesion(pista_video, self.nombre, self.id_modelo, motivo)
        cfg = self.grabador.seleccion.actual()
        if not cfg.unir:
            log.info("[%s] @unir=0, queda sin unir: %s", self.nombre, pista_video.carpeta)
            return
        if pista_audio is not None and pista_audio.segmentos:
            _escribir_info_sesion(pista_audio, self.nombre, self.id_modelo, motivo)
        extra_audio = (", %d de audio" % pista_audio.segmentos
                       if pista_audio and pista_audio.segmentos else " (sin audio)")
        log.info("[%s] cierre: %s (%d segmentos de video%s) -- %s | "
                 "encolado para unir", self.nombre, pista_video.etiqueta,
                 pista_video.segmentos, extra_audio, motivo)
        self.grabador.cola_union.put((self.grabador.escritor, pista_video, pista_audio))

    # -- estado para estado\chaturbate.json ---------------------------------

    def fila_estado(self) -> dict:
        # Mismo schema de fila que grabador_directo.ModeloDirecto.fila_estado
        # (grabador_directo.py:1127-1177), con "sitio" agregado (SPEC 18,
        # dedupe por (sitio, modelo) en estado_captura.fusionar) y
        # "variantes"/"sesiones" como lista de 0 o 1 elemento (una sola
        # sesion posible por diseno, ver docstring del modulo).
        with self._lock:
            pista = self.pista_video
            sesiones_info = [{
                "variante": pista.variante, "calidad": "", "codec": "",
                "sesion": pista.sesion, "carpeta": pista.carpeta,
                "init": pista.init_nombre is not None,
                "segmentos": pista.segmentos, "bytes": pista.bytes,
                "inicio": pista.inicio,
            }] if pista else []
        grabando = bool(sesiones_info)
        if grabando:
            estado = "grabando"
        elif self.degradado:
            estado = "degradado"
        elif self.esperando_publico:
            estado = "esperando"
        else:
            estado = "detectado" if not chaturbate.estado_incierto(self.estado_cam) else "inactivo"
        variantes = [pista.variante] if pista else []
        return {
            "id": self.id_modelo or "",
            "modelo": self.nombre,
            "sitio": "chaturbate",
            "mejor": "",
            "elegida": variantes[0] if variantes else "",
            "variantes": variantes,
            "estado": estado,
            "grabando": grabando,
            "privado": False,
            "privado_desde": None,
            "inicio": pista.inicio if pista else None,
            "ultimo_visto": self.ultimo_visto,
            "ultimo_segmento": pista.ultimo_segmento or None if pista else None,
            "segmentos": pista.segmentos if pista else 0,
            "bytes": pista.bytes if pista else 0,
            "estado_cam": self.estado_cam,
            "esperando": bool(self.esperando_publico),
            "sesiones": sesiones_info,
            "degradado": self.degradado,
        }


# --------------------------------------------------------------------------
# El proceso: un Grabador por corrida -- puerto de grabador_directo.Grabador
# (grabador_directo.py:1190-1389).
# --------------------------------------------------------------------------

INTERVALO_LISTA = 2.0
INTERVALO_ESTADO = 2.0
POOL_HILOS = 12     # la mitad que el camino directo (POOL_HILOS=24): un
                    # solo sitio nuevo, sin @vpn=, no deberia competir el
                    # mismo cupo de red saliente que stripchat.


class Grabador:
    def __init__(self):
        self.seleccion = LectorGrabar(SELECCION)
        self.escritor = Escritor()
        self.pool = cf.ThreadPoolExecutor(max_workers=POOL_HILOS,
                                          thread_name_prefix="chaturbate-dl")
        self.ids_cache = _leer_ids_cache()
        self.cola_union: queue.Queue = queue.Queue()
        # SPEC 20, mismo motivo que grabador_directo.py: qsize() no cuenta
        # la union en vuelo, asi que sin este flag "uniendo" valia 0 con una
        # union corriendo.
        self._uniendo_ahora = False
        self._modelos = {}
        self._lock = threading.RLock()
        self._parar = threading.Event()
        # Separado de _parar por el mismo motivo que grabador_directo.py:
        # 1200-1208 (Grabador.__init__): sin esto directo.json/chaturbate.json
        # queda congelado con "grabando": true durante toda la union.
        self._fin = threading.Event()

    def parar(self) -> None:
        self._parar.set()

    def _bucle_union(self) -> None:
        # Puerto de grabador_directo.py:1230-1239, con la entrada de cola
        # llevando las DOS pistas (video + audio, la segunda puede ser
        # None) en vez de una sola sesion -- ver _cerrar_sesion() y
        # _unir_doble().
        while True:
            item = self.cola_union.get()
            if item is None:
                return
            escritor, pista_video, pista_audio = item
            self._uniendo_ahora = True
            try:
                _unir_doble(escritor, pista_video, pista_audio, parar=self._parar)
            except Exception as e:
                log.error("[UNIR] %s: %s", pista_video.ref, e)
            finally:
                self._uniendo_ahora = False

    def correr(self) -> None:
        os.makedirs(STREAMS_DIR, exist_ok=True)
        os.makedirs(DIR_ESTADO, exist_ok=True)
        log.info("=" * 70)
        log.info("[CHATURBATE] base=%s | estado=%s", BASE, DIR_ESTADO)
        log.info("=" * 70)

        self.escritor.start()
        hilo_vigilante = threading.Thread(target=self._bucle_vigilante,
                                          daemon=True, name="chaturbate-vigilante")
        hilo_vigilante.start()
        hilo_estado = threading.Thread(target=self._bucle_estado,
                                       daemon=True, name="chaturbate-estado")
        hilo_estado.start()
        hilo_union = threading.Thread(target=self._bucle_union,
                                      daemon=True, name="chaturbate-union")
        hilo_union.start()

        if sys.stdin is not None:
            hilo_stdin = threading.Thread(target=self._bucle_stdin,
                                          daemon=True, name="chaturbate-stdin")
            hilo_stdin.start()

        self._parar.wait()

        log.info("[CHATURBATE] parando -- cerrando %d modelo(s)", len(self._modelos))
        with self._lock:
            modelos = list(self._modelos.values())
            self._modelos.clear()
        for m in modelos:
            m.parar()
        for m in modelos:
            m.join(60)
        self.cola_union.put(None)
        hilo_union.join(1200)
        self.pool.shutdown(wait=True)
        self.escritor.detener()
        self._fin.set()
        with contextlib.suppress(Exception):
            self._publicar_estado()
        log.info("[CHATURBATE] listo")

    def _bucle_stdin(self) -> None:
        try:
            for linea in sys.stdin:
                if linea.strip().lower() == "parar":
                    break
        except (OSError, ValueError):
            return
        log.info("[CHATURBATE] orden de parada recibida")
        self.parar()

    def _bucle_vigilante(self) -> None:
        while not self._parar.wait(INTERVALO_LISTA):
            try:
                self._revisar_lista()
            except Exception as e:
                log.error("[VIGILANTE] %s", e)

    def _deseados(self, cfg: _ConfigChaturbate) -> set:
        # Solo nombres literales marcados @chaturbate= Y presentes como
        # linea de seleccion -- sin equivalente de @directo=1/@todo=1: la
        # marca explicita es la unica fuente de verdad (ver
        # nimbo\lista.py:marcar_sitio).
        return {e for e in cfg.entradas if e in cfg.chaturbate and RE_NOMBRE.match(e)}

    def _revisar_lista(self) -> None:
        cfg = self.seleccion.actual()
        deseados = self._deseados(cfg)
        with self._lock:
            actuales = set(self._modelos)
            for nombre in deseados - actuales:
                m = _ModeloChaturbate(nombre, self)
                self._modelos[nombre] = m
                m.start()
            for nombre in actuales - deseados:
                m = self._modelos.pop(nombre)
                m.parar()    # se cierra y une sola, en su propio hilo

    def _bucle_estado(self) -> None:
        while not self._fin.wait(INTERVALO_ESTADO):
            try:
                self._publicar_estado()
            except Exception as e:
                log.error("[ESTADO] %s", e)

    def _publicar_estado(self) -> None:
        # Puerto literal de grabador_directo.py:1370-1389.
        ahora = time.time()
        with self._lock:
            modelos = list(self._modelos.values())
        filas = [m.fila_estado() for m in modelos]
        ids = {f["modelo"]: f["id"] for f in filas if f.get("id")}
        datos = {
            "generado": ahora,
            "modo": "chaturbate",
            "degradado": any(f["degradado"] for f in filas),
            "ids": ids,
            "streams": filas,
            "parando": self._parar.is_set(),
            # La en vuelo MAS las encoladas (SPEC 20), igual que el directo.
            "uniendo": self.cola_union.qsize() + (1 if self._uniendo_ahora else 0),
        }
        _escribir_atomico(ARCHIVO_ESTADO, json.dumps(datos, ensure_ascii=False))


# --------------------------------------------------------------------------
# CLI: modo normal (lo arranca lanzador_chaturbate.py) y --una-vez (diagnostico)
# --------------------------------------------------------------------------

def _correr_una_vez(nombre: str, minutos: float, salida_base) -> int:
    # Puerto de grabador_directo.py:1396-1448.
    global BASE, STREAMS_DIR
    if salida_base:
        salida_base = os.path.abspath(salida_base)
        os.environ["M3U8_BASE"] = salida_base
        os.environ.pop("M3U8_STREAMS", None)
        BASE = salida_base
        STREAMS_DIR = os.path.join(BASE, "streams")
    os.makedirs(STREAMS_DIR, exist_ok=True)
    log.info("[UNA-VEZ] %s por %.1f min, base=%s", nombre, minutos, BASE)

    class _LectorFalso:
        def actual(self):
            return _ConfigChaturbate()

    class _GrabadorFalso:
        def __init__(self):
            self.escritor = Escritor()
            self.escritor.start()
            self.pool = cf.ThreadPoolExecutor(max_workers=6)
            self.seleccion = _LectorFalso()
            self.ids_cache = {}
            # A diferencia de Grabador real, aca no hay un hilo consumidor
            # de cola_union corriendo (es un diagnostico de una sola
            # modelo): se drena a mano, sincronico, despues de parar --
            # ver mas abajo. Sin esto, _cerrar_sesion() de _ModeloChaturbate
            # revienta con AttributeError al cerrar su sesion final.
            self.cola_union: "queue.Queue" = queue.Queue()

    g = _GrabadorFalso()
    m = _ModeloChaturbate(nombre, g)
    m.start()
    try:
        time.sleep(minutos * 60)
    finally:
        m.parar()
        m.join(60)
        # Drenar la union ANTES de parar el escritor: _unir_en_fondo()
        # espera a escritor.barrera(), que solo se cumple si el hilo del
        # escritor sigue vivo para procesarla.
        while not g.cola_union.empty():
            escritor, pista_video, pista_audio = g.cola_union.get_nowait()
            _unir_doble(escritor, pista_video, pista_audio)
        g.pool.shutdown(wait=True)
        g.escritor.detener()
    log.info("[UNA-VEZ] listo -- revisa %s\\streams\\ (unido.json si ya se unio "
             "solo)", BASE)
    return 0


def main(argv) -> int:
    configurar_log()
    ap = argparse.ArgumentParser(
        description="Graba streams de chaturbate sin navegador.")
    ap.add_argument("--una-vez", metavar="MODELO", default=None,
                     help="diagnostico: graba una sola modelo un tiempo fijo, "
                          "sin tocar grabar.txt ni estado\\")
    ap.add_argument("--minutos", type=float, default=5.0,
                     help="duracion de --una-vez (defecto: 5)")
    ap.add_argument("--salida", default=None,
                     help="M3U8_BASE alternativo para --una-vez")
    args = ap.parse_args(argv)

    if args.una_vez:
        return _correr_una_vez(args.una_vez, args.minutos, args.salida)

    grabador = Grabador()
    try:
        grabador.correr()
    except KeyboardInterrupt:
        grabador.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
