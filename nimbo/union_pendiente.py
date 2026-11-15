"""Uniones pendientes (SPEC 23): verlas, protegerlas y reunirlas a mano.

listar() encuentra las sesiones de datos\\streams\\ con segmentos que no
estan en ningun .mp4:

- "sin_unir": hay segmentos y no hay unido.json (una union que fallo, un
  cierre a lo bruto que recuperar_pendientes() todavia no vio, ...).
- "marcador_viejo": hay unido.json, pero el segmento mas nuevo es mas de
  MARGEN_MARCADOR_S posterior a el. Es lo que dejo el bug del 2026-11-08
  (recuperar_pendientes() unia sesiones VIVAS): todo lo grabado despues
  del marcador quedo suelto, --unir lo saltea como "ya unida" y, sin la
  compuerta de cosecha_streams._borrables(), la cosecha lo borraba.

unir() reune UNA sesion lanzando "detectar_m3u8.py --unir <ref>" como
SUBPROCESO -- nunca import: ese modulo termina en "addons = [Detector()]"
(ver CLAUDE.md, "Los dos puentes"). Es el unico lugar de nimbo\\ que lanza
--unir. El turno global de union (cerrojo_union, SPEC 19) lo toma el propio
subproceso; Nimbo no lo pide. Nada es automatico: solo corre cuando el
usuario aprieta "Unir" (POST /api/streams/unir).

Exito = el mtime de unido.json cambio entre antes y despues de correr el
hijo, mismo criterio que grabador_directo._unir_en_fondo (--unir sale con 0
siempre, y no se toca el nucleo para cambiarlo).

Riesgo conocido: "Unir" sobre una sesion que su grabador todavia tiene en
cola (esperando el turno). La union manual deja el marcador y, cuando al
grabador le llega el turno, ve el marcador sin cambio de mtime y loguea un
falso "NO se unio". La mitigacion es GRACIA_PENDIENTE_S: una sesion con un
segmento de hace menos de 10 min no se lista, y en la practica la cola de
un grabador no tarda eso en arrancar despues de cerrar la sesion.

No importa detectar_m3u8.py (mismo motivo que vivo.py y cosecha_streams.py):
MARCADOR, CARPETA_SEGS, CARPETA_PLAYLISTS, SUFIJO_AUDIO y EXT_SEGMENTO son
copias literales de ese modulo, y _hermana_video/_tiene_segmentos calcan
las suyas.
"""

import json
import os
import subprocess
import sys
import time

MARCADOR = "unido.json"              # detectar_m3u8.MARCADOR
CARPETA_SEGS = "segmentos"           # detectar_m3u8.CARPETA_SEGS
CARPETA_PLAYLISTS = "playlists"      # detectar_m3u8.CARPETA_PLAYLISTS
SUFIJO_AUDIO = "_audio"              # detectar_m3u8.SUFIJO_AUDIO (SPEC 22)
EXT_SEGMENTO = (".mp4", ".m4s", ".ts", ".cmfv", ".cmfa")   # detectar_m3u8.EXT_SEGMENTO
FALLIDA = "union_fallida.json"
GRACIA_PENDIENTE_S = 600.0        # el ultimo segmento tiene que tener >10 min
MARGEN_MARCADOR_S = 60.0          # segmentos mas nuevos que el marcador + esto
ESPERA_UNION = 3600               # mismo tope que grabador_directo.ESPERA_UNION
TOPE_MOTIVO = 400

CREAR_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _mtime(ruta):
    try:
        return os.stat(ruta).st_mtime
    except OSError:
        return None


def _leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else None
    except (OSError, ValueError):
        return None


def _escribir_json(ruta, datos):
    """Atomico: .part + os.replace, como todo lo que escribe Nimbo."""
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ruta)


def _stats_segmentos(ruta_sesion):
    """-> (bytes, archivos, ultimo_mtime) de <sesion>\\segmentos\\."""
    total, archivos, ultimo = 0, 0, 0.0
    try:
        with os.scandir(os.path.join(ruta_sesion, CARPETA_SEGS)) as it:
            for e in it:
                if not e.name.lower().endswith(EXT_SEGMENTO):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                archivos += 1
                total += st.st_size
                ultimo = max(ultimo, st.st_mtime)
    except OSError:
        pass
    return total, archivos, ultimo


