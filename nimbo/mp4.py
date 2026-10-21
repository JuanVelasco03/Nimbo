"""Lector minimo de cajas MP4/CMAF para sacar el codec string del init
segment de una sesion viva (ver vivo.py), el dato que hace falta para
`MediaSource.addSourceBuffer(... codecs="...")`.

Por que esto no importa detectar_m3u8.py, que ya tiene exactamente este
mismo recorrido de cajas (_cajas, _escalas_del_init, _pistas_del_fragmento):
ese archivo termina en

    if __name__ == "__main__":
        ...
    else:
        addons = [Detector()]

y ese `else` se ejecuta con cualquier `import detectar_m3u8` que no sea
como script principal -- construiria un Detector entero (con su Escritor,
su _Tunel, su hilo vigilante y su hilo finalizador) VIVO dentro del proceso
de app.py. Las ~60 lineas de abajo son un puerto deliberado de
_cajas/_escalas_del_init/_pistas_del_fragmento de detectar_m3u8.py, no una
reinvencion: los offsets de tkhd/mdhd son identicos a los suyos (ver
_escalas_del_init, detectar_m3u8.py:1100-1126).

Nunca lanza: cualquier init raro (truncado, corrupto, de un codec que no
reconocemos) degrada a {} y el llamador cae al snapshot de siempre.
"""

import struct

# -- recorrido generico de cajas, igual a detectar_m3u8.py:_cajas ---------


def cajas(datos, ini, fin):
    """Recorre las cajas MP4 de datos[ini:fin] -> (tipo, pos, largo, cabecera)."""
    pos = ini
    while pos + 8 <= fin:
        largo = struct.unpack(">I", datos[pos:pos + 4])[0]
        tipo = datos[pos + 4:pos + 8]
        cabecera = 8
        if largo == 1:
            if pos + 16 > fin:
                return
            largo = struct.unpack(">Q", datos[pos + 8:pos + 16])[0]
            cabecera = 16
        if largo < 8 or pos + largo > fin:
            return
        yield tipo, pos, largo, cabecera
        pos += largo


def _u16(datos, pos):
    return struct.unpack(">H", datos[pos:pos + 2])[0]


def _u32(datos, pos):
    return struct.unpack(">I", datos[pos:pos + 4])[0]


# -- codec strings, uno por formato de sample entry ------------------------

def _codec_avc(avcc_datos, p, largo):
    # avcC: version(1) + AVCProfileIndication(1) + profile_compat(1) + level(1) + ...
    if largo < 4 + 3:
        return None
    b = avcc_datos[p + 8 + 1:p + 8 + 4]
    return "avc1.%02x%02x%02x" % (b[0], b[1], b[2])


def _codec_hevc(hvcc_datos, p, largo):
    # hvcC: version(1) + general_profile_space(2b)/tier(1b)/idc(5b) + 32b compat
    #       + 48b constraint flags + general_level_idc(1)
    if largo < 8 + 13:
        return None
    base = p + 8
    b0 = hvcc_datos[base + 1]
    espacio = (b0 >> 6) & 0x3
    tier = (b0 >> 5) & 0x1
    perfil = b0 & 0x1F
    compat = _u32(hvcc_datos, base + 2)
    # compat se invierte bit a bit por convencion del propio formato del string
    compat_rev = int(("{:032b}".format(compat))[::-1], 2)
    restricciones = hvcc_datos[base + 6:base + 12]
    nivel = hvcc_datos[base + 12]
    letra_espacio = {0: "", 1: "A", 2: "B", 3: "C"}.get(espacio, "")
    letra_tier = "H" if tier else "L"
    restr_hex = restricciones.hex().upper().rstrip("0") or "0"
    return "hvc1.%s%d.%X.%s%d.%s" % (
        letra_espacio, perfil, compat_rev, letra_tier, nivel, restr_hex)


def _codec_av1(av1c_datos, p, largo):
    if largo < 8 + 4:
        return None
    base = p + 8
    b0 = av1c_datos[base + 1]
    perfil = (b0 >> 5) & 0x7
    nivel = b0 & 0x1F
    b1 = av1c_datos[base + 2]
    tier = "H" if (b1 >> 7) & 0x1 else "M"
    profundidad = 10 if (b1 >> 6) & 0x1 else 8
    return "av01.%d.%02d%s.%02d" % (perfil, nivel, tier, profundidad)


