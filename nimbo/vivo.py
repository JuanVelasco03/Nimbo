"""Lectura de los segmentos de una sesion viva para el reproductor en
vivo del modal de previa (ver web\\js\\reproductor_vivo.js).

Esto NO lee playlists\\*.m3u8, no mira #EXT-X-MOUFLON y no pide nada por
red: el Chromium del supervisor ya resolvio esas URIs y mitmproxy ya copio
los cuerpos -- lo unico que hace este modulo es listdir + open sobre lo
que detectar_m3u8.py (Escritor, _guardar) ya escribio en
datos\\streams\\<modelo>_<id>\\<variante>_<sesion>\\. Ver CLAUDE.md,
seccion "Reproductor en vivo", para el porque completo.

RE_SEGMENTO, PARTE_ENTERA, EXT_SEGMENTO, CARPETA_SEGS y CARPETA_PLAYLISTS
son copias literales de detectar_m3u8.py (lineas 246, 249, 270, 195, 196):
no se importa ese modulo porque su "else: addons = [Detector()]" (lo que
corre con cualquier import que no sea __main__) construiria un Detector
entero -- con su Escritor, su _Tunel, su hilo vigilante y su finalizador
-- VIVO dentro del proceso de app.py.
"""

import os
import re
import threading
import time

import mp4

RE_SEGMENTO = re.compile(
    r"_(\d+)_[A-Za-z0-9]+_(\d+)(?:_part(\d+))?\.[A-Za-z0-9]+$")
PARTE_ENTERA = 1 << 30
EXT_SEGMENTO = (".mp4", ".m4s", ".ts", ".cmfv", ".cmfa")
CARPETA_SEGS = "segmentos"
CARPETA_PLAYLISTS = "playlists"

# una sesion sin archivos nuevos hace mas de esto ya no se considera "vivo"
# para el badge -- el detector la cierra sola bastante antes (OLVIDAR_STREAM
# en detectar_m3u8.py es 180 s), esto es solo para que el frontend no se
# quede mostrando un video como "en vivo" largo rato despues de un corte.
UMBRAL_VIVO_S = 10.0

# Cuantos grupos (epoch, secuencia) pueden quedar sin mandar antes de
# avisarle al cliente "salto" (= "veni a buscar desde el borde vivo, no
# trates de alcanzar el backlog"). NO puede ser 0: un segmento CMAF
# "standard" del camino directo trae varios moof pegados que
# grabador_directo parte en <nombre>_partN, asi que un grupo son ~4
# archivos y `limite` (8 en reproductor_vivo.js) no alcanza para los
# `atras` grupos que pide el arranque -- con el calculo viejo
# (pendientes > 0) "salto" daba True SIEMPRE, el cliente pedia
# desde=None en cada tick, _arranque le devolvia la misma ventana y
# terminaba reappendeando el mismo tramo cada 500 ms (medido el
# 2026-10-24: eso es lo que se veia como "saltos" y video repetido).
GRUPOS_BACKLOG_SALTO = 3

_lock_cache = threading.Lock()
_cache_perfiles = {}   # ruta_init -> (mtime, size, perfil)


def clave_segmento(nombre: str):
    """-> (epoch, secuencia, parte) o None. Identico a
    detectar_m3u8.py:clave_segmento -- manda el epoch, no la secuencia,
    porque un reinicio de emision hace que la secuencia vuelva a cero."""
    m = RE_SEGMENTO.search(nombre)
    if not m:
        return None
    parte = m.group(3)
    return (int(m.group(2)), int(m.group(1)),
            int(parte) if parte is not None else PARTE_ENTERA)


def cursor_de(clave) -> str:
    return "%d:%d:%d" % clave


def clave_de_cursor(texto):
    if not texto:
        return None
    partes = texto.split(":")
    if len(partes) != 3:
        return None
    try:
        return (int(partes[0]), int(partes[1]), int(partes[2]))
    except ValueError:
        return None


# -- seguridad: la ruta de sesion llega por querystring, nunca confiar ----

def _resolver(dir_streams, rel) -> str | None:
    """rel es "<modelo>_<id>/<variante>_<sesion>" (con "/", aunque estemos
    en Windows) -- exactamente dos componentes, ninguno "..", ninguno
    "playlists" (para no poder apuntar nunca a ese directorio). Calcado del
    patron de grabaciones.py:_resolver, con un nivel mas."""
    if not rel:
        return None
    partes = rel.split("/")
    if len(partes) != 2:
        return None
    for p in partes:
        if not p or "\\" in p or p in (".", "..") or p == CARPETA_PLAYLISTS:
            return None
    ruta = os.path.join(dir_streams, *partes)
    base = os.path.realpath(dir_streams)
    destino = os.path.realpath(ruta)
    if os.path.commonpath([base, destino]) != base:
        return None
    return ruta


def _relativa(dir_streams, carpeta_abs) -> str | None:
    try:
        rel = os.path.relpath(carpeta_abs, dir_streams)
    except ValueError:
        return None
    rel = rel.replace(os.sep, "/")
    if _resolver(dir_streams, rel) is None:
        return None
    return rel


