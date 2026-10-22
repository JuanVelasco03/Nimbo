# -*- coding: utf-8 -*-
"""Sonda del camino directo: bajar segmentos de doppiocdn sin navegador.

Herramienta de diagnostico, NO parte del motor de captura. Prueba, fase por
fase, si se puede reemplazar al Chromium del supervisor (nucleo\\scripts\\
supervisor.py) por pedidos HTTP pelados, y deja la evidencia impresa para que
la decision de integrar o no se tome con datos.

Por que existe: la premisa documentada en CLAUDE.md y en el docstring de
supervisor.py ("las URLs vienen ofuscadas con #EXT-X-MOUFLON y solo el
reproductor del sitio sabe pedirlas") es falsa. El navegador no descifra nada:
solo le presenta al CDN una "pkey" que viaja en claro en el propio master
playlist, y el CDN devuelve la media playlist con las URLs reales ya resueltas.
Sin esa pkey, la misma URL redirige a una playlist de publicidad.

Restricciones duras (ver recomendaciones/plan de la sesion):
  - Solo libreria estandar. Nada de requests, nada de Playwright.
  - NUNCA importar nucleo\\scripts\\detectar_m3u8.py: su "else: addons =
    [Detector()]" arma un Detector entero y vivo con solo importarlo. Las
    constantes que hacen falta de ahi se PORTAN copiadas (con la linea de
    origen citada), igual que ya hacen nimbo\\mp4.py y nimbo\\vivo.py.
  - Escribe solo bajo --salida (por defecto %TEMP%\\nimbo_sonda\\). Nunca en
    datos\\streams\\, estado\\ o nucleo\\.

Uso:
    python pruebas\\sonda_directo.py <nombre_modelo> [--fase N] [--id ID]
        [--salida CARPETA] [--vpn] [--minutos 10]

Cada fase se corre en orden y se aborta en la primera que falla; "--fase N"
corre solo hasta la N inclusive. "--fase 0" no pide red: es el canario de
que el esquema de cifrado (el KEY_MAP de abajo) sigue vigente.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# la consola de Windows suele quedar en cp1252: si algun dato crudo del CDN
# trae un caracter raro (p.ej. al loguear una falla), que no tire excepcion
sys.stdout.reconfigure(errors="replace")

# --------------------------------------------------------------------------
# Rutas del proyecto (esto NO es el proyecto: es un script suelto en
# pruebas\, al lado de nimbo\ y nucleo\, igual que ya preve motor.py:409
# con "pruebas\prueba_motor.py")
# --------------------------------------------------------------------------

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(RAIZ, "nucleo", "scripts", "ffmpeg.exe")

SITIO = "https://es.stripchat.com"
TUNEL = "127.0.0.1:25345"  # ver nucleo\scripts\detectar_m3u8.py:166

# Sin esto el sitio contesta 406 a lo que no parece un navegador.
# Portado de nucleo\scripts\supervisor.py:121-125 (CABECERAS).
CABECERAS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

# --------------------------------------------------------------------------
# Portado de nucleo\scripts\detectar_m3u8.py (NUNCA importado, ver docstring)
# --------------------------------------------------------------------------

# detectar_m3u8.py:246-247 -- nombre de un segmento de doppiocdn:
#   <id>[_<calidad>_<codec>]_<secuencia>_<hash>_<epoch>[_part<n>].mp4
RE_SEGMENTO = re.compile(
    r"_(\d+)_[A-Za-z0-9]+_(\d+)(?:_part(\d+))?\.[A-Za-z0-9]+$")
PARTE_ENTERA = 1 << 30  # detectar_m3u8.py:249


def clave_segmento(nombre: str):
    """Portado de detectar_m3u8.py:950-963. -> (epoch, secuencia, parte)."""
    m = RE_SEGMENTO.search(nombre)
    if not m:
        return None
    parte = m.group(3)
    return (int(m.group(2)), int(m.group(1)),
            int(parte) if parte is not None else PARTE_ENTERA)


# --------------------------------------------------------------------------
# Esquema de cifrado MOUFLON v2
#
# El master playlist trae una lista de tokens "pkey" (#EXT-X-MOUFLON:PSCH:v2:
# <token>) y la media playlist real se pide agregandole uno de esos tokens a
# la query (psch=v2&pkey=<token>). Cada URI real de segmento viaja en una
# linea "#EXT-X-MOUFLON:URI:<url>" INMEDIATAMENTE ANTES de la linea de HLS
# estandar (que siempre apunta a un nombre senuelo, .../media.mp4); esa URL
# real tiene el campo de hash cifrado: 22 caracteres base64 en vez de los 16
# alfanumericos del nombre de archivo real.
#
# Descifrado (igual para los 11 pkey conocidos):
#   token_invertido = hash[::-1]
#   + padding "=" hasta multiplo de 4
#   bytes = base64.b64decode(token_invertido)
#   claro = bytes XOR sha256(pdkey).digest() ciclado
#
# OJO: el par pkey:pdkey NO sale de una tabla publica fija -- se confirmo
# bajando el codigo real de referencia (github.com/aitschti/scp-standalone,
# scp-standalone-v2.py:69-100,789-827): el pdkey lo tiene que aportar el
# usuario a mano en su propio "keys.txt", tipicamente sacandolo una vez de
# una sesion de navegador real. El algoritmo de descifrado SI es publico y
# coincide byte a byte con el de abajo.
#
# El unico par que tenemos verificado es el que se pudo derivar comparando
# un #EXT-X-MOUFLON:URI: ya en disco (cifrado por el navegador logueado)
# contra el nombre de archivo real que grabo el detector para esa misma
# sesion. El master lista 11 pkeys (los otros 10 tambien aparecen en el
# master, en texto plano), pero sin su pdkey no sirven para descifrar.
#
# Lo que SI se confirmo en vivo (ver fase 3): el CDN acepta cualquiera de
# los 11 pkeys que el propio master ofrece y codifica la respuesta con ESE
# pkey -- no hace falta que sea "el que le tocaba" a esa sesion. Por eso
# alcanza con forzar siempre el unico pkey del que tenemos pdkey.
PKEY_VERIFICADO = "NTK9aqcLmNFMWrpQ"
KEY_MAP = {
    PKEY_VERIFICADO: "tOcYOap4Ty1l9Jzb",
}

# (pkey, texto_cifrado, texto_claro_esperado) -- extraidos a mano de
# datos\streams\_gaby2_171961503\playlists\171961503_1080p.m3u8 (msn
# 1849-1852) y de los nombres reales en la carpeta segmentos\ de esa misma
# sesion. Sirven de canario: si esto deja de descifrar bien, el esquema
# cambio y no tiene sentido seguir con las fases siguientes.
PARES_CANARIO = [
    ("NTK9aqcLmNFMWrpQ", "gNmEqXlhpNtSoM7FD7my0L", "7taceM4bsiPt9RHl"),
    ("NTK9aqcLmNFMWrpQ", "wOWpYF7NKauDddERS/E+UU", "IwCrprs607kjry8a"),
    ("NTK9aqcLmNFMWrpQ", "AHjkaLXhbG6OoaaFj1qqHc", "hBmYelledFpFJZMF"),
]

RE_TOKEN_SEGMENTO = re.compile(r"_(\d+)_([A-Za-z0-9+/=]{22})_(\d{10})")


def descifrar_token(token_b64: str, pdkey: str) -> str:
    """token invertido + base64 -> XOR contra sha256(pdkey) ciclado."""
    invertido = token_b64[::-1]
    invertido += "=" * (-len(invertido) % 4)
    crudo = base64.b64decode(invertido)
    clave = hashlib.sha256(pdkey.encode("utf-8")).digest()
    return bytes(b ^ clave[i % len(clave)]
                 for i, b in enumerate(crudo)).decode("utf-8", "replace")


def es_token_valido(claro: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{16}", claro))


# --------------------------------------------------------------------------
# HTTP minimo, con proxy opcional al tunel de la VPN
# --------------------------------------------------------------------------

def _opener(por_tunel: bool):
    if not por_tunel:
        return urllib.request.build_opener()
    proxy = urllib.request.ProxyHandler({
        "http": "http://" + TUNEL, "https": "http://" + TUNEL,
    })
    return urllib.request.build_opener(proxy)


def pedir(url: str, por_tunel=False, cabeceras=None, permitir_redirect=True):
    """GET pelado. -> (status, url_final, cuerpo_bytes) o lanza."""
    todas = dict(CABECERAS)
    if cabeceras:
        todas.update(cabeceras)
    req = urllib.request.Request(url, headers=todas)
    opener = _opener(por_tunel)
    if not permitir_redirect:
        class _SinRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        opener = urllib.request.build_opener(
            *([h for h in opener.handlers
               if not isinstance(h, urllib.request.HTTPRedirectHandler)]
              + [_SinRedirect()]))
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.geturl(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url, e.read()


# --------------------------------------------------------------------------
# Fases
# --------------------------------------------------------------------------

class Falla(Exception):
    pass


def ok(msg):
    print("  OK   %s" % msg)


def falla(msg):
    print("  FALLA %s" % msg)
    raise Falla(msg)


def fase_0_cripto():
    print("[fase 0] verificar el esquema de cifrado (sin red, canario)")
    for pkey, cifrado, esperado in PARES_CANARIO:
        pdkey = KEY_MAP.get(pkey)
        if not pdkey:
            falla("pkey %s no esta en KEY_MAP" % pkey)
        claro = descifrar_token(cifrado, pdkey)
        if claro != esperado:
            falla("%s -> %r, esperaba %r (el esquema cambio)"
                  % (cifrado, claro, esperado))
    ok("%d pares canario descifran correcto" % len(PARES_CANARIO))


def fase_1_id_y_estado(nombre: str, id_forzado, por_tunel: bool):
    print("[fase 1] resolver id y estado de '%s' sin navegador" % nombre)
    id_modelo = id_forzado
    if id_modelo is None:
        url = "%s/api/front/users/user-ids/%s?%d" % (SITIO, nombre,
                                                       int(time.time() * 1000))
        status, _, cuerpo = pedir(url, por_tunel)
        if status != 200:
            falla("user-ids dio %s (¿Cloudflare bloquea la API anonima? "
                  "seguir con --id a mano)" % status)
        datos = json.loads(cuerpo)
        id_modelo = datos.get("id")
        if not id_modelo:
            falla("user-ids 200 pero sin 'id': %r" % datos)
        ok("id resuelto por la API: %s" % id_modelo)
    else:
        ok("id forzado por --id: %s" % id_modelo)

    url = ("%s/api/front/v2/models/%s/cam?timezoneOffset=0&"
           "triggerRequest=loadCam&uniq=%d" % (SITIO, id_modelo,
                                                int(time.time() * 1000)))
    status, _, cuerpo = pedir(url, por_tunel)
    if status != 200:
        falla("cam dio %s" % status)
    datos = json.loads(cuerpo)
    estado = (datos.get("user", {}).get("user", {}) or {}).get("status")
    disponible = (datos.get("cam", {}) or {}).get("isCamAvailable")
    geobaneada = (datos.get("user", {}).get("user", {}) or {}).get(
        "isGeoBanned")
    print("  estado=%r isCamAvailable=%r isGeoBanned=%r"
          % (estado, disponible, geobaneada))
    vivos = {"public", "live", "groupShow", "p2p", "private", "ticketShow"}
    if estado not in vivos:
        falla("la modelo no esta en vivo (estado=%r)" % estado)
    ok("la modelo esta en vivo")
    return id_modelo


def fase_2_master(id_modelo, nombre, por_tunel: bool):
    print("[fase 2] pedir el master playlist sin navegador")
    url = "https://edge-hls.doppiocdn.com/hls/%s/master/%s_auto.m3u8" % (
        id_modelo, id_modelo)
    status, url_final, cuerpo = pedir(url, por_tunel)
    if status == 404:
        falla("404: la modelo no esta emitiendo ahora (o cambio de id)")
    if status != 200:
        falla("master dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in texto:
        falla("200 pero sin #EXT-X-STREAM-INF, no es un master valido")

    tokens = re.findall(r"#EXT-X-MOUFLON:PSCH:v2:(\S+)", texto)
    conocidos = [t for t in tokens if t in KEY_MAP]
    if not conocidos:
        falla("ningun PSCH del master esta en KEY_MAP (rotaron las claves): %r"
              % tokens)
    ok("%d/%d tokens PSCH conocidos" % (len(conocidos), len(tokens)))

    rendiciones = []
    lineas = texto.splitlines()
    for i, ln in enumerate(lineas):
        if ln.startswith("#EXT-X-STREAM-INF"):
            uri = lineas[i + 1].strip() if i + 1 < len(lineas) else None
            m_res = re.search(r"RESOLUTION=(\d+x\d+)", ln)
            m_nombre = re.search(r'NAME="([^"]+)"', ln)
            rendiciones.append({
                "nombre": m_nombre.group(1) if m_nombre else "?",
                "resolucion": m_res.group(1) if m_res else "?",
                "uri": uri,
            })
    print("  rendiciones del master:")
    for r in rendiciones:
        print("    %-10s %-10s %s" % (r["nombre"], r["resolucion"], r["uri"]))

    alto_max = 0
    for r in rendiciones:
        try:
            alto_max = max(alto_max, int(r["resolucion"].split("x")[1]))
        except (ValueError, IndexError):
            pass
    if alto_max < 360:
        falla("todas las rendiciones son miniatura/preview (<360p): "
              "puede hacer falta cookies de sesion")
    ok("al menos una rendicion real (%dp max)" % alto_max)

    # comparar contra lo que ya capturo el navegador logueado, si existe
    ruta_disco = os.path.join(RAIZ, "datos", "streams")
    candidatos = [d for d in os.listdir(ruta_disco)
                  if d.endswith("_" + str(id_modelo))] if os.path.isdir(
        ruta_disco) else []
    if candidatos:
        pl = os.path.join(ruta_disco, candidatos[0], "playlists",
                           "%s_auto.m3u8" % id_modelo)
        if os.path.isfile(pl):
            viejo = open(pl, encoding="utf-8", errors="replace").read()
            sets_nuevo = set(re.findall(r'NAME="([^"]+)"', texto))
            sets_viejo = set(re.findall(r'NAME="([^"]+)"', viejo))
            print("  comparacion con el master que capturo el navegador:")
            print("    anonimo=%s logueado=%s"
                  % (sorted(sets_nuevo), sorted(sets_viejo)))
            if sets_nuevo >= sets_viejo:
                ok("el master anonimo ofrece las mismas rendiciones "
                   "(o mas) que el logueado: la calidad NO depende de "
                   "cookies")
            else:
                print("  AVISO: el master anonimo ofrece MENOS que el "
                      "logueado")
    return rendiciones, conocidos[0]


def fase_3_media_playlist(uri_variante, pkey, por_tunel: bool):
    print("[fase 3] pedir la media playlist real (con y sin pkey)")

    def _con_query(extra):
        base = uri_variante.split("?", 1)[0]
        return base + "?playlistType=standard&preferredVideoCodec=h264" + extra

    # control: sin pkey, para dejar documentado el senuelo de publicidad
    url_sin = _con_query("")
    status, url_final, cuerpo = pedir(url_sin, por_tunel)
    if "/cpa/" in url_final or b"MOUFLON-ADVERT" in cuerpo:
        print("  control sin pkey: %s -> publicidad (%s), como se esperaba"
              % (status, url_final))
    else:
        print("  control sin pkey: %s %s (no dio publicidad; revisar)"
              % (status, url_final))

    url_con = _con_query("&psch=v2&pkey=" + pkey)
    status, url_final, cuerpo = pedir(url_con, por_tunel)
    if status != 200:
        falla("media playlist con pkey dio %s" % status)
    texto = cuerpo.decode("utf-8", "replace")
    if es_publicidad(texto, url_final):
        falla("con pkey SIGUE dando publicidad (url final: %s)" % url_final)
    if "#EXT-X-MAP:URI=" not in texto or "#EXT-X-MOUFLON:URI:" not in texto:
        falla("200 pero sin #EXT-X-MAP/#EXT-X-MOUFLON:URI, no es la playlist "
              "real")
    ok("media playlist real obtenida con pkey (%d bytes)" % len(cuerpo))
    return texto


def es_publicidad(texto: str, url: str) -> bool:
    """Compuerta dura: NUNCA grabar esto como si fuera la emision."""
    return ("/cpa/" in url or "MOUFLON-ADVERT" in texto
            or "PLAYLIST-TYPE:VOD" in texto or "#EXT-X-ENDLIST" in texto)


def fase_4_descifrar(texto_playlist: str):
    print("[fase 4] descifrar las URLs de segmento")
    tokens_vistos = {}
    fallos = []
    for ln in texto_playlist.splitlines():
        if not ln.startswith(("#EXT-X-MAP:URI=", "#EXT-X-MOUFLON:URI:")):
            continue
        m = RE_TOKEN_SEGMENTO.search(ln)
        if not m:
            continue
        secuencia, token_b64, epoch = m.group(1), m.group(2), m.group(3)
        if token_b64 in tokens_vistos:
            continue
        pdkey_ok = None
        claro = None
        for pkey, pdkey in KEY_MAP.items():
            candidato = descifrar_token(token_b64, pdkey)
            if es_token_valido(candidato):
                pdkey_ok, claro = pdkey, candidato
                break
        tokens_vistos[token_b64] = claro
        if claro is None:
            fallos.append((secuencia, token_b64))
        else:
            url_descifrada = ln.split(":URI:")[-1].split(":URI=")[-1].strip(
                '"')
            url_descifrada = url_descifrada.replace(token_b64, claro)
            nombre = url_descifrada.rsplit("/", 1)[-1]
            clave = clave_segmento(nombre)
            print("    msn=%-6s %s -> %s (clave=%s)" % (
                secuencia, token_b64[:10] + "...", nombre, clave))
            if clave is None:
                fallos.append((secuencia, token_b64))

    if fallos:
        falla("%d/%d tokens no descifraron a un nombre valido"
              % (len(fallos), len(tokens_vistos)))
    ok("%d/%d tokens descifrados y validos contra RE_SEGMENTO"
       % (len(tokens_vistos), len(tokens_vistos)))
    return tokens_vistos


def _descifrar_url(linea: str, tokens_vistos: dict) -> str:
    m = RE_TOKEN_SEGMENTO.search(linea)
    url = linea.split(":URI:")[-1].split(":URI=")[-1].strip('"')
    if not m:
        return url
    token_b64 = m.group(2)
    claro = tokens_vistos.get(token_b64)
    if claro is None:
        for pkey, pdkey in KEY_MAP.items():
            candidato = descifrar_token(token_b64, pdkey)
            if es_token_valido(candidato):
                claro = candidato
                break
    return url.replace(token_b64, claro) if claro else url


def fase_5_bajar(texto_playlist: str, tokens_vistos: dict, carpeta: str,
                  por_tunel: bool):
    print("[fase 5] bajar init + segmentos con GET pelado")
    os.makedirs(carpeta, exist_ok=True)
    init_url = None
    segmentos_url = []
    for ln in texto_playlist.splitlines():
        if ln.startswith("#EXT-X-MAP:URI="):
            init_url = _descifrar_url(ln, tokens_vistos)
        elif ln.startswith("#EXT-X-MOUFLON:URI:"):
            segmentos_url.append(_descifrar_url(ln, tokens_vistos))

    if not init_url:
        falla("no se encontro #EXT-X-MAP en la playlist")

    rutas = []
    for i, url in enumerate([init_url] + segmentos_url):
        nombre = url.rsplit("/", 1)[-1]
        status, _, cuerpo = pedir(url, por_tunel)
        if status != 200:
            falla("GET %s dio %s" % (nombre, status))
        firma = cuerpo[4:8] if i == 0 else cuerpo[4:8]
        if i == 0 and firma != b"ftyp":
            falla("el init no empieza con ftyp en el offset 4 (firma=%r)"
                  % firma)
        if i > 0 and firma not in (b"styp", b"sidx", b"moof"):
            falla("segmento %s no tiene firma de caja MP4 conocida (%r)"
                  % (nombre, firma))
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(cuerpo)
        rutas.append(ruta)
        ok("%s (%d bytes)" % (nombre, len(cuerpo)))
    return rutas[0], rutas[1:]


def fase_6_armar(init: str, segmentos: list, carpeta: str):
    print("[fase 6] pegar y remuxear con ffmpeg")
    segmentos = sorted(segmentos,
                        key=lambda r: clave_segmento(os.path.basename(r))
                        or (0, 0, 0))
    pegado = os.path.join(carpeta, "pegado.mp4")
    with open(pegado, "wb") as salida:
        for ruta in [init] + segmentos:
            with open(ruta, "rb") as f:
                salida.write(f.read())

    if not os.path.isfile(FFMPEG):
        falla("no esta %s (ponelo ahi para poder remuxear)" % FFMPEG)

    final = os.path.join(carpeta, "salida.mp4")
    proc = subprocess.run(
        [FFMPEG, "-y", "-fflags", "+ignidx", "-i", pegado,
         "-c", "copy", "-avoid_negative_ts", "make_zero", final],
        capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(final):
        falla("ffmpeg fallo:\n%s" % proc.stderr[-2000:])

    proc = subprocess.run([FFMPEG, "-i", final], capture_output=True,
                           text=True)
    info = proc.stderr
    m = re.search(r"Duration: (\d\d):(\d\d):(\d\d\.\d\d)", info)
    if not m:
        falla("no se pudo leer la duracion de %s" % final)
    segundos = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(
        m.group(3))
    esperado = len(segmentos) * 2.0
    print("  duracion real=%.2fs esperada~=%.2fs (%d segmentos de 2s)"
          % (segundos, esperado, len(segmentos)))
    if abs(segundos - esperado) > max(0.5, esperado * 0.1):
        falla("duracion muy distinta de la esperada: huecos o solapes")
    ok("%s armado y reproducible (%.2fs)" % (final, segundos))
    return final


def fase_7_sostener(id_modelo, variante_uri, pkey, carpeta: str,
                     minutos: float, por_tunel: bool):
    """Sostener la bajada en el tiempo.

    El loop que refresca la playlist NUNCA tiene que bloquearse en una
    descarga: si un GET de 2MB se cuelga (red lenta, CDN con un hickup), un
    loop secuencial se queda sin pedir la playlist durante ese tiempo y la
    ventana deslizante del CDN se come segmentos que todavia no se habian
    ni intentado pedir -- eso fue justamente lo que paso en el primer
    intento (un timeout de 15s en UN segmento se tradujo en 29 perdidos).
    Por eso las bajadas van a un pool de hilos aparte: el loop solo somete
    el trabajo y sigue refrescando.
    """
    import concurrent.futures as cf

    print("[fase 7] sostener %.1f min refrescando la media playlist"
          % minutos)
    os.makedirs(carpeta, exist_ok=True)
    tokens_vistos = {}
    sometidos = set()        # nombres ya entregados a un hilo (en vuelo o listos)
    rutas_por_clave = {}
    # a que init pertenece cada clave: si la modelo reconecta el encoder a
    # mitad de la ventana el CDN manda un #EXT-X-MAP nuevo, y concatenar
    # segmentos de dos generaciones de encoder bajo el init viejo produce
    # un mp4 con metadata rota (visto en la corrida anterior: 294
    # segmentos, 1 sola falla de continuidad, pero la duracion remuxeada
    # salio 66s en vez de ~588s). Cada init es una sesion aparte, igual que
    # ya hace detectar_m3u8.py con sus carpetas de sesion.
    init_por_clave = {}
    fin = time.time() + minutos * 60
    intervalo = 1.0  # TARGETDURATION/2 tipico (TARGETDURATION=2)
    ultimo_msn = None
    init_nombre_url = {}      # para saber cual es el init al terminar
    init_actual = [None]      # mutable para que el loop lo pueda leer/escribir

    def _bajar_a_disco(url, por_tunel, carpeta):
        nombre = url.rsplit("/", 1)[-1]
        status, _, data = pedir(url, por_tunel)
        if status != 200:
            raise RuntimeError("status %s" % status)
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(data)
        return nombre, ruta

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        futuros = {}  # future -> (nombre, clave_o_None)

        while time.time() < fin:
            t0 = time.time()
            url = (variante_uri.split("?", 1)[0]
                   + "?playlistType=standard&preferredVideoCodec=h264"
                   + "&psch=v2&pkey=" + pkey)
            try:
                status, url_final, cuerpo = pedir(url, por_tunel)
            except Exception as e:
                print("  [%s] error de red pidiendo la playlist: %s" % (
                    time.strftime("%H:%M:%S"), e))
                status = None
            if status == 200 and not es_publicidad(
                    cuerpo.decode("utf-8", "replace"), url_final):
                texto = cuerpo.decode("utf-8", "replace")
                for ln in texto.splitlines():
                    if ln.startswith("#EXT-X-MAP:URI="):
                        u = _descifrar_url(ln, tokens_vistos)
                        nombre = u.rsplit("/", 1)[-1]
                        if nombre != init_actual[0]:
                            print("  [%s] init nuevo: %s (la modelo "
                                  "reconecto el encoder, arranca sesion "
                                  "nueva)" % (time.strftime("%H:%M:%S"),
                                              nombre))
                        init_actual[0] = nombre
                        init_nombre_url[nombre] = u
                        if nombre not in sometidos:
                            sometidos.add(nombre)
                            fut = pool.submit(_bajar_a_disco, u, por_tunel,
                                               carpeta)
                            futuros[fut] = (nombre, None, None)
                    elif ln.startswith("#EXT-X-MOUFLON:URI:"):
                        u = _descifrar_url(ln, tokens_vistos)
                        nombre = u.rsplit("/", 1)[-1]
                        if nombre in sometidos:
                            continue
                        clave = clave_segmento(nombre)
                        if clave is None:
                            continue
                        sometidos.add(nombre)
                        init_por_clave[clave] = init_actual[0]
                        fut = pool.submit(_bajar_a_disco, u, por_tunel,
                                           carpeta)
                        futuros[fut] = (nombre, clave, init_actual[0])
            elif status is not None:
                print("  [%s] respuesta mala de la playlist (status=%s)"
                      % (time.strftime("%H:%M:%S"), status))

            # recolectar lo que ya termino, sin esperar a lo que sigue en
            # vuelo (eso es lo que mantiene el loop sin bloquearse)
            listos = [f for f in futuros if f.done()]
            nuevos = 0
            for fut in listos:
                nombre, clave, _init = futuros.pop(fut)
                try:
                    _, ruta = fut.result()
                except Exception as e:
                    print("  [%s] fallo bajando %s: %s -- se pierde (no "
                          "estaba mas en la playlist para reintentar)"
                          % (time.strftime("%H:%M:%S"), nombre, e))
                    sometidos.discard(nombre)  # que se pueda reintentar
                    continue
                if clave is None:
                    continue  # era el init
                rutas_por_clave[clave] = ruta
                nuevos += 1
                msn = clave[0]
                if ultimo_msn is not None and msn != ultimo_msn + 2 \
                        and msn != ultimo_msn:
                    print("  [%s] salto de epoch: %s -> %s (posible hueco)"
                          % (time.strftime("%H:%M:%S"), ultimo_msn, msn))
                ultimo_msn = msn

            print("  [%s] %d segmentos nuevos, %d acumulados, %d en vuelo"
                  % (time.strftime("%H:%M:%S"), nuevos,
                     len(rutas_por_clave), len(futuros)))
            time.sleep(max(0.0, intervalo - (time.time() - t0)))

        print("  esperando a que termine lo que quedo en vuelo...")
        for fut in cf.as_completed(list(futuros), timeout=60):
            nombre, clave, _init = futuros[fut]
            try:
                _, ruta = fut.result()
            except Exception as e:
                print("  fallo bajando %s al cierre: %s" % (nombre, e))
                continue
            if clave is not None:
                rutas_por_clave[clave] = ruta

    if not rutas_por_clave:
        falla("no se bajo ningun segmento durante la ventana")
    if not init_nombre_url:
        falla("nunca se vio un #EXT-X-MAP en toda la ventana")

    # agrupar por sesion (init): un reconnect de la modelo es una sesion
    # nueva, no un hueco -- lo mismo que ya hace detectar_m3u8.py con sus
    # carpetas de sesion por separado.
    sesiones = {}  # init_nombre -> [clave, ...]
    for clave, init_nombre in init_por_clave.items():
        sesiones.setdefault(init_nombre, []).append(clave)
    # orden de aparicion de cada init, para reportar en el orden real
    orden_init = list(init_nombre_url)

    print("  %d sesion(es) detectada(s) (= %d reconexion(es) del encoder)"
          % (len(sesiones), max(0, len(sesiones) - 1)))

    huecos_totales = 0
    duracion_total = 0.0
    segundos_cubiertos_por_sesion = []
    for nombre_init in orden_init:
        # solo las claves que de verdad se bajaron: una que quedo asignada
        # a la sesion pero cuyo archivo nunca llego (404 porque la modelo
        # corto la emision justo ahi, por ejemplo) no puede contar como
        # "sin hueco" solo porque su numero de secuencia sigue en la lista
        claves = sorted(k for k in sesiones.get(nombre_init, [])
                         if k in rutas_por_clave)
        if not claves:
            continue
        # el hueco real se mide por el NUMERO DE SECUENCIA (msn, clave[1]):
        # tiene que ser estrictamente consecutivo. El "epoch" (clave[0]) es
        # el reloj que pone el encoder en el nombre del segmento y tiene
        # jitter normal de +-1s alrededor de los 2s nominales (confirmado:
        # en una sesion sana de 303 segmentos, 292 deltas de epoch dieron
        # exacto 2 y 10 dieron 1 o 3, sin perder un solo msn) -- no es
        # senal de hueco.
        msns = [k[1] for k in claves]
        huecos = sum(1 for a, b in zip(msns, msns[1:]) if b != a + 1)
        huecos_totales += huecos
        init_local = os.path.join(carpeta, nombre_init)
        if not os.path.isfile(init_local):
            print("  sesion %s: el init quedo sin bajar, se omite"
                  % nombre_init)
            continue
        segmentos_ordenados = [rutas_por_clave[k] for k in claves]
        carpeta_sesion = os.path.join(carpeta, "sesion_" + nombre_init[:12])
        os.makedirs(carpeta_sesion, exist_ok=True)
        try:
            final = fase_6_armar(init_local, segmentos_ordenados,
                                  carpeta_sesion)
        except Falla as e:
            print("  sesion %s (%d segmentos, %d huecos): FALLA al armar: %s"
                  % (nombre_init, len(claves), huecos, e))
            continue
        proc = subprocess.run([FFMPEG, "-i", final], capture_output=True,
                               text=True)
        m = re.search(r"Duration: (\d\d):(\d\d):(\d\d\.\d\d)", proc.stderr)
        real = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                + float(m.group(3)) if m else 0.0)
        esperado_sesion = len(claves) * 2.0
        duracion_total += real
        segundos_cubiertos_por_sesion.append(real)
        print("  sesion %s: %d segmentos, %d huecos, duracion=%.2fs "
              "(esperada~=%.2fs)" % (nombre_init, len(claves), huecos, real,
                                      esperado_sesion))
        if huecos or abs(real - esperado_sesion) > max(0.5,
                                                         esperado_sesion * 0.1):
            print("  -> sesion %s con problemas de continuidad" % nombre_init)

    print("  total: %d huecos en %d sesion(es), %.1fs de video reconstruido"
          % (huecos_totales, len(sesiones), duracion_total))
    if huecos_totales > 0:
        falla("%d huecos DENTRO de una sesion (no por reconexion): subir la "
              "frecuencia de refresco o pasar a blocking reload"
              % huecos_totales)
    if not segundos_cubiertos_por_sesion:
        falla("ninguna sesion se pudo armar")
    ok("0 huecos dentro de cada sesion; %d reconexion(es) del encoder "
       "manejada(s) como sesiones separadas, igual que el detector actual"
       % max(0, len(sesiones) - 1))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nombre", help="nombre de la modelo (como en grabar.txt)")
    ap.add_argument("--fase", type=int, default=7,
                     help="corre hasta esta fase inclusive (defecto: 7)")
    ap.add_argument("--id", type=int, default=None,
                     help="id numerico de la modelo, si la fase 1 falla")
    ap.add_argument("--salida", default=None,
                     help="carpeta de trabajo (defecto: %%TEMP%%\\nimbo_sonda\\)")
    ap.add_argument("--vpn", action="store_true",
                     help="salir por el tunel de wireproxy (127.0.0.1:25345)")
    ap.add_argument("--minutos", type=float, default=10.0,
                     help="duracion de la fase 7 (defecto: 10)")
    args = ap.parse_args()

    carpeta = args.salida or os.path.join(
        os.environ.get("TEMP", "."), "nimbo_sonda", args.nombre)

    try:
        fase_0_cripto()
        if args.fase == 0:
            return 0

        id_modelo = fase_1_id_y_estado(args.nombre, args.id, args.vpn)
        if args.fase == 1:
            return 0

        rendiciones, pkey = fase_2_master(id_modelo, args.nombre, args.vpn)
        if args.fase == 2:
            return 0

        # la mejor rendicion real = la de mayor alto conocido
        def _alto(r):
            try:
                return int(r["resolucion"].split("x")[1])
            except (ValueError, IndexError):
                return 0
        elegida = max(rendiciones, key=_alto)
        print("  variante elegida: %s (%s)" % (elegida["nombre"],
                                                 elegida["resolucion"]))
        texto_media = fase_3_media_playlist(elegida["uri"], pkey, args.vpn)
        if args.fase == 3:
            return 0

        tokens_vistos = fase_4_descifrar(texto_media)
        if args.fase == 4:
            return 0

        init, segmentos = fase_5_bajar(texto_media, tokens_vistos, carpeta,
                                        args.vpn)
        if args.fase == 5:
            return 0

        fase_6_armar(init, segmentos, carpeta)
        if args.fase == 6:
            return 0

        fase_7_sostener(id_modelo, elegida["uri"], pkey, carpeta,
                         args.minutos, args.vpn)
        return 0
    except Falla as e:
        print("\nSonda abortada: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
