# -*- coding: utf-8 -*-
r"""Integridad del canal servidor->cliente del reproductor en vivo.

Herramienta de medicion, NO parte del motor de captura ni de Nimbo. Imita
exactamente lo que hace reproductor_vivo.js:_tick() contra
/api/vivo/segmentos -- mismo `limite`, mismo `atras`, misma cadena de
cursores, incluido el `desde=None` cuando el servidor contesta `salto` --
y al final compara lo entregado contra lo que quedo en disco.

Responde dos preguntas, que son las dos puertas de specs\
reproductor_vivo_ticks.md seccion 8.1:

  duplicados ................ fragmentos entregados dos veces (en
                              mode="sequence" un duplicado NO se detecta
                              por timestamp: se pega atras del anterior)
  en disco y nunca entregado  fragmentos que el reproductor nunca pudo
                              appendear, dentro de la ventana sondeada

Las dos tienen que dar 0, antes y despues de tocar el cliente.

Solo lee: la API local (GET) y el disco. No escribe nada, no toca
grabar.txt ni estado\. El puerto y el token salen de instancia.json,
igual que hace cualquier pestana de Nimbo.

Imita las DOS variantes del cliente, para poder comparar sobre la misma
sesion (specs\reproductor_vivo_ticks.md 8):

  --modo grupo  (defecto) el cliente de hoy: consume grupos (epoch,
                secuencia) enteros, deja el ultimo grupo con partes para
                el tick siguiente y avanza el cursor solo hasta lo ultimo
                consumido -- NO hasta el `cursor` que devolvio el servidor.
  --modo parte  el de antes del 2026-10-25: un append por archivo y el
                cursor del servidor tal cual.

Uso:
    python pruebas\sondeo.py <modelo_id/variante_sesion> <segundos> [--modo grupo|parte]
    python pruebas\sondeo.py --listar

(--listar muestra las sesiones vivas segun estado\detector.json +
estado\directo.json, que es de donde salen las rutas relativas.)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "nimbo"))

# los mismos numeros que reproductor_vivo.js (MAX_POR_TICK,
# SEGMENTOS_ATRAS, INTERVALO_TICK_MS): sondear con otros no mediria al
# cliente real
LIMITE = 24
ATRAS = 3
INTERVALO_S = 0.5

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


def instancia():
    with open(os.path.join(RAIZ, "instancia.json"), encoding="utf-8") as f:
        d = json.load(f)
    return "http://127.0.0.1:%d" % d["puerto"], d["token"]


def pedir(base, token, ruta):
    url = base + ruta + ("&" if "?" in ruta else "?") + "t=" + token
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def sesiones_vivas():
    """Lo mismo que hace estado_captura.fusionar() para este fin: las
    carpetas de sesion que publican detector.json y directo.json."""
    raices = []
    for nombre in ("detector.json", "directo.json"):
        try:
            with open(os.path.join(RAIZ, "estado", nombre),
                      encoding="utf-8") as f:
                raices.append(json.load(f))
        except (OSError, ValueError):
            continue
    dir_streams = os.path.join(RAIZ, "datos", "streams")
    salida = []
    for d in raices:
        for fila in d.get("streams") or []:
            for s in fila.get("sesiones") or []:
                carpeta = s.get("carpeta") or ""
                if not carpeta:
                    continue
                try:
                    rel = os.path.relpath(carpeta, dir_streams)
                except ValueError:
                    continue
                if rel.startswith(".."):
                    continue
                salida.append((fila.get("modelo", ""),
                               rel.replace(os.sep, "/")))
    return salida


def en_disco(rel):
    carpeta = os.path.join(RAIZ, "datos", "streams",
                           *rel.split("/"), "segmentos")
    try:
        nombres = os.listdir(carpeta)
    except OSError:
        return {}
    salida = {}
    for n in nombres:
        if not n.lower().endswith(EXT_SEGMENTO):
            continue
        c = clave_segmento(n)
        if c is not None:
            salida[c] = n
    return salida


def agrupar(segmentos):
    """Lo mismo que reproductor_vivo.js:_agrupar: parte consecutivas del
    mismo (epoch, secuencia) juntas, en el orden en que vinieron."""
    grupos = []
    for seg in segmentos:
        c = clave_segmento(seg["nombre"])
        if c is None:
            continue
        if grupos and grupos[-1]["clave"] == (c[0], c[1]):
            grupos[-1]["partes"].append((c, seg))
        else:
            grupos.append({"clave": (c[0], c[1]), "partes": [(c, seg)]})
    return grupos


def consumir_por_grupo(r, vistos, ultima_clave):
    """-> (claves_consumidas, cursor_nuevo, ultima_clave). Copia fiel de
    reproductor_vivo.js:_appendearGrupos: el ultimo grupo con partes queda
    en espera, y el cursor no pasa nunca por encima de lo consumido."""
    grupos = agrupar(r.get("segmentos") or [])
    consumidas = []
    cursor_nuevo = None
    for i, g in enumerate(grupos):
        entero = all(c[2] == PARTE_ENTERA for c, _ in g["partes"])
        if i == len(grupos) - 1 and not entero:
            break                      # borde vivo: esperar al tick que viene
        clave_min = g["partes"][0][0]
        clave_max = g["partes"][-1][0]
        cursor_nuevo = g["partes"][-1][1]["cursor"]
        if ultima_clave is not None and clave_min <= ultima_clave:
            continue                   # ya appendeado (ventana solapada)
        for c, _seg in g["partes"]:
            consumidas.append(c)
        ultima_clave = clave_max
    return consumidas, cursor_nuevo, ultima_clave


def consumir_por_parte(r, vistos, ultima_clave):
    consumidas = []
    cursor_nuevo = None
    for seg in r.get("segmentos") or []:
        c = clave_segmento(seg["nombre"])
        cursor_nuevo = seg["cursor"]
        if c is None:
            continue
        if ultima_clave is not None and c <= ultima_clave:
            continue
        consumidas.append(c)
        ultima_clave = c
    return consumidas, cursor_nuevo, ultima_clave


def sondear(rel, segundos, modo="grupo"):
    base, token = instancia()
    consumir = consumir_por_grupo if modo == "grupo" else consumir_por_parte
    antes = en_disco(rel)
    print("sesion: %s  (modo=%s)" % (rel, modo))
    print("en disco al empezar: %d fragmentos" % len(antes))

    entregados = []          # en orden de entrega
    vistos = set()
    duplicados = 0
    saltos = 0
    ticks = 0
    errores = 0
    cursor = None
    ultima_clave = None
    salto_pendiente = True   # el primer pedido va sin `desde`, como montar()

    fin = time.time() + segundos
    while time.time() < fin:
        ciclo = time.time()
        desde = None if salto_pendiente else cursor
        salto_pendiente = False
        ruta = ("/api/vivo/segmentos?sesion=" + urllib.parse.quote(rel)
                + "&desde=" + (urllib.parse.quote(desde) if desde else "")
                + "&limite=%d&atras=%d" % (LIMITE, ATRAS))
        try:
            r = pedir(base, token, ruta)
        except (urllib.error.URLError, OSError, ValueError) as e:
            errores += 1
            print("  ! %s" % e)
            time.sleep(INTERVALO_S)
            continue
        ticks += 1
        consumidas, cursor_nuevo, ultima_clave = consumir(
            r, vistos, ultima_clave)
        for c in consumidas:
            if c in vistos:
                duplicados += 1
                print("  DUPLICADO: %s" % str(c))
                continue
            vistos.add(c)
            entregados.append(c)
        # con append por grupo el cursor avanza solo hasta lo consumido:
        # el ultimo grupo puede haber quedado en espera
        if modo == "grupo":
            if cursor_nuevo:
                cursor = cursor_nuevo
        else:
            cursor = r.get("cursor")
        if r.get("salto"):
            saltos += 1
            salto_pendiente = True
        espera = INTERVALO_S - (time.time() - ciclo)
        if espera > 0:
            time.sleep(espera)

    despues = en_disco(rel)
    aparecieron = len(set(despues) - set(antes))

    # la ventana sondeada: del primero al ultimo entregado. Lo que este en
    # disco dentro de ese rango y no se entrego nunca es exactamente lo
    # que el reproductor no pudo appendear.
    faltantes = []
    if entregados:
        lo, hi = min(entregados), max(entregados)
        for c in sorted(despues):
            if lo <= c <= hi and c not in vistos:
                faltantes.append(despues[c])

    print()
    print("ticks=%d  entregados=%d  duplicados=%d  salto=%d  errores=%d"
          % (ticks, len(entregados), duplicados, saltos, errores))
    print("aparecieron en disco durante el sondeo: %d" % aparecieron)
    print("EN DISCO Y NUNCA ENTREGADOS (dentro de la ventana): %d"
          % len(faltantes))
    for n in faltantes[:20]:
        print("   %s" % n)
    grupos = len({(c[0], c[1]) for c in entregados})
    print("grupos (epoch,secuencia) entregados: %d  (%.1f partes/grupo)"
          % (grupos, len(entregados) / grupos if grupos else 0))
    return 0 if duplicados == 0 and not faltantes else 1


def main(argv):
    if len(argv) >= 1 and argv[0] == "--listar":
        for modelo, rel in sesiones_vivas():
            print("%-24s %s" % (modelo, rel))
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    modo = "grupo"
    if "--modo" in argv:
        modo = argv[argv.index("--modo") + 1]
        if modo not in ("grupo", "parte"):
            print("--modo tiene que ser grupo o parte")
            return 2
    return sondear(argv[0], float(argv[1]), modo)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
