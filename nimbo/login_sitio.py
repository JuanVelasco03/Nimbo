"""Iniciar sesion en la cuenta real sin pegar nada (SPEC 15): una ventana
de Edge normal, ruteada por el proxy de grabacion, donde el usuario se
loguea a mano -- y el pedido autenticado que el propio sitio dispara al
entrar a Favoritas pasa por adentro de Nimbo (ver nimbo\\puente.py,
metodo request()), que le guarda las cookies y el Authorization a
estado\\sesion_capturada.json.

Funciones sincronas, todas para el pool de servidor.py (ver
Aplicacion.a_hilo): nada de esto es async por si mismo.

No hay nada de Playwright ni CDP aca -- ver sesion.py, "Como se llego a
este diseno", y CLAUDE.md, "Detalles con historia". subprocess.Popen de
Edge, sin --remote-debugging-port y sin nadie manejandolo, es el
navegador normal del usuario con un proxy puesto: lo que Cloudflare
detecta es el protocolo de automatizacion, no la ventana.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

import ajustes as ajustes_mod
import preflight as preflight_mod
import sesion as sesion_mod

CREAR_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Tope de la ventana de captura (criterios de aceptacion): una captura
# abierta para siempre es un hook que siempre esta mirando, el permiso
# tiene que caducar solo.
SEGUNDOS_CAPTURA = 300.0

# SPEC 21: de donde sale la URL de "seguidas" que abre abrir_ventana()
# para cada sitio -- lo unico que distinguia a este modulo de un sitio en
# particular, junto con el campo "sitio" de los dos archivos de vida
# corta del handshake. chaturbate.com/followed-cams/ (medido anonimo,
# sin cuenta: redirige a /auth/login/?next=/followed-cams/) es la misma
# pagina que mide pruebas\sonda_login_chaturbate.py.
_URL_SEGUIDAS = {
    "stripchat": lambda a: a["sitio"].rstrip("/") + "/favorites",
    "chaturbate": lambda a: a["sitio_chaturbate"].rstrip("/") + "/followed-cams/",
}

# Cuanto esperar a que Edge termine de cerrar antes de tocar los archivos
# de su perfil -- las bases SQLite (History, Cookies) quedan bloqueadas
# mientras el proceso sigue vivo, y escribir o borrar encima de eso puede
# dejarlas corruptas.
TOPE_CIERRE_VENTANA = 10.0

# Lo que SI se borra del perfil al cerrar la ventana: historial de
# navegacion, autocompletado de sitios, cache de favicons y restauracion
# de pestanas -- lo que de verdad es "registro de que pagina se visito".
# Nunca Cookies, Network\\Cookies ni Local Storage: eso es lo que deja la
# sesion lista para la proxima vez sin loguearse de cero (ver Decisiones,
# specs\\15-login_sin_pegar.md). Los nombres son los que usa Chromium
# dentro de <perfil>\\Default\\ con --user-data-dir (sin
# --profile-directory, el perfil por defecto es siempre "Default").
_ARCHIVOS_HISTORIAL = (
    "History", "History-journal",
    "Visited Links",
    "Top Sites", "Top Sites-journal",
    "Favicons", "Favicons-journal",
    "Network Action Predictor", "Network Action Predictor-journal",
    "Shortcuts", "Shortcuts-journal",
)
_CARPETAS_HISTORIAL = ("Sessions",)


def precondiciones(ajustes, motor) -> dict:
    """-> {"ok": bool, "motivo": str|None}. Las tres cosas sin las que el
    boton no puede ni intentar: el addon vive en puente.py, que se carga
    junto al detector -- sin detector no hay proxy ni hook (ver CLAUDE.md,
    "Cuatro procesos")."""
    if not motor.detector_vivo():
        return {"ok": False, "motivo": "arranca la captura primero -- "
                "sin el detector corriendo no hay proxy ni hook de login"}
    if sys.platform == "win32" and not preflight_mod._ca_en_el_store():
        return {"ok": False, "motivo": "falta instalar el certificado de "
                "mitmproxy -- resolvelo desde el banner de puesta a punto"}
    if not ajustes_mod.localizar_edge():
        return {"ok": False, "motivo": "no se encontro Microsoft Edge"}
    return {"ok": True, "motivo": None}


def pedir(ajustes, sitio: str = "stripchat", segundos: float = SEGUNDOS_CAPTURA) -> dict:
    """Escribe captura_sesion.json: el permiso que puente.py necesita ver
    para capturar algo (ver Puente._permiso_vigente). -> {"pedida",
    "vence", "sitio"}. Sin "sitio" en el JSON (un archivo de antes de
    SPEC 21) puente.py asume "stripchat" -- ver REGLAS_LOGIN."""
    ahora = time.time()
    datos = {"pedida": ahora, "vence": ahora + segundos, "sitio": sitio}
    _escribir_atomico(ajustes.estado_captura_sesion, json.dumps(datos))
    return datos


def abrir_ventana(ajustes, sitio: str = "stripchat") -> subprocess.Popen:
    """Ventana de Edge normal (sin --remote-debugging-port), con perfil
    propio (ajustes.dir_perfil_login) y el proxy de grabacion puesto, en
    la pagina de seguidas del sitio pedido (_URL_SEGUIDAS) -- el sitio
    dispara el pedido autenticado (o, para chaturbate, simplemente carga
    la pagina con la cookie de sesion puesta) que puente.py necesita
    apenas el usuario termina de loguearse."""
    edge = ajustes_mod.localizar_edge()
    os.makedirs(ajustes.dir_perfil_login, exist_ok=True)
    url = _URL_SEGUIDAS.get(sitio, _URL_SEGUIDAS["stripchat"])(ajustes)
    return subprocess.Popen(
        [edge, "--proxy-server=" + ajustes["proxy"],
         "--user-data-dir=" + ajustes.dir_perfil_login,
         "--no-first-run", "--no-default-browser-check", "--new-window", url],
        creationflags=CREAR_SIN_VENTANA)


def _borrar_historial(ajustes) -> None:
    """Borra del perfil de login lo que es historial de navegacion --
    nunca las cookies ni el local storage. Mejor esfuerzo puro: si algun
    archivo no existe (version de Chromium distinta, perfil nuevo que
    nunca navego) no es un error."""
    base = os.path.join(ajustes.dir_perfil_login, "Default")
    for nombre in _ARCHIVOS_HISTORIAL:
        try:
            os.remove(os.path.join(base, nombre))
        except OSError:
            pass
    for nombre in _CARPETAS_HISTORIAL:
        shutil.rmtree(os.path.join(base, nombre), ignore_errors=True)


def cerrar_ventana(ajustes, proc: subprocess.Popen) -> None:
    """Termina la ventana de Edge y, recien cuando el proceso de verdad
    cerro (TOPE_CIERRE_VENTANA de margen: los archivos del perfil quedan
    bloqueados mientras vive), purga el historial de navegacion. Si el
    usuario ya cerro la ventana a mano, terminate()/wait() no hacen nada
    raro -- un proceso ya muerto sigue siendo valido para los dos."""
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=TOPE_CIERRE_VENTANA)
    except subprocess.TimeoutExpired:
        pass
    _borrar_historial(ajustes)


def recoger(ajustes, sitio: str = "stripchat") -> dict:
    """Lee y borra sesion_capturada.json, descifrando con DPAPI -- mismo
    prefijo y mismo criterio que sesion.Sesion.leer() (SPEC 09): si no
    descifra (otra maquina, DPAPI no disponible) se trata como si no
    hubiera candidato. -> dict con 'url'/'cookies'/'headers', o None.

    SPEC 21: si el candidato en disco es de OTRO sitio que el pedido (un
    archivo viejo que quedo de una vuelta anterior, o una carrera rara
    entre dos clicks) se descarta igual que si no hubiera nada -- nunca
    se verifica contra la cuenta equivocada. Sin "sitio" en el JSON (de
    antes de este spec) se asume "stripchat", igual que puente.py."""
    ruta = ajustes.estado_sesion_capturada
    try:
        with open(ruta, encoding="utf-8") as f:
            crudo = f.read()
    except OSError:
        return None
    try:
        os.remove(ruta)
    except OSError:
        pass

    if crudo.startswith(sesion_mod.PREFIJO_DPAPI):
        try:
            datos = json.loads(sesion_mod._desproteger(
                base64.b64decode(crudo[len(sesion_mod.PREFIJO_DPAPI):])))
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            return None
    else:
        try:
            datos = json.loads(crudo)
        except ValueError:
            return None
    if not isinstance(datos, dict):
        return None
    if (datos.get("sitio") or "stripchat") != sitio:
        return None
    return datos


def limpiar(ajustes) -> None:
    """Borra los dos archivos de vida corta -- se llama siempre al
    terminar la tarea de login (exito, cancelacion o tope de tiempo), en
    su finally."""
    for ruta in (ajustes.estado_captura_sesion, ajustes.estado_sesion_capturada):
        try:
            os.remove(ruta)
        except OSError:
            pass


def _escribir_atomico(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta)
