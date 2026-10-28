"""Licencia con vencimiento: clave entregada a mano, token firmado.

Implementa `specs\\distribucion_instalador.md` S6.

**Apagada por defecto.** Si no existe `nimbo\\licencia_publica.json` con
una clave publica adentro, `activa()` devuelve False y Nimbo arranca sin
preguntar nada. Ese archivo lo inyecta el instalador
(`empaquetado\\construir.py --clave-publica`), nunca el repo: la copia de
desarrollo no se bloquea nunca por este mecanismo.

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

# Lo que inyecta el instalador. En el repo no existe -- a proposito.
CONFIG_LICENCIA = os.path.join(_AQUI, "licencia_publica.json")

NOMBRE_ESTADO = "licencia.json"

# Cuanto se espera al backend. Corto: si no contesta, el token guardado
# cubre el arranque y se vuelve a intentar mas tarde.
ESPERA_RED = 20

# Se renueva cuando queda menos de esta parte de la vida del token.
FRACCION_RENOVAR = 0.5


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
    """Si este paquete exige licencia. False en la copia de desarrollo."""
    return bool(_config().get("clave_publica"))


def _clave_publica() -> bytes:
    try:
        return _b64d(_config()["clave_publica"])
    except (KeyError, ValueError):
        return b""


# -- base64url sin relleno (lo mismo que usa el backend) ------------------

def _b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


# -- id de la maquina -----------------------------------------------------

def id_maquina() -> str:
    """Identificador estable del equipo, legible sin admin.

    MachineGuid lo pone Windows al instalarse y no cambia con el hardware
    ni con las actualizaciones. Si no se puede leer (otro sistema, una
    politica rara), se cae al nombre del equipo + el usuario: peor, pero
    no deja a nadie afuera.
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
    return "%s/%s" % (os.environ.get("COMPUTERNAME", "?"),
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
    payload = leer_token(guardado.get("token") or "")
    vigente = _vigente(payload)

    if not clave:
        return {"ok": False, "activa": True, "motivo": "falta la clave"}

    # ¿hay que renovar? Cuando no hay token vigente, cuando le queda menos
    # de la mitad de la vida, o cuando se pide a mano.
    renovar = forzar or not vigente
    if vigente and not renovar:
        emitido = payload.get("emitido") or 0
        vida = max(1.0, float(payload["exp"]) - float(emitido))
        if (float(payload["exp"]) - time.time()) < vida * FRACCION_RENOVAR:
            renovar = True

    if not renovar:
        return {"ok": True, "activa": True, "vence": payload["exp"]}

    r = _pedir_token(clave)
    if r["ok"]:
        guardado["token"] = r["token"]
        guardado["revisado"] = time.time()
        _guardar(dir_estado, guardado)
        nuevo = leer_token(r["token"])
        return {"ok": True, "activa": True, "vence": nuevo.get("exp"),
                "renovado": True}

    if vigente:
        # No se pudo renovar pero el token todavia vale: seguir. Esto es
        # lo que salva una noche de grabacion con el router caido.
        return {"ok": True, "activa": True, "vence": payload["exp"],
                "aviso": r["error"]}
    return {"ok": False, "activa": True, "motivo": r["error"]}


def activar(dir_estado, clave: str) -> dict:
    """Primera vez (o clave nueva). La clave se guarda solo si sirve."""
    clave = (clave or "").strip()
    if not clave:
        return {"ok": False, "motivo": "ingresa la clave"}
    r = _pedir_token(clave)
    if not r["ok"]:
        return {"ok": False, "motivo": r["error"]}
    _guardar(dir_estado, {"clave": clave, "token": r["token"],
                          "revisado": time.time()})
    payload = leer_token(r["token"])
    return {"ok": True, "vence": payload.get("exp")}
