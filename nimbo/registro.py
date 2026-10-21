"""Lector de los .jsonl que publica el puente (ver CLAUDE.md, seccion
"Los dos puentes"): estado\\eventos_detector.jsonl y
estado\\eventos_supervisor.jsonl. Son archivos de solo append, sin
rotacion -- el de detector ya pasa el MB de entrada -- asi que esto nunca
lee el archivo entero: va directo al final y retrocede en bloques hasta
juntar `limite` filas que pasen el filtro.

Paginado "cargar mas antiguas": el `cursor` que se devuelve es el offset
en bytes donde arranca la fila mas vieja de la pagina actual. Pasarlo de
vuelta como `cursor` hace que la proxima lectura considere solo bytes
*antes* de ese offset -- la fila que lo marca nunca se repite y nunca se
salta ninguna entre dos paginas consecutivas.

Nunca se importa ni se toca nada de nucleo\\scripts\\ -- esto solo lee
archivos que el propio Nimbo (via puente.py / lanzador_supervisor.py)
ya escribe en estado\\.
"""

import json
import os

FUENTES = {
    "detector": "eventos_detector.jsonl",
    "supervisor": "eventos_supervisor.jsonl",
}

BLOQUE = 65536                     # tamano de cada lectura hacia atras
TOPE_BYTES_BARRIDO = 8 * 1024 * 1024   # techo cuando el filtro no matchea casi nada


def _parsear(cruda: bytes):
    """bytes de una linea -> {"t","nivel","prefijo","mensaje"} o None si
    la linea no es JSON valido (ultima linea cortada por una caida a
    mitad de escritura, por ejemplo -- se saltea, no tira la lectura)."""
    try:
        obj = json.loads(cruda.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    t = obj.get("t")
    if not isinstance(t, (int, float)):
        return None
    return {
        "t": t,
        "nivel": str(obj.get("nivel") or ""),
        "prefijo": str(obj.get("prefijo") or ""),
        "mensaje": str(obj.get("mensaje") or ""),
    }


def leer(dir_estado, fuente, limite=400, cursor=None, nivel="", q=""):
    """-> {"filas": [...], "cursor": int|None, "hay_mas": bool, "bytes": int}

    `filas` sale en orden cronologico (vieja -> nueva), lista para pintar
    tal cual. `cursor` es None cuando ya no queda nada mas viejo que leer.
    Lanza ValueError si `fuente` no esta en FUENTES -- nunca se arma una
    ruta con lo que llego crudo por el querystring.
    """
    nombre_archivo = FUENTES.get(fuente)
    if nombre_archivo is None:
        raise ValueError("fuente invalida: %r" % (fuente,))

    limite = max(1, min(int(limite), 1000))
    ruta = os.path.join(dir_estado, nombre_archivo)

    try:
        tam = os.path.getsize(ruta)
    except OSError:
        return {"filas": [], "cursor": None, "hay_mas": False, "bytes": 0}

    pos = tam if cursor is None else max(0, min(int(cursor), tam))

    nivel_norm = (nivel or "").strip().upper()
    q_norm = (q or "").strip().lower()

    en_reversa = []   # [(offset_inicio, fila)], la mas nueva primero
    leidos_bytes = 0
    buffer_anterior = b""

    with open(ruta, "rb") as f:
        while pos > 0 and len(en_reversa) < limite and leidos_bytes < TOPE_BYTES_BARRIDO:
            bloque_tam = min(BLOQUE, pos)
            nueva_pos = pos - bloque_tam
            f.seek(nueva_pos)
            datos = f.read(bloque_tam)
            leidos_bytes += bloque_tam
            buffer = datos + buffer_anterior

            partes = buffer.split(b"\n")
            if nueva_pos > 0:
                # partes[0] puede seguir hacia la izquierda (bloque cortado
                # a mitad de linea) -- se carga para el proximo bloque, mas
                # viejo, en vez de tratarla como completa.
                buffer_anterior = partes[0]
                partes_completas = partes[1:]
                offset = nueva_pos + len(partes[0]) + 1
            else:
                buffer_anterior = b""
                partes_completas = partes
                offset = nueva_pos

            offsets = []
            for p in partes_completas:
                offsets.append(offset)
                offset += len(p) + 1

            pos = nueva_pos

            detenido = False
            for cruda, offset_inicio in reversed(list(zip(partes_completas, offsets))):
                if not cruda.strip():
                    continue
                fila = _parsear(cruda)
                if fila is None:
                    continue
                if nivel_norm and fila["nivel"].upper() != nivel_norm:
                    continue
                if q_norm and q_norm not in fila["prefijo"].lower() and q_norm not in fila["mensaje"].lower():
                    continue
                en_reversa.append((offset_inicio, fila))
                if len(en_reversa) >= limite:
                    detenido = True
                    break
            if detenido:
                break

    filas = [fila for _, fila in reversed(en_reversa)]

    if en_reversa:
        cursor_final = en_reversa[-1][0]
        hay_mas = cursor_final > 0
        cursor_salida = cursor_final if hay_mas else None
    else:
        hay_mas = pos > 0
        cursor_salida = pos if hay_mas else None

    return {"filas": filas, "cursor": cursor_salida, "hay_mas": hay_mas, "bytes": leidos_bytes}
