"""favoritas.json: la lista de modelos que el usuario sigue, independiente
de grabar.txt (favoritas = a quien se puede agregar con un clic; grabar.txt
= a quien se esta grabando ahora). Mismo patron que lista.py: atomico,
sin cache, el archivo es la unica fuente de verdad.

SPEC 18: cada favorita ahora es un objeto {"nombre": ..., "sitio": ...},
no un string suelto -- hace falta para no confundir dos modelos homonimas
de sitios distintos. leer() migra en silencio un elemento que todavia sea
un string de antes de este spec a {"nombre": elemento, "sitio":
"stripchat"}, mismo criterio que sesion.leer() ya usa para el archivo en
claro de antes de DPAPI: la proxima escritura (agregar()/quitar()) deja el
archivo entero en el formato nuevo.
"""
import json
import os
import threading


def _normalizar(elemento) -> dict | None:
    if isinstance(elemento, str):
        nombre = elemento.strip()
        return {"nombre": nombre, "sitio": "stripchat"} if nombre else None
    if isinstance(elemento, dict):
        nombre = str(elemento.get("nombre") or "").strip()
        if not nombre:
            return None
        sitio = str(elemento.get("sitio") or "stripchat").strip() or "stripchat"
        return {"nombre": nombre, "sitio": sitio}
    return None


class Favoritas:
    def __init__(self, ruta: str):
        self.ruta = ruta
        self._lock = threading.Lock()

    def leer(self) -> list:
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(datos, list):
            return []
        salida = []
        for elemento in datos:
            n = _normalizar(elemento)
            if n is not None:
                salida.append(n)
        return salida

    def _escribir(self, favoritas: list) -> None:
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        tmp = self.ruta + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(favoritas, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.ruta)

    def agregar(self, nombre: str, sitio: str = "stripchat") -> bool:
        nombre = nombre.strip()
        sitio = (sitio or "stripchat").strip() or "stripchat"
        with self._lock:
            actuales = self.leer()
            if any(f["nombre"].lower() == nombre.lower() and f["sitio"] == sitio
                   for f in actuales):
                return False
            actuales.append({"nombre": nombre, "sitio": sitio})
            self._escribir(actuales)
            return True

    def quitar(self, nombre: str, sitio: str = "stripchat") -> bool:
        nombre_l = nombre.strip().lower()
        sitio = (sitio or "stripchat").strip() or "stripchat"
        with self._lock:
            actuales = self.leer()
            filtradas = [f for f in actuales
                         if not (f["nombre"].lower() == nombre_l and f["sitio"] == sitio)]
            if len(filtradas) == len(actuales):
                return False
            self._escribir(filtradas)
            return True