def _tiene_segmentos(ruta_sesion):
    """Calco de detectar_m3u8._tiene_segmentos."""
    try:
        with os.scandir(os.path.join(ruta_sesion, CARPETA_SEGS)) as it:
            for e in it:
                if e.name.lower().endswith(EXT_SEGMENTO):
                    return True
    except OSError:
        pass
    return False


def _es_pista_audio(ruta_sesion):
    return os.path.basename(ruta_sesion).endswith(SUFIJO_AUDIO)


def _hermana_video(ruta_sesion):
    """Calco de detectar_m3u8._hermana_video: la carpeta de video de la que
    esta pista de audio es acompanante, o None (no es de audio, o su
    hermana no existe o no tiene segmentos -- huerfana)."""
    if not _es_pista_audio(ruta_sesion):
        return None
    otra = ruta_sesion[:-len(SUFIJO_AUDIO)]
    return otra if _tiene_segmentos(otra) else None


def _hermana_audio(ruta_sesion):
    """La carpeta de audio que acompana a esta de video, o None."""
    if _es_pista_audio(ruta_sesion):
        return None
    otra = ruta_sesion + SUFIJO_AUDIO
    return otra if _tiene_segmentos(otra) else None


def _marcador_viejo(ultimo_mtime, marcador_mtime):
    return (marcador_mtime is not None
            and ultimo_mtime > marcador_mtime + MARGEN_MARCADOR_S)


def es_marcador_viejo(ruta_sesion, ultimo_mtime=None):
    """True si la sesion tiene unido.json y segmentos mas de
    MARGEN_MARCADOR_S posteriores a el. Lo usa tambien
    cosecha_streams._borrables() para no borrar la unica copia."""
    if ultimo_mtime is None:
        ultimo_mtime = _stats_segmentos(ruta_sesion)[2]
    return _marcador_viejo(ultimo_mtime, _mtime(os.path.join(ruta_sesion, MARCADOR)))


def _carpetas_sesion(dir_streams):
    """-> [(ref, ruta)] de toda carpeta <stream>\\<sesion>\\ con segmentos\\."""
    if not os.path.isdir(dir_streams):
        return []
    salida = []
    try:
        streams = sorted(os.listdir(dir_streams))
    except OSError:
        return []
    for stream in streams:
        if stream == CARPETA_PLAYLISTS:
            continue
        ruta_stream = os.path.join(dir_streams, stream)
        if not os.path.isdir(ruta_stream):
            continue
        try:
            sesiones = sorted(os.listdir(ruta_stream))
        except OSError:
            continue
        for sesion in sesiones:
            if sesion == CARPETA_PLAYLISTS:
                continue
            ruta = os.path.join(ruta_stream, sesion)
            if os.path.isdir(os.path.join(ruta, CARPETA_SEGS)):
                salida.append((stream + "/" + sesion, ruta))
    return salida


def listar(dir_streams, sesiones_vivas):
    """-> [item] ordenada por ultimo_mtime, la mas vieja primero.

    sesiones_vivas: lo que devuelve vivo.sesiones_vivas() (lista de dicts
    con "sesion") o directamente un set de refs.

    Una "_audio" con hermana de video no aparece sola: se evalua junto a
    su video (si es la de audio la que tiene marcador_viejo, se reporta la
    de video como marcador_viejo, porque la re-union --forzar de la de
    video rehace las dos en el mismo ffmpeg). Las "_audio" huerfanas
    quedan afuera: --unir solo les pone un marcador vacio."""
    vivas = set()
    for s in sesiones_vivas or []:
        vivas.add(s["sesion"] if isinstance(s, dict) else s)
    ahora = time.time()
    salida = []
    for ref, ruta in _carpetas_sesion(dir_streams):
        if _es_pista_audio(ruta):
            continue        # acompanante (se evalua con su video) o huerfana
        b, n, ultimo = _stats_segmentos(ruta)
        if not n:
            continue
        audio = _hermana_audio(ruta)
        ref_audio = ref + SUFIJO_AUDIO
        ab, an, a_ultimo = _stats_segmentos(audio) if audio else (0, 0, 0.0)
        if ref in vivas or (audio and ref_audio in vivas):
            continue
        ultimo_total = max(ultimo, a_ultimo)
        if ahora - ultimo_total < GRACIA_PENDIENTE_S:
            continue

        marcador = _mtime(os.path.join(ruta, MARCADOR))
        if marcador is None:
            tipo = "sin_unir"
        elif _marcador_viejo(ultimo, marcador):
            tipo = "marcador_viejo"
        elif audio and _marcador_viejo(a_ultimo, _mtime(os.path.join(audio, MARCADOR))):
            tipo = "marcador_viejo"
        else:
            # Unida y al dia. Una "_audio" SIN marcador al lado de un video
            # con marcador es el fallback .raw.mp4 de SPEC 22 (el audio se
            # conserva a proposito): fuera de alcance, no es pendiente.
            continue

        fallida = _leer_json(os.path.join(ruta, FALLIDA)) or {}
        salida.append({
            "sesion": ref,
            "tipo": tipo,
            "bytes": b + ab,
            "archivos": n + an,
            "ultimo_mtime": ultimo_total,
            "intentos": int(fallida.get("intentos") or 0),
            "motivo": str(fallida.get("motivo") or ""),
        })
    salida.sort(key=lambda s: s["ultimo_mtime"])
    return salida