# -- quien esta vivo ahora mismo, segun detector.json ----------------------

def sesiones_vivas(dir_streams, estado_detector: dict) -> list:
    """-> [{"nombre","id","sesion","carpeta","calidad","codec","variante",
    "elegida"}], una entrada por Stream con sesiones en estado_detector
    (ver puente.py: el campo "sesiones" que se agrego por fila)."""
    salida = []
    for fila in (estado_detector or {}).get("streams") or []:
        sesiones = fila.get("sesiones") or []
        if not sesiones:
            continue
        for s in sesiones:
            carpeta = s.get("carpeta") or ""
            rel = _relativa(dir_streams, carpeta) if carpeta else None
            if rel is None:
                continue
            salida.append({
                "nombre": fila.get("modelo", ""), "id": fila.get("id", ""),
                "sesion": rel, "variante": s.get("variante", ""),
                "calidad": s.get("calidad", ""), "codec": s.get("codec", ""),
                "elegida": s.get("variante") == fila.get("elegida"),
            })
    return salida


# -- el init de la sesion, con el de una hermana como red de seguridad ----

def _init_de_hermana(carpeta_sesion: str) -> str:
    """Puerto literal de detectar_m3u8.py:_init_de_hermana (linea 1346):
    el init describe pistas/timescale y es el mismo mientras la modelo no
    reinicie la emision, asi que el de una sesion hermana (misma variante,
    mismo stream) sirve si esta sesion no tiene uno propio todavia."""
    padre = os.path.dirname(carpeta_sesion)
    variante = os.path.basename(carpeta_sesion).rsplit("_", 1)[0]
    candidatos = []
    try:
        hermanas = os.listdir(padre)
    except OSError:
        return ""
    for nombre in hermanas:
        otra = os.path.join(padre, nombre)
        if otra == carpeta_sesion or not os.path.isdir(otra):
            continue
        if nombre.rsplit("_", 1)[0] != variante:
            continue
        try:
            for n in sorted(os.listdir(otra)):
                if n.lower().startswith("init"):
                    candidatos.append((os.path.getmtime(otra),
                                        os.path.join(otra, n)))
                    break
        except OSError:
            continue
    return max(candidatos)[1] if candidatos else ""


def ruta_init(carpeta: str) -> str:
    try:
        for n in sorted(os.listdir(carpeta)):
            if n.lower().startswith("init"):
                return os.path.join(carpeta, n)
    except OSError:
        return ""
    return _init_de_hermana(carpeta)


def perfil_cacheado(carpeta: str) -> dict:
    """mp4.perfil_de_init, cacheado por (ruta, mtime, size) -- el init no
    cambia mientras dure la sesion, no hace falta reparsear en cada tick."""
    ruta = ruta_init(carpeta)
    if not ruta:
        return {}
    try:
        st = os.stat(ruta)
    except OSError:
        return {}
    clave = (st.st_mtime, st.st_size)
    with _lock_cache:
        visto = _cache_perfiles.get(ruta)
        if visto and visto[0] == clave:
            return dict(visto[1], ruta=ruta)
    perfil = mp4.perfil_de_init(ruta)
    with _lock_cache:
        _cache_perfiles[ruta] = (clave, perfil)
    return dict(perfil, ruta=ruta) if perfil else {}


# -- candidatos, dedup y punto de arranque seguro --------------------------

def _candidatos(carpeta_segs) -> list:
    """-> [(clave, nombre)] ordenado por clave. Ignora cualquier nombre que
    no pase EXT_SEGMENTO (eso ya descarta los ".part" a medio escribir:
    ".mp4.part".endswith(".mp4") es False) o que no matchee RE_SEGMENTO."""
    try:
        nombres = os.listdir(carpeta_segs)
    except OSError:
        return []
    pares = []
    for n in nombres:
        if not n.lower().endswith(EXT_SEGMENTO):
            continue
        clave = clave_segmento(n)
        if clave is None:
            continue
        pares.append((clave, n))
    pares.sort(key=lambda p: p[0])
    return pares


def _filtrar_duplicados(pares: list) -> list:
    """Dos guardas contra appendear 2 veces el mismo contenido (medido:
    no ocurre en ~17h de datos reales, pero en mode='sequence' del
    SourceBuffer un duplicado no se detecta por timestamp -- ver
    CLAUDE.md, "Reproductor en vivo"):

    1) si un grupo (epoch, secuencia) tiene alguna parte, se descarta la
       entrada PARTE_ENTERA de ese mismo grupo.
    2) se descarta tambien cualquier PARTE_ENTERA cuya secuencia ya haya
       aparecido con partes en OTRO grupo (epoch distinto).
    """
    grupos = {}
    for clave, nombre in pares:
        grupos.setdefault((clave[0], clave[1]), []).append((clave, nombre))

    secuencias_con_partes = {
        g[1] for g, entradas in grupos.items()
        if any(c[2] != PARTE_ENTERA for c, _ in entradas)
    }

    salida = []
    for (epoch, secuencia), entradas in grupos.items():
        tiene_partes = any(c[2] != PARTE_ENTERA for c, _ in entradas)
        for clave, nombre in entradas:
            if clave[2] == PARTE_ENTERA:
                if tiene_partes:
                    continue                           # guarda 1
                if secuencia in secuencias_con_partes:
                    continue                           # guarda 2
            salida.append((clave, nombre))
    salida.sort(key=lambda p: p[0])
    return salida


