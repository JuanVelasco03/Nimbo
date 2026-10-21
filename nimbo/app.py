"""Nimbo -- lo unico que el usuario abre.

    pythonw -m nimbo.app        (o el acceso directo del escritorio)

Levanta el servidor local, abre la ventana (Edge en modo --app, sin barra
de direcciones), y deja un icono en la bandeja del sistema para volver a
abrirla o parar todo sin que haga falta dejar la ventana abierta.

Instancia unica: si Nimbo ya esta corriendo (el archivo instancia.json
apunta a un servidor que de verdad responde), esta segunda ejecucion solo
abre otra ventana contra ESE servidor y termina -- no arranca un segundo
motor, que se pelearia por el puerto 8080 y por el perfil del navegador.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import ajustes as ajustes_mod
import vivo_temporal as vivo_temporal_mod

try:
    import servidor as servidor_mod
except ImportError as e:
    # A diferencia de pystray/Pillow (opcionales: sin ellas se cae a modo
    # sin bandeja, ver ejecutar_sin_bandeja), websockets no es opcional --
    # sin ella no hay servidor ni interfaz. Mejor este mensaje claro que
    # un traceback crudo al abrir Nimbo.
    print("[NIMBO] falta instalar una dependencia obligatoria:", e)
    print("[NIMBO] ejecuta: pip install -r requirements.txt")
    sys.exit(1)

ARCHIVO_INSTANCIA = os.path.join(ajustes_mod.RAIZ, "instancia.json")
DIR_PERFIL_VENTANA = os.path.join(ajustes_mod.RAIZ, "perfil_ventana")
CREAR_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _log(mensaje):
    print("[NIMBO]", mensaje, flush=True)


# -- instancia unica ---------------------------------------------------

def _instancia_viva():
    try:
        with open(ARCHIVO_INSTANCIA, encoding="utf-8") as f:
            datos = json.load(f)
        with socket.create_connection(("127.0.0.1", datos["puerto"]), timeout=1):
            return datos
    except (OSError, ValueError, KeyError):
        return None


def _guardar_instancia(puerto, token):
    os.makedirs(ajustes_mod.RAIZ, exist_ok=True)
    tmp = ARCHIVO_INSTANCIA + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"puerto": puerto, "token": token, "pid": os.getpid()}, f)
    os.replace(tmp, ARCHIVO_INSTANCIA)


def _borrar_instancia():
    try:
        os.remove(ARCHIVO_INSTANCIA)
    except OSError:
        pass


# -- la ventana ----------------------------------------------------------

def _localizar_edge():
    hallado = shutil.which("msedge")
    if hallado:
        return hallado
    for ruta in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(ruta):
            return ruta
    return ""


def abrir_ventana(url: str):
    """Ventana sin barra de direcciones, perfil propio (no es el de
    nucleo\\perfil: ese es exclusivo del supervisor). Si no hay Edge, cae al
    navegador por defecto -- se ve con pestana y barra, pero funciona
    igual."""
    edge = _localizar_edge()
    os.makedirs(DIR_PERFIL_VENTANA, exist_ok=True)
    if edge:
        subprocess.Popen(
            [edge, "--app=" + url, "--user-data-dir=" + DIR_PERFIL_VENTANA,
             "--no-first-run", "--no-default-browser-check"],
            creationflags=CREAR_SIN_VENTANA)
    else:
        _log("no se encontro Edge: se abre con el navegador por defecto")
        import webbrowser
        webbrowser.open(url)


# -- bandeja del sistema (opcional) -----------------------------------

def _icono_nimbo():
    from PIL import Image, ImageDraw
    ruta = os.path.join(ajustes_mod.DIR_WEB, "icono.ico")
    if os.path.isfile(ruta):
        try:
            return Image.open(ruta)
        except OSError:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 61, 61), fill=(60, 120, 255, 255))
    d.text((22, 16), "M", fill=(255, 255, 255, 255))
    return img


def _resumen_estado(app) -> str:
    # app.estado es la cache que mantiene servidor.py:_bucle_estado --
    # tiene hasta 4s de atraso, no hace falta recalcular nada solo para
    # el tooltip de la bandeja (eso era justo lo caro de la version con
    # SSE: un estado_completo() entero cada 4s).
    estado = app.estado
    if not estado:
        return "Nimbo"
    m = estado.get("motor") or {}
    if m["parando"]:
        return "Nimbo -- deteniendo..."
    if m.get("reiniciando"):
        return "Nimbo -- reiniciando el %s..." % m["reiniciando"]
    if m.get("rendido"):
        return "Nimbo -- %s no arranca" % ", ".join(m["rendido"])
    if not (m["detector_vivo"] and m["supervisor_vivo"]):
        return "Nimbo -- detenido"
    streams = (estado.get("detector") or {}).get("streams") or []
    grabando = sum(1 for s in streams if s.get("grabando"))
    privado = sum(1 for s in streams if s.get("privado"))
    partes = []
    if grabando:
        partes.append("%d grabando" % grabando)
    if privado:
        partes.append("%d en privado" % privado)
    return "Nimbo -- " + (", ".join(partes) if partes else "en marcha")


def ejecutar_con_bandeja(app, url):
    """Bloquea aqui hasta que el usuario elige 'Salir'. Si pystray no
    esta disponible, cae a un bucle simple (ver ejecutar_sin_bandeja)."""
    import pystray

    icono = pystray.Icon("nimbo", _icono_nimbo(), "Nimbo")

    def accion_abrir(icon, item):
        abrir_ventana(url)

    def accion_arrancar(icon, item):
        threading.Thread(target=app.motor.arrancar, daemon=True).start()

    def accion_parar(icon, item):
        threading.Thread(target=app.motor.parar, daemon=True).start()

    def accion_salir(icon, item):
        icon.stop()

    icono.menu = pystray.Menu(
        pystray.MenuItem("Abrir Nimbo", accion_abrir, default=True),
        pystray.MenuItem("Arrancar todo", accion_arrancar),
        pystray.MenuItem("Parar todo", accion_parar),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", accion_salir),
    )

    parar_hilo = threading.Event()

    def refrescar_titulo():
        while not parar_hilo.is_set():
            try:
                icono.title = _resumen_estado(app)
            except Exception:
                pass
            parar_hilo.wait(4.0)

    threading.Thread(target=refrescar_titulo, daemon=True).start()
    icono.run()      # bloquea hasta accion_salir / icono.stop()
    parar_hilo.set()


def ejecutar_sin_bandeja(app):
    _log("bandeja desactivada o no disponible -- Ctrl+C aqui para parar todo")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


# -- arranque -------------------------------------------------------------

def main():
    a = ajustes_mod.cargar()

    existente = _instancia_viva()
    if existente:
        _log("Nimbo ya esta corriendo: abro otra ventana contra el mismo servidor")
        abrir_ventana("http://127.0.0.1:%d/?t=%s" %
                      (existente["puerto"], existente["token"]))
        return 0

    # Aplicacion() ya existe en este punto (el constructor de Servidor la
    # crea sincronico, antes de levantar el loop) -- se puede purgar
    # grabar.txt ANTES de arrancar() para que la primera foto de estado
    # (la que arrancar() siembra en app.estado) ya salga limpia, en vez de
    # mostrar por un instante una modelo que esta misma purga va a sacar.
    servidor = servidor_mod.Servidor(a, ajustes_mod.DIR_WEB)
    app = servidor.app

    # una corrida anterior que murio a mitad (cerrado de un ctrl+c, por
    # ejemplo) puede haber dejado una modelo pegada en grabar.txt por una
    # vista en vivo temporal que nunca llego a cerrarse -- antes de que el
    # detector arranque y la vea, se cierra todo lo que quedo anotado.
    n = vivo_temporal_mod.purgar(app.lista, a.dir_estado)
    if n:
        _log("%d vista(s) en vivo temporal(es) sin cerrar de la corrida anterior" % n)

    servidor.arrancar()      # no vuelve hasta que el socket esta escuchando
    url = "http://127.0.0.1:%d/?t=%s" % (servidor.puerto, servidor.token)
    _guardar_instancia(servidor.puerto, servidor.token)
    _log("servidor en " + url)

    if a["arrancar_al_abrir"]:
        threading.Thread(target=app.motor.arrancar, daemon=True).start()

    abrir_ventana(url)

    try:
        if a["bandeja"]:
            try:
                ejecutar_con_bandeja(app, url)
            except ImportError:
                ejecutar_sin_bandeja(app)
        else:
            ejecutar_sin_bandeja(app)
    finally:
        _log("cerrando Nimbo -- parada ordenada (puede tardar si hay "
             "una grabacion uniendose)")
        app.motor.parar()
        servidor.parar()
        _borrar_instancia()
        _log("listo")

    return 0


if __name__ == "__main__":
    sys.exit(main())
