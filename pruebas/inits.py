# -*- coding: utf-8 -*-
r"""Cuantas rotaciones de sesion podrian seguir en el MISMO SourceBuffer.

Herramienta de medicion, NO parte del motor. Solo lee disco.

Por que (specs\reproductor_vivo_ticks.md 5): cada reconexion del encoder o
cambio de calidad crea una carpeta de sesion nueva, y el cliente la remonta
desde cero -- MediaSource nuevo, init nuevo, arranque 3 grupos atras. Eso es
un corte de varios segundos, el artefacto mas visible de la previa. Si el
init nuevo fuera byte a byte igual al de la sesion anterior, se podria
seguir appendeando en el mismo SourceBuffer y el corte seria casi nada.

Esto mide exactamente eso, antes de escribir una linea de ese arreglo:
agrupa las sesiones de cada modelo por VARIANTE (mismo
<calidad>_<codec>), las ordena por el sello de tiempo del nombre y compara
el init de cada una con el de la anterior -- por hash del contenido, no por
nombre (los nombres siempre difieren: traen un id aleatorio del CDN).

Uso:
    python pruebas\inits.py
"""
import hashlib
import os
import sys

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_STREAMS = os.path.join(RAIZ, "datos", "streams")


def ruta_init(carpeta):
    """Igual que vivo.py:ruta_init pero sin el respaldo de la hermana: aca
    interesa el init PROPIO de cada sesion."""
    try:
        for n in sorted(os.listdir(carpeta)):
            if n.lower().startswith("init"):
                return os.path.join(carpeta, n)
    except OSError:
        pass
    return ""


def huella(ruta):
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
    except OSError:
        return None, 0
    return hashlib.sha1(datos).hexdigest()[:12], len(datos)


def main():
    if not os.path.isdir(DIR_STREAMS):
        print("no existe %s" % DIR_STREAMS)
        return 1

    iguales = distintos = 0
    sin_init = 0
    detalle = []

    for modelo in sorted(os.listdir(DIR_STREAMS)):
        dir_modelo = os.path.join(DIR_STREAMS, modelo)
        if not os.path.isdir(dir_modelo):
            continue
        # <variante>_<sello>: la variante es todo menos el ultimo "_"
        por_variante = {}
        for sesion in os.listdir(dir_modelo):
            carpeta = os.path.join(dir_modelo, sesion)
            if not os.path.isdir(carpeta):
                continue
            variante, _, sello = sesion.rpartition("_")
            if not variante:
                continue
            por_variante.setdefault(variante, []).append((sello, sesion))

        for variante, sesiones in sorted(por_variante.items()):
            sesiones.sort()
            if len(sesiones) < 2:
                continue
            anterior = None
            for sello, sesion in sesiones:
                ruta = ruta_init(os.path.join(dir_modelo, sesion))
                if not ruta:
                    sin_init += 1
                    continue
                h, tam = huella(ruta)
                if anterior is not None:
                    if h == anterior[0]:
                        iguales += 1
                        marca = "IGUAL"
                    else:
                        distintos += 1
                        marca = "distinto"
                    detalle.append("%-28s %-16s %s -> %s  %s (%d/%d bytes)"
                                   % (modelo, variante, anterior[1], sello,
                                      marca, anterior[2], tam))
                anterior = (h, sello, tam)

    for fila in detalle:
        print(fila)
    total = iguales + distintos
    print()
    print("rotaciones comparadas (misma modelo, misma variante): %d" % total)
    print("  init IDENTICO al anterior: %d  (%.0f%%)"
          % (iguales, 100.0 * iguales / total if total else 0))
    print("  init distinto:             %d  (%.0f%%)"
          % (distintos, 100.0 * distintos / total if total else 0))
    print("  sesiones sin init propio:  %d" % sin_init)
    print()
    print("Leer asi: solo las rotaciones con init IDENTICO podrian seguir en")
    print("el mismo SourceBuffer sin remontar. Con el resto el corte es")
    print("inevitable -- es otro init, no se puede appendear encima.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
