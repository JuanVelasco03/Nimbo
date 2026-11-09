# -*- coding: utf-8 -*-
"""cerrojo_union.py

Turno unico de union para toda la instalacion (SPEC 19): que no haya nunca
mas de una union a la vez entre el detector, el camino directo, chaturbate y
el `--unir` a mano.

Mismo criterio que hace seguros a mouflon.py y chaturbate.py: protocolo puro,
importable desde cualquier lado sin efecto alguno. No abre hilos, no escribe
en disco, no tiene estado de modulo mutable mas alla de un Lock. Solo
libreria estandar: lo importan por igual detectar_m3u8.py (que no se
compila) y los dos grabadores (que si van al binario).

El turno es un mutex nombrado de Windows (CreateMutexW por ctypes), no un
archivo de cerrojo: Windows lo suelta solo cuando el proceso dueño muere, asi
que no hay cerrojo rancio que limpiar a mano ni heartbeat que mantener. El
nombre depende de STREAMS_DIR para que una instalacion de prueba
(NIMBO_BASE/M3U8_BASE distinto) no se bloquee con la real, ni al reves.

Reentrancia a traves del limite de proceso: el subproceso `--unir` que lanzan
el directo y chaturbate hereda por entorno (NIMBO_UNION_TURNO) el hecho de
que su padre ya tiene el turno, y no vuelve a pedirlo -- si lo pidiera, se
trabaria con su propio padre. Esa variable se lee UNA sola vez, al importar
el modulo (_HEREDADO), para que una escritura posterior de este mismo
proceso (al tomar el turno el mismo) no se confunda con un turno heredado.
"""

import contextlib
import ctypes
import hashlib
import logging
import os
import sys
import threading
import time

NOMBRE_ENTORNO = "NIMBO_UNION_TURNO"

# Tope absoluto de espera, en segundos. NO es el caso normal: en operacion
# normal el turno se espera lo que haga falta (modelo 1, despues 2, despues
# 3). Existe solo como red contra un turno filtrado -- algo que Windows ya
# hace casi imposible, porque suelta el mutex cuando el proceso dueño muere
# (y el que espera recibe WAIT_ABANDONED, que igual le da la propiedad). 3 h
# es mas del doble del peor caso medido: 7 GB frenados a RITMO_UNION por
# 1 + MAX_REPARACIONES pasadas = ~39 min.
ESPERA_TURNO = 10800.0

# Cada cuanto se despierta WaitForSingleObject para mirar el evento de
# parada. Un segundo: no se nota en una espera de minutos y hace que la
# parada no tenga que esperar a que termine la union ajena.
TAJADA_ESPERA = 1.0

log = logging.getLogger(__name__)

_local = threading.Lock()

# Presencia (no valor exacto) de la variable: alcanza, porque un proceso
# hijo solo ve esta variable si SU PROPIO padre la puso al tomar el turno
# para la misma instalacion -- nunca hay un --unir heredando el turno de una
# instalacion distinta.
_HEREDADO = bool(os.environ.get(NOMBRE_ENTORNO))

_avisado_sin_turno = False

if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
else:
    _kernel32 = None


