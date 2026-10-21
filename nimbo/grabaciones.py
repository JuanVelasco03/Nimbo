"""Listado, duracion, borrado y apertura de carpeta para los .mp4 de
ajustes.grabaciones (datos\\grabaciones dentro de Nimbo, ver ajustes.py y
motor.py: M3U8_BASE). Nimbo no escribe nunca ahi mientras graba -- eso es
enteramente del detector -- esto solo lee el directorio y, cuando el
usuario lo pide desde la pestana Grabaciones, borra (a la papelera, nunca
os.remove directo: son horas de grabacion, no algo para perder por un
click de mas) o abre el explorador.

Convencion de nombre del detector (ver detectar_m3u8.py: unir_sesion):
    <modelo>_<id>_<calidad>_<codec>_<AAAAMMDD-hhmmss>.mp4
Cuando una sesion quedo partida en varios archivos (herramienta aparte,
no el detector -- no se encontro ese patron en detectar_m3u8.py, pero el
rango coincide exacto con la duracion real de cada parte, verificado con
ffmpeg -i) se le agrega:
    ...-<AAAAMMDD-hhmmss>-<inicio>-<fin>-seg<N>.mp4
con <inicio>/<fin> como "hh.mm.ss.mmm" relativos al arranque de la
sesion -- eso da la duracion de esa parte GRATIS, sin abrir el archivo.
"""

import ctypes
import json
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

RE_NOMBRE = re.compile(
    r"^(?P<modelo>.+)_(?P<id>\d+)_(?P<calidad>[^_]+)_(?P<codec>[^_]+)_"
    r"(?P<fecha>\d{8}-\d{6})"
    r"(?:-(?P<ini>\d{2}\.\d{2}\.\d{2}\.\d{3})-(?P<fin>\d{2}\.\d{2}\.\d{2}\.\d{3})"
    r"-seg(?P<seg>\d+))?\.mp4$",
    re.IGNORECASE,
)

ARCHIVO_DURACIONES = "duraciones.json"
_lock_cache = threading.Lock()
_sondeos_en_curso = set()   # nombres de archivo con un hilo de ffmpeg ya lanzado
# Como mucho 2 ffmpeg -i a la vez: un listado con muchos archivos sin
# duracion todavia no tiene que competir por disco/CPU con una grabacion
# real en curso, que es muchisimo mas importante.
_CUPO_SONDEOS = threading.Semaphore(2)


def _hhmmss_a_segundos(texto: str) -> float:
    # "hh.mm.ss.mmm"
    partes = texto.split(".")
    h, m, s, ms = int(partes[0]), int(partes[1]), int(partes[2]), int(partes[3])
    return h * 3600 + m * 60 + s + ms / 1000.0


