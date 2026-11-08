"""Aviso de actualizacion contra un manifiesto firmado en GitHub (SPEC 17,
C12).

`nimbo\\version.py` es la fuente unica de que version es esta build.
`verificar()` baja `releases/manifest.json` del propio repo publico
(`raw.githubusercontent.com` -- cero infraestructura nueva, ver
Decisiones del spec), valida su firma con la clave publica del par de
actualizaciones (separado del de licencia: `empaquetado\\actualizaciones\\
claves.py`, mismo `nimbo\\firma.py`) y compara contra `version.VERSION`.

Enterarse alcanza para este spec: el resultado linkea a la pagina de
releases de GitHub, nunca descarga ni instala nada.

Si `GitHub` esta caido, sin red, o el manifiesto no trae firma valida,
esto **degrada en silencio** -- igual criterio que `licencia.py`: sin una
respuesta valida, no hay aviso y Nimbo sigue andando. `buscar_actualizaciones`
apagado corta ANTES de tocar la red: Nimbo graba contenido de un sitio de
terceros, no tiene por que salir a internet por su cuenta sin que quede
a la vista (ver CLAUDE.md, Decisiones del spec).
"""
import base64
import json
import time
import urllib.error
import urllib.request

import firma
import version as version_mod

URL_MANIFIESTO = ("https://raw.githubusercontent.com/JuanVelasco03/Nimbo/"
                  "main/releases/manifest.json")

# Clave publica del par de actualizaciones (empaquetado\actualizaciones\
# claves.py) -- la privada nunca esta en este repo.
CLAVE_PUBLICA = "eDCepZ5fQ8le3F0-1Me1-FQA7R4cCXY6kgjnVazpTgM"

ESPERA_RED = 15.0

# Cuanto se le cree al ultimo resultado antes de volver a pedir la red:
# la pestana Ajustes puede abrirse muchas veces por hora, y no tiene
# sentido golpear GitHub en cada una -- "buscar ahora" (forzar=True) lo
# saltea. El bucle de servidor.py ya revisa cada 24h por su cuenta.
INTERVALO_CACHE_S = 3600.0

_cache = {"revisado": None, "resultado": None}


def _b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _version_tupla(v: str) -> tuple:
    partes = []
    for p in (v or "").split("."):
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    return tuple(partes)


def _version_mayor(a: str, b: str) -> bool:
    """True si la version `a` (la del manifiesto) es mayor que `b` (la de
    esta instalacion) -- comparando tuplas de enteros, no las cadenas
    (asi "1.2.10" no queda "menor" que "1.2.9")."""
    return _version_tupla(a) > _version_tupla(b)


def _bajar_manifiesto() -> dict:
    pedido = urllib.request.Request(URL_MANIFIESTO)
    # Sin proxy: esto no tiene nada que ver con el de grabacion, y puede
    # no estar levantado todavia (mismo criterio que licencia._pedir_token).
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with abridor.open(pedido, timeout=ESPERA_RED) as r:
        return json.loads(r.read().decode("utf-8"))


def _manifiesto_valido(manifiesto) -> bool:
    if not isinstance(manifiesto, dict):
        return False
    firma_b64 = manifiesto.get("firma")
    if not firma_b64 or not manifiesto.get("version"):
        return False
    sin_firma = {k: v for k, v in manifiesto.items() if k != "firma"}
    crudo = json.dumps(sin_firma, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        return firma.verificar(_b64d(CLAVE_PUBLICA), crudo, _b64d(firma_b64))
    except (ValueError, TypeError):
        return False


def verificar(ajustes, forzar: bool = False) -> dict | None:
    """-> {"hay_nueva", "version", "notas", "url", "revisado"} o None.

    None cubre tres casos distintos a proposito, todos "no hay nada para
    mostrar": el toggle esta apagado, todavia no se pudo evaluar nunca, o
    la ultima vez que se pudo bajar el manifiesto no tenia firma valida.
    Nunca levanta -- quien llama (el bucle de servidor.py, o el boton
    "buscar ahora") no tiene por que lidiar con una excepcion de red.
    """
    if not ajustes["buscar_actualizaciones"]:
        return None

    ahora = time.time()
    if (not forzar and _cache["revisado"] is not None
            and ahora - _cache["revisado"] < INTERVALO_CACHE_S):
        return _cache["resultado"]

    try:
        manifiesto = _bajar_manifiesto()
    except (OSError, ValueError, UnicodeDecodeError):
        return _cache["resultado"]      # degrada en silencio: se queda con lo ultimo que valia

    if not _manifiesto_valido(manifiesto):
        return _cache["resultado"]

    resultado = {
        "hay_nueva": _version_mayor(manifiesto["version"], version_mod.VERSION),
        "version": manifiesto["version"],
        "notas": manifiesto.get("notas", ""),
        "url": manifiesto.get("url", ""),
        "revisado": ahora,
    }
    _cache["revisado"] = ahora
    _cache["resultado"] = resultado
    return resultado
