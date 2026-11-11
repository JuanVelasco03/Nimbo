# -*- coding: utf-8 -*-
r"""Sincronia audio-video de un .mp4 ya terminado (SPEC 22).

Herramienta de medicion, NO parte del motor. Solo lee: abre el archivo con
ffprobe y nada mas. Solo libreria estandar + ffprobe, mismo criterio que el
resto de pruebas\.

Es la evidencia de que la union de una pasada (SPEC 22) no desincroniza.
Antes de ese spec una grabacion de chaturbate salia de tres ffmpeg -- remux
del video, remux del audio y una mezcla que pegaba el segundo adentro del
primero desde t=0, sin offset y sin comparar duraciones -- asi que el
sonido podia quedar adelantado exactamente los segundos de hueco que le
tocaron al video y a nadie mas. Ahora las dos pistas entran en el mismo
ffmpeg con -copyts, conservan su tiempo absoluto, y el unico corrimiento es
el "-avoid_negative_ts make_zero" de la salida, que mueve las dos por
igual.

Que mide, y por que hacen falta las dos cosas:

  primer/ultimo pts ... de cada pista, leidos de los PAQUETES (no de la
                        cabecera): "duration" del contenedor puede venir
                        del mvhd y no de lo que de verdad hay dentro.
  cabeza / cola ....... cuanto mas tarde arranca y termina el audio que el
                        video. **No son desincronizacion.** Las dos
                        chunklists de chaturbate se piden por separado y no
                        cierran en el mismo segundo, asi que una pista
                        puede tener material de mas en un extremo: ahi
                        falta contenido en la otra, no esta corrida.
  discontinuidades .... por pista, cuantas veces el dts se separa del
                        final del paquete anterior. Tiene que dar 0: un
                        salto ahi es un archivo corrupto, no un hueco (un
                        hueco de verdad lo absorbe el muxer alargando la
                        duracion del fotograma anterior, asi que ni se ve
                        aca ni en "huecos").
  deriva .............. cola menos cabeza. **ESTE es el numero que
                        decide.** Si el desfase al final es el mismo que al
                        principio, el audio no se corrio en ningun punto:
                        va pegado al video de punta a punta. Es justo lo
                        que rompia antes de SPEC 22 -- el Reencadenador
                        corria dos veces, una por pista, y cerraba los
                        huecos de cada una por separado: si al video le
                        faltaban 10 s se los cerraba (quedando 10 s mas
                        corto) y al audio no, asi que la mezcla los pegaba
                        desde t=0 y de ahi en adelante el sonido iba 10 s
                        adelantado.

Uso:
    python pruebas\sincronia_chaturbate.py <ruta.mp4> [...]
    python pruebas\sincronia_chaturbate.py --todas    (datos\grabaciones)

--todas recorre los paquetes de TODAS las grabaciones, asi que sobre una
carpeta con decenas de GB tarda minutos: para una sola, pasarle la ruta.

Lee ffprobe de M3U8_FFPROBE, del nucleo (al lado de ffmpeg.exe) o del PATH.
"""
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_GRABACIONES = os.path.join(RAIZ, "datos", "grabaciones")

# Banda que SPEC 22 pide para la deriva, en segundos.
BANDA_S = 0.100


def ffprobe() -> str:
    """Donde esta ffprobe. Mismo patron que detectar_m3u8.py para ffmpeg."""
    propio = os.environ.get("M3U8_FFPROBE")
    if propio and os.path.isfile(propio):
        return propio
    al_lado = os.path.join(RAIZ, "nucleo", "scripts", "ffprobe.exe")
    if os.path.isfile(al_lado):
        return al_lado
    return shutil.which("ffprobe") or ""


def pistas(exe: str, ruta: str):
    """-> [{"indice","codec","tipo"}] de los streams del archivo."""
    proc = subprocess.run(
        [exe, "-v", "error", "-show_streams", "-of", "json", ruta],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-300:])
    datos = json.loads(proc.stdout or "{}")
    return [{"indice": s.get("index"),
             "codec": s.get("codec_name"),
             "tipo": s.get("codec_type")}
            for s in datos.get("streams", [])]


# Cuanto puede separarse un paquete del final del anterior sin que cuente
# como discontinuidad. El AAC va en bloques de 1024 muestras y el video en
# ticks de 90 kHz, asi que el redondeo normal entre paquetes son unos pocos
# ms (medido: +-20 ms); media parte de chaturbate son 400 ms.
TOLERANCIA_S = 0.050


