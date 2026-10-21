"""Arranca supervisor.py desde fuera, sin cambiarle una linea.

    python lanzador_supervisor.py

Corre con el Python de sistema (el que tiene Playwright instalado, NO el
que trae empaquetado mitmdump). Hace tres cosas que supervisor.py no
ofrece por su cuenta:

  1. Estado por modelo (estado/supervisor.json cada 5s, misma cadencia que
     INTERVALO_LISTA del propio supervisor). El dict de Emision es local a
     supervisar() (no se puede leer desde fuera), asi que se intercepta con
     una subclase que se anota sola en un registro al construirse --
     "supervisor.Emision = _EmisionVigilada" antes de llamar a
     supervisor.supervisar(): como esa funcion busca "Emision" en el
     namespace del propio modulo en cada llamada (no la trae "congelada" en
     una variable local al definirse), el cambio se aplica sin tocar el
     archivo original. Patron de monkeypatch estandar de Python, no un
     truco fragil.
  2. Reenvia los eventos del logger "supervisor" a un .jsonl, igual que
     puente.py hace con el logger "detector".
  3. Parada ordenada: una orden por stdin cancela la tarea de
     supervisar(), lo que dispara su propio 'finally' (cierra cada pestana
     y el contexto de Chromium). Matar el proceso a lo bruto deja Chromium
     huerfano y el perfil con cerrojos -- el arranque siguiente fallaria.

Variables de entorno:
  NIMBO_SCRIPTS   carpeta nucleo\\scripts\\ (obligatoria)
  NIMBO_ESTADO    carpeta donde escribir los JSON de estado (si falta,
                     se asume <raiz de este archivo>/../estado)
  NIMBO_VER       "1" = navegador visible (igual que --ver); por defecto
                     headless
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time

GRABADOR_SCRIPTS = os.environ.get("NIMBO_SCRIPTS", "")
if not GRABADOR_SCRIPTS or not os.path.isdir(GRABADOR_SCRIPTS):
    sys.stderr.write(
        "[NIMBO] falta NIMBO_SCRIPTS (la carpeta nucleo\\scripts\\) "
        "o no existe: %r\n" % GRABADOR_SCRIPTS)
    sys.exit(2)
sys.path.insert(0, GRABADOR_SCRIPTS)

import supervisor  # noqa: E402  tiene que ir despues de tocar sys.path

_AQUI = os.path.dirname(os.path.abspath(__file__))
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_AQUI), "estado")
ARCHIVO_ESTADO = os.path.join(DIR_ESTADO, "supervisor.json")
ARCHIVO_EVENTOS = os.path.join(DIR_ESTADO, "eventos_supervisor.jsonl")
HEADLESS = os.environ.get("NIMBO_VER", "").strip() not in ("1", "si", "true")

INTERVALO_ESTADO = 5.0   # igual que INTERVALO_LISTA del supervisor original


def _log(mensaje):
    # Igual que en puente.py: a stderr y punto, sin pasar por el logging
    # de supervisor.py, para no depender de como este configurado.
    print("[NIMBO] " + mensaje, file=sys.stderr, flush=True)


def _escribir_atomico(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta)


class _Eventos:
    """Handler minimo de logging: un registro del logger "supervisor" por
    linea JSON, con el prefijo [XXX] ya separado del mensaje."""

    def __init__(self, ruta):
        self.ruta = ruta
        self._lock = threading.Lock()
        self.level = 0
        os.makedirs(os.path.dirname(ruta), exist_ok=True)

    def handle(self, record):
        try:
            msg = record.getMessage()
            prefijo, resto = "", msg
            if msg.startswith("["):
                fin = msg.find("]")
                if fin > 0:
                    prefijo, resto = msg[1:fin], msg[fin + 1:].strip()
            fila = {"t": record.created, "nivel": record.levelname,
                    "prefijo": prefijo, "mensaje": resto}
            with self._lock:
                with open(self.ruta, "a", encoding="utf-8") as f:
                    f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        except OSError:
            pass          # perder un evento no es motivo para tirar el hilo

    def acquire(self):
        pass

    def release(self):
        pass

    def createLock(self):
        pass


# -- alcanzar las Emision vivas sin tocar supervisor.py ---------------------

_registro = {}
_registro_lock = threading.Lock()


class _EmisionVigilada(supervisor.Emision):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with _registro_lock:
            _registro[self.nombre] = self


supervisor.Emision = _EmisionVigilada

# Lista propia para saber quien sigue en grabar.txt AHORA: si una modelo se
# quita, su Emision puede seguir viva un rato en el registro de arriba (el
# original no nos avisa cuando la descarta) -- filtrando por esto, nunca se
# publica una tarjeta de alguien que ya no esta.
_lista = supervisor.Lista(supervisor.GRABAR)


def _publicar_estado():
    ahora = time.time()
    try:
        activos = set(_lista.actual())
    except Exception:
        activos = None    # si falla la lectura, mejor publicar todo

    with _registro_lock:
        emisiones = dict(_registro)

    filas = []
    for nombre, em in emisiones.items():
        if activos is not None and nombre not in activos:
            continue
        filas.append({
            "nombre": em.nombre,
            "sid": em.sid,
            "estado": em.estado,
            "pagina_abierta": em.pagina is not None,
            "alto": em.alto,
            "ultimo_avance": em.ultimo_avance or None,
            "fallos": em.fallos,
            "necesita_vpn": em.necesita_vpn,
            "abierta_en": em.abierta_en or None,
        })
    datos = {"generado": ahora, "emisiones": filas,
             "vpn": sorted(_lista.vpn)}
    _escribir_atomico(ARCHIVO_ESTADO, json.dumps(datos, ensure_ascii=False))


async def _bucle_estado(parar_evt):
    while not parar_evt.is_set():
        try:
            _publicar_estado()
        except Exception as e:
            # Esto es solo un espejo del estado real: perder un ciclo no
            # afecta a la grabacion. Nunca tirar el hilo por esto.
            _log("fallo publicando estado (sigue intentando): %r" % (e,))
        try:
            await asyncio.wait_for(parar_evt.wait(), timeout=INTERVALO_ESTADO)
        except asyncio.TimeoutError:
            pass


def _hilo_stdin(loop, tarea_super):
    # Importante para quien lance este proceso (ver nimbo/motor.py): el
    # hijo tiene que abrirse con stdin=PIPE y ese extremo no se cierra
    # hasta que se quiera parar de verdad. Un stdin heredado ya cerrado o
    # invalido se ve igual que un EOF inmediato y para todo al instante --
    # a proposito: no hay forma de distinguir "stdin roto" de "el padre se
    # fue", y seguir grabando sin que nadie lo sepa es peor.
    try:
        for linea in sys.stdin:
            if linea.strip().lower() == "parar":
                break
    except (OSError, ValueError):
        return
    _log("orden de parada recibida -- cerrando las paginas y el navegador")
    loop.call_soon_threadsafe(tarea_super.cancel)


async def _principal():
    tarea_super = asyncio.create_task(
        supervisor.supervisar(headless=HEADLESS), name="supervisar")

    propio_parar = asyncio.Event()
    tarea_estado = asyncio.create_task(
        _bucle_estado(propio_parar), name="nimbo-estado")

    loop = asyncio.get_running_loop()
    if sys.stdin is not None:
        threading.Thread(target=_hilo_stdin, args=(loop, tarea_super),
                         name="nimbo-stdin", daemon=True).start()

    try:
        await tarea_super
    except asyncio.CancelledError:
        pass
    finally:
        propio_parar.set()
        tarea_estado.cancel()
        try:
            await tarea_estado
        except asyncio.CancelledError:
            pass

    _log("supervisor termino, lanzador sale")


def main():
    supervisor.configurar_log()
    logging.getLogger("supervisor").addHandler(_Eventos(ARCHIVO_EVENTOS))
    os.makedirs(DIR_ESTADO, exist_ok=True)
    _log("lanzador activo -- estado en " + ARCHIVO_ESTADO)
    asyncio.run(_principal())


if __name__ == "__main__":
    main()
