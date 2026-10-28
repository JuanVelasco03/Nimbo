"""Arma la carpeta que el instalador va a copiar: Nimbo con todo adentro.

    python empaquetado\\construir.py --todo            (el comando normal: cierra el codigo)
    python empaquetado\\construir.py --mitmproxy "C:\\Program Files\\mitmproxy\\bin"
    python empaquetado\\construir.py --completo        (con respaldo de navegador)
    python empaquetado\\construir.py --descargar --sin-compilar   (paquete abierto, para depurar)

El resultado queda en dist\\Nimbo\\ y es **autocontenido**: se puede copiar a
una maquina sin Python, sin mitmproxy y sin ffmpeg y Nimbo arranca igual.
Eso es lo que hace `ajustes.python_embarcado()` /
`ajustes.mitmdump_embarcado()`: si runtime\\ existe gana sobre cualquier cosa
instalada en el sistema.

Lo que arma:

    dist\\Nimbo\\
      arrancar.bat              (busca runtime\\python primero)
      nimbo\\                   el codigo de la interfaz
      nucleo\\scripts\\         el motor de captura + ffmpeg.exe + wireproxy.exe
      runtime\\python\\         CPython embeddable + websockets/pystray/Pillow
      runtime\\mitmproxy\\      el zip standalone de mitmproxy, copiado tal cual
      runtime\\navegadores\\    solo con --completo (Chromium de Playwright)
      paquete.json             que se armo, cuando y con que

Ver specs\\distribucion_instalador.md secciones 2 a 4. Lo que NO entra esta
en la S2.2 y aca se cumple por lista blanca: se copia solo lo que esta
enumerado en _ARBOL, nunca un arbol entero con exclusiones -- un .conf de
WireGuard o un sesion.json que se filtre al paquete es una credencial
regalada, y una lista negra olvidada es como se filtran.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)

# Lo mismo que requirements.txt, pero resuelto para el Python embebido: el
# embeddable no trae pip, asi que las ruedas las baja el pip de la maquina
# que construye (ver _poner_dependencias).
DEPENDENCIAS = ["websockets", "pystray", "Pillow"]

# La version del embeddable. Tiene que ser 3.12 o mas: nada del codigo usa
# sintaxis mas nueva, pero el supervisor necesita una que tenga rueda de
# Playwright.
VERSION_PYTHON = "3.12.8"
URL_EMBEDDABLE = ("https://www.python.org/ftp/python/%s/"
                  "python-%s-embed-amd64.zip")

# -- lista blanca de lo que se copia del repo ------------------------------
#
# (origen relativo a la raiz, destino relativo al paquete, filtro)
# El filtro recibe el nombre del archivo y devuelve si entra.

def _py(nombre):
    return nombre.endswith(".py")


def _web(nombre):
    return nombre.endswith((".html", ".css", ".js", ".ico", ".png", ".svg",
                            ".woff2"))


def _nucleo(nombre):
    # Los .py del motor, la plantilla documentada de grabar.txt y los dos
    # binarios. Nada de .conf (claves privadas de WireGuard), nada de
    # grabar.txt ni activos.txt (datos de usuario / operativos).
    return (nombre.endswith(".py")
            or nombre in ("grabar.template.txt", "ffmpeg.exe",
                          "wireproxy.exe"))


_ARBOL = [
    ("nimbo", "nimbo", _py),
    ("nimbo/web", "nimbo/web", _web),
    ("nimbo/web/js", "nimbo/web/js", _web),
    ("nucleo/scripts", "nucleo/scripts", _nucleo),
]

# Con --compilado, los .py de nimbo\ no van: estan adentro del binario.
# Estos tres son la excepcion y tienen que seguir en disco igual --
# cualquier otra cosa que los necesite los carga por ruta, no por import
# (ver specs\distribucion_instalador.md S5.1):
#
#   puente.py              lo carga mitmdump con -s
#   lanzador_supervisor.py lo arranca motor.py con el Python embebido
#                          (Playwright necesita su driver de Node; meter
#                          eso en el binario es pelearse con rutas para
#                          nada, y supervisor.py queda abierto igual)
#   __init__.py            inofensivo, y evita sorpresas si algo trata a
#                          nimbo\ como paquete
_PY_EN_DISCO_COMPILADO = ("puente.py", "lanzador_supervisor.py",
                          "__init__.py")

# Lo mismo para el nucleo: grabador_directo.py queda adentro del binario
# (lo arranca --modo directo), asi que no hace falta en disco. mouflon.py
# SI, porque detectar_m3u8.py hace `import mouflon` al correr bajo el
# interprete de mitmdump, que no ve nada de lo compilado.
_NUCLEO_FUERA_COMPILADO = ("grabador_directo.py",)

# Valor de --compilado cuando se pide el flag pelado (o viene de --todo):
# "compila vos mismo con Nuitka antes de armar el paquete", en vez de
# "usa este app.dist\\ que ya esta hecho".
COMPILAR_AHORA = "<nuitka>"

# Archivos sueltos de la raiz.
_SUELTOS = ["arrancar.bat", "LEEME.md", "requirements.txt"]

# Si alguno de estos llega al paquete, el build se aborta. Es la red de
# seguridad de la lista blanca: si un filtro se afloja por error, esto lo
# caza antes de que el .exe salga de la maquina.
_PROHIBIDOS = (".conf", "sesion.json", "config.json", "instancia.json",
               "grabar.txt", "grabar.txt.bak", "activos.txt",
               "vpn_tunel.txt", "licencia.json", "licencia_privada.pem")


def _log(*a):
    print("[CONSTRUIR]", *a, flush=True)


def _copiar_arbol(origen, destino, filtro):
    """Copia los archivos de un nivel (no recursivo: _ARBOL enumera cada
    carpeta a proposito, para que agregar una subcarpeta al repo sea una
    decision explicita y no algo que se cuela)."""
    if not os.path.isdir(origen):
        _log("  OJO: no existe", origen)
        return 0
    os.makedirs(destino, exist_ok=True)
    n = 0
    for nombre in sorted(os.listdir(origen)):
        ruta = os.path.join(origen, nombre)
        if not os.path.isfile(ruta) or not filtro(nombre):
            continue
        shutil.copy2(ruta, os.path.join(destino, nombre))
        n += 1
    return n


def copiar_codigo(salida, compilado=False):
    total = 0
    for rel_origen, rel_destino, filtro in _ARBOL:
        origen = os.path.join(RAIZ, rel_origen.replace("/", os.sep))
        destino = os.path.join(salida, rel_destino.replace("/", os.sep))
        if compilado and rel_origen == "nimbo":
            filtro = lambda n: n in _PY_EN_DISCO_COMPILADO
        elif compilado and rel_origen == "nucleo/scripts":
            base = filtro
            filtro = lambda n: base(n) and n not in _NUCLEO_FUERA_COMPILADO
        n = _copiar_arbol(origen, destino, filtro)
        _log("  %-22s %d archivos" % (rel_origen, n))
        total += n
    for nombre in _SUELTOS:
        ruta = os.path.join(RAIZ, nombre)
        if os.path.isfile(ruta):
            shutil.copy2(ruta, os.path.join(salida, nombre))
            total += 1
    # Las carpetas que Nimbo espera poder escribir. Las crea cargar() solo,
    # pero dejarlas hechas evita que el primer arranque falle si el
    # instalador puso todo de solo lectura.
    for d in ("estado", "datos", "registro"):
        os.makedirs(os.path.join(salida, d), exist_ok=True)
    return total


# -- el Python embebido ----------------------------------------------------

def _bajar_embeddable(version, cache):
    import urllib.request
    url = URL_EMBEDDABLE % (version, version)
    destino = os.path.join(cache, os.path.basename(url))
    if os.path.isfile(destino):
        _log("  zip ya descargado:", destino)
        return destino
    os.makedirs(cache, exist_ok=True)
    _log("  bajando", url)
    with urllib.request.urlopen(url, timeout=180) as r, \
            open(destino + ".part", "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(destino + ".part", destino)
    return destino


def _habilitar_site(dir_python):
    """Arregla el ._pth del embeddable: site-packages y nimbo\\ en el path.

    Dos cosas, las dos imprescindibles:

    1. El embeddable viene con site-packages apagado (su python3xx._pth no
       incluye el directorio y trae 'import site' comentado), asi que las
       dependencias que ponemos al lado no se verian.
    2. **Tener un ._pth apaga el agregado automatico de la carpeta del
       script a sys.path.** Y los modulos de nimbo\\ se importan sin
       paquete (`import ajustes`, ver CLAUDE.md "Imports planos"),
       justamente porque app.py se corre como script y su carpeta queda
       en sys.path. Con el embeddable eso deja de pasar y Nimbo no
       arranca: ModuleNotFoundError en el primer import. De ahi la linea
       ..\\..\\nimbo -- las rutas del ._pth son relativas a la carpeta del
       propio ._pth, o sea runtime\\python\\.

       (Encontrado corriendo el paquete, no leyendo codigo: el build
       quedaba perfecto y el .exe no abria.)
    """
    pths = [n for n in os.listdir(dir_python) if n.endswith("._pth")]
    if not pths:
        raise SystemExit("el zip embeddable no trae ._pth -- "
                         "revisa que sea el correcto")
    ruta = os.path.join(dir_python, pths[0])
    with open(ruta, encoding="utf-8") as f:
        lineas = [l.rstrip("\n") for l in f]
    for entrada in ("Lib\\site-packages", "..\\..\\nimbo"):
        if entrada not in lineas:
            lineas.append(entrada)
    lineas = ["import site" if l.strip() == "#import site" else l
              for l in lineas]
    if "import site" not in [l.strip() for l in lineas]:
        lineas.append("import site")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    _log("  %s: site-packages + nimbo\\ en el path" % pths[0])


def _poner_dependencias(dir_python, version, completo):
    """pip de ESTA maquina bajando ruedas para el Python embebido.

    --target + --platform + --python-version fuerzan ruedas binarias que
    correspondan al interprete embebido, no al que corre este script
    (Pillow trae .pyd compilado: una rueda de otra version no carga).
    """
    destino = os.path.join(dir_python, "Lib", "site-packages")
    os.makedirs(destino, exist_ok=True)
    paquetes = list(DEPENDENCIAS)
    if completo:
        paquetes.append("playwright")
    corto = ".".join(version.split(".")[:2])
    cmd = [sys.executable, "-m", "pip", "install",
           "--target", destino,
           "--only-binary=:all:",
           "--platform", "win_amd64",
           "--python-version", corto,
           "--upgrade"] + paquetes
    _log("  pip install --target (%s)" % ", ".join(paquetes))
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("pip fallo armando el runtime")
    # pip --target deja los dist-info y los .exe de consola; los scripts no
    # sirven (apuntan al python de esta maquina) y solo ocupan.
    bin_basura = os.path.join(destino, "bin")
    if os.path.isdir(bin_basura):
        shutil.rmtree(bin_basura, ignore_errors=True)


def armar_python(salida, zip_python, version, descargar, completo):
    dir_python = os.path.join(salida, "runtime", "python")
    if not zip_python:
        if descargar:
            zip_python = _bajar_embeddable(version, os.path.join(AQUI, "cache"))
        else:
            raise SystemExit(
                "falta el Python embeddable. Pasa --python-zip <ruta al "
                "python-%s-embed-amd64.zip> o --descargar para bajarlo de "
                "python.org." % version)
    os.makedirs(dir_python, exist_ok=True)
    _log("  descomprimiendo", os.path.basename(zip_python))
    with zipfile.ZipFile(zip_python) as z:
        z.extractall(dir_python)
    # El embeddable trae la stdlib en un zip (python312.zip) y NO trae
    # pythonw.exe en versiones viejas; si falta, arrancar.bat y
    # _sin_consola caen a python.exe solos, pero conviene avisar.
    if not os.path.isfile(os.path.join(dir_python, "pythonw.exe")):
        _log("  OJO: el zip no trae pythonw.exe -- va a parpadear una consola")
    _habilitar_site(dir_python)
    _poner_dependencias(dir_python, version, completo)
    return dir_python


# -- mitmproxy -------------------------------------------------------------

def copiar_mitmproxy(salida, origen):
    """El zip standalone de mitmproxy, copiado tal cual.

    Trae su propio runtime en _internal\\ (layout de PyInstaller, Python
    3.14 adentro), asi que no hay nada que resolver: es autocontenido.
    Ver specs\\distribucion_instalador.md S2.1.
    """
    exe = os.path.join(origen, "mitmdump.exe")
    if not os.path.isfile(exe):
        raise SystemExit("no hay mitmdump.exe en %s -- pasa --mitmproxy con "
                         "la carpeta bin\\ de mitmproxy" % origen)
    destino = os.path.join(salida, "runtime", "mitmproxy")
    if os.path.isdir(destino):
        shutil.rmtree(destino)
    # Solo mitmdump: mitmproxy.exe (la TUI) y mitmweb.exe no se usan y
    # comparten el mismo _internal\, asi que no ahorran nada sacarlos de
    # la copia... pero tampoco cuesta no copiarlos.
    os.makedirs(destino, exist_ok=True)
    shutil.copy2(exe, os.path.join(destino, "mitmdump.exe"))
    interno = os.path.join(origen, "_internal")
    if os.path.isdir(interno):
        shutil.copytree(interno, os.path.join(destino, "_internal"))
    else:
        # Build viejo "onefile": el exe se basta solo.
        _log("  (sin _internal: mitmdump parece ser onefile)")
    return destino


# -- navegadores de Playwright --------------------------------------------

def copiar_navegadores(salida, dir_python, solo_shell=False):
    """Baja el Chromium de Playwright DENTRO del paquete.

    Se corre el playwright del runtime embebido con
    PLAYWRIGHT_BROWSERS_PATH apuntando a runtime\\navegadores, que es
    exactamente la variable que ajustes.cargar() va a poner en el entorno
    del lado del usuario (ver ajustes.navegadores_embarcados).

    Por defecto baja **dos** builds, y es lo que mas pesa de todo el
    paquete (medido: 437 MB el Chromium completo + 278 MB el headless
    shell). Playwright usa el shell cuando el supervisor corre headless
    --el caso normal-- y el completo cuando el usuario prende
    "ver navegador" en Ajustes. `solo_shell` saltea el completo y ahorra
    esos 437 MB, a cambio de que prender "ver navegador" deje de
    funcionar: usarlo solo si se sabe que nadie lo va a tocar.
    """
    destino = os.path.join(salida, "runtime", "navegadores")
    os.makedirs(destino, exist_ok=True)
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = destino
    exe = os.path.join(dir_python, "python.exe")
    cmd = [exe, "-m", "playwright", "install", "chromium"]
    if solo_shell:
        cmd.append("--only-shell")
    _log("  playwright install chromium%s -> runtime\\navegadores"
         % (" --only-shell" if solo_shell else ""))
    r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("playwright install fallo")
    return destino


# -- licencia (S6) ---------------------------------------------------------

def copiar_compilado(salida, origen):
    """Mete el binario de Nuitka en el paquete, DENTRO de nimbo\\.

    `origen` es el app.dist\\ que deja empaquetado\\compilar.py.

    **Que vaya adentro de nimbo\\ es load-bearing, no estetica.** Nuitka
    standalone le deja a cada modulo compilado un `__file__` que apunta a
    donde estaria el .py dentro de la carpeta del binario, y medio Nimbo
    calcula rutas desde ahi:

        ajustes._AQUI    -> DIR_WEB, PUENTE, LANZADOR_SUPERVISOR, RAIZ
        motor._AQUI      -> LANZADOR_DIRECTO
        licencia._AQUI   -> licencia_publica.json
        activacion       -> PAGINA, via DIR_WEB

    Con el exe en bin\\, todo eso apuntaria a <paquete>\\bin\\... y no
    encontraria ni la interfaz. Poniendolo en nimbo\\, `_AQUI` vale
    <paquete>\\nimbo igual que sin compilar, `RAIZ` sigue siendo el
    paquete, y no hay que tocar una linea de codigo.

    El Python embebido sigue haciendo falta: lo usa el supervisor
    (lanzador_supervisor.py) y la union (detectar_m3u8.py --unir).
    """
    exe = os.path.join(origen, "Nimbo.exe")
    if not os.path.isfile(exe):
        raise SystemExit("no hay Nimbo.exe en %s -- corre primero "
                         "empaquetado\\compilar.py" % origen)
    destino = os.path.join(salida, "nimbo")
    os.makedirs(destino, exist_ok=True)
    n = 0
    for base, _, archivos in os.walk(origen):
        rel = os.path.relpath(base, origen)
        aca = destino if rel == "." else os.path.join(destino, rel)
        os.makedirs(aca, exist_ok=True)
        for nombre in archivos:
            shutil.copy2(os.path.join(base, nombre),
                         os.path.join(aca, nombre))
            n += 1
    _log("  binario compilado en nimbo\\Nimbo.exe (%d archivos)" % n)
    return destino


def compilar_con_nuitka():
    """Corre empaquetado\\compilar.py y devuelve el app.dist\\ que dejo.

    Esto es lo que hace que `--todo` salga cerrado. La maquinaria de
    compilar existia desde la S5 de specs\\02-distribucion_instalador.md y
    no la usaba nadie, porque el camino comodo (`--todo`) no pasaba por
    ella: dos comandos encadenados a mano es exactamente como se saltea el
    segundo, y cada build apurado salia con los 24 .py de nimbo\\ en claro.

    Se importa en vez de lanzarse como subproceso para que un fallo de
    Nuitka corte este build con su propio SystemExit, sin tener que
    reinterpretar un codigo de salida.
    """
    if AQUI not in sys.path:
        sys.path.insert(0, AQUI)
    import compilar as compilar_mod
    salida = os.path.join(RAIZ, "dist", "compilado")
    compilar_mod.compilar(salida, consola=False)
    return os.path.join(salida, "app.dist")


# -- frontend minificado --------------------------------------------------

def minificar_js(salida):
    """Minifica en el lugar los modulos ES de nimbo\\web\\js\\ del paquete.

    Sin --bundle a proposito: los modulos se importan por nombre desde
    index.html y entre si (main.js -> vista_*.js), asi que tiene que
    seguir habiendo un archivo por archivo con su nombre. Bundlear
    obligaria a tocar los <script type="module"> y no agrega nada.

    Si no hay npx/esbuild **avisa y sigue**: el build no puede depender de
    Node. runtime\\ no lo lleva y app.css ya viene compilado en el repo
    justo por eso (CLAUDE.md, "Servidor y frontend"). Un build sin Node
    tiene que seguir saliendo.

    Devuelve si de verdad minifico algo (va a paquete.json).
    """
    dir_js = os.path.join(salida, "nimbo", "web", "js")
    if not os.path.isdir(dir_js):
        _log("  OJO: no hay nimbo\\web\\js\\ en el paquete")
        return False
    archivos = [os.path.join(dir_js, n) for n in sorted(os.listdir(dir_js))
                if n.endswith(".js")]
    if not archivos:
        _log("  OJO: no hay .js que minificar")
        return False
    npx = shutil.which("npx")
    if not npx:
        _log("  OJO: no hay npx en el PATH -- los .js van sin minificar")
        return False
    antes = sum(os.path.getsize(ruta) for ruta in archivos)
    # --no-install: que no baje esbuild de la nada en medio de un build.
    # Si no esta instalado (npm install), esto falla y seguimos sin
    # minificar, que es el comportamiento querido.
    cmd = ([npx, "--no-install", "esbuild"] + archivos
           + ["--minify", "--outdir=" + dir_js, "--allow-overwrite"])
    r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        for linea in ((r.stderr or "") + (r.stdout or "")).splitlines()[:6]:
            if linea.strip():
                _log("   ", linea.strip())
        _log("  OJO: esbuild no corrio -- los .js van sin minificar "
             "(npm install para tenerlo)")
        return False
    despues = sum(os.path.getsize(ruta) for ruta in archivos)
    _log("  %d .js minificados: %.0f KB -> %.0f KB"
         % (len(archivos), antes / 1024, despues / 1024))
    return True


def poner_licencia(salida, clave_publica, url, anon):
    """Inyecta la clave publica y a donde validar, en el paquete.

    Sin este archivo el paquete no pide ninguna clave: licencia.py se
    apaga sola cuando no hay clave publica (ver licencia.activa()). Asi la
    copia de desarrollo nunca queda bloqueada por este mecanismo, y por
    eso el archivo esta en .gitignore.

    La anon key de Supabase va adentro del paquete y eso esta bien: es
    publica por diseno y la tabla de licencias tiene RLS sin policies, asi
    que no sirve para leer nada (ver empaquetado\\licencia\\esquema.sql).
    La **privada** de la firma no va: vive solo en la Edge Function.
    """
    datos = {"clave_publica": clave_publica}
    if url:
        datos["url"] = url
    if anon:
        datos["anon"] = anon
    ruta = os.path.join(salida, "nimbo", "licencia_publica.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2)
        f.write("\n")
    if not url:
        _log("  OJO: licencia sin --url-licencia: el paquete va a pedir la "
             "clave y no va a poder validarla (solo sirve con "
             "claves.py --instalar)")
    _log("  licencia activada en el paquete")


# -- control final ---------------------------------------------------------

# -- el instalador (Inno Setup) -------------------------------------------

def localizar_iscc() -> str:
    """ISCC.exe, el compilador de linea de comandos de Inno Setup.

    El instalador de Inno es **per-user por defecto** y lo deja en
    %LOCALAPPDATA%\\Programs\\Inno Setup 6\\, no en Program Files, y no lo
    pone en el PATH. Mirar solo Program Files decia "no esta instalado"
    con Inno 6.7.3 instalado y funcionando, asi que se prueban los tres
    lugares y, si no, el registro de desinstalacion -- que es de donde
    salio la ruta real.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    candidatos = []
    if local:
        candidatos.append(os.path.join(local, "Programs", "Inno Setup 6",
                                       "ISCC.exe"))
    candidatos += [r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                   r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    hallado = shutil.which("ISCC")
    if hallado:
        candidatos.append(hallado)
    for ruta in candidatos:
        if ruta and os.path.isfile(ruta):
            return ruta

    # Ultimo recurso: el registro. Inno escribe su InstallLocation ahi.
    try:
        import winreg
    except ImportError:
        return ""
    ramas = ((winreg.HKEY_CURRENT_USER,
              r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE,
              r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE,
              r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
    for raiz, sub in ramas:
        try:
            with winreg.OpenKey(raiz, sub) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as hijo:
                            nombre = winreg.QueryValueEx(hijo, "DisplayName")[0]
                            if "inno setup" not in str(nombre).lower():
                                continue
                            donde = winreg.QueryValueEx(hijo,
                                                        "InstallLocation")[0]
                            exe = os.path.join(str(donde), "ISCC.exe")
                            if os.path.isfile(exe):
                                return exe
                    except OSError:
                        continue
        except OSError:
            continue
    return ""


def compilar_instalador(salida):
    """Corre ISCC sobre nimbo.iss y devuelve la ruta del .exe.

    Tarda. Medido en la instalacion real: 3m27s para un paquete completo
    de 1,1 GB, que quedo en un .exe de 317 MB (Chromium comprime muy bien
    con el lzma2/max del .iss).
    """
    iscc = localizar_iscc()
    if not iscc:
        raise SystemExit(
            "no se encontro ISCC.exe. Instala Inno Setup 6 desde\n"
            "  https://jrsoftware.org/isdl.php\n"
            "y volve a correr esto (o compila a mano:\n"
            "  \"%LOCALAPPDATA%\\Programs\\Inno Setup 6\\ISCC.exe\" "
            "empaquetado\\nimbo.iss)")
    iss = os.path.join(AQUI, "nimbo.iss")
    # /DOrigen: que empaquete LA carpeta que acabamos de armar, no la que
    # el .iss tiene por defecto. Importa cuando se usa --salida para
    # probar un build sin pisar el bueno.
    cmd = [iscc, "/DOrigen=" + salida, iss]
    nombre = "NimboSetup"
    if os.path.normcase(salida) != os.path.normcase(
            os.path.join(RAIZ, "dist", "Nimbo")):
        # Y que no se llame igual que el instalador de verdad.
        nombre = "NimboSetup-" + os.path.basename(salida)
        cmd.insert(1, "/F" + nombre)
    _log("  %s" % iscc)
    _log("  compilando %s (paciencia: son varios minutos)" % os.path.basename(iss))
    arranque = time.time()
    r = subprocess.run(cmd, cwd=RAIZ, capture_output=True,
                       text=True, errors="replace")
    salida_iscc = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        # Las lineas utiles son las de "Error"/"Warning"; el resto es un
        # "Compressing: <archivo>" por cada archivo del paquete.
        for linea in salida_iscc.splitlines():
            if ("error" in linea.lower() or "warning" in linea.lower()
                    or "aborted" in linea.lower()):
                print("  " + linea.strip())
        raise SystemExit("ISCC fallo (codigo %d)" % r.returncode)
    exe = os.path.join(RAIZ, "dist", nombre + ".exe")
    tam = os.path.getsize(exe) / 1024**2 if os.path.isfile(exe) else 0
    _log("  listo en %.0f s -- %.0f MB" % (time.time() - arranque, tam))
    return exe


def limpiar_pycache(salida):
    """Saca los __pycache__\\ del paquete.

    _copiar_arbol no los copia (solo copia archivos de un nivel), pero
    aparecen igual en cuanto algo corre Python adentro de dist\\ -- por
    ejemplo una verificacion a mano antes de empaquetar. Son inofensivos
    (bytecode de los .py que viajan al lado), pero no tienen por que
    estar, y uno viejo de otra version de Python es ruido al depurar.
    """
    n = 0
    for base, carpetas, _ in os.walk(salida):
        for d in list(carpetas):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(base, d), ignore_errors=True)
                carpetas.remove(d)
                n += 1
    if n:
        _log("  %d __pycache__\\ sacados" % n)


def auditar(salida, compilado=False):
    """Que no se haya colado nada de _PROHIBIDOS. Aborta si encuentra algo.

    Con `compilado`, audita ademas que el paquete este **de verdad**
    cerrado. Mismo razonamiento que la lista blanca de copiar_codigo: un
    --compilado que silenciosamente no se aplico es el mismo tipo de fallo
    que un .conf que se cuela, y merece el mismo SystemExit. Es una red,
    no el mecanismo: lo que filtra los .py es copiar_codigo.
    """
    malos = []
    for base, _, archivos in os.walk(salida):
        # runtime\ es codigo ajeno (CPython, mitmproxy, Chromium): trae
        # .conf de ejemplo y no es donde se filtrarian nuestras claves.
        if os.path.join(salida, "runtime") in base:
            continue
        for n in archivos:
            bajo = n.lower()
            if bajo in _PROHIBIDOS or bajo.endswith(".conf"):
                malos.append(os.path.relpath(os.path.join(base, n), salida))
    if malos:
        raise SystemExit("ABORTADO: el paquete incluye archivos que no se "
                         "distribuyen:\n  " + "\n  ".join(malos))
    _log("  auditoria: sin credenciales ni datos de usuario")

    if not compilado:
        return
    abiertos = []
    dir_nimbo = os.path.join(salida, "nimbo")
    # Solo el primer nivel: adentro de nimbo\ vive tambien el arbol que
    # dejo Nuitka (copiar_compilado), y ahi puede haber .py de terceros
    # que no son codigo nuestro en claro.
    if os.path.isdir(dir_nimbo):
        for n in sorted(os.listdir(dir_nimbo)):
            if (n.endswith(".py") and os.path.isfile(os.path.join(dir_nimbo, n))
                    and n not in _PY_EN_DISCO_COMPILADO):
                abiertos.append("nimbo\\" + n)
    if not os.path.isfile(os.path.join(dir_nimbo, "Nimbo.exe")):
        abiertos.append("falta nimbo\\Nimbo.exe "
                        "(copiar_compilado no corrio)")
    for n in _NUCLEO_FUERA_COMPILADO:
        if os.path.isfile(os.path.join(salida, "nucleo", "scripts", n)):
            abiertos.append("nucleo\\scripts\\" + n)
    if abiertos:
        raise SystemExit(
            "ABORTADO: se pidio un paquete compilado y quedo codigo "
            "abierto:\n  " + "\n  ".join(abiertos)
            + "\n\nSi alguno de esos .py tiene que estar en disco, sumalo "
              "a _PY_EN_DISCO_COMPILADO con el motivo.")
    _log("  auditoria: codigo cerrado (solo %s en disco)"
         % ", ".join(_PY_EN_DISCO_COMPILADO))


def _medir(ruta):
    total = 0
    for base, _, archivos in os.walk(ruta):
        for n in archivos:
            try:
                total += os.path.getsize(os.path.join(base, n))
            except OSError:
                pass
    return total


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", default=os.path.join(RAIZ, "dist", "Nimbo"))
    p.add_argument("--mitmproxy", default=r"C:\Program Files\mitmproxy\bin",
                   help="carpeta bin\\ del mitmproxy standalone")
    p.add_argument("--python-zip", default="",
                   help="python-X.Y.Z-embed-amd64.zip ya descargado")
    p.add_argument("--version-python", default=VERSION_PYTHON)
    p.add_argument("--descargar", action="store_true",
                   help="bajar el embeddable de python.org si no se pasa --python-zip")
    p.add_argument("--completo", action="store_true",
                   help="incluir Playwright y Chromium (el respaldo del "
                        "supervisor). Medido: el paquete pasa de 206 MB a "
                        "1,1 GB -- la rueda de playwright trae el driver de "
                        "Node (~130 MB) y se bajan dos builds de navegador")
    p.add_argument("--solo-shell", action="store_true",
                   help="con --completo, bajar solo el headless shell y no "
                        "el Chromium completo: ahorra 437 MB pero rompe la "
                        "opcion \"ver navegador\" de Ajustes")
    p.add_argument("--clave-publica", default="",
                   help="clave publica Ed25519 (base64url) para activar la "
                        "licencia en este paquete; sin esto el paquete no "
                        "pide clave")
    p.add_argument("--url-licencia", default="",
                   help="endpoint que valida la clave "
                        "(https://<proyecto>.supabase.co/functions/v1/validar)")
    p.add_argument("--anon-licencia", default="",
                   help="anon key del proyecto de Supabase (es publica)")
    p.add_argument("--compilado", nargs="?", const=COMPILAR_AHORA, default="",
                   help="cerrar el codigo: los .py de nimbo\\ NO van al "
                        "paquete (salvo puente.py y lanzador_supervisor.py, "
                        "que se cargan por ruta). Solo, compila con Nuitka "
                        "aca mismo; con una ruta, usa ese app.dist\\ ya "
                        "armado (para no recompilar cinco minutos mientras "
                        "se itera sobre el empaquetado)")
    p.add_argument("--sin-compilar", action="store_true",
                   help="armar el paquete ABIERTO a proposito (depurar el "
                        "paquete leyendo los .py). Gana sobre --todo; que "
                        "exista este escape es lo que permite que el default "
                        "sea cerrado")
    p.add_argument("--sin-minificar", action="store_true",
                   help="no pasar los .js por esbuild")
    p.add_argument("--instalador", action="store_true",
                   help="al terminar, compilar empaquetado\\nimbo.iss con "
                        "Inno Setup y dejar dist\\NimboSetup.exe")
    p.add_argument("--todo", action="store_true",
                   help="el comando de un solo paso: equivale a --descargar "
                        "--completo --licencia --compilado --instalador")
    p.add_argument("--licencia", action="store_true",
                   help="activar la licencia tomando los datos de "
                        "empaquetado\\licencia\\proyecto.local.json (lo que "
                        "quedo del despliegue); equivale a pasar "
                        "--clave-publica/--url-licencia/--anon-licencia a mano")
    args = p.parse_args(argv)

    if args.todo:
        # El comando de un solo paso. Son los cinco que se usan para
        # armar lo que se le manda a otra persona; cada uno sigue
        # existiendo suelto para armar variantes (p.ej. --completo
        # --solo-shell, o sin licencia para probar).
        args.descargar = args.completo = True
        args.licencia = args.instalador = True
        if not args.compilado:
            args.compilado = COMPILAR_AHORA
    if args.sin_compilar:
        # El escape explicito gana sobre --todo.
        args.compilado = ""

    # OJO con el orden: la clave publica de la licencia se resuelve ACA,
    # antes de Nuitka. Es la unica dependencia de orden entre esta spec y
    # la 07 (que mete la clave adentro del binario): si se compilara
    # primero, la clave no entraria.
    if args.licencia:
        ruta = os.path.join(AQUI, "licencia", "proyecto.local.json")
        try:
            with open(ruta, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            raise SystemExit(
                "--licencia necesita %s (lo escribe el despliegue del "
                "backend -- ver empaquetado\\licencia\\LEEME.md)" % ruta)
        args.clave_publica = args.clave_publica or cfg.get("clave_publica", "")
        args.url_licencia = args.url_licencia or cfg.get("url", "")
        args.anon_licencia = args.anon_licencia or cfg.get("anon", "")
        if not args.clave_publica:
            raise SystemExit("%s no trae clave_publica" % ruta)

    if args.compilado == COMPILAR_AHORA:
        _log("0/5 cerrando el codigo con Nuitka")
        args.compilado = compilar_con_nuitka()

    salida = os.path.abspath(args.salida)
    if os.path.isdir(salida):
        _log("limpiando", salida)
        shutil.rmtree(salida)
    os.makedirs(salida, exist_ok=True)

    _log("1/5 codigo de Nimbo")
    copiar_codigo(salida, compilado=bool(args.compilado))
    if args.compilado:
        copiar_compilado(salida, os.path.abspath(args.compilado))
    if args.sin_minificar:
        _log("  .js sin minificar (--sin-minificar)")
        js_minificado = False
    else:
        js_minificado = minificar_js(salida)

    _log("2/5 Python embebido")
    dir_python = armar_python(salida, args.python_zip, args.version_python,
                              args.descargar, args.completo)

    _log("3/5 mitmproxy")
    copiar_mitmproxy(salida, args.mitmproxy)

    if args.completo:
        _log("4/5 navegador de respaldo")
        copiar_navegadores(salida, dir_python, args.solo_shell)
    else:
        _log("4/5 navegador de respaldo: omitido (sin --completo)")

    if args.clave_publica:
        poner_licencia(salida, args.clave_publica, args.url_licencia,
                       args.anon_licencia)

    _log("5/5 auditoria")
    limpiar_pycache(salida)
    auditar(salida, compilado=bool(args.compilado))

    with open(os.path.join(salida, "paquete.json"), "w", encoding="utf-8") as f:
        json.dump({
            "armado": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "version_python": args.version_python,
            "completo": bool(args.completo),
            "licencia": bool(args.clave_publica),
            # Para contestar "este .exe que mande estaba cerrado?" sin
            # tener que abrirlo.
            "compilado": bool(args.compilado),
            "js_minificado": bool(js_minificado),
        }, f, indent=2)
        f.write("\n")

    _log("paquete listo: %s  (%.0f MB)" % (salida, _medir(salida) / 1024**2))

    if not args.instalador:
        _log("ahora: compila empaquetado\\nimbo.iss con Inno Setup, o volve "
             "a correr esto con --instalador")
        return 0

    _log("6/6 instalador")
    exe = compilar_instalador(salida)
    _log("")
    _log("TODO LISTO: %s" % exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
