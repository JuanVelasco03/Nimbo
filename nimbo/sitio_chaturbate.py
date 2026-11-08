"""Consultas al sitio chaturbate: estado online, miniatura. SPEC 18 --
subclase de sitio.SitioBase, que es donde vive lo que de verdad se
comparte con stripchat (transporte, las dos caches con TTL, la LRU de
miniaturas). Ver sitio.py para esas piezas; aca solo lo especifico del
sitio nuevo.

Sin busqueda con autocompletado: a diferencia de
"/api/front/v4/models/search/suggestion" de stripchat, chaturbate no
expone (medido: no se encontro ninguno sin cuenta) un endpoint de
busqueda en vivo que junte nombre + estado online. buscar() devuelve
siempre [] -- Decisiones de SPEC 18: "si no lo expone, 'agregar modelo'
es un campo de texto simple, sin autocompletado". No es un error, es el
mismo "se degrada a nada" que ya usa Sitio.buscar() si stripchat rotara
su endpoint.

Sin VPN: chaturbate no pasa por el proxy de grabacion (fuera de alcance,
SPEC 18), asi que estado()/imagen() de aca nunca reciben vpn=True -- a
diferencia de Sitio, que sí lo necesita para las modelos marcadas @vpn=.
"""
import sys
import time

from sitio import SitioBase

_chaturbate_cache = {}


def _chaturbate(ruta_scripts):
    """Importa nucleo\\scripts\\chaturbate.py -- protocolo puro, igual de
    seguro de importar que mouflon.py (ver su docstring): sin
    'addons = [...]', sin estado de modulo mutable."""
    mod = _chaturbate_cache.get(ruta_scripts)
    if mod is None:
        if ruta_scripts not in sys.path:
            sys.path.insert(0, ruta_scripts)
        import chaturbate as mod
        _chaturbate_cache[ruta_scripts] = mod
    return mod


class Chaturbate(SitioBase):
    CLAVE_SITIO = "sitio_chaturbate"
    # jpeg.live.mmcdn.com: snapshot en vivo (chaturbate.url_snapshot).
    # thumb.live.mmcdn.com: miniatura de catalogo, mas chica -- tambien
    # confirmada en vivo (ver pruebas\sonda_chaturbate.py).
    HOSTS_IMAGEN_PERMITIDOS = frozenset({"jpeg.live.mmcdn.com", "thumb.live.mmcdn.com"})

    CACHE_ESTADO_SEGUNDOS = 20.0

    def __init__(self, ajustes):
        super().__init__(ajustes)

    # -- estado online -----------------------------------------------------

    def estado(self, nombre: str) -> dict:
        """-> {"estado": "public"/"?"/"http_..."/"error:...", "id": str|None}.
        Mismo shape que Sitio.estado (sin "vpn": chaturbate no lo usa)."""
        cb = _chaturbate(self.ajustes.scripts)
        info = cb.consultar_estado(nombre)
        return {"estado": info["estado"], "id": info["id"]}

    def estado_cacheado(self, nombre: str) -> dict:
        clave = nombre.strip().lower()
        ahora = time.time()
        with self._lock:
            visto = self._cache_estado.get(clave)
        if visto and ahora - visto[0] < self.CACHE_ESTADO_SEGUNDOS:
            return visto[1]
        resultado = self.estado(nombre)
        with self._lock:
            self._cache_estado[clave] = (ahora, resultado)
        return resultado

    # -- busqueda ------------------------------------------------------------

    def buscar(self, consulta: str, limite: int = 8) -> list:
        """-> [] siempre -- ver el docstring del modulo."""
        return []

    # -- miniaturas ------------------------------------------------------

    def url_snapshot(self, nombre: str) -> str:
        """La URL publica y estable del snapshot en vivo (sin cookies) --
        /api/sitio/imagen la pide con el Referer que hace falta (ver
        SitioBase.imagen), igual que con las de stripchat."""
        cb = _chaturbate(self.ajustes.scripts)
        return cb.url_snapshot(nombre)
