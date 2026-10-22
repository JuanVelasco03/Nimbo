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


def fusionar(detector: dict | None, directo: dict | None) -> dict | None:
    """-> dict con el shape de detector.json, o None si los dos faltan.

    Si falta uno de los dos, se devuelve el otro tal cual (con el campo
    "directo" agregado solo cuando ese JSON existe) -- no hace falta
    fusionar nada si solo hay una fuente.
    """
    if not directo:
        return detector
    if not detector:
        base = {"generado": directo.get("generado"), "tunel_activo": False,
                "pedidos_tunel": 0, "streams": []}
    else:
        base = dict(detector)

    base["streams"] = list(base.get("streams") or []) + list(directo.get("streams") or [])
    base["generado"] = max(base.get("generado") or 0, directo.get("generado") or 0)
    base["directo"] = {
        "modo": directo.get("modo"),
        "degradado": directo.get("degradado"),
        "ids": directo.get("ids") or {},
    }
    return base