def recorrer(exe: str, ruta: str, indice: int):
    """-> dict con los extremos y las discontinuidades de esa pista.

    Se recorren los PAQUETES y no se confia en el "duration" de la
    cabecera: el mvhd puede declarar una cosa y los paquetes traer otra,
    que es justo el sintoma que este spec vino a mirar.

    Los extremos salen del pts (tiempo de PRESENTACION, el que ve el
    espectador) y la continuidad del dts (tiempo de DECODIFICACION, el
    orden en que van los paquetes en el archivo). Tiene que ser asi: con
    B-frames el pts NO es monotono -- medido sobre una grabacion real de
    chaturbate, 4.840 paquetes de video dieron 2.178 "saltos hacia atras"
    de hasta 84 ms que no son huecos, son reordenamiento normal de H.264.
    El dts si es monotono siempre.

    'huecos' (span menos contenido) suele dar ~0 incluso con segmentos
    perdidos, porque el muxer absorbe el agujero alargando la duracion del
    ultimo paquete antes del corte (comprobado: borrados 9,6 s de video, el
    span siguio en 304,000 s con 288 paquetes menos). Queda como control de
    cordura, no como veredicto -- el veredicto es la deriva, en medir().
    """
    proc = subprocess.run(
        [exe, "-v", "error", "-select_streams", str(indice),
         "-show_entries", "packet=pts_time,dts_time,duration_time",
         "-of", "csv=p=0", ruta],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-300:])

    primero = ultimo = None
    dts_ini = dts_fin = None
    n = 0
    contenido = 0.0
    fin_previo = None
    saltos = atras = 0
    segundos_saltos = 0.0
    peor = 0.0

    def _f(campo):
        try:
            return float(campo)
        except ValueError:
            return None

    for linea in proc.stdout.splitlines():
        campos = linea.split(",")
        if len(campos) < 3:
            continue
        pts, dts, dur = _f(campos[0]), _f(campos[1]), _f(campos[2])
        if dur is None:
            dur = 0.0
        if pts is None:
            pts = dts
        if pts is None:
            continue
        n += 1
        contenido += dur
        if primero is None or pts < primero:
            primero = pts
        fin = pts + dur
        if ultimo is None or fin > ultimo:
            ultimo = fin
        if dts is not None:
            if dts_ini is None:
                dts_ini = dts
            dts_fin = dts + dur
            if fin_previo is not None:
                fuera = dts - fin_previo
                if fuera > TOLERANCIA_S:
                    saltos += 1
                    segundos_saltos += fuera
                    peor = max(peor, fuera)
                elif fuera < -TOLERANCIA_S:
                    atras += 1
                    peor = max(peor, -fuera)
            fin_previo = dts + dur
    span = (dts_fin - dts_ini) if None not in (dts_ini, dts_fin) \
        else (ultimo - primero)
    return {
        "primero": primero, "ultimo": ultimo, "paquetes": n,
        "contenido": contenido, "span": span,
        "huecos": max(span - contenido, 0.0),
        "saltos": saltos, "atras": atras,
        "segundos_saltos": segundos_saltos, "peor": peor,
    }


