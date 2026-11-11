# -*- coding: utf-8 -*-
"""progreso_union.py

Progreso de la union en curso (SPEC 20): fase, bytes hechos de totales,
pasada de reparacion, estado del freno y ritmo medido, publicados en
estado\\union.json para que la pestana Grabaciones pueda decir cuanto falta
para que un .mp4 quede terminado.

Mismo criterio que hace seguros a mouflon.py, chaturbate.py y
cerrojo_union.py: protocolo puro, importable desde cualquier lado sin
efecto alguno. No abre hilos, no construye nada al importar, no tiene
estado de modulo mutable, solo libreria estandar -- lo importan por igual
detectar_m3u8.py (que no se compila) y los dos grabadores (que si van al
binario).

Las fases son "copia" (la copia al escenario rapido de SPEC 12) y "remux"
(el ffmpeg, una vez por pasada de reparacion). SPEC 22 retiro la tercera,
"mezcla": existia porque una grabacion de chaturbate se unia dos veces y
despues se mezclaba con un ffmpeg propio, y ese ffmpeg era el unico que no
pasaba por unir_sesion(). Ahora las dos pistas entran en la misma pasada,
asi que esa fase no tiene publicador -- y una fase que nadie publica es
una mentira en el docstring.

UN solo registro en el archivo, no una lista: SPEC 19 ya garantiza una
union a la vez en toda la instalacion (el turno de cerrojo_union.py). Y
desde SPEC 22 cada union tiene **un solo escritor**: el proceso que corre
unir_sesion() y nadie mas -- el detector en proceso, o el subproceso
`--unir` del directo o de chaturbate. Antes el de chaturbate eran dos (el
subproceso para los remux y el grabador para la mezcla), y el argumento de
"nunca dos a la vez" habia que sostenerlo con eso en contra.
Este modulo se apoya en esa garantia y no agrega ninguna propia.

Esto es TELEMETRIA, no parte de la union: si escribir falla (disco lleno,
permisos, la carpeta no existe) se traga el error y se sigue uniendo. Sin
union.json la interfaz se ve como antes de este spec y el .mp4 sale igual.
"""

import json
import os
import threading
import time

ARCHIVO = "union.json"

NOMBRE_ENTORNO_SITIO = "NIMBO_UNION_SITIO"

# Cada cuanto se reescribe union.json. 2s es el mismo ritmo con el que ya
# late todo lo demas (INTERVALO_ESTADO de puente.py y de los dos
# grabadores) y el mismo con el que _Freno.paso() ya consulta
# hay_grabaciones(), asi que el informe viaja gratis dentro de una
# comprobacion que ya se hacia.
INTERVALO_UNION = 2.0

# Ventana sobre la que se promedia el ritmo para el ETA. Mas corta y el
# numero salta con cada hipo del disco; mas larga y tarda en reflejar que
# el freno se acaba de soltar (o de poner), que es justo cuando el ETA
# cambia de verdad.
VENTANA_RITMO = 10.0

# Default del campo "sitio" cuando nadie puso NIMBO_UNION_SITIO: el
# detector y el camino directo, que son los dos caminos de stripchat.
SITIO_DEFECTO = "stripchat"


def sitio_del_entorno() -> str:
    """El sitio de la union en curso segun NIMBO_UNION_SITIO.

    La pone quien lanza el subproceso `--unir`, igual que SPEC 19 pone
    NIMBO_UNION_TURNO. Solo chaturbate necesita ponerla; ausente =
    SITIO_DEFECTO. Se lee en cada llamada (no al importar, como el
    _HEREDADO de cerrojo_union.py) porque el detector en proceso puede
    unir sesiones de stripchat durante toda su vida sin que nadie toque
    la variable, y el valor tiene que seguir siendo el del momento.
    """
    return (os.environ.get(NOMBRE_ENTORNO_SITIO) or "").strip().lower() \
        or SITIO_DEFECTO