def _codec_vp9(vpcc_datos, p, largo):
    if largo < 8 + 3:
        return None
    base = p + 8
    perfil = vpcc_datos[base]
    nivel = vpcc_datos[base + 1]
    profundidad = vpcc_datos[base + 2]
    return "vp09.%02d.%02d.%02d" % (perfil, nivel, profundidad)


def _codec_aac(esds_datos, p, largo):
    """Recorre los descriptores de un esds buscando el AudioSpecificConfig
    (tag 0x05) para sacar el audioObjectType (los 5 bits altos). Si el
    parseo falla, el fallback es "mp4a.40.2" (AAC-LC): es el caso
    abrumadoramente mayoritario (confirmado: 48 kHz en todo el log real
    del grabador) y preferible a no reportar codec de audio alguno."""
    try:
        pos = p + 8 + 4  # version/flags(4) de la caja esds en si
        fin = p + largo
        while pos < fin:
            tag = esds_datos[pos]
            pos += 1
            longitud = 0
            while pos < fin:
                b = esds_datos[pos]
                pos += 1
                longitud = (longitud << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            inicio_datos = pos
            if tag == 0x05 and longitud >= 2:
                primer_byte = esds_datos[inicio_datos]
                segundo_byte = esds_datos[inicio_datos + 1]
                tipo_objeto = (primer_byte >> 3) & 0x1F
                if tipo_objeto == 0x1F:  # escape: tipo extendido, no lo seguimos
                    return "mp4a.40.2"
                return "mp4a.40.%d" % tipo_objeto
            pos = inicio_datos + longitud
    except (IndexError, struct.error):
        pass
    return "mp4a.40.2"


# -- recorrido de un stsd para encontrar la entrada y su caja de codec -----

_VISUAL = {b"avc1", b"avc3", b"hvc1", b"hev1", b"av01", b"vp09"}
_AUDIO = {b"mp4a"}
_CABEZA_VISUAL = 78   # ver docstring del modulo: reserved/dataref/dims/etc
_CABEZA_AUDIO = 28


def _primera_entrada_stsd(datos, pos, largo):
    """pos/largo son los de la caja stsd (con su cabecera de 8 incluida).
    -> (formato, pos_entrada, largo_entrada) de la primera sample entry."""
    base = pos + 8 + 4 + 4  # caja(8) + version/flags(4) + entry_count(4)
    if base + 8 > pos + largo:
        return None
    largo_entrada = _u32(datos, base)
    formato = datos[base + 4:base + 8]
    if largo_entrada < 8 or base + largo_entrada > pos + largo:
        return None
    return formato, base, largo_entrada


def _codec_de_entrada(datos, formato, pos, largo):
    if formato in _VISUAL:
        salto = _CABEZA_VISUAL
        buscar = {
            b"avc1": (b"avcC", _codec_avc), b"avc3": (b"avcC", _codec_avc),
            b"hvc1": (b"hvcC", _codec_hevc), b"hev1": (b"hvcC", _codec_hevc),
            b"av01": (b"av1C", _codec_av1),
            b"vp09": (b"vpcC", _codec_vp9),
        }.get(formato)
    elif formato in _AUDIO:
        salto = _CABEZA_AUDIO
        buscar = (b"esds", _codec_aac)
    else:
        return None, None
    if buscar is None:
        return None, None
    tipo_hijo, fn = buscar
    inicio_hijas = pos + 8 + salto
    for tipo, p, l, _ in cajas(datos, inicio_hijas, pos + largo):
        if tipo == tipo_hijo:
            codec = fn(datos, p, l)
            tipo_pista = "video" if formato in _VISUAL else "audio"
            return tipo_pista, codec
    return None, None


# -- recorrido completo del init: moov/trak/{tkhd,mdia/{mdhd,hdlr,...}} ----

def pistas_del_init(datos):
    """-> [{"pista": int, "tipo": "video"|"audio", "formato": str,
            "codec": str|None, "escala": int}] de cada trak del moov."""
    salida = []
    for tipo, pos, largo, _ in cajas(datos, 0, len(datos)):
        if tipo != b"moov":
            continue
        for t2, p2, l2, _ in cajas(datos, pos + 8, pos + largo):
            if t2 != b"trak":
                continue
            pista_id = escala = None
            tipo_pista = formato = codec = None
            for t3, p3, l3, _ in cajas(datos, p2 + 8, p2 + l2):
                if t3 == b"tkhd":
                    q = p3 + (28 if datos[p3 + 8] == 1 else 20)
                    pista_id = _u32(datos, q)
                elif t3 == b"mdia":
                    for t4, p4, l4, _ in cajas(datos, p3 + 8, p3 + l3):
                        if t4 == b"mdhd":
                            q = p4 + (28 if datos[p4 + 8] == 1 else 20)
                            escala = _u32(datos, q)
                        elif t4 == b"minf":
                            for t5, p5, l5, _ in cajas(datos, p4 + 8, p4 + l4):
                                if t5 != b"stbl":
                                    continue
                                for t6, p6, l6, _ in cajas(datos, p5 + 8, p5 + l5):
                                    if t6 != b"stsd":
                                        continue
                                    entrada = _primera_entrada_stsd(datos, p6, l6)
                                    if entrada is None:
                                        continue
                                    formato, pe, le = entrada
                                    tipo_pista, codec = _codec_de_entrada(
                                        datos, formato, pe, le)
            if pista_id is not None and tipo_pista is not None:
                salida.append({
                    "pista": pista_id, "tipo": tipo_pista,
                    "formato": formato.decode("ascii", "replace"),
                    "codec": codec, "escala": escala or 0,
                })
    return salida


def perfil_de_init(ruta):
    """-> {"codecs": "avc1.64001f,mp4a.40.2", "mime": 'video/mp4; codecs="..."',
           "pistas": [...], "escalas": {pista: escala}} | {} si no se pudo
    leer o no se encontro ninguna pista reconocible. Nunca lanza."""
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
        pistas = pistas_del_init(datos)
    except (OSError, struct.error, IndexError):
        return {}
    pistas = [p for p in pistas if p["codec"]]
    if not pistas:
        return {}
    # video primero: es el orden que importa para el SourceBuffer muxado
    pistas.sort(key=lambda p: 0 if p["tipo"] == "video" else 1)
    codecs = ",".join(p["codec"] for p in pistas)
    escalas = {p["pista"]: p["escala"] for p in pistas if p["escala"]}
    return {
        "codecs": codecs,
        "mime": 'video/mp4; codecs="%s"' % codecs,
        "pistas": pistas,
        "escalas": escalas,
    }


# -- tiempos de fragmento: solo para pruebas\prueba_vivo.py --auditar -----

def _duracion_trun(datos, pos, largo, por_defecto):
    banderas = _u32(datos, pos + 8) & 0xFFFFFF
    n = _u32(datos, pos + 12)
    q = pos + 16
    if banderas & 0x01:
        q += 4
    if banderas & 0x04:
        q += 4
    if not banderas & 0x100:
        return n * por_defecto
    paso = 4 * bin(banderas & 0xF00).count("1")
    total = 0
    for i in range(n):
        total += _u32(datos, q + i * paso)
    return total


def tiempos_del_fragmento(datos):
    """-> [(pista, tiempo, duracion)] de cada traf de un fragmento (moof+mdat).
    Puerto de detectar_m3u8.py:_pistas_del_fragmento, sin la posicion de
    tfdt (aca no hace falta reescribirlo, solo medirlo)."""
    salida = []
    for tipo, pos, largo, _ in cajas(datos, 0, len(datos)):
        if tipo != b"moof":
            continue
        for t2, p2, l2, _ in cajas(datos, pos + 8, pos + largo):
            if t2 != b"traf":
                continue
            pista = tiempo = None
            dur_defecto = 0
            duracion = 0
            for t3, p3, l3, _ in cajas(datos, p2 + 8, p2 + l2):
                if t3 == b"tfhd":
                    banderas = _u32(datos, p3 + 8) & 0xFFFFFF
                    pista = _u32(datos, p3 + 12)
                    q = p3 + 16
                    if banderas & 0x01:
                        q += 8
                    if banderas & 0x02:
                        q += 4
                    if banderas & 0x08:
                        dur_defecto = _u32(datos, q)
                elif t3 == b"tfdt":
                    version = datos[p3 + 8]
                    q = p3 + 12
                    tiempo = (struct.unpack(">Q", datos[q:q + 8])[0] if version == 1
                              else _u32(datos, q))
                elif t3 == b"trun":
                    duracion += _duracion_trun(datos, p3, l3, dur_defecto)
            if pista is not None and tiempo is not None:
                salida.append((pista, tiempo, duracion))
    return salida
