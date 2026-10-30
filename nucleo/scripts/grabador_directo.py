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
# Nimbo pasa esta segun lo configurado en "Carpetas y discos" (config.json);
# M3U8_BASE sigue siendo el defecto cuando no viene (SPEC 12).
STREAMS_DIR = os.environ.get("M3U8_STREAMS") or os.path.join(BASE, "streams")
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
# Tope del subproceso --unir, en segundos. La cuenta: una sesion de 7 GB
# leida al ritmo del freno (RITMO_UNION, 25 MB/s) tarda ~4,8 min por pasada,
# y en el peor caso son 1 + MAX_REPARACIONES = 7 pasadas de remux mas la
# copia al escenario: ~39 min. Era un timeout=1200 literal (20 min) y dos
# sesiones de 5,85 y 7,13 GB lo agotaron la noche del 2026-10-29 (SPEC 13);
# con el freno puesto unir tarda mas todavia, asi que el tope sube con el.
ESPERA_UNION = 3600

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

# -- politica de "solo grabar en show publico" ------------------------------
#
# Antes, un 403/404 en la media playlist (show privado/p2p/etc, o el CDN
# rotando el host) dejaba la sesion ABIERTA: _intervalo() seguia devolviendo
# el ritmo de grabacion activa (max(0.5, target_duration/2), ~0.5-1.3s) y
# _paso() seguia llamando _refrescar_todas() en cada vuelta -- un bucle
# caliente de ~1 pedido/s por modelo contra el sitio, medido en vivo: 57% de
# los eventos del camino directo eran ese warning, con medianas de 1.3-1.8s
# entre reintentos (deberia haber sido "ritmo normal, 60s", el propio
# mensaje de log mentia). Y como fila_estado() define grabando como "hay una
# sesion abierta" (no "entran bytes"), el badge seguia diciendo GRABANDO sin
# que llegara un solo byte.
#
# Fuera de show publico, ahora se deja de pedir la media playlist al
# instante (no se llama _refrescar_todas) y se espera a ESPERA_NO_PUBLICO
# antes de volver a mirar. Las sesiones quedan abiertas, congeladas: es
# @inactividad (ya existe, ya la configura el usuario, ver
# detectar_m3u8.py:78 y _cerrar_por_inactividad mas abajo) quien las cierra
# y las une si no vuelven a tiempo -- asi una modelo que entra y sale de
# privado en segundos reusa la MISMA sesion en vez de fragmentar un .mp4
# por cada ida y vuelta.
ESTADOS_PUBLICOS = {"public", "live"}
ESPERA_NO_PUBLICO = 30.0
# Solo para @inactividad=0 ("no cortes por falta de bytes" -- una señal
# distinta de "el sitio dice que no es publico"): sin esto una modelo que
# desactivo el corte por inactividad quedaria con la sesion abierta para
# siempre en cuanto el sitio deja de reportar publico.
ESPERA_CIERRE_NO_PUBLICO = 120.0
# Backoff propio de la media playlist cuando el status sigue mal tras pedir
# un master nuevo (antes: ~1-2s sin tope, ver _refrescar_variante).
ESPERA_STATUS_MALO = 5.0

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


_LINEAS_UNION = ("[LISTO]", "[UNIR]", "[SALTO]", "[ERROR]", "[DISCO]")


def _resumen_union(proc, tope: int = 3) -> str:
    """Las ultimas lineas interesantes de lo que escribio `--unir`. Antes
    esa salida se descartaba salvo en los branches de error, asi que el
    log del directo no decia QUE habia unido el hijo (y con el filtro
    laxo de antes podia ser la sesion de otra modelo).

    Mira stderr Y stdout: el hijo loguea con el StreamHandler de
    `logging`, que escribe en **stderr**, asi que leer solo stdout dejaba
    el resumen siempre vacio."""
    texto = ((getattr(proc, "stderr", "") or "")
             + (getattr(proc, "stdout", "") or ""))
    utiles = [l.strip() for l in texto.splitlines()
              if any(m in l for m in _LINEAS_UNION)]
    return " | ".join(utiles[-tope:]) if utiles else "(el hijo no dijo nada)"


