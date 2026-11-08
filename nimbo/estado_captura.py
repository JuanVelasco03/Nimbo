"""Fusiona estado\\detector.json (lo escribe nimbo\\puente.py),
estado\\directo.json (lo escribe nucleo\\scripts\\grabador_directo.py) y
estado\\chaturbate.json (lo escribe nucleo\\scripts\\grabador_chaturbate.py,
SPEC 18) en un solo dict con el mismo schema que detector.json --
"streams" concatenado -- para que vivo.py, cosecha_streams.py y el
frontend sigan sin saber de donde salio cada sesion (ver CLAUDE.md, "Los
dos puentes", y specs\\grabador_directo.md S4.4/S5).

El schema de una fila de directo.json/chaturbate.json es, a proposito,
igual al de una fila de detector.json (ver
grabador_directo.py:_ModeloDirecto.fila_estado), asi que alcanza con
concatenar listas. Esto no escribe nada: es pura lectura/fusion de lo que
ya publicaron los procesos.

SPEC 18: con dos sitios, dos modelos homonimas (una por sitio) ya no se
pueden deduplicar por "modelo.lower()" a secas -- se pisarian en la
grilla. La clave de dedupe pasa a (sitio, modelo.lower()); una fila sin
"sitio" (un detector.json/directo.json de antes de este spec, o un
JSON parcial durante una actualizacion) se asume "stripchat", el
comportamiento de siempre.
"""

import time

# Los procesos escriben cada 2s (ver INTERVALO_ESTADO en puente.py y en
# grabador_directo.py/grabador_chaturbate.py) -- 15s tolera un hipo de
# disco o un GC sin falsos positivos, pero una parada del camino directo
# (@inactividad=120s por sesion + la cola de union, secuencial: medidos 20
# minutos para 10 sesiones) deja directo.json mucho mas viejo que eso.
# Antes "grabando": true para 10 modelos quedaba congelado en la ventana
# durante toda la parada -- ver grabador_directo.py:Grabador._fin /
# _bucle_estado.
FRESCURA_S = 15.0


def _sitio_de(fila: dict) -> str:
    return (fila.get("sitio") or "stripchat").strip().lower() or "stripchat"


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
             chaturbate: dict | None = None,
             ahora: float | None = None, frescura: float = FRESCURA_S) -> dict | None:
    """-> dict con el shape de detector.json, o None si las tres faltan.

    Si falta alguna de las tres, se fusiona con las que haya -- no hace
    falta que existan las tres para que esto devuelva algo.
    """
    if ahora is None:
        ahora = time.time()

    if directo and ahora - (directo.get("generado") or 0) > frescura:
        directo = _marcar_vencido(directo)
    if detector and ahora - (detector.get("generado") or 0) > frescura:
        detector = _marcar_vencido(detector)
    if chaturbate and ahora - (chaturbate.get("generado") or 0) > frescura:
        chaturbate = _marcar_vencido(chaturbate)

    fuentes_directas = [f for f in (directo, chaturbate) if f]
    if not fuentes_directas:
        return detector
    if not detector:
        primera = fuentes_directas[0]
        base = {"generado": primera.get("generado"), "tunel_activo": False,
                "pedidos_tunel": 0, "streams": []}
    else:
        base = dict(detector)

    streams = list(base.get("streams") or [])
    for fuente in fuentes_directas:
        streams += list(fuente.get("streams") or [])
    # Dedupe por (sitio, modelo): normalmente no se solapan (@directo=1
    # saca a la modelo del supervisor, @chaturbate= la saca del proxy),
    # pero si se solapan en la ventana de un cambio de marca en
    # grabar.txt. Gana la fila fresca sobre la vencida y, a igualdad, la
    # que tenga sesiones abiertas -- sin esto la grilla mostraba dos
    # tarjetas para la misma modelo y streams.find() del frontend se
    # quedaba siempre con la primera, sin ningun criterio. Dos modelos
    # homonimas de sitios DISTINTOS no son la misma clave: las dos quedan.
    por_clave: dict[tuple[str, str], dict] = {}
    for fila in streams:
        nombre = (fila.get("modelo") or "").lower()
        if not nombre:
            continue
        clave = (_sitio_de(fila), nombre)
        actual = por_clave.get(clave)
        if actual is None:
            por_clave[clave] = fila
            continue
        fresca_actual = actual.get("estado") != "sin_dato"
        fresca_nueva = fila.get("estado") != "sin_dato"
        if fresca_nueva and not fresca_actual:
            por_clave[clave] = fila
        elif fresca_nueva == fresca_actual and fila.get("sesiones") and not actual.get("sesiones"):
            por_clave[clave] = fila
    base["streams"] = list(por_clave.values())
    base["generado"] = max([base.get("generado") or 0]
                            + [f.get("generado") or 0 for f in fuentes_directas])
    if directo:
        base["directo"] = {
            "modo": directo.get("modo"),
            "degradado": directo.get("degradado"),
            "ids": directo.get("ids") or {},
            "parando": directo.get("parando", False),
            "uniendo": directo.get("uniendo", 0),
        }
    if chaturbate:
        base["chaturbate"] = {
            "modo": chaturbate.get("modo"),
            "degradado": chaturbate.get("degradado"),
            "ids": chaturbate.get("ids") or {},
            "parando": chaturbate.get("parando", False),
            "uniendo": chaturbate.get("uniendo", 0),
        }
    return base
