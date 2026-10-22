"""Comprobaciones de puesta a punto, en espanol y con que hacer si algo
falta. La interfaz las pide por /api/preflight y solo muestra un aviso
si alguna fallo -- si todo esta bien, no se nota que esto existe.
"""
import os
import shutil

import ajustes as ajustes_mod


def _tiene_chromium_playwright() -> bool:
    base = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright")
    try:
        return any(n.lower().startswith("chromium-") for n in os.listdir(base))
    except OSError:
        return False


def comprobar(ajustes) -> list:
    filas = []

    def fila(ok, nombre, mensaje, accion=""):
        filas.append({"ok": bool(ok), "nombre": nombre, "mensaje": mensaje,
                     "accion": accion})

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

    ok = os.path.isfile(ajustes.ffmpeg)
    fila(ok, "ffmpeg",
         "encontrado" if ok else "falta nucleo\\scripts\\ffmpeg.exe",
         "" if ok else "Copia ffmpeg.exe a " + ajustes.scripts)

    ok = bool(ajustes["python"]) and os.path.isfile(ajustes["python"])
    fila(ok, "Python con Playwright",
         ajustes["python"] if ok else "no se encontro un Python con Playwright instalado",
         "" if ok else "python -m pip install playwright  &&  "
                       "python -m playwright install chromium")

    ok = _tiene_chromium_playwright()
    fila(ok, "Navegador de Playwright",
         "instalado" if ok else "falta el Chromium que usa Playwright",
         "" if ok else "python -m playwright install chromium")

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
