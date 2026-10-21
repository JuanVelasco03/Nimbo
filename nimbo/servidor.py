"""Servidor HTTP local de Nimbo: sirve la interfaz, la API de control
(arrancar/parar, la lista de modelos, los ajustes) y los eventos en vivo
por SSE (texto/event-stream).

Solo escucha en 127.0.0.1 y exige un token en cada pedido (?t=... en la
URL): el Chromium que graba sale por el proxy de grabacion y no tiene por
que poder manejar la aplicacion que lo controla.

Los pedidos que pueden tardar (arrancar, parar) no bloquean la respuesta:
se lanzan en un hilo aparte y el progreso real se ve por /eventos. Parar
con una grabacion uniendose puede tardar minutos de verdad; dejar la
conexion HTTP colgada ese tiempo seria fragil (proxies, timeouts del
navegador) y no hace falta: los avisos ya cuentan la historia completa.
"""

import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import ajustes as ajustes_mod
import favoritas as favoritas_mod
import grabaciones as grabaciones_mod
import inicio_automatico as inicio_mod
import lista as lista_mod
import motor as motor_mod
import preflight as preflight_mod
import registro as registro_mod
import sesion as sesion_mod
import sitio as sitio_mod
import vivo as vivo_mod
import vivo_temporal as vivo_temporal_mod


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class _Suscriptores:
    """Pub-sub minimo para /eventos. Una cola por pestana conectada; si
    una se llena (cliente lento o colgado) se descartan sus mensajes mas
    viejos -- nunca se bloquea a quien publica, y nunca afecta a las
    demas pestanas."""

    def __init__(self, tope=200):
        self._colas = set()
        self._lock = threading.Lock()
        self._tope = tope

    def suscribirse(self) -> "queue.Queue":
        q = queue.Queue(maxsize=self._tope)
        with self._lock:
            self._colas.add(q)
        return q

    def desuscribirse(self, q) -> None:
        with self._lock:
            self._colas.discard(q)

    def publicar(self, tipo: str, datos: dict) -> None:
        mensaje = json.dumps(
            {"tipo": tipo, "datos": datos, "t": time.time()},
            ensure_ascii=False)
        with self._lock:
            colas = list(self._colas)
        for q in colas:
            try:
                q.put_nowait(mensaje)
            except queue.Full:
                try:
                    q.get_nowait()         # descarta el mas viejo, sigue
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(mensaje)
                except queue.Full:
                    pass


class Aplicacion:
    """Todo el estado vivo del servidor: ajustes, lista, motor, y el
    canal de eventos que los une con las pestanas abiertas."""

    def __init__(self, ajustes, dir_web):
        self.ajustes = ajustes
        self.dir_web = dir_web
        self.lista = lista_mod.Lista(ajustes.grabar_txt)
        self.favoritas = favoritas_mod.Favoritas(
            os.path.join(ajustes_mod.RAIZ, "favoritas.json"))
        self.sesion = sesion_mod.Sesion(
            os.path.join(ajustes_mod.RAIZ, "sesion.json"))
        self.sitio = sitio_mod.Sitio(ajustes)
        self.suscriptores = _Suscriptores()
        self.token = secrets.token_urlsafe(24)
        self.motor = motor_mod.Motor(ajustes, en_evento=self._en_evento)
        threading.Thread(target=self._bucle_cosecha, name="nimbo-cosecha",
                          daemon=True).start()

    def _bucle_cosecha(self) -> None:
        """Cierra solas las vistas en vivo temporales cuyo navegador se
        cerro de golpe (crash, proceso matado) y nunca llego a mandar el
        POST de /api/vivo/cerrar -- ver vivo_temporal.cosechar."""
        while True:
            time.sleep(10.0)
            try:
                vivo_temporal_mod.cosechar(self.lista, self.ajustes.dir_estado)
            except OSError:
                pass

    def _en_evento(self, tipo, datos) -> None:
        self.suscriptores.publicar(tipo, datos)

    def _disco(self) -> dict:
        try:
            uso = shutil.disk_usage(self.ajustes.dir_datos)
            return {"libres": uso.free, "total": uso.total}
        except OSError:
            return None

    def estado_completo(self) -> dict:
        return {
            "motor": self.motor.estado(),
            "lista": self.lista.leer(),
            "favoritas": self.favoritas.leer(),
            "detector": _leer_json(self.ajustes.estado_detector),
            "supervisor": _leer_json(self.ajustes.estado_supervisor),
            "disco": self._disco(),
            "sesion": self.sesion.estado(),
            "siguiendo": _leer_json(self.ajustes.estado_siguiendo),
            "vivo": vivo_mod.sesiones_vivas(
                self.ajustes.streams, _leer_json(self.ajustes.estado_detector)),
        }


