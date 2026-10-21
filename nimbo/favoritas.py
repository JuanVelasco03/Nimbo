"""favoritas.json: la lista de modelos que el usuario sigue, independiente
de grabar.txt (favoritas = a quien se puede agregar con un clic; grabar.txt
= a quien se esta grabando ahora). Mismo patron que lista.py: atomico,
sin cache, el archivo es la unica fuente de verdad.
"""
import json
import os
import threading


class Favoritas:
    def __init__(self, ruta: str):
        self.ruta = ruta
        self._lock = threading.Lock()

    def leer(self) -> list:
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
            return [str(n) for n in datos] if isinstance(datos, list) else []
        except (OSError, ValueError):
            return []

    def _escribir(self, nombres: list) -> None:
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        tmp = self.ruta + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(nombres, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.ruta)

    def agregar(self, nombre: str) -> bool:
        nombre = nombre.strip()
        with self._lock:
            actuales = self.leer()
            if any(n.lower() == nombre.lower() for n in actuales):
                return False
            actuales.append(nombre)
            self._escribir(actuales)
            return True

    def quitar(self, nombre: str) -> bool:
        nombre_l = nombre.strip().lower()
        with self._lock:
            actuales = self.leer()
            filtradas = [n for n in actuales if n.lower() != nombre_l]
            if len(filtradas) == len(actuales):
                return False
            self._escribir(filtradas)
            return True
