"""La pantalla de activacion: pedir la clave antes de abrir Nimbo.

Corre **antes** que `servidor.py` y es deliberadamente otro servidor, uno
minimo y descartable:

- No hay toolkit grafico en el proyecto (la interfaz es Edge en modo
  --app) y no vale la pena agregar uno para una pantalla que se ve una
  vez.
- No se puede usar `servidor.py`: levantarlo significa levantar el motor,
  el WebSocket, los bucles de estado y la cosecha -- todo lo que esta
  licencia tiene que decidir si puede arrancar. Meter un modo "sin
  licencia" ahi seria ensuciar el camino caliente para el caso de una
  pantalla que la mayoria de las corridas no ve.

Por eso aca si vale un `ThreadingHTTPServer`: atiende tres rutas, vive
unos segundos y muere. Lo que `CLAUDE.md` descarta es tenerlo como
servidor principal (un hilo por SSE abierto, para siempre), no esto.
"""
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import ajustes as ajustes_mod
import licencia as licencia_mod

PAGINA = os.path.join(ajustes_mod.DIR_WEB, "activacion.html")


def _log(mensaje):
    print("[NIMBO]", mensaje, flush=True)


class _Pedido(BaseHTTPRequestHandler):
    # inyectados por pantalla()
    dir_estado = ""
    token = ""
    terminado = None
    resultado = None

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass          # nada de ruido en la consola

    # -- util ------------------------------------------------------------

    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _token_ok(self, qs):
        return secrets.compare_digest(
            (parse_qs(qs).get("t") or [""])[0], self.token)

    # -- rutas -----------------------------------------------------------

    def do_GET(self):
        partes = urlsplit(self.path)
        if partes.path != "/" or not self._token_ok(partes.query):
            self._json(403, {"error": "no"})
            return
        try:
            with open(PAGINA, "rb") as f:
                cuerpo = f.read()
        except OSError:
            self._json(500, {"error": "falta activacion.html"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):
        partes = urlsplit(self.path)
        if not self._token_ok(partes.query):
            self._json(403, {"error": "no"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            datos = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"error": "cuerpo invalido"})
            return

        if partes.path == "/activar":
            r = licencia_mod.activar(self.dir_estado, datos.get("clave", ""))
            if r["ok"]:
                # Se responde ANTES de soltar al hilo principal: si no, la
                # pagina se queda esperando una respuesta de un servidor
                # que ya se esta apagando.
                self._json(200, r)
                _Pedido.resultado = True
                _Pedido.terminado.set()
            else:
                self._json(200, r)
            return

        if partes.path == "/salir":
            self._json(200, {"ok": True})
            _Pedido.resultado = False
            _Pedido.terminado.set()
            return

        self._json(404, {"error": "no existe"})


def pantalla(dir_estado, abrir_ventana) -> bool:
    """Abre la pantalla y bloquea hasta que se activa o se cierra.

    True si quedo activada (Nimbo puede seguir arrancando), False si el
    usuario desistio.
    """
    token = secrets.token_urlsafe(24)
    terminado = threading.Event()
    _Pedido.dir_estado = dir_estado
    _Pedido.token = token
    _Pedido.terminado = terminado
    _Pedido.resultado = False

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Pedido)
    puerto = servidor.server_address[1]
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True,
                            name="nimbo-activacion")
    hilo.start()

    url = "http://127.0.0.1:%d/?t=%s" % (puerto, token)
    _log("hace falta activar Nimbo: " + url)
    abrir_ventana(url)

    try:
        # Sin tope: que el usuario tarde lo que tarde en pegar la clave.
        # Si cierra la ventana sin activar, Nimbo queda esperando aca --
        # es lo mismo que pasa con la ventana principal y la bandeja.
        terminado.wait()
    except KeyboardInterrupt:
        _Pedido.resultado = False
    finally:
        servidor.shutdown()
        servidor.server_close()
    return bool(_Pedido.resultado)