# -- unir una -----------------------------------------------------------

def _ruta_de_ref(dir_streams, ref):
    """<stream>/<sesion> -> ruta absoluta adentro de dir_streams, o None."""
    partes = (ref or "").replace("\\", "/").split("/")
    if len(partes) != 2 or not all(partes) or any(
            p in (".", "..") or ":" in p for p in partes):
        return None
    base = os.path.realpath(dir_streams)
    ruta = os.path.realpath(os.path.join(base, *partes))
    try:
        if os.path.commonpath([base, ruta]) != base:
            return None
    except ValueError:
        return None
    return ruta if os.path.isdir(ruta) else None


def _cola(texto_err, texto_out):
    """Las ultimas lineas de stderr + stdout (logging escribe en stderr),
    recortadas a TOPE_MOTIVO caracteres."""
    lineas = [l.strip() for l in ((texto_err or "") + "\n" + (texto_out or "")).splitlines()
              if l.strip()]
    motivo = ""
    for linea in reversed(lineas):
        candidato = linea + (" | " + motivo if motivo else "")
        if len(candidato) > TOPE_MOTIVO:
            break
        motivo = candidato
    if not motivo and lineas:
        motivo = lineas[-1][-TOPE_MOTIVO:]
    return motivo


def _a_texto(b):
    if b is None:
        return ""
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace")
    return b


def _sitio_de(ruta):
    """Solo para el campo "sitio" de estado\\union.json (SPEC 20).
    Chaturbate: tiene pista de audio acompanante, o el "id" de la carpeta
    no es numerico (es el token alfanumerico de /api/chatvideocontext/)."""
    if _hermana_audio(ruta):
        return "chaturbate"
    stream = os.path.basename(os.path.dirname(ruta))
    ident = stream.rpartition("_")[2]
    return "chaturbate" if ident and not ident.isdigit() else ""


def _registrar_fallo(ruta, motivo):
    previo = _leer_json(os.path.join(ruta, FALLIDA)) or {}
    datos = {
        "intentos": int(previo.get("intentos") or 0) + 1,
        "ultimo": time.time(),
        "motivo": (motivo or "fallo sin detalle")[:TOPE_MOTIVO],
    }
    try:
        _escribir_json(os.path.join(ruta, FALLIDA), datos)
    except OSError:
        pass


def _borrar_fallo(ruta):
    try:
        os.remove(os.path.join(ruta, FALLIDA))
    except OSError:
        pass


def _reescribir_salida(ruta_sesion, de, a):
    """Cambia el "salida" del marcador de esa carpeta si apuntaba a 'de'."""
    marcador = os.path.join(ruta_sesion, MARCADOR)
    datos = _leer_json(marcador)
    if not datos or os.path.normcase(datos.get("salida") or "") != os.path.normcase(de):
        return
    datos["salida"] = a
    _escribir_json(marcador, datos)


