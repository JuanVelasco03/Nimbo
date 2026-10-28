"""Comprobaciones de puesta a punto, en espanol y con que hacer si algo
falta. La interfaz las pide por /api/preflight y solo muestra un aviso
si alguna fallo -- si todo esta bien, no se nota que esto existe.
"""
import os
import shutil
import subprocess
import sys

import ajustes as ajustes_mod

CREAR_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# El .cer que mitmdump genera solo la primera vez que corre. No esta en
# el paquete del instalador a proposito: cada instalacion tiene su propia
# CA, y meter una compartida seria darle a todos la misma llave para
# interceptar el trafico de todos.
CERT_CA = os.path.join(os.path.expanduser("~"), ".mitmproxy",
                       "mitmproxy-ca-cert.cer")


def _dir_navegadores() -> str:
    """Donde viven los navegadores de Playwright.

    PLAYWRIGHT_BROWSERS_PATH primero: es lo que ajustes.cargar() deja
    puesto cuando el paquete trae los suyos adentro (ver
    ajustes.navegadores_embarcados). Sin esa variable, Playwright usa
    ~\\AppData\\Local\\ms-playwright, que es el caso de la copia de
    desarrollo.
    """
    propio = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if propio:
        return propio
    return os.path.join(os.path.expanduser("~"), "AppData", "Local",
                        "ms-playwright")


def _tiene_chromium_playwright() -> bool:
    try:
        return any(n.lower().startswith("chromium-")
                   for n in os.listdir(_dir_navegadores()))
    except OSError:
        return False


def _ca_en_el_store() -> bool:
    """Si la CA de mitmproxy esta en algun store de confianza.

    Sin eso mitmdump no puede abrir el HLS de doppiocdn: el navegador (y
    el camino directo) rechazan el certificado que firma al vuelo.

    Se miran los **dos** stores, y eso no es de mas: el instalador de
    mitmproxy la pone en el de la maquina (con elevacion), que es donde
    esta en la instalacion de desarrollo -- mirar solo el del usuario daba
    "falta el certificado" en una instalacion que graba perfecto. Nimbo,
    en cambio, la instala en el del usuario, que es el que se puede
    escribir sin admin. Cualquiera de los dos alcanza.
    """
    for args in (["certutil", "-user", "-store", "ROOT"],
                 ["certutil", "-store", "ROOT"]):
        try:
            r = subprocess.run(args, capture_output=True, text=True,
                               timeout=30, errors="replace",
                               creationflags=CREAR_SIN_VENTANA)
        except (OSError, subprocess.SubprocessError):
            continue
        if "mitmproxy" in (r.stdout or "").lower():
            return True
    return False


def instalar_certificado() -> dict:
    """Mete la CA de mitmproxy en el store del usuario.

    Es lo unico del paquete que toca la maquina de verdad, asi que es un
    boton explicito en el banner de puesta a punto y no un paso silencioso
    del instalador (ver specs\\distribucion_instalador.md S4.1): un
    instalador que agrega una CA raiz sin decirlo hace exactamente lo que
    hace un malware.

    No necesita admin: -user escribe el store del usuario. Devuelve el
    detalle para mostrarlo tal cual si falla.
    """
    if sys.platform != "win32":
        return {"ok": False, "mensaje": "solo en Windows"}
    if not os.path.isfile(CERT_CA):
        return {"ok": False,
                "mensaje": "todavia no existe " + CERT_CA + " -- arranca "
                           "Nimbo una vez para que mitmdump lo genere y "
                           "volve a intentar"}
    try:
        r = subprocess.run(["certutil", "-user", "-addstore", "ROOT", CERT_CA],
                           capture_output=True, text=True, timeout=60,
                           errors="replace", creationflags=CREAR_SIN_VENTANA)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "mensaje": "no se pudo ejecutar certutil: %s" % e}
    if r.returncode != 0:
        salida = ((r.stdout or "") + (r.stderr or "")).strip()
        return {"ok": False, "mensaje": salida[-400:] or
                "certutil devolvio %d" % r.returncode}
    return {"ok": True, "mensaje": "certificado instalado"}


