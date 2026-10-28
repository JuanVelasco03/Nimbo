"""Ed25519 con solo libreria estandar: firmar, verificar y generar claves.

Esta aca porque la licencia (licencia.py) tiene que **verificar** una
firma, y la verificacion es lo unico que corre del lado del usuario. Se
hace a mano en vez de agregar `cryptography` a requirements.txt por dos
motivos:

1. `nimbo\\` depende de stdlib + websockets, y eso es parte del diseno
   (ver CLAUDE.md: el interprete de la ventana solo necesita stdlib +
   pystray/Pillow). Una dependencia con `.pyd` compilado obliga a cuadrar
   ruedas con el Python embebido del instalador.
2. Verificar una firma es ~80 lineas de aritmetica modular y se hace una
   vez por arranque. No hay nada que optimizar.

Es la implementacion de referencia del **RFC 8032** (seccion 6, con
coordenadas extendidas para no invertir en cada paso), traducida pero no
reinventada: el algoritmo no se toca.

`firmar()`, `generar()` y `publica_de()` **no se usan del lado del
usuario**: son para la herramienta que emite las claves
(empaquetado\\licencia\\claves.py) y para las pruebas. El que corre en la
instalacion es `verificar()`, y nada mas.
"""
import hashlib
import os

# -- la curva (RFC 8032, 5.1) ---------------------------------------------

P = 2**255 - 19                 # el primo del cuerpo
ORDEN = 2**252 + 27742317777372353535851937790883648493   # orden del grupo


def _inv(x):
    return pow(x, P - 2, P)


D = -121665 * _inv(121666) % P
RAIZ_MENOS_1 = pow(2, (P - 1) // 4, P)


def _recuperar_x(y, signo):
    """La x que corresponde a una y comprimida, o None si no hay punto."""
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if signo else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * RAIZ_MENOS_1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != signo:
        x = P - x
    return x


_G_Y = 4 * _inv(5) % P
_G_X = _recuperar_x(_G_Y, 0)
# Punto base, en coordenadas extendidas (X, Y, Z, T) con x=X/Z, y=Y/Z.
G = (_G_X, _G_Y, 1, _G_X * _G_Y % P)


def _sumar(p1, p2):
    a = (p1[1] - p1[0]) * (p2[1] - p2[0]) % P
    b = (p1[1] + p1[0]) * (p2[1] + p2[0]) % P
    c = 2 * p1[3] * p2[3] * D % P
    d = 2 * p1[2] * p2[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _multiplicar(k, punto):
    """Doblar y sumar. No es de tiempo constante -- no hace falta: aca
    solo se multiplica por escalares publicos (al verificar)."""
    q = (0, 1, 1, 0)
    while k > 0:
        if k & 1:
            q = _sumar(q, punto)
        punto = _sumar(punto, punto)
        k >>= 1
    return q


def _iguales(p1, p2):
    if (p1[0] * p2[2] - p2[0] * p1[2]) % P != 0:
        return False
    return (p1[1] * p2[2] - p2[1] * p1[2]) % P == 0


def _comprimir(punto):
    z = _inv(punto[2])
    x = punto[0] * z % P
    y = punto[1] * z % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _descomprimir(datos):
    if len(datos) != 32:
        return None
    y = int.from_bytes(datos, "little")
    signo = y >> 255
    y &= (1 << 255) - 1
    x = _recuperar_x(y, signo)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _sha512_mod(datos):
    return int.from_bytes(hashlib.sha512(datos).digest(), "little") % ORDEN


def _expandir(privada):
    h = hashlib.sha512(privada).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


# -- lo que se usa ---------------------------------------------------------

def verificar(publica: bytes, mensaje: bytes, firma: bytes) -> bool:
    """True solo si la firma es de esa clave y ese mensaje exacto.

    Devuelve False ante cualquier cosa mal formada en vez de levantar:
    quien llama esta decidiendo "¿vale esta licencia?", y una excepcion
    ahi es un camino mas para equivocarse.
    """
    if not isinstance(publica, bytes) or len(publica) != 32:
        return False
    if not isinstance(firma, bytes) or len(firma) != 64:
        return False
    a = _descomprimir(publica)
    if a is None:
        return False
    r_bytes = firma[:32]
    r = _descomprimir(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(firma[32:], "little")
    if s >= ORDEN:
        return False
    h = _sha512_mod(r_bytes + publica + mensaje)
    return _iguales(_multiplicar(s, G), _sumar(r, _multiplicar(h, a)))


def publica_de(privada: bytes) -> bytes:
    a, _ = _expandir(privada)
    return _comprimir(_multiplicar(a, G))


def firmar(privada: bytes, mensaje: bytes) -> bytes:
    """Solo para la herramienta que emite claves -- no corre en la
    instalacion del usuario."""
    a, prefijo = _expandir(privada)
    publica = _comprimir(_multiplicar(a, G))
    r = _sha512_mod(prefijo + mensaje)
    r_punto = _comprimir(_multiplicar(r, G))
    h = _sha512_mod(r_punto + publica + mensaje)
    s = (r + h * a) % ORDEN
    return r_punto + int.to_bytes(s, 32, "little")


def generar():
    """(privada, publica) nuevas. La privada son 32 bytes al azar."""
    privada = os.urandom(32)
    return privada, publica_de(privada)