def _reemplazar_mp4(ruta, salida_vieja, salida_nueva, avisar):
    """marcador_viejo, despues de una re-union exitosa: el .mp4 viejo
    (incompleto) va a la Papelera y el nuevo toma su nombre. Si algo falla,
    queda el nuevo con su _N y se avisa; el nuevo NUNCA se borra."""
    if not salida_vieja or not salida_nueva:
        return
    if os.path.normcase(salida_vieja) == os.path.normcase(salida_nueva):
        return
    if not os.path.isfile(salida_nueva):
        return
    if salida_nueva.lower().endswith(".raw.mp4"):
        # La re-union no pudo remuxear: no se cambia un .mp4 armado por un
        # crudo. Quedan los dos.
        avisar("union manual: %s salio en crudo (.raw.mp4); el .mp4 anterior "
               "no se toca" % os.path.basename(salida_nueva), nivel="warning")
        return
    destino = salida_vieja
    if destino.lower().endswith(".raw.mp4"):
        destino = destino[:-len(".raw.mp4")] + ".mp4"
    try:
        if os.path.exists(salida_vieja):
            import grabaciones as grabaciones_mod    # perezoso: ctypes/Windows
            grabaciones_mod._a_la_papelera([salida_vieja])
        if os.path.exists(destino):
            raise OSError("%s ya existe" % os.path.basename(destino))
        os.rename(salida_nueva, destino)
    except OSError as e:
        avisar("union manual: no se pudo dejar %s con el nombre original (%s); "
               "queda como %s" % (os.path.basename(destino), e,
                                  os.path.basename(salida_nueva)), nivel="warning")
        return
    for carpeta in (ruta, ruta + SUFIJO_AUDIO):
        try:
            _reescribir_salida(carpeta, salida_nueva, destino)
        except OSError:
            pass


def _avisar_nulo(texto, nivel="info"):
    pass


def unir(ajustes, ref, tipo, avisar=None):
    """Une UNA sesion pendiente. -> (ok: bool, motivo: str).

    'ref' tiene que haber salido de listar() -- quien llama lo verifica
    (servidor.py). Con tipo == "marcador_viejo" va --forzar y, si sale
    bien, el .mp4 viejo se reemplaza por el nuevo (_reemplazar_mp4)."""
    import motor as motor_mod   # perezoso: motor importa mucho
    avisar = avisar or _avisar_nulo
    a = ajustes
    ruta = _ruta_de_ref(a.streams, ref)
    if ruta is None:
        return False, "sesion inexistente"
    marcador = os.path.join(ruta, MARCADOR)
    antes = _mtime(marcador)
    salida_vieja = ""
    if tipo == "marcador_viejo":
        salida_vieja = str((_leer_json(marcador) or {}).get("salida") or "")

    cmd = [a["python"], os.path.join(a.scripts, "detectar_m3u8.py"), "--unir", ref]
    if tipo == "marcador_viejo":
        cmd.append("--forzar")
    env = motor_mod.entorno_union(a)
    sitio = _sitio_de(ruta)
    if sitio:
        env["NIMBO_UNION_SITIO"] = sitio

    motivo = ""
    try:
        proc = subprocess.run(
            cmd, cwd=a.scripts, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=ESPERA_UNION,
            creationflags=CREAR_SIN_VENTANA,
        )
        salida_err, salida_out = _a_texto(proc.stderr), _a_texto(proc.stdout)
    except subprocess.TimeoutExpired as e:
        motivo = "timeout: --unir no termino en %d s" % ESPERA_UNION
        cola = _cola(_a_texto(e.stderr), _a_texto(e.stdout))
        if cola:
            motivo = (motivo + " | " + cola)[:TOPE_MOTIVO]
        _registrar_fallo(ruta, motivo)
        return False, motivo
    except OSError as e:
        motivo = "no se pudo lanzar --unir: %s" % e
        _registrar_fallo(ruta, motivo)
        return False, motivo

    despues = _mtime(marcador)
    if despues is None or despues == antes:
        motivo = _cola(salida_err, salida_out) or "--unir termino sin dejar unido.json"
        _registrar_fallo(ruta, motivo)
        return False, motivo

    if tipo == "marcador_viejo":
        salida_nueva = str((_leer_json(marcador) or {}).get("salida") or "")
        _reemplazar_mp4(ruta, salida_vieja, salida_nueva, avisar)
    _borrar_fallo(ruta)
    return True, ""
