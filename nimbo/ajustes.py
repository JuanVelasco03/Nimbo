"""Donde esta todo y como se configura Nimbo.

Nada esta clavado a una letra de unidad: la raiz se calcula desde la
ubicacion de este archivo. Se puede redirigir todo con NIMBO_BASE, que es
como se prueba sin tocar la instalacion de verdad.

Nimbo es un proyecto totalmente independiente de F:\\grabador: no lo lee en
ningun momento del runtime. El motor de captura (detectar_m3u8.py +
supervisor.py, mas ffmpeg.exe/wireproxy.exe y sus datos operativos) vive
copiado dentro de nucleo\\ (ver CLAUDE.md, seccion "El nucleo copiado", para
por que la copia tiene que respetar el nombre scripts\\ -- ambos scripts
calculan su propia base a partir de esa carpeta).

Las grabaciones y los streams a medio unir se guardan en <raiz>\\datos:
motor.py arranca el detector con M3U8_BASE=<raiz>\\datos, mecanismo que
detectar_m3u8.py ya trae para redirigir streams\\ y grabaciones\\ sin
tocarlo. grabar.txt y activos.txt siguen junto al script, en
nucleo\\scripts\\ (M3U8_BASE no los mueve: son operativos del detector, no
datos de usuario; ver ajustes.dir_datos).
"""

import json
import os
import shutil
import subprocess
import sys

# -- raiz del proyecto -----------------------------------------------------

# ajustes.py vive en <raiz>\nimbo\, asi que la raiz es dos niveles arriba.
_AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.environ.get("NIMBO_BASE") or os.path.dirname(_AQUI)

CONFIG = os.path.join(RAIZ, "config.json")
DIR_ESTADO = os.path.join(RAIZ, "estado")
DIR_REGISTRO = os.path.join(RAIZ, "registro")
DIR_WEB = os.path.join(_AQUI, "web")
# El motor de captura, copiado (ver docstring del modulo y CLAUDE.md,
# "El nucleo copiado"). El nombre de la subcarpeta scripts\ no es
# cosmetico: supervisor.py y detectar_m3u8.py la reconocen por nombre
# (SUBCARPETAS_SCRIPT) para calcular su propia base.
DIR_NUCLEO = os.path.join(RAIZ, "nucleo")
# Lo que trae el instalador para no depender de nada instalado en la
# maquina: Python embebido, mitmdump standalone y (si se armo el paquete
# completo) los navegadores de Playwright. En la copia de desarrollo esta
# carpeta no existe y todo cae a lo que haya en el sistema, como siempre
# -- ver localizar_python / localizar_mitmdump y
# specs\distribucion_instalador.md S4.
DIR_RUNTIME = os.path.join(RAIZ, "runtime")
# Grabaciones y streams: viven en Nimbo (ver M3U8_BASE en
# _arrancar_detector, nimbo\motor.py). Nunca se sube a git -- son .mp4
# y segmentos crudos, no codigo.
DIR_DATOS = os.path.join(RAIZ, "datos")

# Los JSON de estado los escriben los procesos hijos (el addon puente, el
# lanzador del supervisor y grabador_directo.py); Nimbo solo los lee.
ESTADO_DETECTOR = os.path.join(DIR_ESTADO, "detector.json")
ESTADO_SUPERVISOR = os.path.join(DIR_ESTADO, "supervisor.json")
ESTADO_DIRECTO = os.path.join(DIR_ESTADO, "directo.json")
# Este lo escribe el propio Nimbo (ver sesion.py / servidor.py), no un
# hijo -- la llamada a "favoritas en linea" de la cuenta real es un
# pedido HTTP comun, no hace falta un proceso aparte para eso.
ESTADO_SIGUIENDO = os.path.join(DIR_ESTADO, "siguiendo.json")

PUENTE = os.path.join(_AQUI, "puente.py")
LANZADOR_SUPERVISOR = os.path.join(_AQUI, "lanzador_supervisor.py")

# -- valores por defecto ---------------------------------------------------

# proxy: el puerto de mitmdump. 8080 es el de la instalacion por defecto y es
# el que espera supervisor.py en su GRAB_PROXY.
DEFECTOS = {
    "python": "",
    "mitmdump": "",
    "proxy": "127.0.0.1:8080",
    "sitio": "https://es.stripchat.com",
    "arrancar_al_abrir": True,
    "bandeja": True,
    "ver_navegador": False,
    "dias_segmentos": 1,
}

# Los archivos que tienen que estar en nucleo\scripts\ para poder arrancar
# el motor de captura. mouflon.py y grabador_directo.py son el camino
# directo (ver specs\grabador_directo.md); ffmpeg.exe no esta en la lista:
# su falta se degrada sola (el detector deja el pegote .raw.mp4 sin
# remuxear), no impide arrancar.
_NUCLEO_REQUERIDO = ("detectar_m3u8.py", "supervisor.py", "mouflon.py",
                     "grabador_directo.py")