def _leer_cache(ruta_cache):
    try:
        with open(ruta_cache, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _escribir_cache(ruta_cache, datos):
    tmp = ruta_cache + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.replace(tmp, ruta_cache)
    except OSError:
        pass


def _duracion_ffmpeg(ffmpeg_exe, ruta) -> float | None:
    try:
        r = subprocess.run(
            [ffmpeg_exe, "-i", ruta], capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    salida = (r.stderr or b"").decode("utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)", salida)
    if not m:
        return None
    h, mi, s, cs = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(cs) / (10 ** len(cs))


def _sondear_en_segundo_plano(dir_estado, ffmpeg_exe, nombre, ruta, mtime, tam):
    with _CUPO_SONDEOS:
        try:
            duracion = _duracion_ffmpeg(ffmpeg_exe, ruta)
            if duracion is None:
                return
            ruta_cache = os.path.join(dir_estado, ARCHIVO_DURACIONES)
            with _lock_cache:
                cache = _leer_cache(ruta_cache)
                cache[nombre] = {"mtime": mtime, "tam": tam, "duracion": duracion}
                _escribir_cache(ruta_cache, cache)
        finally:
            _sondeos_en_curso.discard(nombre)


def _duracion_de(dir_estado, ffmpeg_exe, nombre, ruta, mtime, tam, cache):
    """Duracion en segundos, o None si todavia no se sabe. Si hace falta
    ffmpeg y no esta corriendo ya un sondeo para este archivo, lanza uno
    en un hilo aparte (no bloquea el listado) y cachea el resultado para
    la proxima vez que se pida el listado."""
    visto = cache.get(nombre)
    if visto and visto.get("mtime") == mtime and visto.get("tam") == tam:
        return visto.get("duracion")
    if not ffmpeg_exe or nombre in _sondeos_en_curso:
        return None
    _sondeos_en_curso.add(nombre)
    hilo = threading.Thread(
        target=_sondear_en_segundo_plano,
        args=(dir_estado, ffmpeg_exe, nombre, ruta, mtime, tam), daemon=True)
    hilo.start()
    return None


def listar(dir_grabaciones, dir_estado, ffmpeg_exe=""):
    """-> {"grupos": [...], "resumen": {...}}

    Agrupa por sesion (modelo+id+fecha de arranque): un recorte que el
    detector tuvo que partir en varias partes (-seg1, -seg2...) aparece
    como UN grupo con varias "partes", no como archivos sueltos sin
    relacion entre si."""
    ruta_cache = os.path.join(dir_estado, ARCHIVO_DURACIONES)
    with _lock_cache:
        cache = _leer_cache(ruta_cache)

    grupos_por_clave = {}
    orden_claves = []
    tamano_total = 0
    archivos_totales = 0

    try:
        nombres = os.listdir(dir_grabaciones)
    except OSError:
        return {"grupos": [], "resumen": {"archivos": 0, "bytes": 0}}

    for nombre in nombres:
        if not nombre.lower().endswith(".mp4"):
            continue
        ruta = os.path.join(dir_grabaciones, nombre)
        try:
            st = os.stat(ruta)
        except OSError:
            continue
        archivos_totales += 1
        tamano_total += st.st_size

        m = RE_NOMBRE.match(nombre)
        if m:
            d = m.groupdict()
            modelo, id_, calidad, codec, fecha = (
                d["modelo"], d["id"], d["calidad"], d["codec"], d["fecha"])
            seg = int(d["seg"]) if d["seg"] else None
            rango_ini = _hhmmss_a_segundos(d["ini"]) if d["ini"] else None
            rango_fin = _hhmmss_a_segundos(d["fin"]) if d["fin"] else None
        else:
            # nombre que no sigue la convencion (renombrado a mano, u otro
            # origen) -- se muestra igual, como grupo de una sola parte.
            modelo, id_, calidad, codec, fecha = nombre, "", "", "", ""
            seg, rango_ini, rango_fin = None, None, None

        if rango_ini is not None and rango_fin is not None:
            duracion = rango_fin - rango_ini
        else:
            duracion = _duracion_de(dir_estado, ffmpeg_exe, nombre, ruta,
                                     st.st_mtime, st.st_size, cache)

        parte = {
            "archivo": nombre, "seg": seg,
            "tamano_bytes": st.st_size, "mtime": st.st_mtime,
            "duracion_seg": duracion,
        }

        clave = ("%s|%s|%s" % (modelo.lower(), id_, fecha)) if m else ("sin-convencion|" + nombre)
        if clave not in grupos_por_clave:
            grupos_por_clave[clave] = {
                "clave": clave, "modelo": modelo, "id": id_,
                "calidad": calidad, "codec": codec, "fecha": fecha,
                "partes": [],
            }
            orden_claves.append(clave)
        grupos_por_clave[clave]["partes"].append(parte)

    grupos = []
    for clave in orden_claves:
        g = grupos_por_clave[clave]
        g["partes"].sort(key=lambda p: (p["seg"] if p["seg"] is not None else 0, p["mtime"]))
        duraciones = [p["duracion_seg"] for p in g["partes"]]
        g["duracion_total_seg"] = sum(duraciones) if all(d is not None for d in duraciones) else (
            sum(d for d in duraciones if d is not None) or None)
        g["duracion_parcial"] = any(d is None for d in duraciones) and any(d is not None for d in duraciones)
        g["tamano_total_bytes"] = sum(p["tamano_bytes"] for p in g["partes"])
        g["inicio_mtime"] = min(p["mtime"] for p in g["partes"])
        g["fin_mtime"] = max(p["mtime"] for p in g["partes"])
        grupos.append(g)

    grupos.sort(key=lambda g: g["inicio_mtime"], reverse=True)

    return {
        "grupos": grupos,
        "resumen": {"archivos": archivos_totales, "bytes": tamano_total},
    }


# -- seguridad: nunca confiar en un nombre que llega por querystring ------

def _resolver(dir_grabaciones, nombre) -> str | None:
    """-> ruta absoluta dentro de dir_grabaciones, o None si `nombre` es
    invalido (separadores de ruta, "..", o termina fuera de la carpeta).
    Nunca se arma una ruta directo con lo que llega por HTTP."""
    if not nombre or "/" in nombre or "\\" in nombre or nombre in (".", ".."):
        return None
    ruta = os.path.join(dir_grabaciones, nombre)
    base = os.path.realpath(dir_grabaciones)
    destino = os.path.realpath(ruta)
    if os.path.commonpath([base, destino]) != base:
        return None
    return ruta


def ruta_absoluta(dir_grabaciones, nombre) -> str | None:
    ruta = _resolver(dir_grabaciones, nombre)
    if ruta is None or not os.path.isfile(ruta):
        return None
    return ruta


# -- borrado: siempre a la papelera, nunca permanente ---------------------

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 3
_FOF_ALLOWUNDO = 0x40
_FOF_NOCONFIRMATION = 0x10
_FOF_NOERRORUI = 0x400
_FOF_SILENT = 0x4


def _a_la_papelera(rutas: list) -> None:
    """Mueve una o mas rutas a la Papelera de reciclaje (SHFileOperationW,
    FOF_ALLOWUNDO) -- reversible desde el explorador si alguien borro lo
    que no queria. Nunca os.remove: son grabaciones de horas, no algo
    para perder sin vuelta atras por un click de mas."""
    buf = "\0".join(rutas) + "\0\0"
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT
    resultado = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if resultado != 0 or op.fAnyOperationsAborted:
        raise OSError("no se pudo mover a la papelera (codigo %d)" % resultado)


def eliminar(dir_grabaciones, nombres: list) -> dict:
    """-> {"borrados": [...], "errores": {nombre: motivo}}"""
    rutas, borrados, errores = [], [], {}
    for nombre in nombres:
        ruta = ruta_absoluta(dir_grabaciones, nombre)
        if ruta is None:
            errores[nombre] = "no encontrado"
            continue
        rutas.append(ruta)
    if rutas:
        try:
            _a_la_papelera(rutas)
            borrados = [os.path.basename(r) for r in rutas]
        except OSError as e:
            for r in rutas:
                errores[os.path.basename(r)] = str(e)
    return {"borrados": borrados, "errores": errores}


def abrir_carpeta(dir_grabaciones, nombre="") -> bool:
    """Abre el Explorador en dir_grabaciones, con `nombre` seleccionado si
    se indica uno valido. Local-only: Nimbo solo escucha en 127.0.0.1,
    esto corre en la misma maquina que lo pide (igual que motor.py ya
    lanza procesos hijos)."""
    ruta = ruta_absoluta(dir_grabaciones, nombre) if nombre else None
    try:
        if ruta:
            subprocess.Popen(["explorer", "/select,", ruta])
        else:
            subprocess.Popen(["explorer", dir_grabaciones])
        return True
    except OSError:
        return False
