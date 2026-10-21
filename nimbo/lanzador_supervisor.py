"""Arranca supervisor.py desde fuera, sin cambiarle una linea.

    python lanzador_supervisor.py

Corre con el Python de sistema (el que tiene Playwright instalado, NO el
que trae empaquetado mitmdump). Hace cuatro cosas que supervisor.py no
ofrece por su cuenta:

  1. Estado por modelo (estado/supervisor.json cada INTERVALO_ESTADO). El
     dict de Emision es local a supervisar() (no se puede leer desde
     fuera), asi que se intercepta con una subclase que se anota sola en
     un registro al construirse -- "supervisor.Emision = _EmisionVigilada"
     antes de llamar a supervisor.supervisar(): como esa funcion busca
     "Emision" en el namespace del propio modulo en cada llamada (no la
     trae "congelada" en una variable local al definirse), el cambio se
     aplica sin tocar el archivo original. Patron de monkeypatch estandar
     de Python, no un truco fragil.
  2. Reenvia los eventos del logger "supervisor" a un .jsonl, igual que
     puente.py hace con el logger "detector".
  3. Parada ordenada: una orden por stdin cancela la tarea de
     supervisar(), lo que dispara su propio 'finally' (cierra cada pestana
     y el contexto de Chromium). Matar el proceso a lo bruto deja Chromium
     huerfano y el perfil con cerrojos -- el arranque siguiente fallaria.
  4. Acorta supervisor.INTERVALO_LISTA (mismo patron de monkeypatch que
     "Emision" arriba): 5s de latencia entre tocar "grabar" en la UI y que
     se abra la pestana era mucho para algo que solo re-statea un archivo
     local. Ver el comentario junto a la asignacion mas abajo.

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

INTERVALO_ESTADO = 2.0   # cadencia propia de supervisor.json, independiente
                          # de INTERVALO_LISTA (ver mas abajo)
# Mismo tope que TOPE_BYTES_BARRIDO de registro.py: un .jsonl entero
# siempre entra en un barrido completo.
MAX_EVENTOS = 8 * 1024 * 1024


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
                try:
                    if os.path.getsize(self.ruta) > MAX_EVENTOS:
                        os.replace(self.ruta, self.ruta + ".1")
                except OSError:
                    pass
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
        self._primera_consulta = True
        with _registro_lock:
            _registro[self.nombre] = self

    async def consultar(self):
        # El goto es lo unico que la grabacion necesita: el detector arranca
        # cuando el reproductor pide el master, no cuando abrir() vuelve.
        # paso_estado() (supervisor.py) serializa consultar() -> abrir(),
        # regalando el round-trip HTTP de la consulta (0.5-2s) antes de
        # emitir la navegacion. Solo en la primera consulta de esta Emision,
        # y solo si no es @vpn= (para no pisar la ventana exclusiva de
        # con_vpn), la lanzamos en paralelo con abrir(): si la consulta dice
        # que esta offline, paso_estado() cierra la pagina al toque --
        # PEGAJOSO es False en Nimbo y sin VPN no hay rama pegajosa -- asi
        # que el costo de equivocarse es una pagina abierta de mas por 1-2s.
        # necesita_vpn ya esta seteado para cuando esto corre: supervisar()
        # lo asigna de forma sincrona antes de que la tarea de bucle() llegue
        # a su primer await.
        if (self._primera_consulta and not self.necesita_vpn
                and not self.pagina):
            self._primera_consulta = False
            abriendo = asyncio.create_task(self.abrir())
            try:
                return await super().consultar()
            finally:
                await abriendo   # nunca dos new_page() a la vez
        return await super().consultar()


supervisor.Emision = _EmisionVigilada

# El bucle maestro de supervisar() solo nota nombres nuevos (o bajas) de
# grabar.txt en su tick, y lee INTERVALO_LISTA como global del modulo en
# cada iteracion del "await asyncio.sleep(INTERVALO_LISTA)" -- mismo
# patron de monkeypatch que Emision arriba. 5s era hasta 5s de latencia
# entre tocar "grabar" en la UI y que se abra la pestana; el tick solo
# hace os.stat() + compara (mtime, size) (Lista.actual), reparseando
# grabar.txt/activos.txt unicamente si cambio -- no cuesta red ni CPU
# medible a 1s. No se bajo mas (p.ej. a 0.2s): el mismo tick tambien
# cierra pestanas de nombres que salieron de la lista, y un editor no
# atomico (no es el caso de Nimbo, que escribe .part + os.replace) podria
# dejar ver un grabar.txt a medio escribir con mas frecuencia.
supervisor.INTERVALO_LISTA = 1.0

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
