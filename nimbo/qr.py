"""Generador de codigos QR, solo libreria estandar (SPEC 17, C10).

Igual que `nimbo\\firma.py` con Ed25519: en vez de sumar una dependencia
nueva (p.ej. el paquete `qrcode`) para mostrar una URL corta en Ajustes,
se implementa la matriz QR a mano -- es el mismo criterio del repo (ver
CLAUDE.md, el interprete de la ventana solo necesita stdlib +
pystray/Pillow).

Alcance deliberadamente chico: solo modo **byte**, nivel de correccion
**L** (el que mas capacidad da) y versiones 1 a 5 (hasta 106 bytes de
datos) -- de sobra para una URL LAN como
"http://192.168.1.50:8787/" (ver nimbo\\lan.py:ip_lan). Una URL mas larga
que eso levanta ValueError en vez de fallar en silencio.

La mascara se deja siempre en 0 (una de las 8 validas por el estandar):
elegir la mascara "optima" por puntaje de penalizacion no cambia si el
codigo es legible, solo que tan parejo se ve -- y agrega bastante
superficie de bug a algo que esta app usa una vez por sesion, para un
QR que un lector real (probado con pyzbar) decodifica igual.

Probado contra `pyzbar` (no es dependencia de Nimbo, solo de la prueba)
con URLs de 10 a 90 caracteres: decodifica exactamente la URL dada, para
las cinco versiones (1 a 5).
"""
import struct
import zlib

# -- aritmetica en GF(256), igual campo que usa Reed-Solomon para QR -------

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _inicializar_gf():
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D         # polinomio primitivo de QR: x^8+x^4+x^3+x^2+1
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_inicializar_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _poli_mul(p: list, q: list) -> list:
    r = [0] * (len(p) + len(q) - 1)
    for i, pi in enumerate(p):
        if pi == 0:
            continue
        for j, qj in enumerate(q):
            if qj:
                r[i + j] ^= _gf_mul(pi, qj)
    return r


def _generador_rs(nsym: int) -> list:
    """El polinomio generador de Reed-Solomon para nsym simbolos de
    correccion: producto de (x - alpha^i) para i en [0, nsym)."""
    g = [1]
    for i in range(nsym):
        g = _poli_mul(g, [1, _GF_EXP[i]])
    return g


def _ecc_rs(datos: bytes, nsym: int) -> bytes:
    """Resto de dividir datos (como polinomio) por el generador -- division
    larga en GF(256) via LFSR, el algoritmo de siempre para calcular los
    codewords de correccion de QR."""
    gen = _generador_rs(nsym)
    buf = bytearray(datos) + bytearray(nsym)
    for i in range(len(datos)):
        coef = buf[i]
        if coef:
            for j, g in enumerate(gen):
                buf[i + j] ^= _gf_mul(g, coef)
    return bytes(buf[len(datos):])


# -- tabla de versiones (solo 1 a 5, nivel L, un solo bloque) ---------------
# version -> (dimension, codewords totales, codewords de correccion)
_VERSIONES = {
    1: (21, 26, 7),
    2: (25, 44, 10),
    3: (29, 70, 15),
    4: (33, 100, 20),
    5: (37, 134, 26),
}
# Centros de los patrones de alineacion por version (ISO/IEC 18004 tabla
# E.1) -- version 1 no lleva ninguno.
_ALINEACION = {2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30]}


def _elegir_version(largo_datos: int):
    """-> (version, dim, codewords_datos, codewords_ecc) para el primer
    version que entra, o levanta ValueError si ni la version 5 alcanza."""
    for version, (dim, total, ecc) in _VERSIONES.items():
        datos_cw = total - ecc
        # encabezado: 4 bits de modo + 8 bits de largo (char count indicator
        # de 8 bits vale para las versiones 1-9, que es todo lo que esto
        # soporta).
        bits_necesarios = 4 + 8 + largo_datos * 8
        if bits_necesarios <= datos_cw * 8:
            return version, dim, datos_cw, ecc
    raise ValueError("la URL es demasiado larga para este generador de QR "
                     "(maximo ~106 bytes en modo byte, nivel L)")


# -- armado de los codewords de datos ---------------------------------------

def _codewords_datos(datos: bytes, datos_cw: int) -> list:
    bits = []
    def _agregar(valor, n):
        bits.extend((valor >> i) & 1 for i in range(n - 1, -1, -1))

    _agregar(0b0100, 4)             # modo byte
    _agregar(len(datos), 8)         # char count indicator (8 bits, v1-9)
    for b in datos:
        _agregar(b, 8)

    capacidad = datos_cw * 8
    if len(bits) > capacidad:
        raise ValueError("la URL no entra en la version elegida")
    bits.extend([0] * min(4, capacidad - len(bits)))   # terminador
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(map(str, bits[i:i + 8])), 2)
                for i in range(0, len(bits), 8)]
    relleno = (0xEC, 0x11)
    i = 0
    while len(codewords) < datos_cw:
        codewords.append(relleno[i % 2])
        i += 1
    return codewords


# -- la matriz ---------------------------------------------------------------

def _dibujar_buscador(grilla, funcion, cx, cy, dim):
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            x, y = cx + dx, cy + dy
            if 0 <= x < dim and 0 <= y < dim:
                dist = max(abs(dx), abs(dy))
                grilla[y][x] = dist not in (2, 4)
                funcion[y][x] = True


def _dibujar_alineacion(grilla, funcion, cx, cy):
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            x, y = cx + dx, cy + dy
            dist = max(abs(dx), abs(dy))
            grilla[y][x] = dist != 1
            funcion[y][x] = True