class Handler(BaseHTTPRequestHandler):
    app: Aplicacion = None   # se fija una vez, antes de servir (ver crear_servidor)
    server_version = "Nimbo/0.1"

    def log_message(self, fmt, *args):
        pass    # el log util es el de los hijos (detector/supervisor), no este

    # -- utilidades ---------------------------------------------------

    def _partes(self):
        s = urlsplit(self.path)
        return s.path, parse_qs(s.query)

    def _token_valido(self, qs) -> bool:
        dado = (qs.get("t") or [""])[0]
        return secrets.compare_digest(dado, self.app.token)

    def _json(self, codigo, datos) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _cuerpo_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        datos = self.rfile.read(n) if n else b""
        return json.loads(datos) if datos else {}

    def _archivo_estatico(self, ruta_rel) -> None:
        if not ruta_rel or ruta_rel == "/":
            ruta_rel = "/index.html"
        # Sin ".." ni separadores propios: nada fuera de dir_web.
        partes = [p for p in ruta_rel.split("/") if p and p != ".."]
        destino = os.path.join(self.app.dir_web, *partes)
        if not os.path.isfile(destino):
            self._json(404, {"error": "no encontrado"})
            return
        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
        with open(destino, "rb") as f:
            cuerpo = f.read()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    _RE_RANGO = re.compile(r"bytes=(\d*)-(\d*)")

    def _servir_video(self, ruta) -> None:
        """GET con soporte de Range (206 Partial Content): sin esto el
        <video> puede arrancar pero no puede saltar a la mitad de un
        archivo de varios GB -- el navegador necesita poder pedir un
        rango de bytes para eso. Nunca carga el archivo entero en
        memoria: lee y escribe en bloques de 1 MB."""
        try:
            tam = os.path.getsize(ruta)
        except OSError:
            self._json(404, {"error": "no encontrado"})
            return
        tipo = mimetypes.guess_type(ruta)[0] or "video/mp4"

        inicio, fin, codigo = 0, tam - 1, 200
        cabecera_rango = self.headers.get("Range")
        if cabecera_rango:
            m = self._RE_RANGO.match(cabecera_rango.strip())
            if m:
                codigo = 206
                if m.group(1):
                    inicio = int(m.group(1))
                    fin = int(m.group(2)) if m.group(2) else tam - 1
                elif m.group(2):
                    # "bytes=-500" -- los ultimos 500 bytes
                    inicio = max(0, tam - int(m.group(2)))
                    fin = tam - 1
        inicio = max(0, min(inicio, tam - 1 if tam else 0))
        fin = max(inicio, min(fin, tam - 1 if tam else 0))
        longitud = (fin - inicio + 1) if tam else 0

        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(longitud))
        self.send_header("Cache-Control", "no-store")
        if codigo == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (inicio, fin, tam))
        self.end_headers()

        try:
            with open(ruta, "rb") as f:
                f.seek(inicio)
                restante = longitud
                while restante > 0:
                    bloque = f.read(min(1024 * 1024, restante))
                    if not bloque:
                        break
                    self.wfile.write(bloque)
                    restante -= len(bloque)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass   # el usuario cerro el modal o salto a otra parte a mitad

    def _servir_bytes(self, datos, tipo) -> None:
        """Un fragmento CMAF entero, de una, sin Range: a diferencia de
        _servir_video (pensado para que el usuario salte dentro de un
        .mp4 ya terminado), un init o un segmento de una sesion viva se
        appendea entero a un SourceBuffer -- una respuesta 206 parcial
        rompe appendBuffer. "Accept-Ranges: none" le dice al navegador
        que ni lo intente."""
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "none")
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(datos)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _eventos(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.app.suscriptores.suscribirse()
        try:
            # primer mensaje: estado completo, para que la pestana no
            # tenga que esperar al proximo evento para pintar algo
            inicial = json.dumps({"tipo": "estado_inicial",
                                  "datos": self.app.estado_completo(),
                                  "t": time.time()}, ensure_ascii=False)
            self.wfile.write(("data: " + inicial + "\n\n").encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    mensaje = q.get(timeout=15)
                    self.wfile.write(("data: " + mensaje + "\n\n").encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": latido\n\n")   # mantiene vivas las
                                                         # conexiones intermedias
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.suscriptores.desuscribirse(q)

    # -- rutas -----------------------------------------------------------

    def do_GET(self):
        ruta, qs = self._partes()

        # El token protege la pagina (sin el no se llega ni a abrirla) y
        # todo lo que de verdad importa: la API y los eventos en vivo. Los
        # DEMAS archivos estaticos (app.css, js/main.js y lo que importe...)
        # no lo llevan: el propio index.html y los "import" entre modulos
        # los piden con una URL relativa comun ("app.css", no
        # "app.css?t=..."), asi que exigirselo los dejaria siempre en 403 --
        # y no hace falta, no son sensibles.
        necesita_token = (ruta in ("/", "/index.html") or ruta == "/eventos"
                          or ruta.startswith("/api/"))
        if necesita_token and not self._token_valido(qs):
            self._json(403, {"error": "token invalido"})
            return

        if ruta == "/eventos":
            self._eventos()
        elif ruta == "/api/estado":
            self._json(200, self.app.estado_completo())
        elif ruta == "/api/lista":
            self._json(200, self.app.lista.leer())
        elif ruta == "/api/sitio/buscar":
            consulta = (qs.get("q") or [""])[0]
            self._json(200, {"resultados": self.app.sitio.buscar(consulta)})
        elif ruta == "/api/sitio/estado":
            nombre = (qs.get("nombre") or [""])[0]
            if not nombre:
                self._json(400, {"error": "falta 'nombre'"})
                return
            vpn = nombre.lower() in self.app.lista.leer()["vpn"]
            self._json(200, self.app.sitio.estado_cacheado(nombre, vpn=vpn))
        elif ruta == "/api/sitio/imagen":
            url_pedida = (qs.get("url") or [""])[0]
            resultado = self.app.sitio.imagen(url_pedida)
            if resultado is None:
                self._json(404, {"error": "no encontrado"})
                return
            tipo, datos = resultado
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(datos)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(datos)
        elif ruta == "/api/preflight":
            self._json(200, {"filas": preflight_mod.comprobar(self.app.ajustes)})
        elif ruta == "/api/registro":
            fuente = (qs.get("fuente") or ["detector"])[0]
            cursor_crudo = (qs.get("cursor") or [""])[0]
            try:
                limite = min(int((qs.get("limite") or ["400"])[0]), 1000)
                cursor = int(cursor_crudo) if cursor_crudo else None
            except ValueError:
                self._json(400, {"error": "limite/cursor invalido"})
                return
            try:
                resultado = registro_mod.leer(
                    self.app.ajustes.dir_estado, fuente, limite=limite,
                    cursor=cursor, nivel=(qs.get("nivel") or [""])[0],
                    q=(qs.get("q") or [""])[0])
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, resultado)
        elif ruta == "/api/grabaciones":
            a = self.app.ajustes
            self._json(200, grabaciones_mod.listar(a.grabaciones, a.dir_estado, a.ffmpeg))
        elif ruta == "/api/grabaciones/video":
            nombre = (qs.get("archivo") or [""])[0]
            ruta_video = grabaciones_mod.ruta_absoluta(self.app.ajustes.grabaciones, nombre)
            if ruta_video is None:
                self._json(404, {"error": "no encontrado"})
                return
            self._servir_video(ruta_video)
        elif ruta == "/api/preferencias":
            a = self.app.ajustes
            self._json(200, {
                "arrancar_al_abrir": a["arrancar_al_abrir"],
                "bandeja": a["bandeja"],
                "ver_navegador": a["ver_navegador"],
                "inicio_automatico": inicio_mod.esta_activo(),
            })
        elif ruta == "/api/vivo/sesiones":
            detector = _leer_json(self.app.ajustes.estado_detector)
            self._json(200, {"sesiones": vivo_mod.sesiones_vivas(
                self.app.ajustes.streams, detector)})
        elif ruta == "/api/vivo/segmentos":
            sesion = (qs.get("sesion") or [""])[0]
            desde = (qs.get("desde") or [None])[0]
            try:
                limite = min(int((qs.get("limite") or ["40"])[0]), 200)
                atras = min(int((qs.get("atras") or ["3"])[0]), 20)
            except ValueError:
                self._json(400, {"error": "limite/atras invalido"})
                return
            resultado = vivo_mod.listar(
                self.app.ajustes.streams, sesion, desde=desde,
                limite=limite, atras=atras)
            if not resultado:
                self._json(404, {"error": "no encontrado"})
                return
            self._json(200, resultado)
        elif ruta == "/api/vivo/init":
            sesion = (qs.get("sesion") or [""])[0]
            datos = vivo_mod.leer_init(self.app.ajustes.streams, sesion)
            if datos is None:
                self._json(404, {"error": "no encontrado"})
                return
            self._servir_bytes(datos, "video/mp4")
        elif ruta == "/api/vivo/segmento":
            sesion = (qs.get("sesion") or [""])[0]
            nombre = (qs.get("nombre") or [""])[0]
            datos = vivo_mod.leer_segmento(self.app.ajustes.streams, sesion, nombre)
            if datos is None:
                self._json(404, {"error": "no encontrado"})
                return
            self._servir_bytes(datos, "video/mp4")
        elif ruta.startswith("/api/"):
            self._json(404, {"error": "no encontrado"})
        else:
            self._archivo_estatico(ruta)

    def do_POST(self):
        ruta, qs = self._partes()
        if not self._token_valido(qs):
            self._json(403, {"error": "token invalido"})
            return
        try:
            cuerpo = self._cuerpo_json()
        except ValueError:
            self._json(400, {"error": "json invalido"})
            return

        try:
            if ruta == "/api/lista/agregar":
                self.app.lista.agregar_modelo(cuerpo["nombre"])
            elif ruta == "/api/lista/quitar":
                self.app.lista.quitar_modelo(cuerpo["nombre"])
            elif ruta == "/api/lista/vpn":
                self.app.lista.marcar_vpn(cuerpo["nombre"], bool(cuerpo.get("activo")))
            elif ruta == "/api/lista/directiva":
                self.app.lista.establecer_directiva(cuerpo["clave"], cuerpo["valor"])
            elif ruta == "/api/favoritas/agregar":
                self.app.favoritas.agregar(cuerpo["nombre"])
                self._json(200, {"favoritas": self.app.favoritas.leer()})
                return
            elif ruta == "/api/favoritas/quitar":
                self.app.favoritas.quitar(cuerpo["nombre"])
                self._json(200, {"favoritas": self.app.favoritas.leer()})
                return
            elif ruta == "/api/sesion/guardar":
                resultado = self.app.sesion.guardar_desde_curl(cuerpo["curl"])
                self._json(200, {"sesion": resultado})
                return
            elif ruta == "/api/sesion/olvidar":
                self.app.sesion.borrar()
                try:
                    os.remove(self.app.ajustes.estado_siguiendo)
                except OSError:
                    pass
                self._json(200, {"sesion": self.app.sesion.estado()})
                return
            elif ruta == "/api/sesion/actualizar":
                threading.Thread(
                    target=self.app.sesion.actualizar_siguiendo,
                    args=(self.app.ajustes.estado_siguiendo,),
                    daemon=True).start()
                self._json(202, {"aceptado": True})
                return
            elif ruta == "/api/vivo/abrir":
                if not self.app.motor.estado()["supervisor_vivo"]:
                    self._json(409, {"error": "el motor no esta corriendo"})
                    return
                resultado = vivo_temporal_mod.abrir(
                    self.app.lista, self.app.ajustes.dir_estado, cuerpo["nombre"])
                self._json(200, resultado)
                return
            elif ruta == "/api/vivo/latido":
                vivo_temporal_mod.latido(self.app.ajustes.dir_estado, cuerpo["nombre"])
                self._json(200, {"ok": True})
                return
            elif ruta == "/api/vivo/cerrar":
                vivo_temporal_mod.cerrar(
                    self.app.lista, self.app.ajustes.dir_estado, cuerpo["nombre"])
                self._json(200, {"ok": True})
                return
            elif ruta == "/api/motor/arrancar":
                threading.Thread(target=self.app.motor.arrancar, daemon=True).start()
                self._json(202, {"aceptado": True})
                return
            elif ruta == "/api/motor/parar":
                threading.Thread(target=self.app.motor.parar, daemon=True).start()
                self._json(202, {"aceptado": True})
                return
            elif ruta == "/api/preferencias":
                clave = cuerpo["clave"]
                if clave not in ("arrancar_al_abrir", "bandeja", "ver_navegador"):
                    self._json(400, {"error": "preferencia desconocida"})
                    return
                self.app.ajustes[clave] = bool(cuerpo["valor"])
                self.app.ajustes.guardar()
                self._json(200, {clave: self.app.ajustes[clave]})
                return
            elif ruta == "/api/sistema/inicio_automatico":
                if cuerpo.get("activo"):
                    inicio_mod.activar()
                else:
                    inicio_mod.desactivar()
                self._json(200, {"inicio_automatico": inicio_mod.esta_activo()})
                return
            elif ruta == "/api/grabaciones/borrar":
                archivos = cuerpo["archivos"]
                if not isinstance(archivos, list) or not archivos:
                    self._json(400, {"error": "falta 'archivos' (lista)"})
                    return
                resultado = grabaciones_mod.eliminar(self.app.ajustes.grabaciones, archivos)
                self._json(200, resultado)
                return
            elif ruta == "/api/grabaciones/abrir_carpeta":
                ok = grabaciones_mod.abrir_carpeta(
                    self.app.ajustes.grabaciones, cuerpo.get("archivo", ""))
                self._json(200, {"abierto": ok})
                return
            else:
                self._json(404, {"error": "no encontrado"})
                return
        except (KeyError, ValueError) as e:
            self._json(400, {"error": str(e)})
            return

        self._json(200, self.app.lista.leer())


def crear_servidor(ajustes, dir_web, puerto=0):
    """-> (httpd, app, url_con_token). httpd.serve_forever() lo corre;
    httpd.shutdown() lo para. El puerto 0 (por defecto) deja que el
    sistema elija uno libre."""
    app = Aplicacion(ajustes, dir_web)

    class HandlerConApp(Handler):
        pass
    HandlerConApp.app = app

    puerto = puerto or _puerto_libre()
    httpd = ThreadingHTTPServer(("127.0.0.1", puerto), HandlerConApp)
    httpd.daemon_threads = True
    url = "http://127.0.0.1:%d/?t=%s" % (puerto, app.token)
    return httpd, app, url


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    a = ajustes_mod.cargar()
    httpd, app, url = crear_servidor(a, ajustes_mod.DIR_WEB)
    print("Nimbo escuchando en", url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
