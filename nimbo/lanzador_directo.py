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


def main():
    grabador_directo.configurar_log()
    logging.getLogger("directo").addHandler(_Eventos(ARCHIVO_EVENTOS))
    os.makedirs(DIR_ESTADO, exist_ok=True)
    _log("lanzador activo -- estado en " +
         os.path.join(DIR_ESTADO, "directo.json"))
    sys.exit(grabador_directo.main([]))


if __name__ == "__main__":
    main()
