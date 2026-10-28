"""Portadas (un fotograma real) de los .mp4 de ajustes.grabaciones, para
la pestana Grabaciones (vista_grabaciones.js). Mismo esquema que las
duraciones de grabaciones.py: indice JSON atomico en dir_estado +
generacion con ffmpeg en segundo plano, nunca en el camino de un pedido
HTTP.

Las portadas en si (los .jpg) viven en ajustes.dir_miniaturas, DENTRO de
datos\\ -- son cache regenerable, asi que se borran con os.remove y nunca
pasan por la papelera (a diferencia de grabaciones.py, que son horas de
grabacion real)."""

import json
import os
import subprocess
import threading

ARCHIVO_INDICE = "miniaturas.json"
_lock_indice = threading.Lock()
_en_curso = set()   # nombres de archivo .mp4 con un hilo de ffmpeg ya lanzado
# Una portada por vez: a diferencia del sondeo de duraciones (2 a la vez),
# esto puede correr con cientos de .mp4 pendientes mientras el detector
# todavia esta escribiendo segmentos nuevos en el mismo disco.
_CUPO = threading.Semaphore(1)

_FRACCION_CORTE = 0.10   # 10% de la duracion
_CORTE_SIN_DURACION = 60.0
_CORTE_RESERVA = 3.0     # si el de arriba no entra en un archivo corto


def ruta_absoluta(dir_miniaturas, nombre) -> str | None:
    """-> ruta absoluta dentro de dir_miniaturas, o None si `nombre` es
    invalido. Mismo patron anti-traversal que grabaciones.py:_resolver --
    nunca se arma una ruta directo con lo que llega por HTTP."""
    if not nombre or "/" in nombre or "\\" in nombre or nombre in (".", ".."):
        return None
    ruta = os.path.join(dir_miniaturas, nombre)
    base = os.path.realpath(dir_miniaturas)
    destino = os.path.realpath(ruta)
    if os.path.commonpath([base, destino]) != base or not os.path.isfile(destino):
        return None
    return ruta


def _nombre_cache(nombre_mp4: str) -> str:
    base, _ext = os.path.splitext(nombre_mp4)
    return base + ".jpg"