def compilado() -> bool:
    """Si esto corre dentro del binario compilado con Nuitka.

    Cambia una sola cosa: como se arranca el camino directo. Compilado no
    hay un lanzador_directo.py suelto que pasarle a un interprete, asi
    que motor.py relanza el propio binario con --modo directo (ver
    nimbo\\modos.py y specs\\distribucion_instalador.md S5.2). El
    supervisor y la union siguen yendo por el Python embebido en los dos
    casos: supervisor.py y detectar_m3u8.py viven en disco de todas
    formas (mitmdump los carga por ruta).
    """
    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def nucleo_incompleto(ruta_scripts: str) -> list:
    """Que archivos imprescindibles faltan en nucleo\\scripts\\."""
    return [n for n in _NUCLEO_REQUERIDO
            if not os.path.isfile(os.path.join(ruta_scripts, n))]


def mitmdump_embarcado() -> str:
    """El mitmdump que trae el instalador, o "" si esta es una copia de
    desarrollo.

    Va primero que todo: si Nimbo se instalo con su runtime adentro no
    tiene por que depender de lo que el usuario tenga instalado aparte,
    ni heredar su version. Es el zip standalone de mitmproxy copiado tal
    cual, con su _internal\\ al lado (ver
    specs\\distribucion_instalador.md S2.1).
    """
    ruta = os.path.join(DIR_RUNTIME, "mitmproxy", "mitmdump.exe")
    return ruta if os.path.isfile(ruta) else ""


def python_embarcado() -> str:
    """El Python embebido que trae el instalador, o "" si no esta.

    Es el zip "embeddable" de python.org con las dependencias ya puestas
    adentro (ver empaquetado\\construir.py). Trae pythonw.exe al lado de
    python.exe, asi que _sin_consola sigue funcionando sin tocarlo.
    """
    ruta = os.path.join(DIR_RUNTIME, "python", "python.exe")
    return ruta if os.path.isfile(ruta) else ""


def navegadores_embarcados() -> str:
    """La carpeta de navegadores de Playwright del paquete completo, o ""
    si se armo el liviano (sin respaldo de navegador).

    Playwright la busca por PLAYWRIGHT_BROWSERS_PATH, que cargar() mete
    en el entorno de este proceso -- motor.py le pasa a sus hijos una
    copia de os.environ, asi que el supervisor la hereda sin que motor.py
    tenga que saber nada de esto.
    """
    ruta = os.path.join(DIR_RUNTIME, "navegadores")
    return ruta if os.path.isdir(ruta) else ""


def localizar_mitmdump() -> str:
    """mitmdump.exe. El instalador de mitmproxy no lo pone en el PATH de
    todas las consolas, asi que tambien se mira donde lo deja por defecto."""
    propio = mitmdump_embarcado()
    if propio:
        return propio
    hallado = shutil.which("mitmdump")
    if hallado:
        return hallado
    for ruta in (r"C:\Program Files\mitmproxy\bin\mitmdump.exe",
                 r"C:\Program Files (x86)\mitmproxy\bin\mitmdump.exe"):
        if os.path.isfile(ruta):
            return ruta
    return ""