def nombre_turno(raiz: str) -> str:
    """'Local\\NimboUnion.<hash de raiz>'. raiz = STREAMS_DIR.

    "Local\\" = sesion del usuario: los procesos de Nimbo corren en la
    misma. El hash (primeros 16 hex de sha256) evita nombres kilometricos y
    hace que dos raices distintas (instalacion real vs. un NIMBO_BASE /
    M3U8_BASE de prueba) nunca compartan turno.
    """
    normalizada = os.path.normcase(os.path.abspath(raiz or ""))
    h = hashlib.sha256(normalizada.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return "Local\\NimboUnion.%s" % h


def _avisar_sin_turno(logger: logging.Logger) -> None:
    global _avisado_sin_turno
    if _avisado_sin_turno:
        return
    _avisado_sin_turno = True
    logger.warning(
        "[UNIR] turno: no se pudo tomar un mutex del sistema (ctypes/"
        "CreateMutexW no disponible) -- sigo sin turno, como antes de "
        "SPEC 19"
    )


@contextlib.contextmanager
def turno(etiqueta: str, parar=None, raiz: str = "", log: logging.Logger = None):
    """Un turno de union. Nadie mas une mientras este abierto.

    - Si lo heredo de un padre (_HEREDADO), no hace nada: el padre lo tiene.
    - Si ctypes/CreateMutexW falla (o no es Windows), avisa UNA vez y sigue
      sin turno: degrada exactamente al comportamiento de antes de este
      spec, nunca deja una sesion sin unir.
    - `parar`: threading.Event opcional. Si se pone mientras se espera, se
      deja de esperar y se une igual (parada en curso: nada esta grabando).
    - `log`: logger del llamador (el de "detector"/"directo"/"chaturbate"),
      para que la linea de turno termine en el mismo eventos_*.jsonl que el
      resto de esa union. Por defecto el logger de este modulo.
    """
    logger = log or globals()["log"]

    if _HEREDADO:
        yield
        return

    with _local:
        handle = None
        tomado_por_mi = False
        avisado = False
        espera = 0.0
        try:
            if _kernel32 is not None:
                nombre = nombre_turno(raiz)
                h = _kernel32.CreateMutexW(None, False, nombre)
                if h:
                    handle = h
                else:
                    _avisar_sin_turno(logger)
            else:
                _avisar_sin_turno(logger)

            if handle:
                tajada_ms = int(TAJADA_ESPERA * 1000)
                while True:
                    resultado = _kernel32.WaitForSingleObject(handle, tajada_ms)
                    if resultado in (WAIT_OBJECT_0, WAIT_ABANDONED):
                        tomado_por_mi = True
                        if resultado == WAIT_ABANDONED:
                            logger.warning(
                                "[UNIR] turno %s: el dueño anterior murio con "
                                "el turno tomado -- lo heredo", etiqueta)
                        break
                    if resultado == WAIT_TIMEOUT:
                        if not avisado:
                            logger.info(
                                "[UNIR] turno %s: esperando (otra union en "
                                "curso)", etiqueta)
                            avisado = True
                        espera += TAJADA_ESPERA
                        if parar is not None and parar.is_set():
                            logger.info(
                                "[UNIR] turno %s: parada pedida tras %.0f s "
                                "de espera -- uno sin esperar mas", etiqueta,
                                espera)
                            break
                        if espera >= ESPERA_TURNO:
                            logger.error(
                                "[UNIR] turno %s: tope de espera (%.0f s) "
                                "agotado -- uno igual, sin turno", etiqueta,
                                ESPERA_TURNO)
                            break
                        continue
                    # WAIT_FAILED u otro valor inesperado: no se puede
                    # esperar este mutex. Degradar sin turno para esta
                    # llamada, igual que si ctypes hubiera fallado antes.
                    logger.warning(
                        "[UNIR] turno %s: WaitForSingleObject fallo "
                        "(codigo %s) -- uno sin turno", etiqueta,
                        ctypes.get_last_error())
                    break

                if tomado_por_mi and avisado:
                    logger.info(
                        "[UNIR] turno %s: tomado tras %.0f s", etiqueta,
                        espera)

            if tomado_por_mi:
                os.environ[NOMBRE_ENTORNO] = nombre_turno(raiz)

            inicio_turno = time.monotonic()
            yield
        finally:
            os.environ.pop(NOMBRE_ENTORNO, None)
            if handle:
                if tomado_por_mi:
                    try:
                        _kernel32.ReleaseMutex(handle)
                    except OSError:
                        pass
                    logger.info(
                        "[UNIR] turno %s: suelto tras %.0f s", etiqueta,
                        time.monotonic() - inicio_turno)
                _kernel32.CloseHandle(handle)
