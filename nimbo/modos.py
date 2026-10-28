"""Multi-entrada del binario compilado: `Nimbo.exe --modo <X>`.

Ver `specs\\distribucion_instalador.md` S5.2. Sin compilar, esto no se usa
nunca: `motor.py` arranca a sus hijos como `[python, lanzador_*.py]` como
siempre (ver `ajustes.compilado`).

El problema que resuelve: compilado no hay `.py` sueltos que pasarle a un
interprete, asi que el binario se relanza a si mismo y este modulo decide
que hacer. **El hijo es otro proceso**, asi que importar aca lo que
necesita no viola la invariante dura del repo (nunca importar
`detectar_m3u8.py` desde el proceso de Nimbo): el que se importa es
`grabador_directo`, no el detector, y en un proceso que no es el de la
ventana.

Hoy el unico modo es `directo`. El supervisor **no** esta aca a proposito:
necesita Playwright con su driver de Node, y empaquetar eso adentro de un
binario de Nuitka es pelearse con rutas de archivos de datos para nada --
`supervisor.py` vive en disco de todas formas, porque no se puede cerrar
(lo importa `sitio.py` y lo carga el lanzador por ruta). Lo mismo vale
para la union, que sigue siendo `python detectar_m3u8.py --unir` como
subproceso.
"""
import sys

MODOS = ("directo",)


def correr(argv) -> int:
    """Atiende --modo y devuelve el codigo de salida del proceso."""
    try:
        nombre = argv[argv.index("--modo") + 1]
    except (ValueError, IndexError):
        print("uso: Nimbo.exe --modo {%s}" % "|".join(MODOS), file=sys.stderr)
        return 2

    if nombre == "directo":
        # Import adentro de la rama: el hijo del camino directo no necesita
        # el servidor, ni la bandeja, ni websockets.
        import lanzador_directo
        lanzador_directo.main()     # el suyo ya hace sys.exit()
        return 0

    print("modo desconocido: %s (hay: %s)" % (nombre, ", ".join(MODOS)),
          file=sys.stderr)
    return 2