def _leer_indice(ruta_indice):
    try:
        with open(ruta_indice, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _escribir_indice(ruta_indice, datos):
    tmp = ruta_indice + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.replace(tmp, ruta_indice)
    except OSError:
        pass


def disponible(dir_miniaturas, dir_estado, nombre_mp4, mtime, tam):
    """-> nombre del .jpg (relativo a dir_miniaturas) si ya existe una
    portada valida para ese mtime/tam, o None. Nunca genera nada ni
    bloquea -- es lo que llama grabaciones.listar() por cada grupo."""
    ruta_indice = os.path.join(dir_estado, ARCHIVO_INDICE)
    with _lock_indice:
        indice = _leer_indice(ruta_indice)
    entrada = indice.get(nombre_mp4)
    if not entrada or entrada.get("mtime") != mtime or entrada.get("tam") != tam:
        return None
    if not entrada.get("ok"):
        return None
    cache = _nombre_cache(nombre_mp4)
    if not os.path.isfile(os.path.join(dir_miniaturas, cache)):
        return None
    return cache


def _punto_de_corte(duracion_seg):
    if duracion_seg is None:
        return _CORTE_SIN_DURACION
    return max(0.0, duracion_seg * _FRACCION_CORTE)


def _extraer_frame(ffmpeg_exe, ruta_mp4, destino, segundos) -> bool:
    try:
        r = subprocess.run(
            [ffmpeg_exe, "-nostdin", "-ss", str(segundos), "-i", ruta_mp4,
             "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", "-y", destino],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and os.path.isfile(destino) and os.path.getsize(destino) > 0


def generar(ffmpeg_exe, ruta_mp4, destino, duracion_seg) -> bool:
    """Intenta generar `destino` (un .jpg) a partir de ruta_mp4. Primero al
    punto de corte normal (10% de la duracion, o 60s si no se sabe); si eso
    no produce nada (archivo mas corto que el corte), un segundo intento
    cerca del arranque. -> True si quedo un .jpg valido."""
    if not ffmpeg_exe or not os.path.isfile(ffmpeg_exe):
        return False
    if _extraer_frame(ffmpeg_exe, ruta_mp4, destino, _punto_de_corte(duracion_seg)):
        return True
    return _extraer_frame(ffmpeg_exe, ruta_mp4, destino, _CORTE_RESERVA)


def _generar_en_segundo_plano(dir_miniaturas, dir_estado, ffmpeg_exe,
                               nombre_mp4, ruta_mp4, mtime, tam, duracion_seg):
    with _CUPO:
        try:
            destino = os.path.join(dir_miniaturas, _nombre_cache(nombre_mp4))
            ok = generar(ffmpeg_exe, ruta_mp4, destino, duracion_seg)
            if not ok:
                try:
                    os.remove(destino)
                except OSError:
                    pass
            ruta_indice = os.path.join(dir_estado, ARCHIVO_INDICE)
            with _lock_indice:
                indice = _leer_indice(ruta_indice)
                indice[nombre_mp4] = {"mtime": mtime, "tam": tam, "ok": ok}
                _escribir_indice(ruta_indice, indice)
        finally:
            _en_curso.discard(nombre_mp4)


def generar_faltantes(grupos, dir_grabaciones, dir_miniaturas, dir_estado,
                       ffmpeg_exe, tope):
    """Lanza en segundo plano hasta `tope` portadas que todavia no existen
    (o cuyo .mp4 cambio), de mas nuevo a mas viejo -- `grupos` ya viene en
    ese orden desde grabaciones.listar(). Solo la primera parte de cada
    grupo lleva portada: la tarjeta es el grupo entero. No bloquea: cada
    portada se genera en su propio hilo, con _CUPO limitando a una por
    vez."""
    if not ffmpeg_exe or not os.path.isfile(ffmpeg_exe) or tope <= 0:
        return 0
    try:
        os.makedirs(dir_miniaturas, exist_ok=True)
    except OSError:
        return 0

    ruta_indice = os.path.join(dir_estado, ARCHIVO_INDICE)
    with _lock_indice:
        indice = _leer_indice(ruta_indice)

    lanzadas = 0
    for g in grupos:
        if lanzadas >= tope:
            break
        if not g["partes"]:
            continue
        primera = g["partes"][0]
        nombre_mp4 = primera["archivo"]
        mtime, tam = primera["mtime"], primera["tamano_bytes"]

        entrada = indice.get(nombre_mp4)
        vigente = entrada and entrada.get("mtime") == mtime and entrada.get("tam") == tam
        if vigente and entrada.get("ok"):
            cache = _nombre_cache(nombre_mp4)
            if os.path.isfile(os.path.join(dir_miniaturas, cache)):
                continue
        elif vigente and not entrada.get("ok"):
            continue   # cache negativa: ya fallo para este mtime/tam
        if nombre_mp4 in _en_curso:
            continue

        ruta_mp4 = os.path.join(dir_grabaciones, nombre_mp4)
        if not os.path.isfile(ruta_mp4):
            continue

        _en_curso.add(nombre_mp4)
        hilo = threading.Thread(
            target=_generar_en_segundo_plano,
            args=(dir_miniaturas, dir_estado, ffmpeg_exe, nombre_mp4, ruta_mp4,
                  mtime, tam, g.get("duracion_total_seg")),
            daemon=True)
        hilo.start()
        lanzadas += 1
    return lanzadas


def barrer(dir_miniaturas, nombres_vigentes: set) -> int:
    """Borra (os.remove, no papelera: es cache) los .jpg cuyo .mp4 ya no
    esta en `nombres_vigentes`, y purga esas entradas del indice. ->
    cantidad de .jpg borrados."""
    try:
        archivos = os.listdir(dir_miniaturas)
    except OSError:
        return 0

    nombres_cache_vigentes = {_nombre_cache(n) for n in nombres_vigentes}
    borrados = 0
    for archivo in archivos:
        if not archivo.lower().endswith(".jpg") or archivo in nombres_cache_vigentes:
            continue
        try:
            os.remove(os.path.join(dir_miniaturas, archivo))
            borrados += 1
        except OSError:
            pass

    return borrados


def purgar_indice(dir_estado, nombres_vigentes: set) -> None:
    """Quita del indice las entradas cuyo .mp4 ya no esta en
    `nombres_vigentes`. Se llama junto con barrer()."""
    ruta_indice = os.path.join(dir_estado, ARCHIVO_INDICE)
    with _lock_indice:
        indice = _leer_indice(ruta_indice)
        vivos = {n: v for n, v in indice.items() if n in nombres_vigentes}
        if len(vivos) != len(indice):
            _escribir_indice(ruta_indice, vivos)
