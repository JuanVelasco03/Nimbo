# -*- coding: utf-8 -*-
"""Sonda de estados (SPEC 24, paso 1): volcar el JSON crudo con el que cada
sitio dice en que estado esta una modelo, para fijar con datos reales el
criterio de "camara oculta" (stripchat, CrazyTicket / "Hidden Cam") y los
valores de room_status (chaturbate).

Herramienta de diagnostico, NO parte del motor de captura:
  - Solo libreria estandar. Importa mouflon/chaturbate del nucleo (los dos
    son seguros de importar: sin addons, sin estado de modulo mutable).
  - Solo lee del sitio y de grabar.txt. Escribe unicamente bajo --salida
    (por defecto pruebas\\muestras_estados\\, en .gitignore: son datos de
    terceros). Lo que se aprende va a pruebas\\RESULTADOS.md.

Uso:
    python pruebas\\sonda_estados.py --una <nombre> [--sitio stripchat|chaturbate]
    python pruebas\\sonda_estados.py --vigilar [--cada 60] [--grabar RUTA]

--una: un pedido, guarda el crudo e imprime lo que interesa.
--vigilar: recorre grabar.txt (lineas sueltas = stripchat, @chaturbate= =
chaturbate) y guarda una muestra cada vez que cambia la firma de una modelo
(stripchat: status, cam.show, isCamAvailable; chaturbate: room_status).
Pensado para dejarlo corriendo horas/dias hasta cazar una camara oculta.
"""
import argparse
import json
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(RAIZ, "nucleo", "scripts"))

import chaturbate  # noqa: E402
import mouflon  # noqa: E402

SALIDA = os.path.join(AQUI, "muestras_estados")
GRABAR = os.environ.get("M3U8_GRABAR") or os.path.join(
    RAIZ, "nucleo", "scripts", "grabar.txt")
PALABRAS = ("show", "ticket", "hidden", "active")


def _guardar(salida, sitio, nombre, crudo):
    os.makedirs(salida, exist_ok=True)
    ruta = os.path.join(salida, "%s_%s_%s.json" % (
        sitio, nombre, time.strftime("%Y%m%d-%H%M%S")))
    parcial = ruta + ".part"
    with open(parcial, "w", encoding="utf-8") as f:
        json.dump(crudo, f, ensure_ascii=False, indent=1)
    os.replace(parcial, ruta)
    return ruta


def _resumen_show(show):
    """cam.show puede quedar viejo (con endedAt) despues de que el show
    termino: para la firma alcanza con lo que lo identifica."""
    if not isinstance(show, dict):
        return show
    return (show.get("id"), show.get("mode"), show.get("endedAt"))


# -- stripchat --------------------------------------------------------------

def _ids_cache():
    ruta = os.path.join(RAIZ, "estado", "ids.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def muestra_stripchat(nombre, ids):
    """-> (firma, crudo, texto) o lanza mouflon.ErrorMouflon."""
    id_modelo = ids.get(nombre.lower())
    if not id_modelo:
        id_modelo = mouflon.resolver_id(nombre)
        ids[nombre.lower()] = id_modelo
    c = mouflon.consultar_cam(id_modelo)
    crudo = c["crudo"]
    cam = crudo.get("cam") or {}
    status = c["estado"]
    firma = (status, _resumen_show(cam.get("show")), cam.get("isCamAvailable"))
    lineas = [
        "[stripchat] %s (id %s)" % (nombre, id_modelo),
        "  status         = %r" % status,
        "  isCamAvailable = %r" % cam.get("isCamAvailable"),
        "  cam.show       = %s" % json.dumps(cam.get("show"), ensure_ascii=False),
    ]
    for k in sorted(cam):
        if k in ("show", "isCamAvailable"):
            continue
        if any(p in k.lower() for p in PALABRAS):
            lineas.append("  cam.%-12s = %s" % (
                k, json.dumps(cam[k], ensure_ascii=False)[:200]))
    return firma, crudo, "\n".join(lineas)


# -- chaturbate -------------------------------------------------------------

def muestra_chaturbate(nombre):
    c = chaturbate.consultar_estado(nombre)
    crudo = c.get("crudo") or {}
    status = crudo.get("room_status", c.get("estado"))
    lineas = ["[chaturbate] %s" % nombre,
              "  room_status = %r" % status,
              "  estado      = %r" % c.get("estado")]
    for k in sorted(crudo):
        if k != "room_status" and any(p in k.lower() for p in PALABRAS):
            lineas.append("  %-14s = %s" % (
                k, json.dumps(crudo[k], ensure_ascii=False)[:200]))
    return (status,), crudo, "\n".join(lineas)


# -- grabar.txt -------------------------------------------------------------

def leer_lista(ruta):
    """-> (stripchat, chaturbate). Mismo criterio de parseo que
    grabador_directo.LectorGrabar (portado, minimo)."""
    sueltas, cb = [], []
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
    except OSError:
        return [], []
    for linea in lineas:
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue
        if linea.startswith("@"):
            clave, _, valor = linea[1:].partition("=")
            if clave.strip().lower() == "chaturbate":
                cb += [v.strip() for v in valor.split(",") if v.strip()]
            continue
        if linea.lower() not in {"*", "all", "todo", "todos"}:
            sueltas.append(linea)
    cb_set = {n.lower() for n in cb}
    return [n for n in sueltas if n.lower() not in cb_set], cb


# -- modos ------------------------------------------------------------------

def una(args):
    if args.sitio == "chaturbate":
        _, crudo, texto = muestra_chaturbate(args.una)
    else:
        _, crudo, texto = muestra_stripchat(args.una, _ids_cache())
    print(texto)
    print("  -> %s" % _guardar(args.salida, args.sitio, args.una, crudo))


def vigilar(args):
    firmas = {}
    ids = _ids_cache()
    print("vigilando %s cada %ds -> %s" % (args.grabar, args.cada, args.salida))
    while True:
        sc, cb = leer_lista(args.grabar)
        pares = [("stripchat", n) for n in sc] + [("chaturbate", n) for n in cb]
        for sitio, nombre in pares:
            try:
                if sitio == "stripchat":
                    firma, crudo, texto = muestra_stripchat(nombre, ids)
                else:
                    firma, crudo, texto = muestra_chaturbate(nombre)
            except (mouflon.ErrorMouflon, OSError) as e:
                print("%s [%s] %s: %s" % (time.strftime("%H:%M:%S"), sitio,
                                          nombre, e))
                continue
            clave = (sitio, nombre.lower())
            if firmas.get(clave) != firma:
                firmas[clave] = firma
                ruta = _guardar(args.salida, sitio, nombre, crudo)
                print("%s cambio\n%s\n  -> %s" % (
                    time.strftime("%H:%M:%S"), texto, ruta), flush=True)
            time.sleep(0.5)
        time.sleep(args.cada)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--una", metavar="NOMBRE")
    modo.add_argument("--vigilar", action="store_true")
    p.add_argument("--sitio", choices=("stripchat", "chaturbate"),
                   default="stripchat")
    p.add_argument("--cada", type=int, default=60)
    p.add_argument("--grabar", default=GRABAR)
    p.add_argument("--salida", default=SALIDA)
    args = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    try:
        una(args) if args.una else vigilar(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