def _arranque(pares: list, atras: int) -> int:
    """Indice del primer archivo "seguro" para arrancar: el comienzo de uno
    de los ultimos `atras` grupos (epoch, secuencia) cuyo primer archivo sea
    un keyframe garantizado -- parte 0, o un segmento entero sin partes (las
    partes LL-HLS no son independientes: solo la parte 0 arranca en IDR)."""
    if not pares:
        return 0
    inicios = []
    anterior = None
    for i, (clave, _nombre) in enumerate(pares):
        grupo = (clave[0], clave[1])
        if grupo != anterior:
            inicios.append(i)
            anterior = grupo
    seguros = [i for i in inicios if pares[i][0][2] in (0, PARTE_ENTERA)]
    if not seguros:
        return 0            # ninguno seguro: mejor esfuerzo, desde el principio
    return seguros[-atras] if len(seguros) >= atras else seguros[0]


# -- API publica ------------------------------------------------------------

def listar(dir_streams, rel, desde=None, limite=40, atras=3) -> dict:
    carpeta = _resolver(dir_streams, rel)
    if carpeta is None or not os.path.isdir(carpeta):
        return {}
    carpeta_segs = os.path.join(carpeta, CARPETA_SEGS)
    pares = _filtrar_duplicados(_candidatos(carpeta_segs))

    if desde is None:
        indice = _arranque(pares, atras)
    else:
        clave_desde = clave_de_cursor(desde)
        if clave_desde is None:
            indice = _arranque(pares, atras)
        else:
            indice = 0
            while indice < len(pares) and pares[indice][0] <= clave_desde:
                indice += 1

    ventana = pares[indice:indice + max(1, limite)]
    # nunca cortar un grupo a la mitad: las partes de un mismo segmento
    # CMAF no son independientes (solo la parte 0 arranca en IDR), y
    # dejar la mitad afuera obliga al cliente a pedir de nuevo el grupo
    # entero en el tick siguiente.
    if ventana and indice + len(ventana) < len(pares):
        grupo_final = ventana[-1][0][:2]
        j = indice + len(ventana)
        while j < len(pares) and pares[j][0][:2] == grupo_final:
            ventana.append(pares[j])
            j += 1
    resto = pares[indice + len(ventana):]
    pendientes = len({(c[0], c[1]) for c, _ in resto})

    ultimo_mtime = 0.0
    if pares:
        try:
            ultimo_mtime = os.path.getmtime(
                os.path.join(carpeta_segs, pares[-1][1]))
        except OSError:
            pass

    perfil = perfil_cacheado(carpeta)
    segmentos = []
    for clave, nombre in ventana:
        try:
            tam = os.path.getsize(os.path.join(carpeta_segs, nombre))
        except OSError:
            continue
        segmentos.append({
            "nombre": nombre, "cursor": cursor_de(clave), "bytes": tam,
            "epoch": clave[0], "secuencia": clave[1],
            "parte": None if clave[2] == PARTE_ENTERA else clave[2],
        })

    return {
        "sesion": rel,
        "cursor": segmentos[-1]["cursor"] if segmentos else desde,
        "init": ({"nombre": os.path.basename(perfil["ruta"])}
                  if perfil.get("ruta") else None),
        "codecs": perfil.get("codecs"),
        "mime": perfil.get("mime"),
        "segmentos": segmentos,
        "salto": pendientes > GRUPOS_BACKLOG_SALTO,
        "vivo": bool(ultimo_mtime) and (time.time() - ultimo_mtime) < UMBRAL_VIVO_S,
        "ultimo_mtime": ultimo_mtime,
    }


def leer_init(dir_streams, rel) -> bytes | None:
    carpeta = _resolver(dir_streams, rel)
    if carpeta is None:
        return None
    ruta = ruta_init(carpeta)
    if not ruta or not os.path.isfile(ruta):
        return None
    try:
        with open(ruta, "rb") as f:
            return f.read()
    except OSError:
        return None


def leer_segmento(dir_streams, rel, nombre) -> bytes | None:
    carpeta = _resolver(dir_streams, rel)
    if carpeta is None or not nombre:
        return None
    if "/" in nombre or "\\" in nombre or nombre in (".", ".."):
        return None
    if clave_segmento(nombre) is None:
        return None
    ruta = os.path.join(carpeta, CARPETA_SEGS, nombre)
    base = os.path.realpath(os.path.join(carpeta, CARPETA_SEGS))
    if os.path.commonpath([base, os.path.realpath(ruta)]) != base:
        return None
    if not os.path.isfile(ruta):
        return None
    try:
        with open(ruta, "rb") as f:
            return f.read()
    except OSError:
        return None
