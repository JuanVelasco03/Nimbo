# -*- coding: utf-8 -*-
"""grabador_directo.py

El camino directo: graba streams de doppiocdn sin navegador, descifrando
el protocolo MOUFLON v2 (ver nucleo\\scripts\\mouflon.py) y bajando init +
segmentos con pedidos HTTP pelados. Ver specs\\grabador_directo.md para la
especificacion completa y pruebas\\RESULTADOS.md para la evidencia medida
que respalda cada decision.

Lo arranca nimbo\\lanzador_directo.py (igual que nimbo\\lanzador_supervisor.py
arranca supervisor.py), que reenvia el logger "directo" a
estado\\eventos_directo.jsonl. Este archivo tambien corre solo para
diagnostico:

    python grabador_directo.py --una-vez <modelo> --minutos 5 [--salida DIR] [--vpn]

Un hilo por modelo (_ModeloDirecto). Cada vuelta:
  1. Cada 60s: consulta /cam para ver si sigue en vivo (sin eso, no
     molesta la red en cada vuelta de 1s).
  2. Tambien cada 60s: relee el master y decide que rendicion(es) grabar
     segun @calidad/@codec/@fallback/@privado -- @variantes=todas abre una
     sesion por cada rendicion que coincida, no solo la mejor.
  3. Refresca la media playlist de cada sesion abierta cada
     TARGETDURATION/2.
  4. Compuerta dura antes de escribir un solo byte: mouflon.es_publicidad().
  5. Descifra los #EXT-X-MOUFLON:URI: (mouflon.descifrar_playlist);
     3 fallos seguidos (publicidad, o el 100% de los tokens sin
     descifrar) para ESA variante -> "degradado": se deja de intentar esa
     modelo y queda a la vista en estado\\directo.json para que el humano
     la pase a @navegador= si hace falta.
  6. Las descargas van a un pool de hilos compartido; el loop solo somete
     trabajo y recolecta lo que ya termino (nunca espera a una descarga
     colgada: ver RESULTADOS.md S2.5.d / specs S2.5.a).
  7. Un #EXT-X-MAP nuevo es una sesion nueva (reconexion del encoder), no
     un hueco: se cierra la sesion actual y se abre otra.
  8. Al cerrar una sesion, la union se delega a
     "detectar_m3u8.py --unir <sesion>" como SUBPROCESO -- nunca se
     importa ese modulo (su "else: addons = [Detector()]" construiria un
     Detector entero y vivo con solo importarlo).

activos.txt (lo escribe el detector) se lee aca para traducir IDs
numericos de grabar.txt a nombre, igual que ya hace supervisor.py -- este
modulo nunca lo escribe (invariante de CLAUDE.md: activos.txt es del
detector en exclusiva).

Parada por stdin ("parar\\n"), nunca por kill -- mismo contrato que el
detector y el supervisor (ver CLAUDE.md, "Tres procesos").
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

# -- rutas (mismo patron que detectar_m3u8.py / supervisor.py) -------------

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
import mouflon  # noqa: E402

BASE = os.environ.get("M3U8_BASE") or _raiz_proyecto()
STREAMS_DIR = os.path.join(BASE, "streams")
SELECCION = os.environ.get("M3U8_GRABAR") or _al_lado("grabar.txt")
ACTIVOS = os.environ.get("M3U8_ACTIVOS") or os.path.join(
    os.path.dirname(SELECCION), "activos.txt")
REGISTRO = os.path.join(BASE, "directo.log")

TUNEL = os.environ.get("M3U8_TUNEL", mouflon.TUNEL_DEFECTO).strip()

# nucleo\scripts\detectar_m3u8.py, invocado como SUBPROCESO para la union
# (nunca importado: ver docstring del modulo).
PYTHON_EXE = sys.executable
DETECTAR_PY = os.path.join(AQUI, "detectar_m3u8.py")
_SIN_VENTANA = ({"creationflags": subprocess.CREATE_NO_WINDOW}
                if hasattr(subprocess, "CREATE_NO_WINDOW") else {})

# Donde Nimbo (puente.py/lanzador_supervisor.py) deja/lee los JSON de
# estado. Mismo fallback que puente.py (nimbo\puente.py): este archivo
# vive dos niveles mas adentro (nucleo\scripts\), de ahi el dirname extra.
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_raiz_proyecto()), "estado")
ARCHIVO_ESTADO = os.path.join(DIR_ESTADO, "directo.json")
# Cache id<->nombre: si la API de user-ids esta bloqueada (Cloudflare),
# con el id ya conocido de una vez anterior alcanza para pedir el master
# directo (ver specs\grabador_directo.md S6, "Cloudflare en la API").
RUTA_IDS_CACHE = os.path.join(DIR_ESTADO, "ids.json")

MARCADOR = "unido.json"              # detectar_m3u8.py:194
CARPETA_SEGS = "segmentos"           # detectar_m3u8.py:195
CARPETA_PLAYLISTS = "playlists"      # detectar_m3u8.py:196

RE_NOMBRE = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")
RE_FILA_ACTIVOS = re.compile(r"^(\d{4,})\s+([A-Za-z0-9_.\-]{2,40})\s")

MAX_REGISTRO = 20 * 1024 * 1024       # mismo tope que detectar_m3u8.py/motor.py

log = logging.getLogger("directo")


def configurar_log() -> None:
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


def _limpiar(nombre: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", nombre)[:60] or "x"


def _escribir_atomico(ruta: str, texto: str) -> None:
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    # En Windows, servidor.py:_leer_json puede tener `ruta` abierta para
    # lectura (CreateFile sin FILE_SHARE_DELETE) justo en este instante;
    # os.replace() entonces tira WinError 5 (acceso denegado), no un
    # sharing violation claro. Es transitorio -- el lector suelta el
    # handle en microsegundos -- asi que reintentar alcanza; no hace
    # falta tocar el lado que lee (ya traga OSError y devuelve None).
    for intento in range(5):
        try:
            os.replace(tmp, ruta)
            return
        except PermissionError:
            if intento == 4:
                raise
            time.sleep(0.05 * (intento + 1))


def _nombres_conocidos() -> dict:
    """id -> nombre, de activos.txt (lo escribe el detector). Mismo
    puerto que supervisor.py:nombres_conocidos() -- solo LECTURA, nunca
    se escribe aca (invariante de CLAUDE.md)."""
    mapa = {}
    try:
        with open(ACTIVOS, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                m = RE_FILA_ACTIVOS.match(linea.strip())
                if m and m.group(2) != "desconocido":
                    mapa[m.group(1)] = m.group(2)
    except OSError:
        pass
    return mapa


_lock_ids_cache = threading.Lock()


def _leer_ids_cache() -> dict:
    try:
        with open(RUTA_IDS_CACHE, encoding="utf-8") as f:
            datos = json.load(f)
        return {str(k).lower(): int(v) for k, v in datos.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _guardar_id_cache(nombre_lower: str, id_modelo: int) -> None:
    with _lock_ids_cache:
        datos = _leer_ids_cache()
        if datos.get(nombre_lower) == id_modelo:
            return
        datos[nombre_lower] = id_modelo
        try:
            _escribir_atomico(RUTA_IDS_CACHE, json.dumps(datos, ensure_ascii=False))
        except OSError as e:
            log.warning("[IDS] no se pudo guardar la cache: %s", e)


def _guardar_playlist(carpeta_stream: str, nombre: str, texto_cifrado: str) -> None:
    """La playlist tal cual llego, CIFRADA -- es la fuente de verdad del
    diagnostico si algo sale mal (ver specs\\grabador_directo.md S4.3)."""
    try:
        destino = os.path.join(carpeta_stream, CARPETA_PLAYLISTS, nombre)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        tmp = destino + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto_cifrado)
        os.replace(tmp, destino)
    except OSError as e:
        log.warning("[PLAYLIST] %s: %s", nombre, e)


def _escribir_info_sesion(sesion, modelo: str, id_modelo, motivo: str) -> None:
    try:
        os.makedirs(sesion.carpeta, exist_ok=True)
        with open(os.path.join(sesion.carpeta, "info.json"), "w",
                  encoding="utf-8") as f:
            json.dump({
                "id": str(id_modelo) if id_modelo else "",
                "modelo": modelo,
                "variante": sesion.variante,
                "sesion": sesion.sesion,
                "calidad": sesion.calidad,
                "codec": sesion.codec,
                "segmentos": sesion.segmentos,
                "bytes": sesion.bytes,
                "init": sesion.init_nombre is not None,
                "motivo_cierre": motivo,
                "modo": "directo",
                "inicio": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(sesion.inicio)),
                "fin": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.warning("[INFO] %s", e)


def _unir_en_fondo(escritor: "Escritor", sesion) -> None:
    """Delega la union a detectar_m3u8.py --unir como SUBPROCESO: correr
    como __main__ no arma un Detector (ver docstring del modulo) y hereda
    el Reencadenador, los 6 intentos de reparacion, _hay_espacio y
    _marcar_unida sin duplicar nada de eso aca."""
    if not escritor.barrera().wait(180):
        log.warning("[UNIR] %s: escrituras pendientes, sigo igual", sesion.etiqueta)
    filtro = sesion.etiqueta.lower()
    cmd = [PYTHON_EXE, DETECTAR_PY, "--unir", filtro]
    marcador = os.path.join(sesion.carpeta, MARCADOR)
    try:
        proc = subprocess.run(cmd, cwd=AQUI, env=dict(os.environ),
                               capture_output=True, text=True, timeout=1200,
                               **_SIN_VENTANA)
        if proc.returncode != 0:
            log.error("[UNIR] %s: --unir fallo (codigo %s): %s", sesion.etiqueta,
                      proc.returncode, (proc.stderr or proc.stdout or "")[-400:])
        elif not os.path.isfile(marcador):
            # --unir devuelve 0 igual si no encontro ninguna sesion que
            # coincida con el filtro (p.ej. M3U8_BASE relativo resuelto
            # distinto en este subproceso) -- "returncode == 0" solo no
            # prueba que de verdad unio algo.
            log.error(
                "[UNIR] %s: --unir termino sin error pero no generó %s -- "
                "revisa M3U8_BASE (tiene que ser una ruta ABSOLUTA) -- "
                "salida: %s", sesion.etiqueta, MARCADOR,
                (proc.stdout or "")[-400:])
        else:
            log.info("[UNIR] %s: listo", sesion.etiqueta)
    except subprocess.TimeoutExpired:
        log.error("[UNIR] %s: --unir no termino en 20 min", sesion.etiqueta)
    except OSError as e:
        log.error("[UNIR] %s: no se pudo lanzar --unir: %s", sesion.etiqueta, e)


def _descargar(url: str, por_tunel: bool) -> bytes:
    status, _, datos = mouflon.pedir(url, por_tunel, TUNEL, timeout=15.0)
    if status != 200:
        raise RuntimeError("status %s" % status)
    return datos


# --------------------------------------------------------------------------
# Escritor en segundo plano -- portado de detectar_m3u8.Escritor (mismo
# patron: hilo + queue, .part + os.replace, nunca se importa ese modulo).
# --------------------------------------------------------------------------

class Escritor(threading.Thread):
    def __init__(self, maxsize: int = 4000):
        super().__init__(daemon=True, name="directo-escritor")
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
# nunca se importa detectar_m3u8.py; ver su docstring)
# --------------------------------------------------------------------------

_SIN_FILTRO = {"max", "maxima", "mejor", "best", "auto", "*", "todas", "todos", ""}


def _lista(valor: str) -> tuple:
    partes = tuple(v for v in valor.replace(" ", "").split(",") if v)
    if not partes or any(v in _SIN_FILTRO for v in partes):
        return ()
    return partes


def _alto(valor: str, por_defecto: int) -> int:
    if valor in {"0", "no", "false", "off", "nunca"}:
        return 0
    m = re.match(r"^(\d{2,4})p?$", valor)
    return int(m.group(1)) if m else por_defecto


class _ConfigGeneral:
    """Lo que grabador_directo.py entiende de grabar.txt. Subconjunto
    deliberado de detectar_m3u8.Config -- ver specs\\grabador_directo.md
    S5.1 para que directivas aplican y cuales se ignoran."""

    __slots__ = ("entradas", "todo", "calidad", "codec", "fallback",
                 "una_variante", "borrosas", "privado", "vpn", "directo",
                 "navegador", "inactividad", "unir")

    def __init__(self):
        self.entradas = set()
        self.todo = False
        self.calidad = ()
        self.codec = ()
        self.fallback = True
        self.una_variante = True
        self.borrosas = False
        self.privado = 160
        self.vpn = frozenset()
        self.directo = False
        self.navegador = frozenset()
        self.inactividad = 120.0
        self.unir = True

    def acepta_variante(self, calidad, codec, fuente=False, borrosa=False) -> bool:
        """Portado literal de detectar_m3u8.Config.acepta_variante."""
        if borrosa and not self.borrosas:
            return False
        if self.es_privado(mouflon.altura_de(calidad)):
            return False
        if fuente and not self.calidad and not self.codec:
            return True
        if self.calidad and not calidad.lower().startswith(self.calidad):
            return False
        if self.codec and codec.lower() != "na" and codec.lower() not in self.codec:
            return False
        return True

    def es_privado(self, altura: int) -> bool:
        return bool(self.privado) and 0 < altura <= self.privado


# Directivas que entiende el detector pero no tienen sentido aca: no hay
# player al que ajustarle nada (ver specs\grabador_directo.md S5.1).
_IGNORADAS = {
    "forzar": "no hay player al que recortarle el master",
    "forzar_calidad": "no hay player al que recortarle el master",
    "cambio": "la variante se elige al abrir el master, no se sigue un ABR",
    "espera": "el master lista todas las variantes de una, no hace falta esperar",
}


class LectorGrabar:
    """Relee grabar.txt solo cuando cambia (mtime) -- mismo patron que
    detectar_m3u8.Seleccion, portado (nunca importado)."""

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._firma = None
        self._cfg = _ConfigGeneral()
        self._avisadas = set()

    def actual(self) -> _ConfigGeneral:
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
        cfg = _ConfigGeneral()
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
                if clave == "calidad":
                    cfg.calidad = _lista(valor)
                elif clave == "codec":
                    cfg.codec = tuple(
                        "hevc" if v == "h265" else v for v in _lista(valor))
                elif clave == "variantes":
                    cfg.una_variante = valor not in {"todas", "all", "*", "0"}
                elif clave == "fallback":
                    cfg.fallback = valor not in {"0", "no", "false", "off"}
                elif clave in {"borrosas", "borrosa", "blur"}:
                    cfg.borrosas = valor not in {"0", "no", "false", "off"}
                elif clave in {"privado", "privados", "minimo"}:
                    cfg.privado = _alto(valor, cfg.privado)
                elif clave in {"inactividad", "timeout"}:
                    try:
                        cfg.inactividad = max(0.0, float(valor))
                    except ValueError:
                        pass
                elif clave == "unir":
                    cfg.unir = valor not in {"0", "no", "false", "off"}
                elif clave == "vpn":
                    cfg.vpn |= frozenset(
                        v.strip() for v in valor.split(",") if v.strip())
                elif clave == "directo":
                    cfg.directo = valor not in {"0", "no", "false", "off"}
                elif clave == "navegador":
                    cfg.navegador |= frozenset(
                        v.strip() for v in valor.split(",") if v.strip())
                elif clave in {"todo", "all"}:
                    cfg.todo = valor not in {"0", "no", "false", "off"}
                elif clave in _IGNORADAS:
                    if clave not in self._avisadas:
                        self._avisadas.add(clave)
                        log.info("[CONFIG] @%s= no aplica en modo directo (%s)",
                                 clave, _IGNORADAS[clave])
                # directivas que no son ninguna de estas (p.ej. "espera" ya
                # cubierta arriba) se ignoran en silencio: las entiende el
                # detector, no hace falta duplicar su aviso de "desconocida"
                continue
            if linea in {"*", "all", "todo", "todos"}:
                cfg.todo = True
            else:
                cfg.entradas.add(linea.lower())
        return cfg


# --------------------------------------------------------------------------
# Una sesion de grabacion (una variante abierta de un modelo)
# --------------------------------------------------------------------------

class _SesionDirecta:
    __slots__ = ("variante", "calidad", "codec", "carpeta", "sesion",
                 "init_nombre", "segmentos", "bytes", "inicio", "vistos")

    def __init__(self, variante, calidad, codec, carpeta, sesion):
        self.variante = variante
        self.calidad = calidad
        self.codec = codec
        self.carpeta = carpeta
        self.sesion = sesion
        self.init_nombre = None
        self.segmentos = 0
        self.bytes = 0
        self.inicio = time.time()
        self.vistos = set()

    @property
    def etiqueta(self) -> str:
        return os.path.basename(self.carpeta)


# --------------------------------------------------------------------------
# Un hilo por modelo
# --------------------------------------------------------------------------

class _ModeloDirecto(threading.Thread):
    CHEQUEO_ESTADO = 60.0
    FALLOS_PARA_DEGRADAR = 3

    def __init__(self, nombre: str, grabador: "Grabador", por_tunel: bool = False):
        super().__init__(daemon=True, name="directo-" + nombre)
        self.nombre = nombre
        self.grabador = grabador
        self.por_tunel = por_tunel
        self._parar = threading.Event()

        self.id_modelo = grabador.ids_cache.get(nombre.lower())
        self.estado_cam = "?"
        self.proximo_chequeo = 0.0
        self.target_duration = 2.0
        self.ultimo_visto = time.time()
        self.privado = False
        self.privado_desde = 0.0
        self.degradado = False

        self.sesiones = {}       # variante -> _SesionDirecta
        self.uris = {}           # variante -> uri (puede rotar de host)
        self.fallos = {}         # variante -> fallos consecutivos
        self._futuros = {}       # future -> (variante, url, es_init)
        self._lock = threading.Lock()

    # -- ciclo de vida -------------------------------------------------

    def parar(self) -> None:
        self._parar.set()

    def run(self) -> None:
        log.info("[%s] arranca (vpn=%s)", self.nombre, self.por_tunel)
        try:
            while not self._parar.is_set():
                try:
                    self._paso()
                except Exception as e:
                    log.error("[%s] %s", self.nombre, e)
                self._parar.wait(self._intervalo())
        finally:
            self._cerrar_todas("hilo terminado")
        log.info("[%s] termina", self.nombre)

    def _intervalo(self) -> float:
        return max(0.5, self.target_duration / 2.0) if self.sesiones else 5.0

    def _en_vivo(self) -> bool:
        if self.estado_cam in mouflon.ESTADOS_VIVOS:
            return True
        # una consulta que no pudo contestar no dice que dejo de emitir
        return mouflon.estado_incierto(self.estado_cam)

    def _carpeta_stream(self) -> str:
        sufijo = str(self.id_modelo) if self.id_modelo else "sinid"
        return os.path.join(STREAMS_DIR, _limpiar(self.nombre + "_" + sufijo))

    # -- paso principal --------------------------------------------------

    def _paso(self) -> None:
        if self.degradado:
            if self.sesiones:
                self._cerrar_todas("degradado")
            return

        cfg_general = self.grabador.seleccion.actual()
        ahora = time.time()
        if ahora >= self.proximo_chequeo:
            self.proximo_chequeo = ahora + self.CHEQUEO_ESTADO
            self._chequear_estado()
            if not self.degradado and self._en_vivo() and self.id_modelo:
                self._reconciliar_variantes(cfg_general)

        if not self._en_vivo():
            if self.sesiones:
                self._cerrar_todas("la modelo no esta en vivo (%s)" % self.estado_cam)
            return

        self._refrescar_todas()

    # -- estado (sin abrir pagina) ---------------------------------------

    def _chequear_estado(self) -> None:
        if self.id_modelo is None:
            try:
                self.id_modelo = mouflon.resolver_id(self.nombre, self.por_tunel, TUNEL)
                _guardar_id_cache(self.nombre.lower(), self.id_modelo)
                log.info("[%s] id resuelto: %s", self.nombre, self.id_modelo)
            except mouflon.ErrorMouflon as e:
                log.warning("[%s] no se pudo resolver el id (%s); reintento en %ds",
                            self.nombre, e, int(self.CHEQUEO_ESTADO))
                return
        try:
            cam = mouflon.consultar_cam(self.id_modelo, self.por_tunel, TUNEL)
        except mouflon.ErrorMouflon as e:
            nuevo = "error:%s" % type(e).__name__
            if nuevo != self.estado_cam:
                log.info("[%s] estado: %s -> %s", self.nombre, self.estado_cam, nuevo)
            self.estado_cam = nuevo
            return
        nuevo = cam["estado"]
        if nuevo != self.estado_cam:
            log.info("[%s] estado: %s -> %s", self.nombre, self.estado_cam, nuevo)
        self.estado_cam = nuevo
        if nuevo in mouflon.ESTADOS_VIVOS:
            self.ultimo_visto = time.time()

    # -- master: que variantes grabar -------------------------------------

    def _reconciliar_variantes(self, cfg_general: _ConfigGeneral) -> None:
        try:
            texto_master = mouflon.pedir_master(self.id_modelo, self.por_tunel, TUNEL)
        except mouflon.ErrorMouflon as e:
            log.warning("[%s] master: %s", self.nombre, e)
            return

        _, rendiciones, _ = mouflon.leer_master(texto_master)
        if not rendiciones:
            return

        if cfg_general.una_variante:
            elegida = mouflon.mejor_rendicion(rendiciones, cfg_general)
            deseadas = [elegida] if elegida else []
        else:
            deseadas = [r for r in rendiciones
                        if cfg_general.acepta_variante(r.calidad, r.codec,
                                                       r.fuente, r.borrosa)]
            if not deseadas and cfg_general.fallback:
                mejor = mouflon.mejor_rendicion(rendiciones, cfg_general)
                deseadas = [mejor] if mejor else []

        # show privado: NINGUNA rendicion real supera el umbral de altura
        privado_ahora = all(
            cfg_general.es_privado(r.alto if r.fuente else mouflon.altura_de(r.calidad))
            for r in rendiciones)
        if privado_ahora != self.privado:
            self.privado = privado_ahora
            if privado_ahora:
                self.privado_desde = time.time()
                log.info("[%s] en show privado: corto y espero", self.nombre)
            else:
                log.info("[%s] volvio del show privado", self.nombre)
        if privado_ahora:
            deseadas = []

        _guardar_playlist(self._carpeta_stream(),
                          mouflon.nombre_archivo(mouflon.url_master(self.id_modelo)),
                          texto_master)

        nombres_deseados = {r.nombre for r in deseadas}
        for variante in set(self.sesiones) - nombres_deseados:
            self._cerrar_variante(variante, "ya no coincide con @calidad/@codec")
        for r in deseadas:
            if r.nombre in self.sesiones:
                self.uris[r.nombre] = r.uri   # el host (b-hls-NN) puede rotar
            else:
                self._abrir_variante(r)

    def _abrir_variante(self, rendicion) -> None:
        sello = time.strftime("%Y%m%d-%H%M%S")
        carpeta = os.path.join(self._carpeta_stream(), rendicion.nombre + "_" + sello)
        self.sesiones[rendicion.nombre] = _SesionDirecta(
            rendicion.nombre, rendicion.calidad, rendicion.codec, carpeta, sello)
        self.uris[rendicion.nombre] = rendicion.uri
        self.fallos[rendicion.nombre] = 0
        log.info("[%s] sesion nueva: %s", self.nombre, rendicion.nombre)

    # -- media playlist: bajar init + segmentos ---------------------------

    def _refrescar_todas(self) -> None:
        for variante in list(self.sesiones):
            try:
                self._refrescar_variante(variante)
            except Exception as e:
                log.error("[%s/%s] %s", self.nombre, variante, e)
        self._recolectar()

    def _refrescar_variante(self, variante: str) -> None:
        uri = self.uris.get(variante)
        sesion = self.sesiones.get(variante)
        if not uri or sesion is None:
            return
        base = uri.split("?", 1)[0]
        url = (base + "?playlistType=standard&preferredVideoCodec=h264"
               "&psch=v2&pkey=" + mouflon.PKEY_VERIFICADO)
        try:
            status, url_final, cuerpo = mouflon.pedir(url, self.por_tunel, TUNEL,
                                                        timeout=10.0)
        except OSError as e:
            log.warning("[%s/%s] red: %s", self.nombre, variante, e)
            return
        if status != 200:
            # antes solo logueaba y volvia a probar la MISMA uri cada
            # ~1-2s hasta el proximo _reconciliar_variantes (60s) -- si el
            # host/pkey quedo invalido eso eran decenas de WARNING idénticos
            # por nada. Ahora cuenta como fallo (misma vara que publicidad/
            # MAP-MOUFLON faltante): a los 3 seguidos se degrada solo y el
            # aviso le dice al usuario que la pase a @navegador=.
            self._registrar_fallo(variante, "media playlist dio %s" % status)
            return
        texto = cuerpo.decode("utf-8", "replace")

        if mouflon.es_publicidad(texto, url_final):
            self._registrar_fallo(variante, "publicidad en vez de la emision")
            return
        if "#EXT-X-MAP:URI=" not in texto or "#EXT-X-MOUFLON:URI:" not in texto:
            self._registrar_fallo(variante, "la playlist no trae MAP/MOUFLON")
            return

        texto_claro, fallos = mouflon.descifrar_playlist(texto)
        total_tokens = texto.count("#EXT-X-MOUFLON:URI:")
        if total_tokens and len(fallos) == total_tokens:
            self._registrar_fallo(
                variante, "el 100%% de los tokens no descifro (¿rotaron la "
                          "tabla de claves? ver pruebas\\RESULTADOS.md S1)")
            return
        self.fallos[variante] = 0

        _guardar_playlist(self._carpeta_stream(), mouflon.nombre_archivo(base), texto)

        init_url, target, segmentos = mouflon.extraer_segmentos(texto_claro)
        if target:
            self.target_duration = target

        if init_url:
            init_nombre = mouflon.nombre_archivo(init_url)
            if sesion.init_nombre and init_nombre != sesion.init_nombre:
                log.info(
                    "[%s/%s] init nuevo (%s): la modelo reconecto el encoder "
                    "-- cierro la sesion y abro otra", self.nombre, variante,
                    init_nombre)
                self._cerrar_variante(variante, "reconexion de encoder")
                return
            if sesion.init_nombre is None:
                sesion.init_nombre = init_nombre
                self._someter_descarga(variante, init_url, es_init=True)

        for _msn, _epoch, url_seg in segmentos:
            nombre = mouflon.nombre_archivo(url_seg)
            if nombre in sesion.vistos:
                continue
            sesion.vistos.add(nombre)
            self._someter_descarga(variante, url_seg, es_init=False)

    def _registrar_fallo(self, variante: str, motivo: str) -> None:
        n = self.fallos.get(variante, 0) + 1
        self.fallos[variante] = n
        log.warning("[%s/%s] %s (fallo %d/%d)", self.nombre, variante, motivo,
                    n, self.FALLOS_PARA_DEGRADAR)
        if n >= self.FALLOS_PARA_DEGRADAR:
            self.degradado = True
            log.error(
                "[%s] DEGRADADO tras %d fallos seguidos en %s -- el camino "
                "directo no sirve para esta modelo. Agregala a @navegador= "
                "en grabar.txt para que vuelva a grabarla el navegador.",
                self.nombre, n, variante)

    # -- descargas: pool compartido, nunca bloquea el refresco ------------

    def _someter_descarga(self, variante: str, url: str, es_init: bool) -> None:
        fut = self.grabador.pool.submit(_descargar, url, self.por_tunel)
        with self._lock:
            self._futuros[fut] = (variante, url, es_init)

    def _recolectar(self) -> None:
        with self._lock:
            listos = [f for f in self._futuros if f.done()]
        for fut in listos:
            with self._lock:
                variante, url, es_init = self._futuros.pop(fut)
            try:
                datos = fut.result()
            except Exception as e:
                nombre = mouflon.nombre_archivo(url)
                log.warning("[%s/%s] fallo bajando %s: %s", self.nombre,
                           variante, nombre, e)
                sesion = self.sesiones.get(variante)
                if sesion is not None and not es_init:
                    sesion.vistos.discard(nombre)   # que se pueda reintentar
                continue
            self._guardar_descarga(variante, url, es_init, datos)

    def _guardar_descarga(self, variante: str, url: str, es_init: bool,
                          datos: bytes) -> None:
        sesion = self.sesiones.get(variante)
        if sesion is None:
            return   # la sesion se cerro mientras la descarga estaba en vuelo
        nombre = mouflon.nombre_archivo(url)
        if es_init:
            if not nombre.lower().startswith("init"):
                nombre = "init_" + nombre
            self.grabador.escritor.encolar(os.path.join(sesion.carpeta, nombre), datos)
            log.info("[%s/%s] init | %s", self.nombre, variante, nombre)
            return

        # playlistType=standard trae varios sub-fragmentos CMAF (moof)
        # pegados en un solo archivo -- partirlos antes de escribir es lo
        # que hace que detectar_m3u8.Reencadenador (compartido con la
        # union) no los confunda con huecos (ver mouflon.partir_fragmentos).
        fragmentos = mouflon.partir_fragmentos(datos)
        base, ext = os.path.splitext(nombre)
        nombres = ([nombre] if len(fragmentos) == 1
                   else ["%s_part%d%s" % (base, i, ext)
                         for i in range(len(fragmentos))])
        for nombre_frag, bytes_frag in zip(nombres, fragmentos):
            destino = os.path.join(sesion.carpeta, CARPETA_SEGS, nombre_frag)
            if not self.grabador.escritor.encolar(destino, bytes_frag):
                continue
            sesion.segmentos += 1
            sesion.bytes += len(bytes_frag)
        if sesion.segmentos % 20 == 0:
            log.info("[%s/%s] %d segmentos | %.1f MB | %.1f min", self.nombre,
                     variante, sesion.segmentos, sesion.bytes / 1048576,
                     (time.time() - sesion.inicio) / 60)

    # -- cierre de sesion ---------------------------------------------------

    def _cerrar_variante(self, variante: str, motivo: str) -> None:
        sesion = self.sesiones.pop(variante, None)
        self.uris.pop(variante, None)
        self.fallos.pop(variante, None)
        if sesion is None:
            return
        if not sesion.segmentos:
            log.info("[%s/%s] %s sin segmentos, no se une", self.nombre,
                     variante, sesion.etiqueta)
            return
        _escribir_info_sesion(sesion, self.nombre, self.id_modelo, motivo)
        cfg = self.grabador.seleccion.actual()
        if not cfg.unir:
            log.info("[%s/%s] @unir=0, queda sin unir: %s", self.nombre,
                     variante, sesion.carpeta)
            return
        log.info("[%s/%s] cierre: %s (%d segmentos) -- %s | uniendo en 2do plano",
                 self.nombre, variante, sesion.etiqueta, sesion.segmentos, motivo)
        # Hilo propio, NO el pool de descargas (self.grabador.pool): ese
        # pool tiene tope (POOL_HILOS) y con varias modelos grabando a la
        # vez queda saturado de descargas continuas -- la union, al ser
        # una tarea mas en la misma cola, podia quedarse esperando un
        # hueco que nunca llega (visto en vivo: sesion cerrada, carpeta
        # segmentos\ intacta, sin "UNIR...listo" ni error, minutos
        # despues). Es una unica tarea de corta vida por sesion cerrada,
        # no una descarga continua: no compite por el mismo cupo.
        threading.Thread(target=_unir_en_fondo,
                          args=(self.grabador.escritor, sesion),
                          daemon=True, name="directo-unir").start()

    def _cerrar_todas(self, motivo: str) -> None:
        for variante in list(self.sesiones):
            self._cerrar_variante(variante, motivo)

    # -- estado para estado\directo.json -----------------------------------

    def fila_estado(self) -> dict:
        with self._lock:
            sesiones_info = [{
                "variante": nombre, "calidad": s.calidad, "codec": s.codec,
                "sesion": s.sesion, "carpeta": s.carpeta,
                "init": s.init_nombre is not None, "segmentos": s.segmentos,
                "bytes": s.bytes, "inicio": s.inicio,
            } for nombre, s in self.sesiones.items()]
        grabando = bool(sesiones_info)
        if grabando:
            estado = "grabando"
        elif self.degradado:
            estado = "degradado"
        elif self.privado:
            estado = "privado"
        elif self._en_vivo():
            estado = "detectado"
        else:
            estado = "inactivo"
        variantes = sorted(self.sesiones)
        inicios = [s.inicio for s in self.sesiones.values()]
        return {
            "id": str(self.id_modelo) if self.id_modelo else "",
            "modelo": self.nombre,
            "mejor": "",
            "elegida": variantes[0] if len(variantes) == 1 else "",
            "variantes": variantes,
            "estado": estado,
            "grabando": grabando,
            "privado": self.privado,
            "privado_desde": self.privado_desde or None,
            "inicio": min(inicios) if inicios else None,
            "ultimo_visto": self.ultimo_visto,
            "segmentos": sum(s.segmentos for s in self.sesiones.values()),
            "bytes": sum(s.bytes for s in self.sesiones.values()),
            "sesiones": sesiones_info,
            "degradado": self.degradado,
        }


# --------------------------------------------------------------------------
# El proceso: un Grabador por corrida
# --------------------------------------------------------------------------

INTERVALO_LISTA = 2.0     # relectura de grabar.txt (igual que el detector)
INTERVALO_ESTADO = 2.0    # cadencia de estado\directo.json (su mtime es el
                          # latido, igual que puente.py -- se escribe SIEMPRE)
POOL_HILOS = 24           # descargas simultaneas, compartidas entre modelos


class Grabador:
    def __init__(self):
        self.seleccion = LectorGrabar(SELECCION)
        self.escritor = Escritor()
        self.pool = cf.ThreadPoolExecutor(max_workers=POOL_HILOS,
                                          thread_name_prefix="directo-dl")
        self.ids_cache = _leer_ids_cache()
        self._modelos = {}       # nombre_lower -> _ModeloDirecto
        self._lock = threading.RLock()
        self._parar = threading.Event()
        self.degradado_global = False

    def parar(self) -> None:
        self._parar.set()

    def correr(self) -> None:
        os.makedirs(STREAMS_DIR, exist_ok=True)
        os.makedirs(DIR_ESTADO, exist_ok=True)
        log.info("=" * 70)
        log.info("[DIRECTO] base=%s | estado=%s", BASE, DIR_ESTADO)

        if not mouflon.verificar_canario():
            self.degradado_global = True
            log.error(
                "[DIRECTO] el canario de claves FALLO: la tabla MOUFLON "
                "rotó. El camino directo queda INACTIVO para TODAS las "
                "modelos de @directo=1 -- agregalas a @navegador= (o "
                "apagá @directo=1) para que el navegador las grabe. Ver "
                "pruebas\\RESULTADOS.md S1 para derivar una clave nueva.")
        else:
            log.info("[DIRECTO] canario de claves OK")
        log.info("=" * 70)

        self.escritor.start()
        hilo_vigilante = threading.Thread(target=self._bucle_vigilante,
                                          daemon=True, name="directo-vigilante")
        hilo_vigilante.start()
        hilo_estado = threading.Thread(target=self._bucle_estado,
                                       daemon=True, name="directo-estado")
        hilo_estado.start()

        if sys.stdin is not None:
            hilo_stdin = threading.Thread(target=self._bucle_stdin,
                                          daemon=True, name="directo-stdin")
            hilo_stdin.start()

        self._parar.wait()

        log.info("[DIRECTO] parando -- cerrando %d modelo(s)", len(self._modelos))
        with self._lock:
            modelos = list(self._modelos.values())
            self._modelos.clear()
        for m in modelos:
            m.parar()
        for m in modelos:
            m.join(60)
        self.pool.shutdown(wait=True)
        self.escritor.detener()
        with contextlib.suppress(Exception):
            self._publicar_estado()
        log.info("[DIRECTO] listo")

    # -- stdin: la misma orden "parar" que el detector/supervisor ----------

    def _bucle_stdin(self) -> None:
        try:
            for linea in sys.stdin:
                if linea.strip().lower() == "parar":
                    break
        except (OSError, ValueError):
            return
        log.info("[DIRECTO] orden de parada recibida")
        self.parar()

    # -- vigilante: abre/cierra hilos por modelo segun grabar.txt -----------

    def _bucle_vigilante(self) -> None:
        while not self._parar.wait(INTERVALO_LISTA):
            try:
                self._revisar_lista()
            except Exception as e:
                log.error("[VIGILANTE] %s", e)

    def _deseados(self, cfg: _ConfigGeneral) -> set:
        if not cfg.directo or self.degradado_global:
            return set()
        conocidos = _nombres_conocidos()
        if cfg.todo:
            deseados = {n.lower() for n in conocidos.values()}
        else:
            deseados = set()
            for entrada in cfg.entradas:
                if entrada.isdigit():
                    nombre = conocidos.get(entrada)
                    if nombre:
                        deseados.add(nombre.lower())
                    # si todavia no se conoce el nombre de ese ID (lo
                    # aprende el detector en activos.txt), no hay nada
                    # que resolver_id() pueda hacer con un numero solo:
                    # se espera al proximo ciclo.
                elif RE_NOMBRE.match(entrada):
                    deseados.add(entrada)
        return deseados - cfg.navegador

    def _revisar_lista(self) -> None:
        cfg = self.seleccion.actual()
        deseados = self._deseados(cfg)
        with self._lock:
            actuales = set(self._modelos)
            for nombre in deseados - actuales:
                m = _ModeloDirecto(nombre, self, por_tunel=nombre in cfg.vpn)
                self._modelos[nombre] = m
                m.start()
            for nombre in actuales - deseados:
                m = self._modelos.pop(nombre)
                m.parar()    # se cierra y une sola, en su propio hilo
            for nombre, m in self._modelos.items():
                m.por_tunel = nombre in cfg.vpn

    # -- estado\directo.json: SIEMPRE se reescribe (su mtime es el latido) --

    def _bucle_estado(self) -> None:
        while not self._parar.wait(INTERVALO_ESTADO):
            try:
                self._publicar_estado()
            except Exception as e:
                log.error("[ESTADO] %s", e)

    def _publicar_estado(self) -> None:
        ahora = time.time()
        with self._lock:
            modelos = list(self._modelos.values())
        filas = [m.fila_estado() for m in modelos]
        ids = {f["modelo"]: f["id"] for f in filas if f.get("id")}
        datos = {
            "generado": ahora,
            "modo": "directo",
            "degradado": self.degradado_global or any(f["degradado"] for f in filas),
            "ids": ids,
            "streams": filas,
        }
        _escribir_atomico(ARCHIVO_ESTADO, json.dumps(datos, ensure_ascii=False))


# --------------------------------------------------------------------------
# CLI: modo normal (lo arranca lanzador_directo.py) y --una-vez (diagnostico)
# --------------------------------------------------------------------------

def _correr_una_vez(nombre: str, minutos: float, salida_base, vpn: bool) -> int:
    """Ejercita el camino REAL de produccion (mismo _ModeloDirecto que usa
    Grabador) contra una sola modelo, sin tocar grabar.txt ni estado\\.
    Es el gate de specs\\grabador_directo.md S7 paso 2: al final,
    'python detectar_m3u8.py --unir <sesion>' (invocado solo, aca adentro)
    tiene que dejar un .mp4 indistinguible del que sale del navegador."""
    global BASE, STREAMS_DIR
    if salida_base:
        # absoluta a proposito: el --unir que cierra cada sesion corre en
        # un SUBPROCESO con su propio cwd (cwd=AQUI, ver _unir_en_fondo) --
        # una ruta relativa se resuelve distinto ahi que en este proceso y
        # el hijo no encuentra la sesion (visto en vivo: --unir devolvia 0
        # sin unir nada, "listo" mentía). Absoluta, los dos coinciden.
        salida_base = os.path.abspath(salida_base)
        os.environ["M3U8_BASE"] = salida_base   # para que el --unir hijo coincida
        BASE = salida_base
        STREAMS_DIR = os.path.join(BASE, "streams")
    os.makedirs(STREAMS_DIR, exist_ok=True)
    log.info("[UNA-VEZ] %s por %.1f min, base=%s, vpn=%s", nombre, minutos, BASE, vpn)

    if not mouflon.verificar_canario():
        log.error("[UNA-VEZ] el canario de claves fallo: abortar")
        return 1

    class _LectorFalso:
        def actual(self):
            return _ConfigGeneral()

    class _GrabadorFalso:
        def __init__(self):
            self.escritor = Escritor()
            self.escritor.start()
            self.pool = cf.ThreadPoolExecutor(max_workers=6)
            self.seleccion = _LectorFalso()
            self.ids_cache = {}

    g = _GrabadorFalso()
    m = _ModeloDirecto(nombre, g, por_tunel=vpn)
    m.start()
    try:
        time.sleep(minutos * 60)
    finally:
        m.parar()
        m.join(60)
        g.pool.shutdown(wait=True)
        g.escritor.detener()
    log.info("[UNA-VEZ] listo -- revisa %s\\streams\\ (unido.json si ya se unio "
             "solo)", BASE)
    return 0


def main(argv) -> int:
    configurar_log()
    ap = argparse.ArgumentParser(
        description="Graba streams de doppiocdn sin navegador (camino directo).")
    ap.add_argument("--una-vez", metavar="MODELO", default=None,
                     help="diagnostico: graba una sola modelo un tiempo fijo, "
                          "sin tocar grabar.txt ni estado\\")
    ap.add_argument("--minutos", type=float, default=5.0,
                     help="duracion de --una-vez (defecto: 5)")
    ap.add_argument("--salida", default=None,
                     help="M3U8_BASE alternativo para --una-vez")
    ap.add_argument("--vpn", action="store_true",
                     help="--una-vez: salir por el tunel de wireproxy")
    args = ap.parse_args(argv)

    if args.una_vez:
        return _correr_una_vez(args.una_vez, args.minutos, args.salida, args.vpn)

    grabador = Grabador()
    try:
        grabador.correr()
    except KeyboardInterrupt:
        grabador.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