def _unir_en_fondo(escritor: "Escritor", sesion) -> None:
    """Delega la union a detectar_m3u8.py --unir como SUBPROCESO: correr
    como __main__ no arma un Detector (ver docstring del modulo) y hereda
    el Reencadenador, los 6 intentos de reparacion, _hay_espacio y
    _marcar_unida sin duplicar nada de eso aca.

    El "listo" se gana comparando el mtime del marcador ANTES y DESPUES,
    no con un os.path.isfile pelado: `--unir` saltea sin ruido cualquier
    sesion que ya tenga un unido.json (`s.unida and not forzar`), asi que
    con un marcador viejo encima el isfile daba True y se logueaba "listo"
    sin haber unido una sola cosa -- y el resto de la grabacion no se une
    nunca mas. Visto en vivo el 2026-10-24: cuatro sesiones con un
    unido.json prematuro de 2 min sobre grabaciones de 8 a 43 min, todas
    con su "listo" en el log."""
    if not escritor.barrera().wait(180):
        log.warning("[UNIR] %s: escrituras pendientes, sigo igual", sesion.ref)
    filtro = sesion.ref.lower()
    cmd = [PYTHON_EXE, DETECTAR_PY, "--unir", filtro]
    marcador = os.path.join(sesion.carpeta, MARCADOR)
    try:
        antes = os.path.getmtime(marcador)
    except OSError:
        antes = None
    try:
        # stdin=DEVNULL a proposito: sin esto el subproceso hereda el
        # stdin DE ESTE PROCESO, que es el pipe que motor.py mantiene
        # abierto para mandar "parar" (ver lanzador_directo.py) -- un
        # pipe que nadie en esta rama va a cerrar ni escribir. Nada en
        # --unir lee stdin a proposito, pero no hay motivo para heredar
        # un handle ajeno en un subproceso que no lo necesita.
        proc = subprocess.run(cmd, cwd=AQUI, env=dict(os.environ),
                               capture_output=True, text=True,
                               timeout=ESPERA_UNION,
                               stdin=subprocess.DEVNULL, **_SIN_VENTANA)
        if proc.returncode != 0:
            log.error("[UNIR] %s: --unir fallo (codigo %s): %s", sesion.ref,
                      proc.returncode, (proc.stderr or proc.stdout or "")[-400:])
        elif not os.path.isfile(marcador):
            # --unir devuelve 0 igual si no encontro ninguna sesion que
            # coincida con el filtro (p.ej. M3U8_BASE relativo resuelto
            # distinto en este subproceso) -- "returncode == 0" solo no
            # prueba que de verdad unio algo.
            log.error(
                "[UNIR] %s: --unir termino sin error pero no generó %s -- "
                "revisa M3U8_BASE (tiene que ser una ruta ABSOLUTA) -- "
                "salida: %s", sesion.ref, MARCADOR, _resumen_union(proc))
        else:
            try:
                ahora = os.path.getmtime(marcador)
            except OSError:
                ahora = None
            if antes is not None and ahora == antes:
                log.error(
                    "[UNIR] %s: NO se unio -- ya tenia un %s de antes y "
                    "--unir la salteo, asi que lo grabado despues de ese "
                    "marcador quedo sin unir. Rehacela a mano: "
                    "python %s --unir --forzar \"%s\" -- salida: %s",
                    sesion.ref, MARCADOR, os.path.basename(DETECTAR_PY),
                    sesion.ref, _resumen_union(proc))
            else:
                log.info("[UNIR] %s: listo | %s", sesion.ref,
                         _resumen_union(proc))
    except subprocess.TimeoutExpired:
        log.error("[UNIR] %s: --unir no termino en ESPERA_UNION (%d s); la "
                  "sesion queda sin %s y recuperar_pendientes() la puede "
                  "retomar", sesion.ref, ESPERA_UNION, MARCADOR)
    except OSError as e:
        log.error("[UNIR] %s: no se pudo lanzar --unir: %s", sesion.ref, e)


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
                 "init_nombre", "segmentos", "bytes", "inicio", "vistos",
                 "ultimo_segmento")

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
        # 0.0 a proposito, no time.time(): el vigilante de @inactividad
        # (ver _cerrar_por_inactividad) no cierra una sesion que todavia
        # no bajo nada, mismo criterio que Stream.ultimo_segmento en
        # detectar_m3u8.py (linea ~2581, "and st.ultimo_segmento").
        self.ultimo_segmento = 0.0

    @property
    def etiqueta(self) -> str:
        return os.path.basename(self.carpeta)

    @property
    def ref(self) -> str:
        """"<modelo>_<id>/<variante>_<timestamp>" -- lo que se le pasa a
        "detectar_m3u8.py --unir" como filtro EXACTO (ver
        detectar_m3u8._coincide_filtro). NO alcanza con .etiqueta: no
        lleva modelo ni id, y como motor.py arranca todas las modelos en
        el mismo segundo, el substring coincidia tambien con las sesiones
        VIVAS de las otras y se las unia a medio grabar."""
        return "%s/%s" % (os.path.basename(os.path.dirname(self.carpeta)),
                          os.path.basename(self.carpeta))


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
        self._forzado_por_variante = {}   # variante -> ya se forzo un
                                           # master nuevo en esta racha
        self._proximo_intento = {}        # variante -> backoff propio tras
                                           # un status malo persistente
        self._futuros = {}       # future -> (variante, url, es_init)
        self._lock = threading.Lock()
        # timestamp de cuando se dejo de ver show publico (0.0 = no esta
        # esperando). Las sesiones quedan abiertas y congeladas mientras
        # tanto -- ver ESTADOS_PUBLICOS mas arriba.
        self.esperando_publico = 0.0

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
        if self.esperando_publico:
            return ESPERA_NO_PUBLICO
        return max(0.5, self.target_duration / 2.0) if self.sesiones else 5.0

    def _en_vivo(self) -> bool:
        if self.estado_cam in mouflon.ESTADOS_VIVOS:
            return True
        # una consulta que no pudo contestar no dice que dejo de emitir
        return mouflon.estado_incierto(self.estado_cam)

    def _publico(self) -> bool:
        """Solo "public"/"live" son show publico de verdad. privado/p2p/
        groupShow/ticketShow siguen contando para _en_vivo() (no hay que
        soltar el id ni dejar de consultar el estado) pero no se les pide
        mas la media playlist -- ver el comentario de ESTADOS_PUBLICOS."""
        return self.estado_cam in ESTADOS_PUBLICOS

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
            self._chequear_estado()
            # mientras se espera el regreso a publico, confirmar cada
            # ESPERA_NO_PUBLICO (no CHEQUEO_ESTADO/60s): si no, "volvio a
            # publico" podia tardar hasta el doble de lo que tarda en
            # notarse que se fue.
            self.proximo_chequeo = ahora + (
                ESPERA_NO_PUBLICO if self.esperando_publico else self.CHEQUEO_ESTADO)
            if not self.degradado and self._en_vivo() and self.id_modelo:
                self._reconciliar_variantes(cfg_general)

        if not self._en_vivo():
            if self.sesiones:
                self._cerrar_todas("la modelo no esta en vivo (%s)" % self.estado_cam)
            self.esperando_publico = 0.0
            return

        # @inactividad (ya existe, ya la usa el detector) es quien de
        # verdad cierra y une las sesiones sin bytes recientes, tanto si
        # el motivo es un CDN que dejo de responder en show publico como
        # si es que se fue a privado -- correrlo ANTES de decidir la rama
        # de abajo para que no haga falta un segundo temporizador.
        self._cerrar_por_inactividad(cfg_general)

        if mouflon.estado_incierto(self.estado_cam):
            # la consulta de estado fallo (429/5xx/sin red): no dice nada
            # de si la modelo sigue en publico o no. No se decide nada,
            # se sigue como se venia -- cortar aca cerraria TODAS las
            # grabaciones a la vez ante un hipo del sitio.
            if self.sesiones:
                self._refrescar_todas()
            return

        if not self._publico():
            if not self.esperando_publico:
                self.esperando_publico = ahora
                log.info("[%s] no esta en show publico (%s) -- congelo y "
                         "reintento cada %ds", self.nombre, self.estado_cam,
                         int(ESPERA_NO_PUBLICO))
            # ni se pide la media playlist ni se reconcilian variantes:
            # las sesiones quedan como estan, congeladas, hasta que vuelva
            # a publico o las cierre @inactividad de arriba.
            return

        if self.esperando_publico:
            log.info("[%s] volvio a show publico", self.nombre)
            self.esperando_publico = 0.0

        self._refrescar_todas()

    def _cerrar_por_inactividad(self, cfg_general: "_ConfigGeneral") -> None:
        """Mismo criterio que el vigilante del detector
        (detectar_m3u8.py:2580-2584, @inactividad): sin un segmento real
        hace mas de N segundos, cerrar la variante y encolarla para unir.

        A diferencia del detector, no hace falta resetear
        sesion.ultimo_segmento al cerrar (sus lineas 2450/2609):
        _cerrar_variante() destruye la _SesionDirecta entera, y la que
        la reemplace nace en 0.0 (ver _SesionDirecta.__init__).

        @inactividad=0 significa "no cortes por falta de bytes" -- una
        señal distinta de "el sitio dice que no es publico". Fuera de
        show publico igual hace falta cortar alguna vez (si no, la sesion
        queda abierta para siempre esperando un segmento que no va a
        llegar): ESPERA_CIERRE_NO_PUBLICO cubre ese caso puntual.
        """
        if not self.sesiones:
            return
        limite = cfg_general.inactividad
        if not limite and self.esperando_publico:
            limite = ESPERA_CIERRE_NO_PUBLICO
        if not limite:
            return
        ahora = time.time()
        for variante, sesion in list(self.sesiones.items()):
            # Normalmente (como el detector) solo cuenta un segmento REAL.
            # Pero _reconciliar_variantes() puede abrir una sesion nueva
            # (su propio chequeo de privado via altura de rendicion, mas
            # viejo que esperando_publico, puede no coincidir en el mismo
            # instante) justo cuando ya se sabe por el sitio que no es
            # publico: esa sesion nace en ultimo_segmento=0.0 y, sin este
            # fallback, el "and sesion.ultimo_segmento" de abajo la
            # dejaria abierta para siempre (nunca llega un segmento
            # porque _paso() no la refresca mientras esperando_publico).
            # Mientras se esta esperando, sesion.inicio sirve de
            # referencia para esa sesion sin bytes.
            referencia = sesion.ultimo_segmento or (
                sesion.inicio if self.esperando_publico else 0.0)
            if referencia and ahora - referencia > limite:
                self._cerrar_variante(
                    variante, "sin segmentos hace %ds" % int(limite))

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
        ahora = time.time()
        if ahora < self._proximo_intento.get(variante, 0.0):
            return   # backoff propio tras un status malo persistente, ver abajo
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
            # Un status HTTP (403/404/5xx) en la media playlist casi
            # siempre es la uri/host quedando vieja -- pasa solo al volver
            # de un show privado o cuando el CDN rota el host -- y se
            # arregla pidiendo un master nuevo, NO reintentando la misma
            # uri. Antes se reintentaba la misma uri cada ~1-2s hasta el
            # proximo _reconciliar_variantes (hasta 60s): ruido. Un intento
            # (contarlo como _registrar_fallo, como publicidad/MAP-MOUFLON
            # faltante) tambien estaba mal: eso degrada a los 3 seguidos
            # (3-6s) y un 403 transitorio dejaba a la modelo "offline"
            # para siempre pese a seguir en vivo -- degradado es solo para
            # cuando el CONTENIDO prueba que el protocolo esta roto de
            # verdad, no para un status HTTP que un master nuevo resuelve.
            #
            # Forzar la reconciliacion una vez (self.proximo_chequeo = 0.0)
            # resuelve el caso comun en el ciclo que sigue. Pero si YA se
            # forzo para esta racha y el master nuevo tampoco sirve -- una
            # modelo en show p2p puede seguir dando 403 en la media
            # playlist sin que el master cambie nunca lo suficiente, visto
            # en vivo: 100+ pedidos seguidos al sitio sin pausa -- paso a
            # un backoff propio de ESPERA_STATUS_MALO (antes se reintentaba
            # sin tope, ~1-2s) y vuelvo a forzar la consulta de estado:
            # si el sitio confirma que ya no es show publico, _paso() entra
            # a la rama "esperando_publico" y deja de pedir esta playlist
            # del todo. _forzado_por_variante se limpia solo en el primer
            # 200 que llegue.
            if self._forzado_por_variante.get(variante):
                log.warning(
                    "[%s/%s] media playlist dio %s otra vez tras pedir un "
                    "master nuevo -- reintento cada %ds y pido el estado",
                    self.nombre, variante, status, int(ESPERA_STATUS_MALO))
                self._proximo_intento[variante] = ahora + ESPERA_STATUS_MALO
                self.proximo_chequeo = 0.0
            else:
                log.warning("[%s/%s] media playlist dio %s -- pido un master nuevo",
                            self.nombre, variante, status)
                self.proximo_chequeo = 0.0
                self._forzado_por_variante[variante] = True
            return
        self._forzado_por_variante[variante] = False
        self._proximo_intento.pop(variante, None)
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
            sesion.ultimo_segmento = time.time()
        if sesion.segmentos % 20 == 0:
            log.info("[%s/%s] %d segmentos | %.1f MB | %.1f min", self.nombre,
                     variante, sesion.segmentos, sesion.bytes / 1048576,
                     (time.time() - sesion.inicio) / 60)

    # -- cierre de sesion ---------------------------------------------------

    def _cerrar_variante(self, variante: str, motivo: str) -> None:
        sesion = self.sesiones.pop(variante, None)
        self.uris.pop(variante, None)
        self.fallos.pop(variante, None)
        self._forzado_por_variante.pop(variante, None)
        self._proximo_intento.pop(variante, None)
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
        log.info("[%s/%s] cierre: %s (%d segmentos) -- %s | encolado para unir",
                 self.nombre, variante, sesion.etiqueta, sesion.segmentos, motivo)
        # A la cola de union (self.grabador.cola_union), NO al pool de
        # descargas ni un hilo suelto por cierre: ver _bucle_union para el
        # porque completo (saturacion del pool y, peor, varios --unir
        # corriendo a la vez pisandose el mismo detector.log).
        self.grabador.cola_union.put((self.grabador.escritor, sesion))

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
        # "grabando" se deja con el mismo significado de siempre ("hay una
        # sesion abierta", igual que Stream.grabando del detector -- lo
        # consume app.py para el conteo del tooltip de bandeja). La mentira
        # de "grabando" sin bytes esta acotada por @inactividad y, sobre
        # todo, es visible: ultimo_segmento de abajo es lo que el frontend
        # usa para pintar SIN SEÑAL encima de este mismo badge.
        if grabando:
            estado = "grabando"
        elif self.degradado:
            estado = "degradado"
        elif self.esperando_publico:
            estado = "esperando"
        elif self.privado:
            estado = "privado"
        elif self._en_vivo():
            estado = "detectado"
        else:
            estado = "inactivo"
        variantes = sorted(self.sesiones)
        inicios = [s.inicio for s in self.sesiones.values()]
        ultimos = [s.ultimo_segmento for s in self.sesiones.values()
                   if s.ultimo_segmento]
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
            "ultimo_segmento": max(ultimos) if ultimos else None,
            "estado_cam": self.estado_cam,
            "esperando": bool(self.esperando_publico),
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
        self.cola_union: queue.Queue = queue.Queue()
        self._modelos = {}       # nombre_lower -> _ModeloDirecto
        self._lock = threading.RLock()
        self._parar = threading.Event()   # deja de grabar
        # Separado de _parar: _bucle_estado solia morir en cuanto se pedia
        # la parada, justo cuando empezaba lo que mas tarda (la union,
        # ESPERA_CIERRE_DIRECTO = hasta 1260s en motor.py). directo.json
        # quedaba congelado con "grabando": true para todas las modelos
        # que estaban grabando -- medido en vivo: 20 minutos con el
        # archivo vencido. _fin es la señal real de "dejar de latir",
        # recien al final de correr().
        self._fin = threading.Event()
        self.degradado_global = False

    def parar(self) -> None:
        self._parar.set()

    # -- union de sesiones cerradas: una cola, un solo hilo -----------------
    #
    # Antes cada cierre de sesion mandaba _unir_en_fondo al pool de
    # descargas (quedaba esperando un hueco que con varias modelos
    # grabando a la vez no llegaba nunca) y despues a un threading.Thread
    # suelto por cierre (si dos o tres modelos se sacaban de grabar.txt
    # casi juntas, dos o tres `--unir` corrian a la vez: cada uno abre su
    # propio logging.FileHandler sobre el MISMO detector.log y llama a
    # sesiones_en_disco(), que barre TODAS las carpetas de datos\streams\
    # -- incluidas las de las modelos que siguen grabando -- siempre, sin
    # relacion con el filtro que se le paso). Una cola con un solo hilo
    # consumidor saca la concurrencia de la ecuacion: las uniones se
    # procesan de una, en el orden en que se cerraron las sesiones, y la
    # siguiente modelo puede seguir grabando sin que su union compita por
    # nada con la de otra.

    def _bucle_union(self) -> None:
        while True:
            item = self.cola_union.get()
            if item is None:
                return
            escritor, sesion = item
            try:
                _unir_en_fondo(escritor, sesion)
            except Exception as e:
                log.error("[UNIR] %s: %s", sesion.ref, e)

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
        hilo_union = threading.Thread(target=self._bucle_union,
                                      daemon=True, name="directo-union")
        hilo_union.start()

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
        # Para aca ya estan encoladas todas las uniones que van a salir de
        # este cierre (cada _cerrar_todas() de arriba las puso en
        # cola_union de forma sincronica, antes de que run() del modelo
        # termine) -- se les da el mismo presupuesto que motor.py calcula
        # para la parada completa (ESPERA_CIERRE_DIRECTO) antes de cortar.
        self.cola_union.put(None)
        hilo_union.join(1200)
        self.pool.shutdown(wait=True)
        self.escritor.detener()
        self._fin.set()   # recien aca deja de latir _bucle_estado
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
        # _fin, no _parar: ver el comentario de __init__. Mientras se
        # esta parando pero todavia no se termino de unir, este bucle
        # sigue latiendo -- self._modelos ya esta vacio para entonces
        # (correr() lo vacia antes de esperar la union), asi que
        # _publicar_estado() ya sale solo con streams=[] por si mismo.
        while not self._fin.wait(INTERVALO_ESTADO):
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
            # visibles en la ventana mientras dura la parada (puede ser
            # @inactividad=120s por sesion, y la union es secuencial: se
            # midieron 20 minutos para 10 sesiones) -- sin esto Nimbo
            # parecia colgado con "streams": [] y nada que lo explique.
            "parando": self._parar.is_set(),
            "uniendo": self.cola_union.qsize(),
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
        # M3U8_STREAMS (si la produccion la fijo en este mismo entorno) es mas
        # especifica que M3U8_BASE y ganaria en el --unir hijo, pisando
        # --salida con la carpeta de produccion (SPEC 12): fuera.
        os.environ.pop("M3U8_STREAMS", None)
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