def medir(exe: str, ruta: str) -> bool:
    """Informa una grabacion. -> True si pasa la banda de SPEC 22."""
    print("=" * 72)
    # Con el nombre de la carpeta: el mismo .mp4 rehecho en dos sitios
    # distintos (una prueba contra otra) se distingue.
    padre = os.path.basename(os.path.dirname(os.path.abspath(ruta)))
    print("%s  [%s]" % (os.path.basename(ruta), padre))
    try:
        todas = pistas(exe, ruta)
    except (RuntimeError, ValueError) as e:
        print("  no se pudo leer: %s" % e)
        return False

    video = next((p for p in todas if p["tipo"] == "video"), None)
    audio = next((p for p in todas if p["tipo"] == "audio"), None)
    print("  streams: %s" % (", ".join(
        "%s(%s)" % (p["codec"], p["tipo"]) for p in todas) or "ninguno"))
    if video is None:
        print("  SIN PISTA DE VIDEO")
        return False
    if audio is None:
        # El caso que SPEC 22 vino a cerrar: antes salia asi cuando la
        # mezcla fallaba, con un *_audio.mp4 suelto al lado.
        print("  MUDO: no tiene pista de audio -- no hay sincronia que medir")
        return False

    try:
        v = recorrer(exe, ruta, video["indice"])
        a = recorrer(exe, ruta, audio["indice"])
    except (RuntimeError, ValueError) as e:
        print("  no se pudieron leer los paquetes: %s" % e)
        return False
    if v["primero"] is None or a["primero"] is None:
        print("  alguna pista no trajo ni un paquete")
        return False

    for etiq, p in (("video", v), ("audio", a)):
        print("  %-5s : pts %8.3f -> %8.3f s | span %8.3f s | contenido "
              "%8.3f s | %d paquetes" % (etiq, p["primero"], p["ultimo"],
                                         p["span"], p["contenido"],
                                         p["paquetes"]))
    for etiq, p in (("video", v), ("audio", a)):
        print("  %-5s : huecos %.3f s | %d discontinuidad(es) de dts, "
              "la peor %.3f s" % (etiq, p["huecos"], p["saltos"] + p["atras"],
                                  p["peor"]))

    cabeza = a["primero"] - v["primero"]
    cola = a["ultimo"] - v["ultimo"]
    # EL numero. Si el desfase al final es el mismo que al principio, el
    # audio no se corrio en ningun punto del archivo: va pegado al video de
    # punta a punta. Es lo que SPEC 22 pide como "no deriva".
    #
    # Medido sobre la misma sesion de 5 min a la que se le borraron 9,6 s de
    # segmentos de VIDEO y nada de audio:
    #   camino viejo (una union por pista + mezcla desde t=0)
    #       video 294,400 s / audio 304,000 s -> deriva +9,600 s
    #   camino nuevo (una sola pasada, -copyts, sin cerrar huecos)
    #       video 304,000 s / audio 304,000 s -> deriva -0,001 s
    # El hueco de video se quedo donde estaba (fotograma congelado 9,6 s) y
    # el sonido siguio cuadrando.
    deriva = cola - cabeza
    print("  cabeza (el audio arranca despues) : %+8.3f s" % cabeza)
    print("  cola   (el audio termina despues) : %+8.3f s" % cola)
    print("  DERIVA del desfase (cola - cabeza): %+8.3f s   %s"
          % (deriva, "OK" if abs(deriva) < BANDA_S else "FUERA DE BANDA"))
    solape = min(v["ultimo"], a["ultimo"]) - max(v["primero"], a["primero"])
    total = max(v["ultimo"], a["ultimo"]) - min(v["primero"], a["primero"])
    print("  las dos pistas existen en %.3f s de %.3f s (%.2f %%)"
          % (solape, total, 100.0 * solape / total if total else 0.0))
    if abs(deriva) >= BANDA_S:
        # Desde el .mp4 solo no se puede saber cual de las dos causas es:
        # las dos dejan las pistas continuas. Se distinguen mirando los
        # segmentos en disco (si siguen ahi): si los tfdt crudos de cada
        # pista abarcan ventanas distintas es lo primero; si abarcan la
        # misma y el .mp4 sale mas corto, a una le cerraron los huecos.
        print("  nota: una deriva asi puede ser (a) desincronizacion de "
              "verdad -- a una pista le cerraron los huecos y a la otra no, "
              "que es como se unia antes de SPEC 22 -- o (b) que las dos "
              "ventanas de captura no terminen igual, porque las dos "
              "chunklists se piden por separado y no cierran el mismo "
              "segundo. Si |cabeza| es chica y la cola grande, sospechar "
              "(a).")
    elif abs(cabeza) >= BANDA_S:
        print("  nota: la cabeza no es desincronizacion -- es que la pista "
              "de audio arranco %.0f ms mas tarde (ese tramo no se capturo). "
              "Lo que importa es que la cola sea igual, y lo es."
              % (cabeza * 1000))
    return abs(deriva) < BANDA_S


def main(argv) -> int:
    exe = ffprobe()
    if not exe:
        print("no encuentro ffprobe (probe M3U8_FFPROBE, nucleo\\scripts\\ "
              "o el PATH)")
        return 2

    if "--todas" in argv:
        rutas = sorted(
            os.path.join(DIR_GRABACIONES, n)
            for n in os.listdir(DIR_GRABACIONES)
            if n.lower().endswith(".mp4"))
    else:
        rutas = [a for a in argv if not a.startswith("-")]
    if not rutas:
        print(__doc__)
        return 2

    bien = 0
    for ruta in rutas:
        if not os.path.isfile(ruta):
            print("no esta: %s" % ruta)
            continue
        if medir(exe, ruta):
            bien += 1
    print("=" * 72)
    print("%d de %d con la sincronia dentro de banda (|deriva| < %.0f ms)"
          % (bien, len(rutas), BANDA_S * 1000))
    return 0 if bien == len(rutas) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
