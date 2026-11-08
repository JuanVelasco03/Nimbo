"""Funciones puras para el acceso LAN (SPEC 17, C10): hash de la
contrasena y la cookie de sesion. Nada de esto toca disco ni conoce
`ajustes.py` -- `servidor.py` es quien lee/escribe `config.json` y le pasa
los valores ya resueltos.

La cookie **desbloquea conocer el token**, no lo reemplaza: `api.js` sigue
leyendo `?t=` de la URL como siempre (ver CLAUDE.md, Decisiones del spec).
Por eso esto no firma nada relacionado al token -- solo un payload con
`exp`, para poder expirar y revocar (rotando la clave) sin estado del
lado del servidor aparte de esa clave.
"""
import base64
import hashlib
import hmac
import json
import os
import socket
import time

# PBKDF2-HMAC-SHA256: nunca en claro, nunca reversible -- no hace falta
# mostrarla de nuevo, solo compararla (ver Decisiones del spec: no DPAPI
# aca, DPAPI es para secretos que hay que leer despues).
ITERACIONES_PBKDF2 = 200_000
LARGO_SAL = 16


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# -- contrasena -------------------------------------------------------------

def hashear(pw: str, sal: bytes | None = None) -> tuple[str, str]:
    """-> (hash_hex, sal_hex). Si no se pasa sal, genera una nueva (alta
    de contrasena); si se pasa (verificar_password), recalcula con esa
    misma sal."""
    if sal is None:
        sal = os.urandom(LARGO_SAL)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), sal, ITERACIONES_PBKDF2)
    return h.hex(), sal.hex()


def verificar_password(pw: str, hash_hex: str, sal_hex: str) -> bool:
    """True solo si pw, con la misma sal, da el mismo hash. compare_digest
    para no filtrar por tiempo cuanto de la contrasena coincide."""
    if not hash_hex or not sal_hex:
        return False
    try:
        sal = bytes.fromhex(sal_hex)
    except ValueError:
        return False
    calculado, _ = hashear(pw, sal)
    return hmac.compare_digest(calculado, hash_hex)


# -- cookie de sesion ---------------------------------------------------
#
# <payload_b64>.<firma_hex>, payload = {"exp": epoch}. firma =
# HMAC-SHA256(clave, payload_b64).hexdigest() -- ver modelo de datos del
# spec. clave es `lan_cookie_clave` tal cual vive en config.json (hex).

def emitir_cookie(clave_hex: str, dias: float = 30) -> str:
    payload = json.dumps({"exp": time.time() + dias * 86400.0},
                         separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64e(payload)
    firma = hmac.new(bytes.fromhex(clave_hex), payload_b64.encode("ascii"),
                     hashlib.sha256).hexdigest()
    return payload_b64 + "." + firma


def verificar_cookie(clave_hex: str, valor: str) -> dict | None:
    """-> el payload si la firma es valida y no vencio, o None.

    Nunca levanta: un valor con forma rara (cookie vieja, manipulada, de
    una clave rotada) es tan invalido como una vencida."""
    if not clave_hex or not valor or "." not in valor:
        return None
    payload_b64, firma = valor.rsplit(".", 1)
    try:
        esperada = hmac.new(bytes.fromhex(clave_hex), payload_b64.encode("ascii"),
                            hashlib.sha256).hexdigest()
    except ValueError:
        return None
    if not hmac.compare_digest(esperada, firma):
        return None
    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if float(payload.get("exp") or 0) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    return payload


def generar_clave() -> str:
    """Un `lan_cookie_clave` nuevo (hex): al activar el acceso LAN por
    primera vez y en "Cerrar todas las sesiones" (rotarla invalida de una
    cualquier cookie emitida antes -- no hace falta llevar una lista)."""
    return os.urandom(32).hex()


def ip_lan() -> str:
    """La IP de este equipo en la red local, para el QR y el panel de
    Ajustes. Un socket UDP "conectado" no manda nada por la red (UDP no
    tiene handshake) -- es solo la forma barata de preguntarle al sistema
    operativo que interfaz de salida elegiria para esa ruta."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
