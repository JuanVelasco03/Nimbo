"""Licencia con vencimiento: clave entregada a mano, token firmado.

Implementa `specs\\distribucion_instalador.md` S6 y
`specs\\07-licencia_inviolable.md`.

**Exige licencia salvo en la copia de desarrollo.** `activa()` depende de
si el proceso esta compilado con Nuitka y no tiene la marca de desarrollo
(`nimbo\\licencia_clave.py`, que genera `empaquetado\\construir.py` antes
de compilar y no existe en el repo) -- no de que exista o no un archivo de
datos al lado del binario. La copia de desarrollo (`python nimbo\\app.py`)
nunca esta compilada, asi que `activa()` es False y nunca pide clave.
`nimbo\\licencia_publica.json` solo trae `url` y `anon` (adonde validar),
nunca la clave publica: borrarlo deja a Nimbo sin a donde validar, no sin
licencia.

Como funciona:

1. El usuario ingresa la clave una vez (pantalla de activacion).
2. Nimbo se la manda al backend junto con el id de la maquina y recibe un
   **token firmado con Ed25519** cuyo payload lleva `exp`.
3. De ahi en mas alcanza con verificar la firma local (`firma.verificar`):
   sin red, el token vigente sirve igual. Eso es la **gracia offline**, y
   no es un lujo -- si el chequeo exigiera red en cada arranque, un corte
   de internet dejaria la noche entera sin grabar, justo lo que
   `Motor._bucle_vigilancia` existe para evitar.
4. Cuando le queda menos de la mitad de vida, pide uno nuevo.

Lo que esto NO es: la verificacion corre en la maquina del usuario, asi
que un parche al binario la saltea. Firmar el token (en vez de creerle a
un `{"ok": true}` que se falsifica con un `hosts`) sube la vara de
"interceptar un HTTP" a "encontrar y neutralizar la verificacion adentro
del binario". Para el caso de uso alcanza; para un atacante, no.

La clave del usuario es una credencial: **nunca se loguea**, igual que
`sesion.json` (ver CLAUDE.md, "Detalles con historia").
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

import firma

_AQUI = os.path.dirname(os.path.abspath(__file__))

# Lo que inyecta el instalador: solo adonde validar, nunca la clave
# publica (ver licencia_clave.py mas abajo). En el repo trae datos de
# prueba o no existe -- cualquiera de los dos casos esta bien, porque ya
# no decide si hay licencia.
CONFIG_LICENCIA = os.path.join(_AQUI, "licencia_publica.json")

NOMBRE_ESTADO = "licencia.json"

# Cuanto se espera al backend. Corto: si no contesta, el token guardado
# cubre el arranque y se vuelve a intentar mas tarde.
ESPERA_RED = 20

# Se renueva cuando queda menos de esta parte de la vida del token.
FRACCION_RENOVAR = 0.5

# Margen contra un reloj de BIOS descargado o una correccion de NTP: una
# hora cubre cualquier deriva legitima y no alcanza para estirar un token
# de 7 dias (ver _bump de visto_max mas abajo).
HOLGURA_RELOJ = 3600.0


# -- la clave publica, compilada adentro del binario -----------------------
#
# empaquetado\construir.py genera este modulo ANTES de llamar a Nuitka
# (specs\07-licencia_inviolable.md), para que la clave entre al binario
# como codigo compilado y no como dato legible al lado. En el repo no
# existe -- va a .gitignore y a _PROHIBIDOS de construir.py -- y sin el
# la copia de desarrollo se comporta exactamente como hoy.
try:
    import licencia_clave
    _CLAVE_COMPILADA = licencia_clave.CLAVE_PUBLICA
    _DESARROLLO = bool(getattr(licencia_clave, "DESARROLLO", False))
except ImportError:
    _CLAVE_COMPILADA, _DESARROLLO = "", True


# Deteccion de "esto corre compilado" duplicada a proposito: ajustes.py
# importa este modulo para la puerta de licencia.exigir() (ver
# ajustes.cargar()), e importar ajustes desde aca armaria un ciclo. Mismo
# criterio que la duplicacion de MAX_EVENTOS entre modulos (CLAUDE.md,
# "Los dos puentes") -- tres lineas, no vale la pena romper el import.
def _compilado() -> bool:
    return "__compiled__" in globals() or getattr(sys, "frozen", False)


# -- el paquete trae licencia o no ----------------------------------------

_cfg = None


def _config() -> dict:
    global _cfg
    if _cfg is None:
        try:
            with open(CONFIG_LICENCIA, encoding="utf-8") as f:
                _cfg = json.load(f)
        except (OSError, ValueError):
            _cfg = {}
    return _cfg


def activa() -> bool:
    """Si este proceso exige licencia.

    Invertido a proposito: antes dependia de que existiera
    `nimbo\\licencia_publica.json` con una clave adentro -- un archivo que
    el usuario final podia borrar. Ahora depende de si el proceso esta
    compilado con Nuitka y no tiene la marca de desarrollo, que es lo que
    `empaquetado\\construir.py` decide al generar `licencia_clave.py`, no
    algo que sobreviva al lado del binario ya instalado.
    """
    return _compilado() and not _DESARROLLO


def _clave_publica() -> bytes:
    try:
        return _b64d(_CLAVE_COMPILADA)
    except (ValueError, TypeError):
        return b""


# -- base64url sin relleno (lo mismo que usa el backend) ------------------

def _b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


# -- id de la maquina -----------------------------------------------------

def _serial_volumen() -> str:
    """Numero de serie del volumen de %SystemDrive%, sin dependencias.

    Segundo eslabon de id_maquina(): sobrevive a lo que el primero no
    (una politica de registro rara que esconde MachineGuid), y a
    diferencia del tercero no sale del entorno del proceso. Mismo patron
    ctypes que ya usa motor.py para el watchdog del tunel
    (CreateToolhelp32Snapshot + GetExtendedTcpTable).
    """
    if sys.platform != "win32":
        return ""
    import ctypes
    raiz = os.environ.get("SystemDrive", "C:") + "\\"
    serial = ctypes.c_ulong(0)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(raiz), None, 0,
            ctypes.byref(serial), None, None, None, 0)
    except OSError:
        return ""
    if not ok or not serial.value:
        return ""
    return "vol:%08X" % serial.value


def id_maquina() -> str:
    """Identificador estable del equipo, legible sin admin, en cadena.

    Tres eslabones, del mas fuerte al mas debil
    (specs\\07-licencia_inviolable.md, A6):

    1. MachineGuid de HKLM\\SOFTWARE\\Microsoft\\Cryptography -- lo pone
       Windows al instalarse y no cambia con el hardware ni con las
       actualizaciones.
    2. El numero de serie del volumen del disco de sistema -- cubre la
       politica de registro rara que tumba el primero.
    3. COMPUTERNAME/USERNAME, prefijado "env:" para que se vea en el
       backend que es el eslabon debil: es el unico que sale del entorno
       del proceso, controlable por quien lo corre. Se queda como ultimo
       recurso -- sacarlo deja afuera una instalacion legitima, que es
       peor que el bypass que habilita (que ademas exige romper los dos
       eslabones anteriores a proposito).
    """
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography", 0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                valor, _ = winreg.QueryValueEx(k, "MachineGuid")
                if valor:
                    return str(valor)
        except OSError:
            pass
        serial = _serial_volumen()
        if serial:
            return serial
    return "env:%s/%s" % (os.environ.get("COMPUTERNAME", "?"),
                          os.environ.get("USERNAME", "?"))


# -- el archivo de estado -------------------------------------------------

def _ruta_estado(dir_estado) -> str:
    return os.path.join(dir_estado, NOMBRE_ESTADO)


def _leer(dir_estado) -> dict:
    try:
        with open(_ruta_estado(dir_estado), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar(dir_estado, datos) -> None:
    os.makedirs(dir_estado, exist_ok=True)
    ruta = _ruta_estado(dir_estado)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2)
        f.write("\n")
    os.replace(tmp, ruta)      # atomico, como todo lo que escribe Nimbo


def olvidar(dir_estado) -> None:
    try:
        os.remove(_ruta_estado(dir_estado))
    except OSError:
        pass


# -- el token -------------------------------------------------------------

def leer_token(token: str) -> dict:
    """Valida un token y devuelve su payload, o {} si no sirve.

    Formato: <payload-base64url>.<firma-base64url>. El payload es JSON
    con {clave, maquina, exp, emitido}. Se verifica la firma sobre los
    bytes **crudos** del payload, no sobre el JSON reserializado: dos
    serializaciones distintas del mismo dict no dan el mismo mensaje.
    """
    publica = _clave_publica()
    if not publica or not token or "." not in token:
        return {}
    try:
        parte_payload, parte_firma = token.split(".", 1)
        crudo = _b64d(parte_payload)
        rubrica = _b64d(parte_firma)
    except (ValueError, TypeError):
        return {}
    if not firma.verificar(publica, crudo, rubrica):
        return {}
    try:
        datos = json.loads(crudo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _vigente(payload: dict) -> bool:
    if not payload:
        return False
    if payload.get("maquina") and payload["maquina"] != id_maquina():
        return False
    try:
        return float(payload.get("exp") or 0) > time.time()
    except (TypeError, ValueError):
        return False


# -- hablar con el backend ------------------------------------------------

def _pedir_token(clave: str) -> dict:
    """{ok, token|error}. Nunca levanta."""
    cfg = _config()
    url = cfg.get("url")
    if not url:
        return {"ok": False, "error": "este paquete no trae a donde validar "
                                      "la clave"}
    cuerpo = json.dumps({"clave": clave, "maquina": id_maquina()}).encode()
    pedido = urllib.request.Request(url, data=cuerpo, method="POST")
    pedido.add_header("Content-Type", "application/json")
    if cfg.get("anon"):
        # Supabase exige la anon key en las Edge Functions. No es un
        # secreto: es publica por diseno y no da acceso a nada por si
        # sola (la tabla de licencias no tiene politicas de lectura).
        pedido.add_header("Authorization", "Bearer " + cfg["anon"])
        pedido.add_header("apikey", cfg["anon"])
    # Sin proxy: el de grabacion puede no estar levantado todavia (esto
    # corre antes que el motor) y no tiene nada que ver con esto.
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(pedido, timeout=ESPERA_RED) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            datos = json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "el servidor respondio %d" % e.code}
        return {"ok": False, "error": datos.get("error") or
                ("el servidor respondio %d" % e.code)}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "sin conexion con el servidor de "
                                      "licencias (%s)" % e.__class__.__name__}
    token = (datos or {}).get("token")
    if not token:
        return {"ok": False, "error": datos.get("error") or
                "el servidor no devolvio token"}
    if not _vigente(leer_token(token)):
        return {"ok": False, "error": "el token que devolvio el servidor no "
                                      "es valido para esta maquina"}
    return {"ok": True, "token": token}


# -- la interfaz del modulo -----------------------------------------------

def estado(dir_estado) -> dict:
    """Foto para mostrar: nunca incluye la clave."""
    if not activa():
        return {"activa": False, "ok": True}
    guardado = _leer(dir_estado)
    payload = leer_token(guardado.get("token") or "")
    ok = _vigente(payload)
    resultado = {"activa": True, "ok": ok}
    if payload.get("exp"):
        resultado["vence"] = payload["exp"]
        resultado["dias"] = max(0, (payload["exp"] - time.time()) / 86400.0)
    if not ok:
        resultado["motivo"] = ("todavia no se activo" if not guardado
                               else "la licencia vencio")
    return resultado


def revisar(dir_estado, forzar=False) -> dict:
    """La comprobacion de siempre: al arrancar y una vez por dia.

    Orden a proposito: primero se mira el token guardado y solo se sale a
    la red si hace falta. Si la red falla pero el token sigue vigente, el
    resultado es **ok** -- esa es la gracia offline.
    """
    if not activa():
        return {"ok": True, "activa": False}

    guardado = _leer(dir_estado)
    clave = guardado.get("clave") or ""
    visto_max = float(guardado.get("visto_max") or 0)
    payload = leer_token(guardado.get("token") or "")
    vigente = _vigente(payload)

    # Marca de agua de reloj (A4): si ahora es mas atras que lo que
    # cualquier corrida anterior ya vio -- con margen de HOLGURA_RELOJ
    # para NTP/huso -- no hay que creerle al token guardado. Es el
    # sintoma de congelar el reloj para que un token de 7 dias no venza
    # nunca. Con red esto se revalida y se arregla solo; sin red, el
    # resultado es quedarse sin licencia, que es exactamente la rama que
    # el bypass necesita evitar.
    reloj_atras = time.time() < visto_max - HOLGURA_RELOJ
    if reloj_atras:
        vigente = False

    def _terminar(resultado):
        # Solo crece, y se guarda solo si avanzo mas de un minuto --
        # evita una escritura de disco por cada llamada (revisar() corre
        # tambien en el latido periodico de servidor.py).
        nuevo = max(visto_max, time.time())
        if nuevo - visto_max > 60:
            guardado["visto_max"] = nuevo
            _guardar(dir_estado, guardado)
        return resultado

    if not clave:
        return _terminar({"ok": False, "activa": True,
                          "motivo": "falta la clave"})

    # ¿hay que renovar? Cuando no hay token vigente, cuando el reloj
    # parece haber retrocedido, cuando le queda menos de la mitad de la
    # vida, o cuando se pide a mano.
    renovar = forzar or reloj_atras or not vigente
    if vigente and not renovar:
        emitido = payload.get("emitido") or 0
        vida = max(1.0, float(payload["exp"]) - float(emitido))
        if (float(payload["exp"]) - time.time()) < vida * FRACCION_RENOVAR:
            renovar = True

    if not renovar:
        return _terminar({"ok": True, "activa": True, "vence": payload["exp"]})

    r = _pedir_token(clave)
    if r["ok"]:
        guardado["token"] = r["token"]
        guardado["revisado"] = time.time()
        _guardar(dir_estado, guardado)
        nuevo = leer_token(r["token"])
        return _terminar({"ok": True, "activa": True, "vence": nuevo.get("exp"),
                          "renovado": True})

    if vigente:
        # No se pudo renovar pero el token todavia vale: seguir. Esto es
        # lo que salva una noche de grabacion con el router caido.
        return _terminar({"ok": True, "activa": True, "vence": payload["exp"],
                          "aviso": r["error"]})
    return _terminar({"ok": False, "activa": True, "motivo": r["error"]})


def activar(dir_estado, clave: str) -> dict:
    """Primera vez (o clave nueva). La clave se guarda solo si sirve."""
    clave = (clave or "").strip()
    if not clave:
        return {"ok": False, "motivo": "ingresa la clave"}
    r = _pedir_token(clave)
    if not r["ok"]:
        return {"ok": False, "motivo": r["error"]}
    anterior = _leer(dir_estado)
    visto_max = max(float(anterior.get("visto_max") or 0), time.time())
    _guardar(dir_estado, {"clave": clave, "token": r["token"],
                          "revisado": time.time(), "visto_max": visto_max})
    payload = leer_token(r["token"])
    return {"ok": True, "vence": payload.get("exp")}


# -- la puerta --------------------------------------------------------------

class SinLicencia(Exception):
    """Levanta exigir() cuando este proceso no puede seguir sin activarse.

    Que sea una excepcion y no un booleano es la decision que cierra los
    entrypoints que entran por la ventana (specs\\07-licencia_inviolable.md,
    A2/A3): el default de ignorar un valor de retorno es seguir; el
    default de ignorar una excepcion es morir. Un entrypoint nuevo que se
    olvide de todo esto hereda el comportamiento seguro.
    """

    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def exigir(dir_estado) -> dict:
    """La puerta unica: revisa la licencia y levanta si no sirve.

    La llama `ajustes.cargar()`, que es lo unico que los tres entrypoints
    (app.py, servidor.py, modos.py) llaman antes de hacer cualquier otra
    cosa -- asi que tambien cubre al cuarto que alguien agregue mañana.
    """
    if not activa():
        return {"ok": True, "activa": False}
    if not _CLAVE_COMPILADA:
        # Build roto: se compilo sin generar licencia_clave.py. Abrir sin
        # poder validar nada es peor que no abrir (ver modulo, Decisiones
        # del spec).
        raise SinLicencia("build sin clave de licencia (compilado mal)")
    r = revisar(dir_estado)
    if not r.get("ok"):
        raise SinLicencia(r.get("motivo") or "licencia no valida")
    return r
