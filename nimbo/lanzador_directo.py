"""Arranca grabador_directo.py desde fuera, sin cambiarle una linea.

    python lanzador_directo.py

Corre con el mismo Python que ya arranca el supervisor (a["python"] en
motor.py): no hace falta Playwright ni nada especial, grabador_directo.py
es solo libreria estandar, pero reusar el interprete ya resuelto evita
inventar una tercera forma de encontrar un Python.

A diferencia de lanzador_supervisor.py (que tiene que interceptar clases
internas de supervisor.py con monkeypatch porque ese modulo es ajeno y no
se toca), grabador_directo.py ya es codigo propio: publica su propio
estado\\directo.json y ya escucha su propio stdin para la orden "parar".
Lo unico que le falta, y que este lanzador agrega, es reenviar su logger
("directo") a estado\\eventos_directo.jsonl -- mismo patron que
puente.py/lanzador_supervisor.py usan con "detector"/"supervisor".

Variables de entorno:
  NIMBO_SCRIPTS   carpeta nucleo\\scripts\\ (obligatoria)
  NIMBO_ESTADO    carpeta donde escribir/leer los JSON de estado (si
                     falta, grabador_directo.py asume la instalacion tipica)
  M3U8_BASE, M3U8_GRABAR, M3U8_TUNEL   igual que para el detector
"""

import json
import logging
import os
import sys
import threading

GRABADOR_SCRIPTS = os.environ.get("NIMBO_SCRIPTS", "")
if not GRABADOR_SCRIPTS or not os.path.isdir(GRABADOR_SCRIPTS):
    sys.stderr.write(
        "[NIMBO] falta NIMBO_SCRIPTS (la carpeta nucleo\\scripts\\) "
        "o no existe: %r\n" % GRABADOR_SCRIPTS)
    sys.exit(2)
sys.path.insert(0, GRABADOR_SCRIPTS)

import grabador_directo  # noqa: E402  tiene que ir despues de tocar sys.path

_AQUI = os.path.dirname(os.path.abspath(__file__))
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_AQUI), "estado")
ARCHIVO_EVENTOS = os.path.join(DIR_ESTADO, "eventos_directo.jsonl")
# Mismo tope que puente.py/lanzador_supervisor.py/motor.py (MAX_EVENTOS):
# un .jsonl entero siempre entra en un barrido completo de registro.py.
MAX_EVENTOS = 8 * 1024 * 1024


def _log(mensaje):
    # A stderr y punto, sin pasar por el logging de grabador_directo.py --
    # mismo criterio que puente.py/lanzador_supervisor.py.
    print("[NIMBO] " + mensaje, file=sys.stderr, flush=True)


def _escribir_atomico(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta)


class _Eventos:
    """Handler minimo de logging: copia literal de puente.py/_Eventos y
    lanzador_supervisor.py/_Eventos. Un registro del logger "directo" por
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


# Nuitka le pone __compiled__ a cada modulo que compila. Es el mismo
# marcador que usa ajustes.compilado(); aca se mira el propio globals()
# en vez de importar ajustes para no agregarle al hijo un import que hoy
# no tiene (este modulo corre bajo otro interprete cuando NO esta
# compilado, y ese camino funciona: no se lo toca).
_COMPILADO = "__compiled__" in globals()


def _rutas_compilado():
    """Compilado hay que decirle a grabador_directo donde esta todo.

    Sus constantes se calculan de `__file__`, y adentro del binario ese
    `__file__` es nocional: cae en `<paquete>\\nimbo\\`, no en
    `nucleo\\scripts\\`. Tres cosas quedan mal, y las tres son silenciosas:

      AQUI         el cwd del subproceso de la union
      DETECTAR_PY  <paquete>\\nimbo\\detectar_m3u8.py -- no existe, asi
                   que la union tira FileNotFoundError y la grabacion se
                   queda en segmentos crudos para siempre
      PYTHON_EXE   sys.executable, que en Nuitka standalone apunta a un
                   python.exe que **no existe** (ver ajustes.ejecutable)

    Se parchean desde afuera, que es lo que este lanzador ya hace por
    diseno (igual que lanzador_supervisor.py con las clases del
    supervisor): `grabador_directo.py` no se toca. Son globals del
    modulo y _unir_en_fondo las lee en cada llamada, asi que alcanza con
    pisarlas antes de main().

    grabar.txt no se arregla aca sino con M3U8_GRABAR, que el propio
    modulo ya ofrece como variable de entorno y motor.py le pasa.
    """
    grabador_directo.AQUI = GRABADOR_SCRIPTS
    detectar = os.path.join(GRABADOR_SCRIPTS, "detectar_m3u8.py")
    if os.path.isfile(detectar):
        grabador_directo.DETECTAR_PY = detectar
    else:
        _log("OJO: no encuentro %s -- la union va a fallar" % detectar)
    # El interprete lo resuelve motor.py (es el mismo que usa para el
    # supervisor y para la union del detector) y lo pasa por el entorno:
    # aca no hay forma de encontrarlo sin duplicar ajustes.py.
    python = os.environ.get("NIMBO_PYTHON", "")
    if python and os.path.isfile(python):
        grabador_directo.PYTHON_EXE = python
    else:
        _log("OJO: NIMBO_PYTHON no sirve (%r) -- la union va a fallar"
             % python)
    _log("compilado: union con %s %s"
         % (grabador_directo.PYTHON_EXE, grabador_directo.DETECTAR_PY))


def main():
    if _COMPILADO:
        _rutas_compilado()
    grabador_directo.configurar_log()
    logging.getLogger("directo").addHandler(_Eventos(ARCHIVO_EVENTOS))
    os.makedirs(DIR_ESTADO, exist_ok=True)
    _log("lanzador activo -- estado en " +
         os.path.join(DIR_ESTADO, "directo.json"))
    sys.exit(grabador_directo.main([]))


if __name__ == "__main__":
    main()
