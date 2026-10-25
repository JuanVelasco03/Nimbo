# -*- coding: utf-8 -*-
r"""Perdida real de fragmentos por sesion, y si correlaciona con cuantas
modelos se graban a la vez.

Herramienta de medicion, NO parte del motor. Solo lee disco
(datos\streams\*\*\segmentos); no pide nada por red ni toca estado\.

Que mide: por sesion, cuantos numeros de secuencia FALTAN dentro del rango
que esa sesion alcanzo a escribir -- (max - min + 1) - distintos. Eso es
perdida de verdad (un 404/timeout que no se recupero), no un artefacto del
reproductor: el % tiene que quedar IGUAL antes y despues de tocar
reproductor_vivo.js, porque son procesos distintos. Si cambia, algo del
cliente esta afectando al grabador.

Ademas estima la concurrencia de cada sesion por solape temporal (del epoch
que viene en el nombre del archivo) contra las demas, y agrupa el % de
perdida por nivel de concurrencia -- es lo que descarto tocar POOL_HILOS o
el timeout de mouflon.pedir (specs\reproductor_vivo_ticks.md, 2.6 y 9).

Uso:
    python pruebas\huecos.py [minimo_de_fragmentos]   (defecto 40)
"""
import os
import re
import sys

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_STREAMS = os.path.join(RAIZ, "datos", "streams")

RE_SEGMENTO = re.compile(      # copia de vivo.py:RE_SEGMENTO
    r"_(\d+)_[A-Za-z0-9]+_(\d+)(?:_part(\d+))?\.[A-Za-z0-9]+$")
PARTE_ENTERA = 1 << 30
EXT_SEGMENTO = (".mp4", ".m4s", ".ts", ".cmfv", ".cmfa")


def clave_segmento(nombre):
    m = RE_SEGMENTO.search(nombre)
    if not m:
        return None
    parte = m.group(3)
    return (int(m.group(2)), int(m.group(1)),
            int(parte) if parte is not None else PARTE_ENTERA)


def sesiones(minimo):
    """-> [{"rel","secuencias","inicio","fin"}] una por carpeta de sesion."""
    salida = []
    if not os.path.isdir(DIR_STREAMS):
        return salida
    for modelo in sorted(os.listdir(DIR_STREAMS)):
        dir_modelo = os.path.join(DIR_STREAMS, modelo)
        if not os.path.isdir(dir_modelo):
            continue
        for sesion in sorted(os.listdir(dir_modelo)):
            carpeta = os.path.join(dir_modelo, sesion, "segmentos")
            if not os.path.isdir(carpeta):
                continue
            secuencias = set()
            epocas = []
            total = 0
            try:
                nombres = os.listdir(carpeta)
            except OSError:
                continue
            for n in nombres:
                if not n.lower().endswith(EXT_SEGMENTO):
                    continue
                c = clave_segmento(n)
                if c is None:
                    continue
                total += 1
                secuencias.add(c[1])
                epocas.append(c[0])
            if total < minimo or not secuencias:
                continue
            salida.append({
                "rel": "%s/%s" % (modelo, sesion),
                "secuencias": secuencias,
                "archivos": total,
                "inicio": min(epocas), "fin": max(epocas),
            })
    return salida


def concurrencia(s, todas):
    """Cuantas sesiones de OTRAS modelos solapan en el tiempo con esta."""
    modelo = s["rel"].split("/")[0]
    otras = set()
    for o in todas:
        om = o["rel"].split("/")[0]
        if om == modelo:
            continue
        if o["inicio"] <= s["fin"] and s["inicio"] <= o["fin"]:
            otras.add(om)
    return len(otras) + 1


def main(argv):
    minimo = int(argv[0]) if argv else 40
    todas = sesiones(minimo)
    if not todas:
        print("sin sesiones con >= %d fragmentos en %s" % (minimo, DIR_STREAMS))
        return 1

    print("%-62s %7s %7s %7s %6s" % ("sesion", "segs", "rango", "huecos", "conc"))
    tot_rango = tot_huecos = 0
    por_conc = {}
    for s in sorted(todas, key=lambda x: x["rel"]):
        lo, hi = min(s["secuencias"]), max(s["secuencias"])
        rango = hi - lo + 1
        huecos = rango - len(s["secuencias"])
        conc = concurrencia(s, todas)
        tot_rango += rango
        tot_huecos += huecos
        acum = por_conc.setdefault(conc, [0, 0, 0])
        acum[0] += rango
        acum[1] += huecos
        acum[2] += 1
        print("%-62s %7d %7d %7d %6d"
              % (s["rel"][:62], len(s["secuencias"]), rango, huecos, conc))

    print()
    print("TOTAL: %d secuencias esperadas, %d huecos = %.2f%% de perdida"
          % (tot_rango, tot_huecos,
             100.0 * tot_huecos / tot_rango if tot_rango else 0.0))
    print()
    print("por nivel de concurrencia (modelos solapadas):")
    for conc in sorted(por_conc):
        rango, huecos, n = por_conc[conc]
        print("  %2d modelos: %5d secuencias, %4d huecos = %.2f%%  (%d sesiones)"
              % (conc, rango, huecos,
                 100.0 * huecos / rango if rango else 0.0, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