def _tiene_playwright(python: str) -> bool:
    try:
        r = subprocess.run([python, "-c", "import playwright"],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def localizar_python() -> str:
    """El interprete que corre supervisor.py: tiene que ser uno con
    Playwright instalado. Ojo, no vale cualquiera: en este PC hay tres
    (3.10, 3.12 y 3.13) y Playwright solo esta en el 3.12, que es justo el
    que NO es el de 'py' por defecto.
    """
    # Si el paquete trae su propio Python, ese manda y se corta aca --
    # sin preguntar por Playwright. Es a proposito: en el paquete liviano
    # el embebido NO tiene Playwright, y preguntar hacia que la busqueda
    # lo descartara y se fuera a buscar un Python del sistema, que puede
    # ser cualquier cosa (otra version, otro juego de dependencias) o no
    # existir. Que falte el respaldo de navegador ya lo dice preflight.py;
    # no es motivo para salirse de la instalacion.
    propio = python_embarcado()
    if propio:
        return propio

    candidatos = [sys.executable]
    for nombre in ("python", "python3"):
        hallado = shutil.which(nombre)
        if hallado:
            candidatos.append(hallado)
    candidatos += [r"C:\Python312\python.exe", r"C:\Python311\python.exe",
                   r"C:\Python313\python.exe"]
    for python in candidatos:
        if python and os.path.isfile(python) and _tiene_playwright(python):
            return python
    return sys.executable


def _sin_consola(python: str) -> str:
    """pythonw.exe al lado de python.exe: igual pero sin ventana negra.

    Los hijos se lanzan con CREATE_NO_WINDOW, pero pythonw evita tambien el
    parpadeo de la consola en el arranque.
    """
    # Sobre el basename y sin mirar mayusculas: shutil.which() devuelve lo
    # que diga el PATHEXT del sistema y en esta maquina da "python.EXE",
    # con lo que un replace("python.exe", ...) no cambiaba nada y se
    # seguia usando el exe con consola.
    carpeta, nombre = os.path.split(python)
    if nombre.lower() == "python.exe":
        alterno = os.path.join(carpeta, "pythonw.exe")
        if os.path.isfile(alterno):
            return alterno
    return python


# -- la configuracion ------------------------------------------------------

class Ajustes:
    """config.json, con sus rutas derivadas ya resueltas."""

    def __init__(self, datos=None):
        self.datos = dict(DEFECTOS)
        if datos:
            self.datos.update({k: v for k, v in datos.items() if k in DEFECTOS})

    # -- acceso ------------------------------------------------------------

    def __getitem__(self, clave):
        return self.datos[clave]

    def __setitem__(self, clave, valor):
        if clave not in DEFECTOS:
            raise KeyError(clave)
        self.datos[clave] = valor

    # -- rutas del nucleo (motor de captura copiado) ------------------------

    @property
    def dir_estado(self) -> str:
        return DIR_ESTADO

    @property
    def estado_detector(self) -> str:
        return ESTADO_DETECTOR

    @property
    def estado_supervisor(self) -> str:
        return ESTADO_SUPERVISOR

    @property
    def estado_directo(self) -> str:
        return ESTADO_DIRECTO

    @property
    def estado_siguiendo(self) -> str:
        return ESTADO_SIGUIENDO

    @property
    def scripts(self) -> str:
        return os.path.join(DIR_NUCLEO, "scripts")

    @property
    def grabar_txt(self) -> str:
        return os.path.join(self.scripts, "grabar.txt")

    @property
    def activos_txt(self) -> str:
        return os.path.join(self.scripts, "activos.txt")

    @property
    def dir_datos(self) -> str:
        return DIR_DATOS

    @property
    def streams(self) -> str:
        return os.path.join(self.dir_datos, "streams")

    @property
    def grabaciones(self) -> str:
        return os.path.join(self.dir_datos, "grabaciones")

    @property
    def ffmpeg(self) -> str:
        return os.path.join(self.scripts, "ffmpeg.exe")

    @property
    def perfil(self) -> str:
        return os.path.join(DIR_NUCLEO, "perfil")

    @property
    def detector_log(self) -> str:
        # REGISTRO en detectar_m3u8.py se calcula siempre como
        # os.path.join(BASE, "detector.log"): sigue a M3U8_BASE.
        return os.path.join(self.dir_datos, "detector.log")

    @property
    def supervisor_log(self) -> str:
        return os.path.join(DIR_NUCLEO, "supervisor.log")

    @property
    def detector_py(self) -> str:
        return os.path.join(self.scripts, "detectar_m3u8.py")

    @property
    def supervisor_py(self) -> str:
        return os.path.join(self.scripts, "supervisor.py")

    @property
    def python_sin_consola(self) -> str:
        return _sin_consola(self.datos["python"])

    # -- persistencia ------------------------------------------------------

    def guardar(self) -> None:
        os.makedirs(RAIZ, exist_ok=True)
        tmp = CONFIG + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, CONFIG)      # atomico: nunca se lee a medias

    def completar(self) -> bool:
        """Rellena lo que falte buscandolo. True si algo cambio."""
        cambio = False
        if not os.path.isfile(self.datos["mitmdump"]):
            self.datos["mitmdump"] = localizar_mitmdump()
            cambio = True
        if not os.path.isfile(self.datos["python"]):
            self.datos["python"] = localizar_python()
            cambio = True
        return cambio


def cargar(completar: bool = True) -> Ajustes:
    """Lee config.json; si no existe o esta incompleto, lo deja listo."""
    datos = {}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        pass                          # primera vez, o archivo roto: defectos
    # Antes que nada: si el paquete trae navegadores propios, dejarlo
    # dicho en el entorno de este proceso. setdefault y no asignacion --
    # un valor puesto a mano afuera manda, mismo criterio que M3U8_BASE
    # en motor.py.
    navegadores = navegadores_embarcados()
    if navegadores:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", navegadores)

    a = Ajustes(datos)
    if completar and a.completar():
        try:
            a.guardar()
        except OSError:
            pass                      # sin disco se sigue con lo de memoria
    for d in (DIR_ESTADO, DIR_REGISTRO, DIR_DATOS, os.path.join(DIR_NUCLEO, "scripts")):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return a
