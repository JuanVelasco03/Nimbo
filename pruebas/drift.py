# -*- coding: utf-8 -*-
r"""Timestamps y desfasaje audio/video de los fragmentos de una sesion.

Herramienta de medicion, NO parte del motor. Solo lee disco. Usa
nimbo\mp4.py (tiempos_del_fragmento / pistas_del_init), que ya hace el
recorrido de cajas MP4 -- ojo que las claves del dict de pistas_del_init
son "pista" y "escala", no "id" ni "timescale".

Que mide, y por que importa (specs\reproductor_vivo_ticks.md 2.2 y 2.3):

  discontinuidades .... por pista, cuantas veces tfdt[n+1] != tfdt[n] +
                        duracion[n]. Si da 0, los timestamps que escribe
                        el grabador son CONTIGUOS y el comentario viejo de
                        reproductor_vivo.js ("vienen sucios a proposito")
                        es falso.
  desfasaje A/V ....... la suma de duraciones de cada pista. Por parte de
                        0,5 s no cuadra (el audio AAC va en bloques de
                        1024 muestras), pero el encoder lo compensa cada
                        tanto SOBRE LA LINEA DE TIEMPO ABSOLUTA -- por eso
                        appendear el grupo entero deja que se cancele y
                        appendear parte por parte, con mode="sequence", no.

Uso:
    python pruebas\drift.py <modelo_id/variante_sesion> [n]   (defecto 240)
"""
import os
import sys

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "nimbo"))

import mp4        # noqa: E402  (despues del sys.path)
import vivo       # noqa: E402  (clave_segmento / ruta_init, ya probados)

DIR_STREAMS = os.path.join(RAIZ, "datos", "streams")


def fragmentos(carpeta_segs, n):
    pares = []
    for nombre in os.listdir(carpeta_segs):
        if not nombre.lower().endswith(vivo.EXT_SEGMENTO):
            continue
        c = vivo.clave_segmento(nombre)
        if c is not None:
            pares.append((c, nombre))
    pares.sort(key=lambda p: p[0])
    return pares[-n:] if n and len(pares) > n else pares


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    rel = argv[0]
    n = int(argv[1]) if len(argv) > 1 else 240
    carpeta = os.path.join(DIR_STREAMS, *rel.split("/"))
    carpeta_segs = os.path.join(carpeta, vivo.CARPETA_SEGS)
    if not os.path.isdir(carpeta_segs):
        print("no existe: %s" % carpeta_segs)
        return 1

    ruta_init = vivo.ruta_init(carpeta)
    escalas, tipos = {}, {}
    if ruta_init:
        with open(ruta_init, "rb") as f:
            for p in mp4.pistas_del_init(f.read()):
                escalas[p["pista"]] = p["escala"] or 1
                tipos[p["pista"]] = p["tipo"]

    pares = fragmentos(carpeta_segs, n)
    if not pares:
        print("sin fragmentos")
        return 1

    ultimo = {}            # pista -> tfdt + duracion esperado
    disc = {}              # pista -> discontinuidades
    acum = {}              # pista -> unidades acumuladas
    por_parte = {}         # pista -> {duracion: veces}
    por_grupo = {}         # (epoch,secuencia) -> {pista: unidades}
    primer_tfdt = {}

    for clave, nombre in pares:
        with open(os.path.join(carpeta_segs, nombre), "rb") as f:
            datos = f.read()
        for pista, tfdt, dur in mp4.tiempos_del_fragmento(datos):
            primer_tfdt.setdefault(pista, tfdt)
            esperado = ultimo.get(pista)
            if esperado is not None and tfdt != esperado:
                disc[pista] = disc.get(pista, 0) + 1
                if disc[pista] <= 5:
                    print("  discontinuidad pista %d en %s: tfdt=%d esperado=%d (delta %+d)"
                          % (pista, nombre, tfdt, esperado, tfdt - esperado))
            ultimo[pista] = tfdt + dur
            acum[pista] = acum.get(pista, 0) + dur
            por_parte.setdefault(pista, {})
            por_parte[pista][dur] = por_parte[pista].get(dur, 0) + 1
            g = por_grupo.setdefault((clave[0], clave[1]), {})
            g[pista] = g.get(pista, 0) + dur

    print()
    print("sesion: %s  (%d fragmentos, init=%s)"
          % (rel, len(pares), os.path.basename(ruta_init) if ruta_init else "-"))
    print("discontinuidades de tfdt: %d" % sum(disc.values()))
    for pista in sorted(acum):
        escala = escalas.get(pista, 1) or 1
        print("pista %d (%s, timescale %d): %d fragmentos, %.4f s  (primer tfdt %d = %.1f s)"
              % (pista, tipos.get(pista, "?"), escala,
                 sum(por_parte[pista].values()), acum[pista] / escala,
                 primer_tfdt.get(pista, 0), primer_tfdt.get(pista, 0) / escala))

    print()
    print("duraciones por parte (unidades -> veces -> segundos):")
    for pista in sorted(por_parte):
        escala = escalas.get(pista, 1) or 1
        for dur in sorted(por_parte[pista]):
            print("  pista %d: %7d x%-5d = %.4f s"
                  % (pista, dur, por_parte[pista][dur], dur / escala))

    video = [p for p in acum if tipos.get(p) == "video"]
    audio = [p for p in acum if tipos.get(p) == "audio"]
    if video and audio:
        pv, pa = video[0], audio[0]
        sv = acum[pv] / (escalas.get(pv, 1) or 1)
        sa = acum[pa] / (escalas.get(pa, 1) or 1)
        print()
        print("desfasaje audio-video acumulado: %+.4f s  (video %.4f, audio %.4f)"
              % (sa - sv, sv, sa))
        print()
        print("por grupo (epoch,secuencia) -- lo que un solo appendBuffer")
        print("del grupo entero deja que el encoder compense:")
        mostrados = 0
        for g in sorted(por_grupo, reverse=True):
            u = por_grupo[g]
            if pv not in u or pa not in u:
                continue
            gv = u[pv] / (escalas.get(pv, 1) or 1)
            ga = u[pa] / (escalas.get(pa, 1) or 1)
            print("  seq=%-7d video=%.4f s  audio=%.4f s  delta=%+.1f ms"
                  % (g[1], gv, ga, (ga - gv) * 1000))
            mostrados += 1
            if mostrados >= 12:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
