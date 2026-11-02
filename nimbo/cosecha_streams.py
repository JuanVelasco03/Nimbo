"""Borra sin vuelta atras las sesiones ya unidas de datos\\streams\\, para que
las copias crudas en fMP4 no se acumulen para siempre al lado del .mp4 ya
armado en datos\\grabaciones\\ (ver CLAUDE.md, seccion "Reproductor en
vivo").

A diferencia de grabaciones.py, esto NO va a la Papelera (SPEC 14). Un .mp4
son horas de grabacion y la papelera es la red para un click de mas; un
segmento ya unido es regenerable mientras exista ese .mp4, y ya paso las tres
compuertas de _borrables(). Y con los segmentos en el SSD de sistema la
papelera era el problema entero: el espacio no volvia hasta que Windows
purgara por tope, asi que barrer() informaba bytes liberados que seguian
ocupados. Mismo criterio que miniaturas.py con su cache.

No importa detectar_m3u8.py por el mismo motivo que vivo.py no lo importa:
su "else: addons = [Detector()]" construiria un Detector entero VIVO con
cualquier import que no sea __main__. MARCADOR, CARPETA_SEGS y
CARPETA_PLAYLISTS son copias literales de ese modulo (lineas 194-196); el
recorrido de sesiones_unidas() calca sesiones_en_disco() (lineas 1640-1666),
agregando el tamano en bytes y el mtime del marcador y del ultimo archivo.
"""

import os
import shutil
import time

import vivo as vivo_mod

MARCADOR = "unido.json"              # detectar_m3u8.py:194
CARPETA_SEGS = "segmentos"           # detectar_m3u8.py:195 (= vivo.CARPETA_SEGS)
CARPETA_PLAYLISTS = "playlists"      # detectar_m3u8.py:196 (= vivo.CARPETA_PLAYLISTS)

# Margen por encima de OLVIDAR_STREAM (180s en detectar_m3u8.py): aunque el
# marcador ya este escrito, no se toca una carpeta donde algo siguio
# escribiendo hace menos de esto. No es vivo.UMBRAL_VIVO_S (10s): ese umbral
# es solo para el badge "en vivo" del frontend, este es defensa en
# profundidad contra un escritor todavia activo.
GRACIA_MTIME_S = 300.0


def _mtime(ruta):
    try:
        return os.stat(ruta).st_mtime
    except OSError:
        return 0.0


def sesiones_unidas(dir_streams):
    """-> [{"sesion","ruta","bytes","archivos","unido_mtime","ultimo_mtime"}]
    Solo sesiones con MARCADOR -- sin el, es una grabacion pendiente que
    recuperar_pendientes() todavia puede rehacer y es la unica copia que
    existe. "sesion" va en la misma forma "<modelo>_<id>/<variante>_<sesion>"
    que usa vivo.sesiones_vivas(), para poder cruzarlas directo."""
    if not os.path.isdir(dir_streams):
        return []
    salida = []
    for stream in sorted(os.listdir(dir_streams)):
        if stream == CARPETA_PLAYLISTS:
            continue
        ruta_stream = os.path.join(dir_streams, stream)
        if not os.path.isdir(ruta_stream):
            continue
        try:
            nombres_sesion = sorted(os.listdir(ruta_stream))
        except OSError:
            continue
        for sesion in nombres_sesion:
            if sesion == CARPETA_PLAYLISTS:
                continue
            ruta = os.path.join(ruta_stream, sesion)
            marcador = os.path.join(ruta, MARCADOR)
            if not os.path.isfile(marcador):
                continue
            segs = os.path.join(ruta, CARPETA_SEGS)
            total_bytes = 0
            archivos = 0
            ultimo_mtime = 0.0
            try:
                with os.scandir(segs) as it:
                    for entrada in it:
                        try:
                            st = entrada.stat()
                        except OSError:
                            continue
                        archivos += 1
                        total_bytes += st.st_size
                        ultimo_mtime = max(ultimo_mtime, st.st_mtime)
            except OSError:
                pass
            salida.append({
                "sesion": stream + "/" + sesion,
                "ruta": ruta,
                "bytes": total_bytes,
                "archivos": archivos,
                "unido_mtime": _mtime(marcador),
                "ultimo_mtime": ultimo_mtime,
            })
    return salida


def _borrables(sesiones, nombres_vivas, dias):
    ahora = time.time()
    limite_edad = dias * 86400
    salida = []
    for s in sesiones:
        if s["sesion"] in nombres_vivas:
            continue
        if ahora - s["unido_mtime"] < limite_edad:
            continue
        if ahora - s["ultimo_mtime"] < GRACIA_MTIME_S:
            continue
        salida.append(s)
    return salida


def resumen(dir_streams, estado_detector, dias):
    """-> {"sesiones","bytes","borrables","bytes_borrables"}"""
    sesiones = sesiones_unidas(dir_streams)
    vivas = {s["sesion"] for s in vivo_mod.sesiones_vivas(dir_streams, estado_detector)}
    borrables = _borrables(sesiones, vivas, dias)
    return {
        "sesiones": len(sesiones),
        "bytes": sum(s["bytes"] for s in sesiones),
        "borrables": len(borrables),
        "bytes_borrables": sum(s["bytes"] for s in borrables),
    }


def _borrar_definitivo(ruta):
    """Borra entera una carpeta de sesion, sin pasar por la Papelera.

    MARCADOR se borra AL FINAL, y solo si todo el resto salio bien. Si se
    fuera primero y algo quedara trabado, esa carpeta ya no volveria a
    aparecer en sesiones_unidas() y quedaria huerfana ocupando disco para
    siempre; mientras el marcador siga ahi, el barrido siguiente la reintenta.

    Levanta OSError si algo no se pudo borrar -- shutil.rmtree y os.remove ya
    lo hacen solos, no hace falta envolverlos. barrer() lo atrapa y anota la
    sesion en "errores"."""
    with os.scandir(ruta) as it:
        entradas = list(it)
    for entrada in entradas:
        if entrada.name == MARCADOR:
            continue
        if entrada.is_dir():
            shutil.rmtree(entrada.path)
        else:
            os.remove(entrada.path)
    os.remove(os.path.join(ruta, MARCADOR))
    os.rmdir(ruta)


def barrer(dir_streams, estado_detector, dias):
    """-> {"borradas": [sesion...], "bytes": N, "errores": {sesion: motivo}}
    Borra una carpeta por vez para que una trabada (un archivo todavia
    abierto, el indexador de Windows) no impida borrar las demas: la que
    falla queda en "errores" con el motivo, conserva su unido.json y se
    reintenta en el barrido siguiente."""
    sesiones = sesiones_unidas(dir_streams)
    vivas = {s["sesion"] for s in vivo_mod.sesiones_vivas(dir_streams, estado_detector)}
    borradas, errores = [], {}
    total_bytes = 0
    for s in _borrables(sesiones, vivas, dias):
        try:
            _borrar_definitivo(s["ruta"])
            borradas.append(s["sesion"])
            total_bytes += s["bytes"]
        except OSError as e:
            errores[s["sesion"]] = str(e)
    return {"borradas": borradas, "bytes": total_bytes, "errores": errores}
