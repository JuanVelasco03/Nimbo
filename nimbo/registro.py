"""Lector de los .jsonl que publican el puente, el lanzador del supervisor
y el propio motor (ver CLAUDE.md, seccion "Los dos puentes"):
estado\\eventos_detector.jsonl, estado\\eventos_supervisor.jsonl y
estado\\eventos_motor.jsonl. Los tres rotan a los MAX_EVENTOS (8 MB,
ver motor.py/puente.py/lanzador_supervisor.py) con un unico archivo viejo
de sufijo ".1" -- por eso esto nunca lee mas que el actual + el ".1": va
directo al final del actual y retrocede en bloques, y si se queda corto
sigue por el ".1".

Paginado "cargar mas antiguas": el `cursor` que se devuelve es
"<idx>:<offset>", con idx 0 = archivo actual, 1 = el ".1", y offset el
byte donde arranca la fila mas vieja de la pagina actual. Pasarlo de
vuelta como `cursor` hace que la proxima lectura considere solo bytes
*antes* de ese punto -- la fila que lo marca nunca se repite y nunca se
salta ninguna entre dos paginas consecutivas.

Nunca se importa ni se toca nada de nucleo\\scripts\\ -- esto solo lee
archivos que el propio Nimbo (via puente.py / lanzador_supervisor.py /
motor.py) ya escribe en estado\\.
"""

import json
import os

FUENTES = {
    "detector": "eventos_detector.jsonl",
    "supervisor": "eventos_supervisor.jsonl",
    "motor": "eventos_motor.jsonl",
    "directo": "eventos_directo.jsonl",
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


def _tam(ruta):
    try:
        return os.path.getsize(ruta)
    except OSError:
        return None   # el archivo no existe (todavia no roto, o no hay ".1")


def _barrer_archivo(ruta, pos, limite_restante, tope_bytes_restante, nivel_norm, q_norm):
    """Retrocede desde `pos` en `ruta`, igual que el barrido original de
    antes de que hubiera ".1": junta filas que pasen el filtro hasta
    `limite_restante` o hasta gastar `tope_bytes_restante`.

    -> (en_reversa, pos_final, bytes_leidos) -- `en_reversa` es
    [(offset_inicio, fila), ...] de la mas nueva a la mas vieja dentro de
    este archivo; `pos_final == 0` significa que se llego al principio.
    """
    en_reversa = []
    leidos_bytes = 0
    buffer_anterior = b""

    with open(ruta, "rb") as f:
        while (pos > 0 and len(en_reversa) < limite_restante
               and leidos_bytes < tope_bytes_restante):
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
                if len(en_reversa) >= limite_restante:
                    detenido = True
                    break
            if detenido:
                break

    return en_reversa, pos, leidos_bytes


def leer(dir_estado, fuente, limite=400, cursor=None, nivel="", q=""):
    """-> {"filas": [...], "cursor": str|None, "hay_mas": bool, "bytes": int}

    `filas` sale en orden cronologico (vieja -> nueva), lista para pintar
    tal cual. `cursor` es None cuando ya no queda nada mas viejo que leer;
    si no, es la cadena opaca "<idx>:<offset>" (idx 0 = archivo actual,
    1 = el ".1" que deja la rotacion por tamano) que hay que mandar de
    vuelta tal cual para la pagina siguiente. Lanza ValueError si `fuente`
    no esta en FUENTES, o si `cursor` no tiene ese formato -- nunca se
    arma una ruta con lo que llego crudo por el querystring.
    """
    nombre_archivo = FUENTES.get(fuente)
    if nombre_archivo is None:
        raise ValueError("fuente invalida: %r" % (fuente,))

    limite = max(1, min(int(limite), 1000))
    rutas = [os.path.join(dir_estado, nombre_archivo),
             os.path.join(dir_estado, nombre_archivo + ".1")]

    if cursor is None:
        idx, pos = 0, None   # None = "todavia no se resolvio contra el tamano real"
    else:
        try:
            idx_str, pos_str = str(cursor).split(":", 1)
            idx, pos = int(idx_str), int(pos_str)
        except ValueError:
            raise ValueError("cursor invalido: %r" % (cursor,))
        if idx not in (0, 1) or pos < 0:
            raise ValueError("cursor invalido: %r" % (cursor,))

    nivel_norm = (nivel or "").strip().upper()
    q_norm = (q or "").strip().lower()

    en_reversa_total = []   # [(idx, offset_inicio, fila)], la mas nueva primero
    leidos_bytes = 0

    while idx < len(rutas):
        tam = _tam(rutas[idx])
        if not tam:
            # No existe (nunca roto, o nunca se escribio nada todavia) o
            # esta vacio: nada que leer aqui, pasar derecho al siguiente.
            idx += 1
            pos = None
            continue
        pos_real = tam if pos is None else min(pos, tam)
        restante_limite = limite - len(en_reversa_total)
        restante_bytes = TOPE_BYTES_BARRIDO - leidos_bytes
        if restante_limite <= 0 or restante_bytes <= 0:
            # Se llego al tope sin haber tocado este archivo: el cursor
            # apunta exactamente aqui, listo para la proxima pagina.
            pos = pos_real
            break
        parcial, pos_final, bytes_leidos = _barrer_archivo(
            rutas[idx], pos_real, restante_limite, restante_bytes,
            nivel_norm, q_norm)
        leidos_bytes += bytes_leidos
        en_reversa_total.extend((idx, offset, fila) for offset, fila in parcial)
        # El offset de la ultima fila juntada (no `pos_final`, que es solo
        # la posicion de bloque) es lo que de verdad indica si se termino
        # de revisar el archivo: _barrer_archivo puede frenar a mitad de
        # un bloque ya leido hasta el byte 0 sin haber procesado todas sus
        # filas, si el limite se completo justo ahi.
        if parcial:
            offset_mas_vieja = parcial[-1][0]
            if offset_mas_vieja > 0:
                pos = offset_mas_vieja
                break   # queda mas en este archivo
        elif pos_final > 0:
            # ninguna fila paso el filtro, pero tampoco se termino de leer
            # el archivo (se corto por tope de bytes)
            pos = pos_final
            break
        # se agoto este archivo: seguir con el siguiente
        idx += 1
        pos = None

    filas = [fila for _, _, fila in reversed(en_reversa_total)]

    if idx < len(rutas):
        hay_mas = True
        cursor_salida = "%d:%d" % (idx, pos)
    else:
        hay_mas = False
        cursor_salida = None

    return {"filas": filas, "cursor": cursor_salida, "hay_mas": hay_mas, "bytes": leidos_bytes}
