"""Politica de avisos de salud (SPEC 16): disco, motor rendido, camino
directo degradado -- las tres senales que Nimbo ya mide y hasta ahora no
mostraba (ver "Por que" en specs\\16-avisos_de_salud.md).

evaluar() es una funcion pura sobre el snapshot de estado que
servidor.py:_bucle_estado ya tiene en la mano: no toca disco ni red, asi
que se puede probar con un dict armado a mano. La licencia queda afuera
-- su aviso ya existe en _bucle_licencia y no pasa por estado["salud"].

canario() si toca disco (un import perezoso de mouflon.py) pero el
resultado es estatico por build: se calcula una vez y se cachea en
memoria, nunca entra al delta de 1 Hz.
"""

import sys

_BYTES_POR_GB = 1024 ** 3


def _alertas_disco(estado: dict, ajustes) -> list:
    motor = estado.get("motor") or {}
    motor_corriendo = bool(motor.get("detector_vivo") or motor.get("supervisor_vivo")
                           or motor.get("directo_vivo"))
    alertas = []
    for fila in estado.get("disco") or []:
        unidad = fila.get("unidad") or "?"
        libres_gb = (fila.get("libres") or 0) / _BYTES_POR_GB
        total_gb = (fila.get("total") or 0) / _BYTES_POR_GB
        if libres_gb < ajustes["min_libre_critico_gb"]:
            if ajustes["parar_por_disco"] and motor_corriendo:
                texto = ("quedan %.1f GB de %.1f GB -- el motor se paro solo"
                         % (libres_gb, total_gb))
            else:
                texto = "quedan %.1f GB de %.1f GB" % (libres_gb, total_gb)
            alertas.append({
                "clave": "disco_critico:" + unidad, "tipo": "disco", "nivel": "error",
                "titulo": "Disco %s casi lleno" % unidad,
                "texto": texto,
            })
        elif libres_gb < ajustes["min_libre_gb"]:
            alertas.append({
                "clave": "disco_aviso:" + unidad, "tipo": "disco", "nivel": "aviso",
                "titulo": "Disco %s con poco espacio" % unidad,
                "texto": "quedan %.1f GB de %.1f GB" % (libres_gb, total_gb),
            })
    return alertas


def _alerta_rendido(estado: dict) -> dict | None:
    rendido = (estado.get("motor") or {}).get("rendido") or []
    if not rendido:
        return None
    titulo = (" y ".join(rendido) + (" no arrancan" if len(rendido) > 1 else " no arranca"))
    return {
        "clave": "rendido", "tipo": "motor", "nivel": "error",
        "titulo": titulo,
        "texto": "agoto los reintentos; sigue probando cada 15 minutos",
    }


def _alerta_degradado(estado: dict) -> dict | None:
    detector = estado.get("detector") or {}
    directo = detector.get("directo") or {}
    if not directo.get("degradado"):
        return None
    degradadas = [f.get("modelo") for f in (detector.get("streams") or [])
                  if f.get("degradado")]
    if not degradadas:
        return None    # el canario fallo (degradado_global) pero ninguna
                        # modelo en particular agoto sus 3 fallos todavia
    n = len(degradadas)
    return {
        "clave": "degradado", "tipo": "degradado", "nivel": "error",
        "titulo": "%d modelo%s sin grabar" % (n, "s" if n != 1 else ""),
        "texto": "el camino directo las degrado tras 3 fallos; pasalas a "
                 "@navegador= en grabar.txt para que las grabe el navegador",
    }


def evaluar(estado: dict, ajustes) -> list:
    """-> lista de alertas vigentes (ver estado["salud"] en el spec)."""
    alertas = _alertas_disco(estado, ajustes)
    degradado = _alerta_degradado(estado)
    if degradado:
        alertas.append(degradado)
    rendido = _alerta_rendido(estado)
    if rendido:
        alertas.append(rendido)
    return alertas


# -- canario MOUFLON ---------------------------------------------------
#
# Mismo patron que sitio.py:_supervisor(): importar mouflon.py no tiene
# efectos secundarios (ver CLAUDE.md, "Los dos puentes" -- es el unico
# modulo del nucleo que se puede importar sin construir nada). Nunca
# detectar_m3u8.py.

_mouflon_cache = {}
_canario_cache = None    # None = todavia no se calculo con exito


def _mouflon(ruta_scripts):
    mod = _mouflon_cache.get(ruta_scripts)
    if mod is None:
        if ruta_scripts not in sys.path:
            sys.path.insert(0, ruta_scripts)
        import mouflon as mod
        _mouflon_cache[ruta_scripts] = mod
    return mod


def canario(ruta_scripts):
    """True/False si se pudo evaluar KEY_MAP, None si ni siquiera eso
    (falta mouflon.py en nucleo\\scripts\\). Se cachea en memoria: la
    tabla de claves es estatica por build, no hace falta recalcularla."""
    global _canario_cache
    if _canario_cache is not None:
        return _canario_cache
    try:
        mod = _mouflon(ruta_scripts)
    except Exception:
        return None
    try:
        _canario_cache = bool(mod.verificar_canario())
    except Exception:
        _canario_cache = False
    return _canario_cache
