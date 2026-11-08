"""Notificaciones de Windows para las alertas de salud (SPEC 16).

Notifica solo en la transicion bien->mal de una clave (la primera vez que
se la ve) y, si el estado malo sigue vigente, una vez mas cada
ENFRIAMIENTO_S -- sin el enfriamiento serian dos globos por minuto con un
disco bajo umbral; sin la repeticion, perderse el unico globo significa
no enterarse nunca. No hay "resolver y volver a avisar antes del
enfriamiento": un estado que se resuelve y reaparece en la misma unidad
sigue atado al mismo `clave`, que es justo lo que evita que dos llamadas
a sincronizar() con distinto origen (_bucle_estado para disco/rendido/
degradado, _bucle_licencia para licencia) se pisen entre si.

Nada de esto se persiste: vive en RAM, en self._vistas. Tras un reinicio
de Nimbo, un estado malo que siga vigente vuelve a notificar una vez, que
es lo correcto (ver Decisiones del spec).
"""

import contextlib
import time

ENFRIAMIENTO_S = 1800   # 30 minutos

# tipo de alerta -> clave de preferencia en DEFECTOS. No es "notif_" + tipo
# a secas: el tipo "motor" (alerta "rendido") usa notif_rendido, nombrada
# por el escenario que ve el usuario, no por la seccion interna de estado
# que la genera.
_PREF = {
    "disco": "notif_disco",
    "degradado": "notif_degradado",
    "motor": "notif_rendido",
    "licencia": "notif_licencia",
}


class Notificador:
    """sink(mensaje, titulo) -- pystray.Icon.notify, o None si no hay
    bandeja. Nunca se llama directo: siempre envuelto en try/except, asi
    un backend de notificaciones ausente o roto no tira el bucle de
    estado que lo invoca."""

    def __init__(self, sink=None):
        self.sink = sink
        self._vistas = {}   # clave -> ultimo time.time() notificado

    def sincronizar(self, alertas, ajustes):
        ahora = time.time()
        for alerta in alertas:
            clave = alerta["clave"]
            pref = _PREF.get(alerta["tipo"])
            if pref and not ajustes[pref]:
                continue
            visto = self._vistas.get(clave)
            if visto is not None and ahora - visto < ENFRIAMIENTO_S:
                continue
            self._vistas[clave] = ahora
            self._notificar(alerta)

    def _notificar(self, alerta):
        if self.sink is None:
            return
        with contextlib.suppress(Exception):
            self.sink(alerta["texto"], alerta["titulo"])
