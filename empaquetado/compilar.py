"""Compila nimbo\\ a un binario con Nuitka (S5 de la spec).

    python empaquetado\\compilar.py                 (deja dist\\compilado\\)
    python empaquetado\\compilar.py --comprobar     (solo dice que se cierra y que no)

Es **opcional**: el instalador funciona igual sin esto, solo que con el
codigo de la interfaz legible. Lo que esto cambia es que `nimbo\\*.py` y
el **camino directo** pasan a ser codigo maquina en vez de fuente.

Lo que NO cierra, y no hay forma de evitarlo sin reescribir como
mitmproxy carga sus addons:

  nucleo\\scripts\\detectar_m3u8.py   mitmdump lo carga por ruta (-s)
  nimbo\\puente.py                    idem, es el addon hermano
  nucleo\\scripts\\mouflon.py         lo IMPORTA detectar_m3u8.py al correr
  nucleo\\scripts\\supervisor.py      lo carga el lanzador y lo importa sitio.py

Ojo con mouflon.py: ahi vive la tabla de claves MOUFLON, y tiene que
seguir siendo un .py en disco porque el detector hace `import mouflon`
bajo el interprete de mitmdump (3.14), que no ve nada de lo compilado
aca. Compilarla adentro del binario no la saca de disco. Ver S5.1.

Despues de compilar, `ajustes.compilado()` da True y `motor.py` arranca el
camino directo relanzando el binario con `--modo directo` en vez de
`[python, lanzador_directo.py]` -- por eso el binario tiene que incluir
`modos`, `lanzador_directo` y `grabador_directo`, que Nuitka no puede
descubrir solo (el import vive detras de un sys.path.insert en tiempo de
ejecucion).
"""
import argparse
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
DIR_NIMBO = os.path.join(RAIZ, "nimbo")
DIR_SCRIPTS = os.path.join(RAIZ, "nucleo", "scripts")

# Modulos que Nuitka no encuentra siguiendo imports desde app.py:
#   modos            lo importa una rama condicional del arranque
#   lanzador_directo lo importa modos adentro de una funcion
#   grabador_directo + mouflon  los importa lanzador_directo DESPUES de
#                    tocar sys.path en tiempo de ejecucion
OCULTOS = ("modos", "lanzador_directo", "grabador_directo", "mouflon")

# Lo que queda en disco igual (y por que). Solo informativo: lo copia
# construir.py, no esto.
ABIERTOS = {
    "nucleo\\scripts\\detectar_m3u8.py": "mitmdump lo carga por ruta (-s)",
    "nimbo\\puente.py": "addon hermano, mismo motivo",
    "nucleo\\scripts\\mouflon.py": "detectar_m3u8.py hace import mouflon",
    "nucleo\\scripts\\supervisor.py": "lo carga el lanzador; lo importa sitio.py",
    "nimbo\\lanzador_supervisor.py": "corre bajo el Python embebido, no compilado",
}


def _log(*a):
    print("[COMPILAR]", *a, flush=True)


def comprobar():
    _log("se cierra (pasa a binario):")
    for n in sorted(os.listdir(DIR_NIMBO)):
        if not n.endswith(".py"):
            continue
        rel = "nimbo\\" + n
        if rel in ABIERTOS:
            continue
        _log("   ", rel)
    for n in ("grabador_directo.py",):
        _log("    nucleo\\scripts\\" + n)
    _log("")
    _log("queda legible en disco:")
    for ruta, por_que in ABIERTOS.items():
        _log("    %-38s %s" % (ruta, por_que))
    _log("")
    _log("La tabla de claves MOUFLON esta en mouflon.py, que queda abierto.")
    _log("No se puede cerrar sin cambiar como el detector la importa.")


def compilar(salida, consola):
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-dir=" + salida,
        "--output-filename=Nimbo.exe",
        # La interfaz es Edge en modo --app: este proceso no tiene consola
        # (igual que pythonw.exe hoy).
        "--windows-console-mode=" + ("force" if consola else "disable"),
        "--company-name=Nimbo",
        "--product-name=Nimbo",
        "--file-version=1.0.0.0",
        "--product-version=1.0.0",
        # nimbo\ se importa SIN paquete (ver CLAUDE.md, "Imports planos"):
        # app.py corre como script y su carpeta queda en sys.path. Para
        # Nuitka eso significa compilar con esa carpeta como raiz, no
        # como paquete -- de ahi el --include-plugin-directory.
        "--include-plugin-directory=" + DIR_NIMBO,
    ]
    for modulo in OCULTOS:
        cmd.append("--include-module=" + modulo)
    cmd.append(os.path.join(DIR_NIMBO, "app.py"))

    env = dict(os.environ)
    # Para que --include-module encuentre grabador_directo y mouflon, que
    # viven en nucleo\scripts\ y no en nimbo\.
    rutas = [DIR_NIMBO, DIR_SCRIPTS]
    if env.get("PYTHONPATH"):
        rutas.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(rutas)

    _log("nuitka --standalone (esto tarda varios minutos)")
    r = subprocess.run(cmd, cwd=RAIZ, env=env)
    if r.returncode != 0:
        raise SystemExit("nuitka fallo (codigo %d)" % r.returncode)
    _log("listo:", os.path.join(salida, "app.dist"))
    _log("")
    _log("Pasos que faltan a mano (todavia no automatizados):")
    _log("  1. copiar app.dist\\ dentro del paquete de construir.py")
    _log("  2. borrar de ahi los nimbo\\*.py que quedaron compilados")
    _log("  3. apuntar el acceso directo del .iss a Nimbo.exe en vez de")
    _log("     runtime\\python\\pythonw.exe")
    _log("  Ver empaquetado\\LEEME.md, seccion 'Compilado'.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", default=os.path.join(RAIZ, "dist", "compilado"))
    p.add_argument("--comprobar", action="store_true",
                   help="no compila: solo lista que se cierra y que no")
    p.add_argument("--consola", action="store_true",
                   help="dejar la consola visible (para depurar el binario)")
    args = p.parse_args(argv)

    if args.comprobar:
        comprobar()
        return 0
    compilar(os.path.abspath(args.salida), args.consola)
    return 0


if __name__ == "__main__":
    sys.exit(main())
