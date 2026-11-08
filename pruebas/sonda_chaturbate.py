# -*- coding: utf-8 -*-
"""Sonda de chaturbate: medir el protocolo real antes de escribir
nucleo\\scripts\\chaturbate.py.

Herramienta de diagnostico, NO parte del motor de captura. Calco de
pruebas\\sonda_directo.py (ver ese archivo para el porque de cada
restriccion): mide fase por fase contra el sitio VIVO y deja la evidencia
impresa para que SPEC 18 (specs\\18-segundo-sitio-chaturbate.md, Fase 0 del
plan) decida con datos si la vista en vivo entra o queda fuera de alcance.

Restricciones duras (las mismas que sonda_directo.py):
  - Solo libreria estandar. Nada de requests, nada de Playwright.
  - Escribe solo bajo --salida (por defecto %TEMP%\\nimbo_sonda_cb\\). Nunca
    en datos\\streams\\, estado\\ o nucleo\\.

Resumen de lo medido en vivo el 2026-11-08 (modelo de prueba: willobeyu,
resuelta al azar desde chaturbate.com/sitemap-broadcasters-1.xml -- ver
hallazgos abajo de cada fase):

  - NO hay id numerico. La identidad estable de una sala es el propio
    username (room_status/hls_source salen de
    https://chaturbate.com/api/chatvideocontext/<nombre>/, sin ningun id
    numerico en la respuesta -- "broadcaster_uid"/"room_uid" son un token
    alfanumerico de 7 caracteres, ej. "H349Y5V", no un numero). Esto
    contradice la premisa del spec ("tiene que ser numerico y estable --
    lo exige grabaciones.RE_NOMBRE"): grabaciones.RE_NOMBRE va a necesitar
    un criterio distinto para chaturbate, o aceptar el uid alfanumerico
    como "id". Queda marcado como punto de decision para el paso 2.
  - El manifiesto es LL-HLS real (EXT-X-PART/EXT-X-PRELOAD-HINT/
    EXT-X-SERVER-CONTROL) servido por *.live.mmcdn.com, con un token JWT
    de corta vida en la URL del master (viene en el campo "hls_source" de
    chatvideocontext) que hay que refrescar en cada ciclo de polling (no
    es un pkey fijo como MOUFLON: es un JWT por pedido).
  - **Los segmentos son fMP4/CMAF, no MPEG-TS**: init_*.m4s empieza con
    "ftyp" (brand "iso6"), los segmentos (seg_*.m4s) empiezan con "moof".
    TARGETDURATION=2, con partes LL-HLS de 0.8s (EXT-X-PART) y segmentos
    completos de 1.6s (2 partes). Esto habilita la vista en vivo con
    MediaSource (criterio de "Fuera de scope" del spec): la previa de
    chaturbate puede entrar en el alcance de reproductor_vivo.js.
  - No hace falta cookie ni cuenta para nada de esto: todos los pedidos de
    este archivo van sin cookies, solo con un User-Agent de navegador.
  - Snapshot publico y estable para miniatura:
    https://jpeg.live.mmcdn.com/stream?room=<nombre> (JPEG real, cambia en
    cada pedido -- confirma que es un snapshot en vivo, no un placeholder
    cacheado). https://thumb.live.mmcdn.com/ri/<nombre>.jpg tambien
    responde 200 (mas chico, probablemente la miniatura de catalogo).

Uso:
    python pruebas\\sonda_chaturbate.py <nombre_modelo> [--fase N]
        [--salida CARPETA] [--minutos 3]

Cada fase se corre en orden y se aborta en la primera que falla; "--fase N"
corre solo hasta la N inclusive. "--fase 0" no pide red: documenta que, a
diferencia de MOUFLON, chaturbate no tiene ningun esquema de cifrado que
verificar -- no hace falta canario (ver SPEC 18, parrafo de
nucleo\\scripts\\chaturbate.py: "Canario offline si la fase 0 encuentra algo
versionable que pueda rotar... si no hace falta descifrar nada, no hace
falta canario").
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# la consola de Windows suele quedar en cp1252: que un caracter raro del
# CDN no tire excepcion al loguear una falla (mismo motivo que
# sonda_directo.py:47)
sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(RAIZ, "nucleo", "scripts", "ffmpeg.exe")

SITIO = "https://chaturbate.com"

# Sin un User-Agent de navegador real, varios endpoints de chaturbate
# devuelven 403. Portado del mismo criterio que sonda_directo.py:63-71.
CABECERAS = {
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

# nombre real de un segmento/init/part de chaturbate (llhls.m3u8):
#   <tipo>_<rendicion>_<msn>[_<parte>]_video|audio_<streamid>_llhls.m4s
# ej.: seg_4_1718_video_9246917733601039753_llhls.m4s
#      part_4_1719_0_video_9246917733601039753_llhls.m4s
#      init_4_video_9246917733601039753_llhls.m4s
RE_NOMBRE_SEG = re.compile(
    r"^(init|seg|part)_(\d+)_(?:(\d+)_)?(?:(\d+)_)?(video|audio)_(\d+)_"
    r"llhls\.m4s$")


class Falla(Exception):
    pass


def ok(msg):
    print("  OK   %s" % msg)


def falla(msg):
    print("  FALLA %s" % msg)
    raise Falla(msg)


def pedir(url, cabeceras=None, timeout=15.0):
    """GET pelado. -> (status, url_final, cuerpo_bytes) o lanza."""
    todas = dict(CABECERAS)
    if cabeceras:
        todas.update(cabeceras)
    req = urllib.request.Request(url, headers=todas)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url, e.read()


# --------------------------------------------------------------------------
# Fases
# --------------------------------------------------------------------------

def fase_0_sin_cifrado():
    print("[fase 0] verificar si hace falta un esquema de descifrado (sin red)")
    print("  chaturbate no envuelve las URLs de segmento en ningun token "
          "cifrado propio (nada equivalente a #EXT-X-MOUFLON): el manifiesto "
          "LL-HLS trae las URLs de segmento en claro, protegidas solo por un "
          "JWT de corta vida en la query del MASTER (campo 'hls_source' de "
          "/api/chatvideocontext/<nombre>/). No hace falta canario de "
          "descifrado -- ver SPEC 18, nucleo\\scripts\\chaturbate.py.")
    ok("sin esquema de cifrado que verificar")


def fase_1_id_y_estado(nombre: str):
    print("[fase 1] resolver estado de '%s' sin navegador "
          "(/api/chatvideocontext/)" % nombre)
    url = "%s/api/chatvideocontext/%s/" % (SITIO, nombre)
    status, _, cuerpo = pedir(url)
    if status != 200:
        falla("chatvideocontext dio %s" % status)
    try:
        datos = json.loads(cuerpo)
    except ValueError as e:
        falla("chatvideocontext: JSON invalido (%s)" % e)
    estado = datos.get("room_status")
    uid = datos.get("broadcaster_uid") or datos.get("room_uid")
    hls_source = datos.get("hls_source") or ""
    viewers = datos.get("num_viewers")
    print("  room_status=%r broadcaster_uid=%r (NO es numerico) "
          "num_viewers=%r hls_source=%s" % (
              estado, uid, viewers,
              "presente (%d chars)" % len(hls_source) if hls_source
              else "VACIO"))
    if uid is not None and not str(uid).isdigit():
        print("  AVISO: chaturbate no tiene id numerico estable -- la "
              "identidad de la sala es el propio username. Afecta a "
              "grabaciones.RE_NOMBRE (ver docstring de este archivo).")
    if estado != "public" or not hls_source:
        falla("la modelo no esta transmitiendo en publico ahora mismo "
              "(room_status=%r, hls_source %s) -- probar con otro nombre"
              % (estado, "vacio" if not hls_source else "presente"))
    ok("la modelo esta en vivo, en publico")
    return datos


def fase_2_master(datos: dict):
    print("[fase 2] pedir el master LL-HLS (URL ya trae el JWT de "
          "chatvideocontext)")
    url = datos["hls_source"]
    status, url_final, cuerpo = pedir(url)
    if status != 200:
        falla("master dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in texto:
        falla("200 pero sin #EXT-X-STREAM-INF, no es un master valido")
    ok("master obtenido (%d bytes)" % len(cuerpo))

    base = url_final.rsplit("/", 1)[0]
    rendiciones = []
    lineas = texto.splitlines()
    for i, ln in enumerate(lineas):
        if ln.startswith("#EXT-X-STREAM-INF"):
            uri = lineas[i + 1].strip() if i + 1 < len(lineas) else None
            if uri and not uri.startswith("http"):
                uri = base + "/" + uri if not uri.startswith("/") else (
                    url_final.split("://", 1)[0] + "://"
                    + url_final.split("://", 1)[1].split("/", 1)[0] + uri)
            m_res = re.search(r"RESOLUTION=(\d+x\d+)", ln)
            m_bw = re.search(r"BANDWIDTH=(\d+)", ln)
            m_codecs = re.search(r'CODECS="([^"]+)"', ln)
            rendiciones.append({
                "resolucion": m_res.group(1) if m_res else "?",
                "bandwidth": int(m_bw.group(1)) if m_bw else 0,
                "codecs": m_codecs.group(1) if m_codecs else "?",
                "uri": uri,
            })
    print("  rendiciones del master:")
    for r in rendiciones:
        print("    %-10s %8s bps  %-28s %s" % (
            r["resolucion"], r["bandwidth"], r["codecs"], r["uri"]))
    if not rendiciones:
        falla("el master no trajo ninguna rendicion EXT-X-STREAM-INF")
    ok("%d rendiciones" % len(rendiciones))
    return rendiciones


def fase_3_chunklist(uri_variante: str):
    print("[fase 3] pedir la media playlist (chunklist) real, sin pkey ni "
          "cookies -- la autoriza el 'session=' que ya trae la URL")
    status, url_final, cuerpo = pedir(uri_variante)
    if status != 200:
        falla("chunklist dio %s en %s" % (status, url_final))
    texto = cuerpo.decode("utf-8", "replace")
    if "#EXT-X-MAP:URI=" not in texto:
        falla("200 pero sin #EXT-X-MAP, no es una media playlist fMP4 real")
    if ".ts" in texto and ".m4s" not in texto:
        falla("la playlist apunta a segmentos .ts (MPEG-TS): la vista en "
              "vivo con MediaSource NO es viable, ver 'Fuera de scope' del "
              "spec")
    m = re.search(r"#EXT-X-TARGETDURATION:(\d+(?:\.\d+)?)", texto)
    target = float(m.group(1)) if m else None
    m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', texto)
    init_rel = m.group(1) if m else None
    segmentos_rel = re.findall(r"^(/[^\s#][^\s]*\.m4s[^\s]*)$", texto,
                                re.M)
    print("  TARGETDURATION=%s init=%s" % (target, init_rel))
    print("  %d segmentos completos listados ahora" % len(segmentos_rel))
    if not init_rel:
        falla("no se encontro #EXT-X-MAP:URI en la chunklist")
    if not segmentos_rel:
        falla("no se encontro ningun segmento .m4s (seg_*) en la chunklist")
    ok("chunklist real: fMP4/CMAF confirmado (init .m4s + segmentos .m4s, "
       "nunca .ts)")
    origen = url_final.split("://", 1)[0] + "://" + url_final.split(
        "://", 1)[1].split("/", 1)[0]
    return origen, init_rel, segmentos_rel, target


def fase_4_bajar(origen: str, init_rel: str, segmentos_rel: list,
                  carpeta: str):
    print("[fase 4] bajar init + un par de segmentos con GET pelado")
    os.makedirs(carpeta, exist_ok=True)
    rutas = []
    for i, rel in enumerate([init_rel] + segmentos_rel[:3]):
        url = rel if rel.startswith("http") else origen + rel
        nombre = url.split("?", 1)[0].rsplit("/", 1)[-1]
        status, url_final, cuerpo = pedir(url)
        if status == 410:
            # ventana deslizante: el segmento ya salio de cache por el
            # tiempo que tardamos en pedirlo. No es una falla del
            # protocolo, es esperable con LL-HLS -- se documenta y se
            # sigue con el resto.
            print("  AVISO: %s ya expiro de la cache del edge (410) -- "
                  "ventana deslizante normal de LL-HLS, se omite" % nombre)
            continue
        if status != 200:
            falla("GET %s dio %s" % (nombre, status))
        firma = cuerpo[:4] if len(cuerpo) >= 4 else b""
        if i == 0:
            # init: caja ftyp despues del largo de 4 bytes (offset 4)
            if cuerpo[4:8] != b"ftyp":
                falla("el init no empieza con ftyp en el offset 4 (brand "
                      "leido=%r)" % cuerpo[8:12])
        else:
            if cuerpo[4:8] not in (b"styp", b"sidx", b"moof"):
                falla("segmento %s no tiene firma de caja MP4 conocida "
                      "(%r)" % (nombre, cuerpo[4:8]))
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(cuerpo)
        rutas.append(ruta)
        ok("%s (%d bytes, firma=%r)" % (nombre, len(cuerpo), cuerpo[4:8]))
    if not rutas:
        falla("no se pudo bajar ni el init: la ventana se movio demasiado "
              "rapido, correr de nuevo")
    return rutas[0], rutas[1:]


def fase_5_armar(init: str, segmentos: list, carpeta: str):
    print("[fase 5] pegar y remuxear con ffmpeg")
    if not segmentos:
        print("  AVISO: sin segmentos de sobra para armar (todos "
              "expiraron antes de bajarlos) -- se omite esta fase, no es "
              "una falla del protocolo")
        return None
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
    m = re.search(r"Duration: (\d\d):(\d\d):(\d\d\.\d\d)", proc.stderr)
    if not m:
        falla("no se pudo leer la duracion de %s" % final)
    segundos = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(
        m.group(3))
    ok("%s armado y reproducible (%.2fs, %d segmentos)" % (
        final, segundos, len(segmentos)))
    return final


def fase_6_snapshot(nombre: str):
    print("[fase 6] snapshot publico para miniatura (sin cookies)")
    url = "https://jpeg.live.mmcdn.com/stream?room=%s" % nombre
    status, _, cuerpo = pedir(url)
    if status != 200:
        falla("snapshot dio %s" % status)
    if cuerpo[:3] != b"\xff\xd8\xff":
        falla("la respuesta no es un JPEG (firma=%r)" % cuerpo[:4])
    ok("snapshot en vivo: %d bytes, JPEG valido" % len(cuerpo))

    # segunda pasada para confirmar que varia (no es un placeholder
    # cacheado para siempre)
    time.sleep(1.5)
    status2, _, cuerpo2 = pedir(url)
    if status2 == 200 and cuerpo2 != cuerpo:
        ok("segundo pedido trae bytes distintos (%d vs %d): es un "
           "snapshot en vivo, no un placeholder estatico" % (
               len(cuerpo2), len(cuerpo)))
    else:
        print("  AVISO: el segundo pedido trajo los mismos bytes -- puede "
              "ser casualidad (misma imagen dos ciclos seguidos) o cache; "
              "no es bloqueante")
    return url


def fase_7_sostener(uri_variante: str, carpeta: str, minutos: float):
    """Sostener la bajada en el tiempo, igual que sonda_directo.py fase 7:
    las bajadas van a un pool de hilos aparte para que el loop nunca se
    bloquee refrescando la chunklist (ver sonda_directo.py:506-521 para el
    porque)."""
    print("[fase 7] sostener %.1f min refrescando la chunklist" % minutos)
    os.makedirs(carpeta, exist_ok=True)
    origen = uri_variante.split("://", 1)[0] + "://" + uri_variante.split(
        "://", 1)[1].split("/", 1)[0]
    sometidos = set()
    msns_vistos = []
    init_visto = [None]
    fin = time.time() + minutos * 60
    intervalo = 0.8  # PART-TARGET tipico visto en fase 3

    def _bajar(url, carpeta):
        nombre = url.split("?", 1)[0].rsplit("/", 1)[-1]
        status, _, data = pedir(url)
        if status != 200:
            raise RuntimeError("status %s" % status)
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(data)
        return nombre

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        futuros = {}
        while time.time() < fin:
            t0 = time.time()
            try:
                status, url_final, cuerpo = pedir(uri_variante)
            except Exception as e:
                print("  [%s] error de red: %s" % (
                    time.strftime("%H:%M:%S"), e))
                status = None
            if status == 200:
                texto = cuerpo.decode("utf-8", "replace")
                m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', texto)
                if m and m.group(1) != init_visto[0]:
                    if init_visto[0] is not None:
                        print("  [%s] init nuevo: %s (reconexion del "
                              "encoder)" % (time.strftime("%H:%M:%S"),
                                            m.group(1)))
                    init_visto[0] = m.group(1)
                    u = m.group(1) if m.group(1).startswith("http") \
                        else origen + m.group(1)
                    if u not in sometidos:
                        sometidos.add(u)
                        futuros[pool.submit(_bajar, u, carpeta)] = None
                for rel in re.findall(r"^(/[^\s#][^\s]*\.m4s[^\s]*)$",
                                      texto, re.M):
                    if "seg_" not in rel:
                        continue  # solo segmentos completos, no partes
                    u = origen + rel
                    if u in sometidos:
                        continue
                    sometidos.add(u)
                    mm = re.search(r"seg_\d+_(\d+)_", rel)
                    msn = int(mm.group(1)) if mm else None
                    futuros[pool.submit(_bajar, u, carpeta)] = msn
            elif status is not None:
                print("  [%s] chunklist dio status=%s" % (
                    time.strftime("%H:%M:%S"), status))

            listos = [f for f in futuros if f.done()]
            nuevos = 0
            for fut in listos:
                msn = futuros.pop(fut)
                try:
                    fut.result()
                except Exception as e:
                    print("  [%s] fallo bajando (msn=%s): %s" % (
                        time.strftime("%H:%M:%S"), msn, e))
                    continue
                if msn is not None:
                    msns_vistos.append(msn)
                    nuevos += 1
            print("  [%s] %d segmentos nuevos, %d acumulados, %d en vuelo"
                  % (time.strftime("%H:%M:%S"), nuevos, len(msns_vistos),
                     len(futuros)))
            time.sleep(max(0.0, intervalo - (time.time() - t0)))

        for fut in cf.as_completed(list(futuros), timeout=30):
            msn = futuros[fut]
            try:
                fut.result()
                if msn is not None:
                    msns_vistos.append(msn)
            except Exception as e:
                print("  fallo al cierre (msn=%s): %s" % (msn, e))

    if not msns_vistos:
        falla("no se bajo ningun segmento completo durante la ventana")
    msns_vistos.sort()
    huecos = sum(1 for a, b in zip(msns_vistos, msns_vistos[1:])
                 if b != a + 1)
    print("  %d segmentos, msn %d..%d, %d huecos" % (
        len(msns_vistos), msns_vistos[0], msns_vistos[-1], huecos))
    if huecos:
        falla("%d huecos en la secuencia de msn: subir la frecuencia de "
              "refresco" % huecos)
    ok("0 huecos en %d segmentos sostenidos durante %.1f min" % (
        len(msns_vistos), minutos))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nombre", help="nombre/username de la modelo (tal cual "
                                    "aparece en la URL de su sala)")
    ap.add_argument("--fase", type=int, default=7,
                     help="corre hasta esta fase inclusive (defecto: 7)")
    ap.add_argument("--salida", default=None,
                     help="carpeta de trabajo (defecto: "
                          "%%TEMP%%\\nimbo_sonda_cb\\)")
    ap.add_argument("--minutos", type=float, default=3.0,
                     help="duracion de la fase 7 (defecto: 3)")
    args = ap.parse_args()

    carpeta = args.salida or os.path.join(
        os.environ.get("TEMP", "."), "nimbo_sonda_cb", args.nombre)

    try:
        fase_0_sin_cifrado()
        if args.fase == 0:
            return 0

        datos = fase_1_id_y_estado(args.nombre)
        if args.fase == 1:
            return 0

        rendiciones = fase_2_master(datos)
        if args.fase == 2:
            return 0

        elegida = max(rendiciones, key=lambda r: r["bandwidth"])
        print("  variante elegida: %s (%s)" % (elegida["resolucion"],
                                                 elegida["codecs"]))
        origen, init_rel, segmentos_rel, target = fase_3_chunklist(
            elegida["uri"])
        if args.fase == 3:
            return 0

        init, segmentos = fase_4_bajar(origen, init_rel, segmentos_rel,
                                        carpeta)
        if args.fase == 4:
            return 0

        fase_5_armar(init, segmentos, carpeta)
        if args.fase == 5:
            return 0

        fase_6_snapshot(args.nombre)
        if args.fase == 6:
            return 0

        fase_7_sostener(elegida["uri"], carpeta, args.minutos)
        return 0
    except Falla as e:
        print("\nSonda abortada: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