def _dibujar_patrones_fijos(grilla, funcion, version, dim):
    for cx, cy in ((3, 3), (dim - 4, 3), (3, dim - 4)):
        _dibujar_buscador(grilla, funcion, cx, cy, dim)

    for i in range(8, dim - 8):
        oscuro = i % 2 == 0
        grilla[6][i] = oscuro
        funcion[6][i] = True
        grilla[i][6] = oscuro
        funcion[i][6] = True

    coords = _ALINEACION.get(version, [])
    extremos = {(coords[0], coords[0]), (coords[0], coords[-1]),
               (coords[-1], coords[0])} if coords else set()
    for cy in coords:
        for cx in coords:
            if (cx, cy) in extremos:
                continue    # se solaparia con un patron buscador
            _dibujar_alineacion(grilla, funcion, cx, cy)

    # modulo oscuro, siempre encendido, siempre a la misma posicion
    grilla[4 * version + 9][8] = True
    funcion[4 * version + 9][8] = True

    # reservar (sin valor todavia) las dos copias de la info de formato
    for i in range(9):
        if i != 6:
            funcion[8][i] = True
    for i in range(8):
        if i != 6:
            funcion[i][8] = True
    for i in range(dim - 8, dim):
        funcion[8][i] = True
        funcion[i][8] = True


def _colocar_datos(grilla, funcion, dim, bits_datos):
    idx = 0
    col = dim - 1
    subiendo = True
    while col > 0:
        if col == 6:
            col -= 1        # la columna de sincronismo no lleva datos
        for paso in range(dim):
            fila = dim - 1 - paso if subiendo else paso
            for c in (col, col - 1):
                if funcion[fila][c]:
                    continue
                bit = bits_datos[idx] if idx < len(bits_datos) else 0
                idx += 1
                grilla[fila][c] = bool(bit)
        subiendo = not subiendo
        col -= 2


def _aplicar_mascara_0(grilla, funcion, dim):
    for y in range(dim):
        for x in range(dim):
            if not funcion[y][x] and (y + x) % 2 == 0:
                grilla[y][x] = not grilla[y][x]


def _escribir_info_formato(grilla, funcion, dim, mascara):
    # L=01, M=00, Q=11, H=10 -- siempre L aca (mas capacidad).
    datos = (0b01 << 3) | mascara
    resto = datos
    for _ in range(10):
        resto = (resto << 1) ^ ((resto >> 9) * 0x537)
    bits = (datos << 10 | resto) ^ 0x5412   # BCH(15,5) + mascara fija del estandar

    def _bit(i):
        return bool((bits >> i) & 1)

    for i in range(6):
        grilla[i][8] = _bit(i)
    grilla[7][8] = _bit(6)
    grilla[8][8] = _bit(7)
    grilla[8][7] = _bit(8)
    for i in range(9, 15):
        grilla[8][14 - i] = _bit(i)

    for i in range(8):
        grilla[8][dim - 1 - i] = _bit(i)
    for i in range(8, 15):
        grilla[dim - 15 + i][8] = _bit(i)
    grilla[dim - 8][8] = True   # siempre oscuro


def _matriz(texto: str):
    datos = texto.encode("utf-8")
    version, dim, datos_cw, ecc_cw = _elegir_version(len(datos))
    codewords = _codewords_datos(datos, datos_cw)
    codewords += list(_ecc_rs(bytes(codewords), ecc_cw))

    bits = [(cw >> i) & 1 for cw in codewords for i in range(7, -1, -1)]

    grilla = [[False] * dim for _ in range(dim)]
    funcion = [[False] * dim for _ in range(dim)]
    _dibujar_patrones_fijos(grilla, funcion, version, dim)
    _colocar_datos(grilla, funcion, dim, bits)
    _aplicar_mascara_0(grilla, funcion, dim)
    _escribir_info_formato(grilla, funcion, dim, 0)
    return grilla


# -- PNG crudo, sin Pillow ----------------------------------------------------

def _chunk_png(tipo: bytes, datos: bytes) -> bytes:
    return (struct.pack(">I", len(datos)) + tipo + datos
           + struct.pack(">I", zlib.crc32(tipo + datos)))


def _png_desde_matriz(matriz, escala=8, margen=4) -> bytes:
    """Escala de grises de 8 bits, un pixel = un modulo * escala, con el
    margen de silencio (4 modulos, el minimo del estandar) en blanco."""
    dim = len(matriz)
    lado_mod = dim + margen * 2
    lado_px = lado_mod * escala

    filas = bytearray()
    for ym in range(lado_mod):
        fila_mod = []
        for xm in range(lado_mod):
            dentro = margen <= xm < margen + dim and margen <= ym < margen + dim
            oscuro = dentro and matriz[ym - margen][xm - margen]
            fila_mod.append(0 if oscuro else 255)
        fila_px = bytearray()
        for v in fila_mod:
            fila_px.extend((v,) * escala)
        for _ in range(escala):
            filas.append(0)          # filtro "None" por scanline
            filas.extend(fila_px)

    ihdr = struct.pack(">IIBBBBB", lado_px, lado_px, 8, 0, 0, 0, 0)
    comprimido = zlib.compress(bytes(filas), 9)
    return (b"\x89PNG\r\n\x1a\n" + _chunk_png(b"IHDR", ihdr)
           + _chunk_png(b"IDAT", comprimido) + _chunk_png(b"IEND", b""))


def generar_png(texto: str, escala: int = 8) -> bytes:
    """-> bytes de un .png con el QR de `texto`. Unico punto de entrada
    publico -- servidor.py lo llama para /api/lan/qr."""
    return _png_desde_matriz(_matriz(texto), escala=escala)