def comprobar(ajustes) -> list:
    filas = []

    def fila(ok, nombre, mensaje, accion="", arreglo=None):
        # "arreglo" es opcional y lo usa solo el certificado: {ruta, boton}
        # para que el banner pueda ofrecer el POST que lo resuelve en vez
        # de pedirle al usuario que corra algo a mano.
        f = {"ok": bool(ok), "nombre": nombre, "mensaje": mensaje,
             "accion": accion}
        if arreglo:
            f["arreglo"] = arreglo
        filas.append(f)

    faltan = ajustes_mod.nucleo_incompleto(ajustes.scripts)
    ok = not faltan
    fila(ok, "Motor de captura",
         "nucleo\\scripts\\ completo" if ok else
         "falta en nucleo\\scripts\\: " + ", ".join(faltan),
         "" if ok else "Reinstala Nimbo: nucleo\\scripts\\ tiene que traer "
                       "detectar_m3u8.py, supervisor.py, mouflon.py y "
                       "grabador_directo.py")

    ok = bool(ajustes["mitmdump"]) and os.path.isfile(ajustes["mitmdump"])
    fila(ok, "mitmproxy",
         "encontrado" if ok else "no se encontro mitmdump.exe",
         "" if ok else "Instalalo desde https://mitmproxy.org/downloads/ y volve a abrir Nimbo")

    if sys.platform == "win32":
        ok = _ca_en_el_store()
        fila(ok, "Certificado de mitmproxy",
             "instalado" if ok else
             "falta la CA de mitmproxy: sin ella no se puede grabar",
             "" if ok else "Se instala con un clic, sin permisos de "
                           "administrador",
             None if ok else {"ruta": "/api/preflight/certificado",
                              "boton": "Instalar el certificado"})

    ok = os.path.isfile(ajustes.ffmpeg)
    fila(ok, "ffmpeg",
         "encontrado" if ok else "falta nucleo\\scripts\\ffmpeg.exe",
         "" if ok else "Copia ffmpeg.exe a " + ajustes.scripts)

    # Antes esta fila decia "Python con Playwright" y solo comprobaba que
    # el archivo existiera -- en un paquete liviano (sin respaldo de
    # navegador) daba OK sobre un interprete que no tiene Playwright. Son
    # dos cosas distintas y ahora se informan por separado: que haya
    # interprete, y mas abajo que haya navegador.
    hay = bool(ajustes["python"]) and os.path.isfile(ajustes["python"])
    con_playwright = hay and ajustes_mod._tiene_playwright(ajustes["python"])
    fila(hay, "Interprete de Python",
         (ajustes["python"] + ("" if con_playwright else "  (sin Playwright)"))
         if hay else "no se encontro ningun Python",
         "" if hay else "Reinstala Nimbo: tiene que traer "
                        "runtime\\python\\python.exe")

    # Si esto es una instalacion con runtime propio, el consejo no es
    # "corre pip": el paquete liviano simplemente no trae el respaldo de
    # navegador, y el camino directo (@directo=1) graba igual sin el.
    embarcado = bool(ajustes_mod.python_embarcado())
    ok = con_playwright and _tiene_chromium_playwright()
    fila(ok, "Navegador de Playwright (respaldo)",
         "instalado" if ok else
         ("falta Playwright en el interprete" if not con_playwright
          else "falta el Chromium que usa el supervisor"),
         "" if ok else
         ("Este paquete se armo sin el respaldo de navegador: las modelos "
          "marcadas @directo=1 graban igual, el resto no. Reinstala con el "
          "paquete completo si lo necesitas" if embarcado
          else "python -m playwright install chromium"))

    try:
        libres = shutil.disk_usage(ajustes.dir_datos).free
        ok = libres > 5 * 1024**3
        fila(ok, "Espacio en disco",
             "%.1f GB libres en %s" % (libres / 1024**3, ajustes.dir_datos),
             "" if ok else "Queda poco espacio: libera disco antes de grabar mucho tiempo")
    except OSError:
        fila(False, "Espacio en disco", "no se pudo comprobar " + ajustes.dir_datos, "")

    try:
        vpn_pedida = any(True for _ in open(ajustes.grabar_txt, encoding="utf-8")
                         if _.strip().lower().startswith("@vpn=")
                         and _.split("=", 1)[1].strip())
    except OSError:
        vpn_pedida = False
    if vpn_pedida:
        wireproxy = os.path.join(ajustes.scripts, "wireproxy.exe")
        confs = []
        try:
            confs = [n for n in os.listdir(ajustes.scripts)
                    if n.lower().endswith(".conf")
                    and n != "tunel_wireproxy.conf"]
        except OSError:
            pass
        ok = os.path.isfile(wireproxy) and len(confs) >= 1
        fila(ok, "Tunel para modelos geobloqueadas (@vpn=)",
             "wireproxy y un .conf encontrados" if ok else
             "falta wireproxy.exe o el .conf de WireGuard en " + ajustes.scripts,
             "" if ok else "Ver LEEME.md, seccion 'Modelos geobloqueadas'")

    return filas
