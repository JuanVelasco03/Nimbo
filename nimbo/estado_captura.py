"""Fusiona estado\\detector.json (lo escribe nimbo\\puente.py) y
estado\\directo.json (lo escribe nucleo\\scripts\\grabador_directo.py) en
un solo dict con el mismo schema que detector.json -- "streams"
concatenado -- para que vivo.py, cosecha_streams.py y el frontend sigan
sin saber de donde salio cada sesion (ver CLAUDE.md, "Los dos puentes", y
specs\\grabador_directo.md S4.4/S5).

El schema de una fila de directo.json es, a proposito, igual al de una
fila de detector.json (ver grabador_directo.py:_ModeloDirecto.fila_estado),
asi que alcanza con concatenar listas. Esto no escribe nada: es pura
lectura/fusion de lo que ya publicaron los dos procesos.
"""

import time

# Los dos procesos escriben cada 2s (ver INTERVALO_ESTADO en puente.py y en
# grabador_directo.py) -- 15s tolera un hipo de disco o un GC sin falsos
# positivos, pero una parada del camino directo (@inactividad=120s por
# sesion + la cola de union, secuencial: medidos 20 minutos para 10
# sesiones) deja directo.json mucho mas viejo que eso. Antes "grabando":
# true para 10 modelos quedaba congelado en la ventana durante toda la
# parada -- ver grabador_directo.py:Grabador._fin / _bucle_estado.
FRESCURA_S = 15.0


def _marcar_vencido(fuente: dict) -> dict:
    """Las filas NO se borran: vivo.py/cosecha_streams.barrer() las usan
    para decidir que sesion de datos\\streams\\ no tocar todavia (una de
    las tres compuertas de cosecha_streams, ver CLAUDE.md). Borrarlas
    durante una parada larga podria hacer que la cosecha mande a la
    papelera justo la sesion que se esta uniendo. Se marcan "sin_dato" y
    grabando=False para que el frontend no siga mostrando GRABANDO con
    datos viejos, sin perder el resto de la fila."""
    filas = []
    for fila in fuente.get("streams") or []:
        fila = dict(fila)
        fila["grabando"] = False
        fila["estado"] = "sin_dato"
        filas.append(fila)
    nuevo = dict(fuente)
    nuevo["streams"] = filas
    return nuevo


def fusionar(detector: dict | None, directo: dict | None,
             ahora: float | None = None, frescura: float = FRESCURA_S) -> dict | None:
    """-> dict con el shape de detector.json, o None si los dos faltan.

    Si falta uno de los dos, se devuelve el otro tal cual (con el campo
    "directo" agregado solo cuando ese JSON existe) -- no hace falta
    fusionar nada si solo hay una fuente.
    """
    if ahora is None:
        ahora = time.time()

    if directo and ahora - (directo.get("generado") or 0) > frescura:
        directo = _marcar_vencido(directo)
    if detector and ahora - (detector.get("generado") or 0) > frescura:
        detector = _marcar_vencido(detector)

    if not directo:
        return detector
    if not detector:
        base = {"generado": directo.get("generado"), "tunel_activo": False,
                "pedidos_tunel": 0, "streams": []}
    else:
        base = dict(detector)

    streams = list(base.get("streams") or []) + list(directo.get("streams") or [])
    # Dedupe por modelo: normalmente no se solapan (@directo=1 saca a la
    # modelo del supervisor, ver supervisor.Lista.directo), pero si se
    # solapan en la ventana de un cambio de @navegador= o @directo=. Gana
    # la fila fresca sobre la vencida y, a igualdad, la que tenga sesiones
    # abiertas -- sin esto la grilla mostraba dos tarjetas para la misma
    # modelo y streams.find() del frontend se quedaba siempre con la
    # primera, sin ningun criterio.
    por_modelo: dict[str, dict] = {}
    for fila in streams:
        clave = (fila.get("modelo") or "").lower()
        if not clave:
            continue
        actual = por_modelo.get(clave)
        if actual is None:
            por_modelo[clave] = fila
            continue
        fresca_actual = actual.get("estado") != "sin_dato"
        fresca_nueva = fila.get("estado") != "sin_dato"
        if fresca_nueva and not fresca_actual:
            por_modelo[clave] = fila
        elif fresca_nueva == fresca_actual and fila.get("sesiones") and not actual.get("sesiones"):
            por_modelo[clave] = fila
    base["streams"] = list(por_modelo.values())
    base["generado"] = max(base.get("generado") or 0, directo.get("generado") or 0)
    base["directo"] = {
        "modo": directo.get("modo"),
        "degradado": directo.get("degradado"),
        "ids": directo.get("ids") or {},
        "parando": directo.get("parando", False),
        "uniendo": directo.get("uniendo", 0),
    }
    return base
