"""Dueno de estado\\vivo_temporal.json: el "ver en vivo" de una modelo que
no se esta grabando agrega su nombre a grabar.txt mientras el modal esta
abierto y lo quita al cerrar. Esto es lo que hace esa operacion reversible.

La invariante que importa: **nunca quita de grabar.txt un nombre que no
haya agregado el mismo**. Si la modelo ya estaba en la lista (el usuario
la estaba grabando de antes, o la agrego por su cuenta mientras el modal
seguia abierto), abrir() no anota nada y cerrar() no toca grabar.txt --
es la unica proteccion real contra que una vista previa le corte una
grabacion que el usuario si queria.

Como con todo el estado de Nimbo, vivo_temporal.json se escribe atomico
(.part + os.replace)."""

import json
import os
import threading
import time

ARCHIVO = "vivo_temporal.json"
CADUCIDAD_S = 30.0   # sin latido en este tiempo, se da por cerrado solo
                      # (el modal manda un latido cada 5s mientras este
                      # abierto -- ver vista_previa.js)

_lock = threading.Lock()


def _ruta(dir_estado):
    return os.path.join(dir_estado, ARCHIVO)


def _leer(dir_estado) -> dict:
    try:
        with open(_ruta(dir_estado), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _escribir(dir_estado, datos) -> None:
    ruta = _ruta(dir_estado)
    tmp = ruta + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
        os.replace(tmp, ruta)
    except OSError:
        pass


def abrir(lista, dir_estado, nombre: str) -> dict:
    """-> {"temporal": bool, "ya_estaba": bool}. Si agrego el nombre a
    grabar.txt, lo anota (dueno = esta funcion); si ya estaba, no anota
    nada y esta vista nunca va a poder quitarlo."""
    with _lock:
        agregado = lista.agregar_modelo(nombre)
        datos = _leer(dir_estado)
        clave = nombre.strip().lower()
        if agregado:
            datos[clave] = {"desde": time.time(), "ultimo_latido": time.time()}
            _escribir(dir_estado, datos)
        return {"temporal": agregado, "ya_estaba": not agregado}


def latido(dir_estado, nombre: str) -> bool:
    """True si habia una entrada que refrescar (si no, el llamador no
    tiene nada que mantener vivo: o nunca fue temporal, o ya caduco)."""
    with _lock:
        datos = _leer(dir_estado)
        clave = nombre.strip().lower()
        if clave not in datos:
            return False
        datos[clave]["ultimo_latido"] = time.time()
        _escribir(dir_estado, datos)
        return True


def cerrar(lista, dir_estado, nombre: str) -> bool:
    """Quita de grabar.txt solo si esta anotado como propio. -> True si
    se quito algo."""
    with _lock:
        datos = _leer(dir_estado)
        clave = nombre.strip().lower()
        if clave not in datos:
            return False
        del datos[clave]
        _escribir(dir_estado, datos)
        lista.quitar_modelo(nombre)
        return True


def purgar(lista, dir_estado) -> int:
    """Cierra TODO lo anotado -- se llama al arrancar app.py, antes de
    motor.arrancar(), para que una corrida anterior que murio a mitad
    (Nimbo cerrado de un ctrl+c, por ejemplo) no deje una modelo pegada
    en grabar.txt para siempre. -> cuantas se cerraron."""
    with _lock:
        datos = _leer(dir_estado)
        if not datos:
            return 0
        for clave in list(datos):
            lista.quitar_modelo(clave)
        _escribir(dir_estado, {})
        return len(datos)


def cosechar(lista, dir_estado) -> int:
    """Cierra lo que lleve mas de CADUCIDAD_S sin latido -- la red de
    seguridad para cuando el navegador se cerro de golpe (crash, matar el
    proceso) y nunca llego a mandar el POST de cierre. -> cuantas se
    cerraron."""
    with _lock:
        datos = _leer(dir_estado)
        ahora = time.time()
        vencidas = [c for c, v in datos.items()
                    if ahora - v.get("ultimo_latido", 0) > CADUCIDAD_S]
        if not vencidas:
            return 0
        for clave in vencidas:
            lista.quitar_modelo(clave)
            del datos[clave]
        _escribir(dir_estado, datos)
        return len(vencidas)
