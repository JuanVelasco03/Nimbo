"""Validar, medir espacio y elegir carpetas para "Carpetas y discos" (SPEC 12).

Tres funciones, todas sincronas a proposito: tocan disco (os.makedirs, una
escritura de prueba) o abren un dialogo nativo, asi que quien las llama
(nimbo\\servidor.py) las manda al pool de hilos con Aplicacion.a_hilo, nunca
directo desde una corutina.
"""

import ctypes
import os
import shutil
import sys
from ctypes import wintypes

_MAX_PATH = 260


def validar(ruta: str) -> dict:
    """Si `ruta` sirve como carpeta de datos: absoluta, con letra de
    unidad, creable y con permiso de escritura real -- os.access() no
    alcanza (hay unidades que lo dan positivo y rechazan la escritura
    de todas formas), asi que se prueba escribiendo y borrando un archivo
    chico. -> {"ok": bool, "motivo": str, "ruta": str}."""
    ruta = (ruta or "").strip()
    if not ruta:
        return {"ok": False, "motivo": "la ruta esta vacia", "ruta": ruta}
    if not os.path.isabs(ruta) or not os.path.splitdrive(ruta)[0]:
        return {"ok": False,
                 "motivo": "tiene que ser una ruta absoluta con letra de "
                           "unidad (ej. D:\\videos)",
                 "ruta": ruta}
    try:
        os.makedirs(ruta, exist_ok=True)
    except OSError as e:
        return {"ok": False, "motivo": "no se pudo crear la carpeta (%s)" % e,
                 "ruta": ruta}
    prueba = os.path.join(ruta, ".nimbo_prueba")
    try:
        with open(prueba, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(prueba)
    except OSError as e:
        return {"ok": False,
                 "motivo": "sin permiso de escritura en esa carpeta (%s)" % e,
                 "ruta": ruta}
    return {"ok": True, "motivo": "", "ruta": ruta}


def espacio(ruta: str):
    """{"unidad","libres","total"} de la unidad que contiene `ruta`, o
    None si no se pudo medir (unidad desconectada, ruta inexistente)."""
    try:
        uso = shutil.disk_usage(ruta)
    except OSError:
        return None
    unidad = os.path.splitdrive(os.path.abspath(ruta))[0].upper()
    return {"unidad": unidad, "libres": uso.free, "total": uso.total}


# -- dialogo nativo de carpetas (SHBrowseForFolderW) -----------------------
#
# tkinter no esta garantizado en el Python embebido del paquete (ver
# ajustes.python_embarcado) y el showDirectoryPicker() del navegador no
# devuelve la ruta real de disco por diseno -- asi que el dialogo sale por
# ctypes contra shell32, como ya hace grabaciones.py con la papelera
# (SHFileOperationW).

_BIF_RETURNONLYFSDIRS = 0x0001
_BIF_NEWDIALOGSTYLE = 0x0040
_BFFM_INITIALIZED = 1
_BFFM_SETSELECTIONW = 0x0400 + 103  # WM_USER + 103


class _BROWSEINFO(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", ctypes.c_uint),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


_BFFCALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HWND, ctypes.c_uint, wintypes.LPARAM, wintypes.LPARAM)


def _callback_inicial(inicial: str):
    """Deja el dialogo abierto ya parado en `inicial`, via BFFM_SETSELECTIONW
    en el mensaje BFFM_INITIALIZED. Silencioso si algo falla: sin esto el
    dialogo abre en la carpeta por defecto de Windows, que no es un error."""
    def _cb(hwnd, msg, lparam, lpdata):
        if msg == _BFFM_INITIALIZED and inicial:
            try:
                ctypes.windll.user32.SendMessageW(
                    hwnd, _BFFM_SETSELECTIONW, 1, ctypes.c_wchar_p(inicial))
            except OSError:
                pass
        return 0
    return _BFFCALLBACK(_cb)


def elegir_carpeta(titulo: str, inicial: str = "") -> str:
    """Abre el dialogo nativo "Elegir carpeta" de Windows. Devuelve la ruta
    elegida, o "" si el usuario cancelo, algo fallo o esto no corre en
    Windows.

    `hwndOwner = GetForegroundWindow()` para que aparezca por delante de la
    ventana de Edge en vez de detras, que parece que Nimbo se colgo."""
    if sys.platform != "win32":
        return ""
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CoInitialize(None)
    try:
        buf = ctypes.create_unicode_buffer(_MAX_PATH)
        bi = _BROWSEINFO()
        bi.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
        bi.pidlRoot = None
        bi.pszDisplayName = ctypes.cast(buf, wintypes.LPWSTR)
        bi.lpszTitle = titulo
        bi.ulFlags = _BIF_RETURNONLYFSDIRS | _BIF_NEWDIALOGSTYLE
        callback = _callback_inicial(inicial)
        bi.lpfn = ctypes.cast(callback, ctypes.c_void_p)
        bi.lParam = 0
        bi.iImage = 0
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ""
        try:
            ruta_buf = ctypes.create_unicode_buffer(_MAX_PATH)
            if not shell32.SHGetPathFromIDListW(pidl, ruta_buf):
                return ""
            return ruta_buf.value
        finally:
            ole32.CoTaskMemFree(pidl)
    except OSError:
        return ""
    finally:
        ole32.CoUninitialize()
