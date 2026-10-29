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

# Tope de la request /activar esperando a que entregar_destino() le pase
# la URL final (SPEC 10). Sin tope, un main() que tarda en levantar el
# servidor real deja la pagina de activacion congelada en "Validando..."
# para siempre.
TOPE_DESTINO_S = 30

_cerrar_lock = threading.Lock()


def _log(mensaje):
    print("[NIMBO]", mensaje, flush=True)


class _Pedido(BaseHTTPRequestHandler):
    # inyectados por pantalla()
    dir_estado = ""
    token = ""
    terminado = None
    destino_evento = None
    respondido = None
    destino_url = None
    servidor = None
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
                # La clave ya vale: se libera terminado ACA, no despues de
                # responder (SPEC 10). pantalla() puede retomar main() ya
                # mismo mientras esta request sigue en vuelo esperando a
                # entregar_destino() -- el hilo que la atiende es propio de
                # ThreadingHTTPServer, no bloquea a nadie mas.
                _Pedido.resultado = True
                _Pedido.terminado.set()
                llego = _Pedido.destino_evento.wait(TOPE_DESTINO_S)
                resp = dict(r)
                if llego and _Pedido.destino_url:
                    resp["url"] = _Pedido.destino_url
                self._json(200, resp)
                _Pedido.respondido.set()
                if not llego:
                    # Vencio el tope sin que entregar_destino() corriera:
                    # nadie mas va a cerrar este servidor.
                    cerrar()
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
    usuario desistio. Si da True, el servidor de activacion **sigue
    vivo** en segundo plano (SPEC 10): queda una request /activar en
    vuelo esperando a que quien llamo a pantalla() le pase el destino
    final con entregar_destino(), para que la misma ventana navegue ahi
    en vez de abrir una segunda. Si da False no queda nada pendiente y
    el servidor ya se cerro.
    """
    token = secrets.token_urlsafe(24)
    _Pedido.dir_estado = dir_estado
    _Pedido.token = token
    _Pedido.terminado = threading.Event()
    _Pedido.destino_evento = threading.Event()
    _Pedido.respondido = threading.Event()
    _Pedido.destino_url = None
    _Pedido.resultado = False

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Pedido)
    _Pedido.servidor = servidor
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
        _Pedido.terminado.wait()
    except KeyboardInterrupt:
        _Pedido.resultado = False

    if not _Pedido.resultado:
        # Usuario desistio: no hay destino que esperar, se cierra ahora.
        cerrar()
    return bool(_Pedido.resultado)


def entregar_destino(url, tope_s=TOPE_DESTINO_S) -> bool:
    """Le pasa a la request /activar que sigue en vuelo la URL final del
    servidor real, para que la pagina navegue sola (``location.replace``)
    en vez de que Nimbo abra una ventana nueva.

    True si la respuesta llego a salir con esa URL adentro. False si ya
    no hay nadie esperando -- la ventana de activacion se cerro, o la
    request ya respondio sin url porque vencio su propio tope -- y en
    ese caso quien llama cae al abrir_ventana(url) de siempre.
    """
    if not _Pedido.resultado or _Pedido.respondido.is_set():
        cerrar()
        return False
    _Pedido.destino_url = url
    _Pedido.destino_evento.set()
    llego = _Pedido.respondido.wait(tope_s)
    cerrar()
    return bool(llego and _Pedido.destino_url == url)


def cerrar():
    """Apaga el servidor de activacion. Idempotente y segura desde
    cualquier hilo: la llaman pantalla() (si el usuario desistio),
    entregar_destino() (ya entregado o vencido) y la propia request
    /activar (si vencio el tope sin que entregar_destino() corriera)."""
    with _cerrar_lock:
        servidor = _Pedido.servidor
        if servidor is None:
            return
        _Pedido.servidor = None
    servidor.shutdown()
    servidor.server_close()
