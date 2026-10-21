"""Arrancar Nimbo solo con Windows: un .bat chico en la carpeta de
Inicio del usuario que llama a arrancar.bat. Nada de servicios, nada de
registro -- se activa y desactiva con solo crear o borrar ese archivo.
"""
import os

NOMBRE = "Nimbo.bat"
MARCA = "rem Nimbo -- generado automaticamente, se puede borrar sin miedo"


def _carpeta_inicio() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


def _ruta_shim() -> str:
    return os.path.join(_carpeta_inicio(), NOMBRE)


def _ruta_arrancar() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "arrancar.bat")


def esta_activo() -> bool:
    ruta = _ruta_shim()
    if not os.path.isfile(ruta):
        return False
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            return MARCA in f.read()
    except OSError:
        return False


def activar() -> bool:
    carpeta = _carpeta_inicio()
    if not os.path.isdir(carpeta):
        return False
    with open(_ruta_shim(), "w", encoding="utf-8") as f:
        f.write("@echo off\n%s\nstart \"\" \"%s\"\n" % (MARCA, _ruta_arrancar()))
    return True


def desactivar() -> bool:
    ruta = _ruta_shim()
    if os.path.isfile(ruta) and esta_activo():
        os.remove(ruta)
    return True