class Informe:
    """Publica el progreso de UNA union en estado\\union.json.

    Un solo registro alcanza: SPEC 19 garantiza una union a la vez en toda
    la instalacion. Si escribir falla (disco lleno, permisos), se traga el
    error y se sigue uniendo: esto es telemetria, no parte de la union.

    Uso tipico (ver detectar_m3u8.py:unir_sesion):

        informe = Informe(DIR_ESTADO, etiqueta, archivo=basename(final))
        try:
            informe.fase("copia", total_origen)
            ...                      # informe.avance(n) desde _Freno.paso
            informe.fase("remux", total, intento, MAX_REPARACIONES)
            ...
        finally:
            informe.cerrar()
    """

    def __init__(self, dir_estado: str, etiqueta: str, sitio: str = "",
                 archivo: str = ""):
        self.ruta = os.path.join(dir_estado or ".", ARCHIVO)
        self.etiqueta = etiqueta or "?"
        self.sitio = (sitio or "").strip().lower() or sitio_del_entorno()
        # "" durante la fase "copia", que corre antes de que el .mp4
        # exista. Es la clave con la que grabaciones.listar() encuentra la
        # parte que se esta armando.
        self.archivo = archivo or ""
        self.inicio = time.time()

        self._lock = threading.Lock()
        self._fase = ""
        self._intento = 0
        self._intentos_max = 0
        self._bytes_hechos = 0
        self._bytes_total = 0
        self._frenado = False
        self._inicio_fase = self.inicio
        self._escrito = 0.0            # time.monotonic del ultimo write
        # Ventana de muestras (monotonic, bytes acumulados de ESTA fase)
        # para el ritmo. Se vacia en cada fase: los bytes de la copia no
        # dicen nada del ritmo del remux.
        self._muestras = []

    # -- API ---------------------------------------------------------------

    def fase(self, nombre: str, bytes_total: int, intento: int = 0,
             intentos_max: int = 0) -> None:
        """Arranca una fase nueva. Reinicia bytes_hechos y el ritmo.

        Se llama tambien en cada reintento de reparacion, que es donde el
        .mp4 se borra y vuelve a crecer desde cero -- de ahi que el `%`
        vuelva a empezar sin que el banner desaparezca.
        """
        with self._lock:
            self._fase = nombre
            self._bytes_total = max(int(bytes_total or 0), 0)
            self._intento = int(intento or 0)
            self._intentos_max = int(intentos_max or 0)
            self._bytes_hechos = 0
            self._inicio_fase = time.time()
            self._muestras = []
        self._publicar()

    def reiniciar_medicion(self) -> None:
        """Vuelve bytes_hechos a 0 y vacia la ventana del ritmo, SIN tocar
        la fase ni el intento.

        Lo llama _Freno al construirse (detectar_m3u8.py): cada pasada de
        _alimentar() vuelve a leer la sesion entera desde el principio, asi
        que arrastrar el acumulado de la pasada anterior daria un `%` por
        encima de 100 y un ritmo promediado entre dos pasadas distintas.
        Dentro de UNA pasada, bytes_hechos sigue creciendo monotono.
        """
        with self._lock:
            self._bytes_hechos = 0
            self._muestras = []

    def archivo_destino(self, archivo: str) -> None:
        """El basename del .mp4 que se esta escribiendo, si cambio.

        unir_sesion() lo sabe desde el principio, pero un reintento de
        reparacion no lo mueve y la fase "copia" corre antes de que exista
        -- esto queda para quien tenga que corregirlo a mitad de camino sin
        rearmar el Informe entero.
        """
        with self._lock:
            self.archivo = archivo or ""
        self._publicar()

    def avance(self, n: int, frenado: bool = False) -> None:
        """Suma n bytes hechos y publica, como mucho cada INTERVALO_UNION.

        `n` es el ACUMULADO de bytes que lleva la fase, no un delta: quien
        llama (_Freno.paso) ya lleva su propio contador y pasarselo entero
        evita que un informe perdido desalinee la cuenta para siempre.
        """
        with self._lock:
            self._bytes_hechos = max(int(n or 0), 0)
            self._frenado = bool(frenado)
            ahora = time.monotonic()
            self._muestras.append((ahora, self._bytes_hechos))
            # La ventana se recorta por tiempo, no por cantidad: con el
            # freno puesto llegan ~2 muestras por segundo y sin freno
            # muchas mas, y lo que importa es siempre el mismo tramo de
            # VENTANA_RITMO segundos.
            corte = ahora - VENTANA_RITMO
            while len(self._muestras) > 2 and self._muestras[0][0] < corte:
                self._muestras.pop(0)
            if ahora - self._escrito < INTERVALO_UNION:
                return
            self._escrito = ahora
        self._publicar()

    def cerrar(self) -> None:
        """Deja el archivo con activa: null. Se llama en el `finally` de
        la union, salga bien o mal: un union.json con una union que ya
        termino haria que el banner quedara pegado hasta que el lector lo
        descartara por frescura."""
        self._escribir({"generado": time.time(), "activa": None})

    # -- interno -----------------------------------------------------------

    def _ritmo_bps(self) -> float:
        """Bytes por segundo medidos sobre la ventana, 0 si no alcanza.

        Medido y no teorico a RITMO_UNION a proposito: el freno se pone y
        se suelta solo segun si hay emisiones grabandose, asi que un ETA
        calculado a 25 MB/s miente por un factor grande justo cuando nadie
        graba (que es la mitad del tiempo).
        """
        if len(self._muestras) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._muestras[0], self._muestras[-1]
        if t1 <= t0 or b1 <= b0:
            return 0.0
        return (b1 - b0) / (t1 - t0)

    def _publicar(self) -> None:
        with self._lock:
            activa = {
                "etiqueta": self.etiqueta,
                "archivo": self.archivo,
                "sitio": self.sitio,
                "fase": self._fase,
                "intento": self._intento,
                "intentos_max": self._intentos_max,
                "bytes_hechos": self._bytes_hechos,
                "bytes_total": self._bytes_total,
                "frenado": self._frenado,
                "ritmo_bps": self._ritmo_bps(),
                "inicio": self.inicio,
                "inicio_fase": self._inicio_fase,
            }
        self._escribir({"generado": time.time(), "activa": activa})

    def _escribir(self, datos: dict) -> None:
        """Atomico (.part + os.replace), como todo lo que Nimbo escribe, y
        con TODOS los OSError tragados: esto no puede romper una union."""
        tmp = self.ruta + ".part"
        try:
            os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False)
            os.replace(tmp, self.ruta)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass


def leer(dir_estado: str, frescura: float = 15.0):
    """El bloque "activa" fresco, o None. Para quien quiera leerlo del
    lado del nucleo; nimbo\\grabaciones.py lee el JSON por su cuenta (no
    importa nada del nucleo, mismo criterio que nimbo\\mp4.py/vivo.py).

    La frescura es la red contra un proceso que murio a mitad y dejo el
    archivo con una union fantasma para siempre: con `generado` mas viejo
    que `frescura` se trata como si no hubiera union.
    """
    try:
        with open(os.path.join(dir_estado or ".", ARCHIVO),
                  encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None
    generado = datos.get("generado")
    if not isinstance(generado, (int, float)):
        return None
    if time.time() - generado > frescura:
        return None
    activa = datos.get("activa")
    return activa if isinstance(activa, dict) else None
