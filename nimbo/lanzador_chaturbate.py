"""Arranca grabador_chaturbate.py desde fuera, sin cambiarle una linea.

    python lanzador_chaturbate.py

Puerto literal de nimbo\\lanzador_directo.py (ver ese archivo para el
porque de cada pieza) -- grabador_chaturbate.py ya es codigo propio, igual
que grabador_directo.py: publica su propio estado\\chaturbate.json y ya
escucha su propio stdin para "parar". Lo unico que agrega este lanzador es
reenviar su logger ("chaturbate") a estado\\eventos_chaturbate.jsonl.

Variables de entorno:
  NIMBO_SCRIPTS   carpeta nucleo\\scripts\\ (obligatoria)
  NIMBO_ESTADO    carpeta donde escribir/leer los JSON de estado (si
                     falta, grabador_chaturbate.py asume la instalacion tipica)
  M3U8_BASE, M3U8_GRABAR   igual que para el camino directo (sin M3U8_TUNEL:
                     chaturbate no pasa por el proxy de grabacion)
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

import grabador_chaturbate  # noqa: E402  tiene que ir despues de tocar sys.path

_AQUI = os.path.dirname(os.path.abspath(__file__))
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_AQUI), "estado")
ARCHIVO_EVENTOS = os.path.join(DIR_ESTADO, "eventos_chaturbate.jsonl")
# Mismo tope que puente.py/lanzador_supervisor.py/lanzador_directo.py.
MAX_EVENTOS = 8 * 1024 * 1024


def _log(mensaje):
    print("[NIMBO] " + mensaje, file=sys.stderr, flush=True)


class _Eventos:
    """Copia literal de lanzador_directo.py:_Eventos."""

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
            pass

    def acquire(self):
        pass

    def release(self):
        pass

    def createLock(self):
        pass


# Ver lanzador_directo.py:106-111 para el porque de este marcador.
_COMPILADO = "__compiled__" in globals()


def _rutas_compilado():
    """Mismo parche que lanzador_directo.py:_rutas_compilado, para los
    mismos tres globals rotos adentro del binario (ver ese docstring).
    chaturbate.py no necesita nada de esto: es protocolo puro, sin rutas
    propias (igual que mouflon.py)."""
    grabador_chaturbate.AQUI = GRABADOR_SCRIPTS
    detectar = os.path.join(GRABADOR_SCRIPTS, "detectar_m3u8.py")
    if os.path.isfile(detectar):
        grabador_chaturbate.DETECTAR_PY = detectar
    else:
        _log("OJO: no encuentro %s -- la union va a fallar" % detectar)
    python = os.environ.get("NIMBO_PYTHON", "")
    if python and os.path.isfile(python):
        grabador_chaturbate.PYTHON_EXE = python
    else:
        _log("OJO: NIMBO_PYTHON no sirve (%r) -- la union va a fallar"
             % python)
    _log("compilado: union con %s %s"
         % (grabador_chaturbate.PYTHON_EXE, grabador_chaturbate.DETECTAR_PY))


def main():
    if _COMPILADO:
        _rutas_compilado()
    grabador_chaturbate.configurar_log()
    logging.getLogger("chaturbate").addHandler(_Eventos(ARCHIVO_EVENTOS))
    os.makedirs(DIR_ESTADO, exist_ok=True)
    _log("lanzador activo -- estado en " +
         os.path.join(DIR_ESTADO, "chaturbate.json"))
    sys.exit(grabador_chaturbate.main([]))


if __name__ == "__main__":
    main()
