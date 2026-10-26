"""Addon de mitmproxy. Se carga JUNTO al detector, nunca en su lugar:

    mitmdump -s detectar_m3u8.py -s puente.py

Esto no es parte del motor de captura copiado (nucleo\\scripts\\): vive en
Nimbo y lo mira desde fuera, sin cambiarle una linea. Hace tres cosas:

  1. Publica en JSON el estado real de la grabacion (estado/detector.json),
     leyendo directamente los objetos Stream del Detector -- no la tabla de
     texto de activos.txt, que tiene columnas de ancho variable y pierde la
     senal de "privado" si dejan de llegar segmentos.
  2. Reenvia los eventos del logger "detector" a un .jsonl, ya separados en
     prefijo/mensaje (sin necesitar regex).
  3. Ofrece una parada ordenada: una orden por stdin dispara el apagado
     normal de mitmproxy (el mismo que hace Ctrl+C), que es el que corre el
     hook done() del detector y une las grabaciones en curso.

Como mitmdump trae su propio Python empaquetado (3.14 en esta instalacion,
visto con --version), este archivo usa SOLO libreria estandar: nada de lo
que tenga Nimbo en su Python 3.12 es alcanzable desde aqui.

Como se accede al Detector vivo (confirmado leyendo la fuente real de
mitmproxy 12.2.3, mitmproxy/addonmanager.py):
  - Cada script de -s se carga como un modulo con su propio namespace; el
    modulo del detector define "addons = [Detector()]" a nivel de modulo.
  - AddonManager.register() recorre ese modulo CON sus sub-addons
    (traverse(), que sigue el atributo .addons) y los registra en
    self.lookup por "nombre = atributo .name, o si no hay, la clase en
    minusculas". El Detector no define .name, asi que su clave es
    "detector".
  - Por eso ctx.master.addons.get("detector") devuelve la instancia real,
    sin tener que importar detectar_m3u8.py (que si se importara de
    cualquier otra forma, construiria un Detector SEGUNDO y por su cuenta:
    ver advertencia en el modulo original sobre "addons = [Detector()]" en
    la rama de import).
  - ctx.master.shutdown() ya es thread-safe de fabrica (usa
    event_loop.call_soon_threadsafe por dentro, visto en mitmproxy/master.py):
    se puede llamar directo desde un hilo nuestro, sin reenviarlo al loop.

Si cualquiera de las tres cosas de arriba falla, el detector sigue grabando
igual: todo esto corre en hilos aparte y con sus propios try/except; el
peor caso es que Nimbo se quede ciego un rato, nunca que se pierda nada.
"""

import json
import os
import sys
import threading
import time

# -- donde escribir ---------------------------------------------------------
# Nimbo le pasa la carpeta de estado por variable de entorno al lanzar
# mitmdump (ver nimbo/motor.py). Si falta -- por ejemplo, al probar este
# archivo suelto con otro -s -- se calcula sola asumiendo la instalacion
# tipica: este archivo vive en <raiz>\nimbo\puente.py, el estado en
# <raiz>\estado\.
_AQUI = os.path.dirname(os.path.abspath(__file__))
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(
    os.path.dirname(_AQUI), "estado")
ARCHIVO_ESTADO = os.path.join(DIR_ESTADO, "detector.json")
ARCHIVO_EVENTOS = os.path.join(DIR_ESTADO, "eventos_detector.jsonl")

# Igual que INTERVALO_VIGILANTE del detector (su linea ~198): no tiene
# sentido mirar mas seguido que lo que el mismo cambia.
INTERVALO_ESTADO = 2.0
ESPERA_DETECTOR = 0.5   # reintento mientras el otro -s todavia no cargo
# Mismo tope que TOPE_BYTES_BARRIDO de registro.py: un .jsonl entero
# siempre entra en un barrido completo.
MAX_EVENTOS = 8 * 1024 * 1024


def _log(mensaje):
    """Mensajes propios de este addon. Van a stderr sin pasar por el
    logging de mitmproxy: asi no dependen de como este configurado el
    logger raiz (nivel, formato) y motor.py los ve igual que todo lo demas
    (captura el stderr entero del proceso)."""
    print("[NIMBO] " + mensaje, file=sys.stderr, flush=True)


def _escribir_atomico(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta)          # nunca se lee un archivo a medio escribir


class _Eventos:
    """logging.Handler minimo: cada registro del logger "detector" se
    vuelca como una linea JSON. Los mensajes del detector usan argumentos
    diferidos (log.info("[X] %s", y)) y el prefijo [XXX] siempre al
    principio, asi que separarlo es cortar por el primer "]", sin regex.
    """

    def __init__(self, ruta):
        self.ruta = ruta
        self._lock = threading.Lock()
        self.level = 0
        os.makedirs(os.path.dirname(ruta), exist_ok=True)

    def handle(self, record):
        try:
            msg = record.getMessage()
            prefijo, resto = "", msg
            if msg.startswith("["):
                fin = msg.find("]")
                if fin > 0:
                    prefijo, resto = msg[1:fin], msg[fin + 1:].strip()
            fila = {"t": record.created, "nivel": record.levelname,
                    "prefijo": prefijo, "mensaje": resto}
            linea = json.dumps(fila, ensure_ascii=False)
            with self._lock:
                try:
                    if os.path.getsize(self.ruta) > MAX_EVENTOS:
                        os.replace(self.ruta, self.ruta + ".1")
                except OSError:
                    pass
                with open(self.ruta, "a", encoding="utf-8") as f:
                    f.write(linea + "\n")
        except OSError:
            pass          # perder un evento no es motivo para tirar el hilo

    # logging.Logger llama a estos nombres; con handle() alcanza para
    # nuestro uso (no hace falta Formatter ni setLevel reales).
    def acquire(self):
        pass

    def release(self):
        pass

    def createLock(self):
        pass


class Puente:
    """Mira al Detector desde fuera; no cambia nada de lo que hace."""

    def __init__(self):
        self._parar = threading.Event()
        self._hilo_estado = None
        self._hilo_stdin = None
        self._manejador = None

    # -- ciclo de vida de mitmproxy ------------------------------------

    def running(self):
        # running() llega cuando mitmproxy ya arranco del todo: ctx.master
        # existe y el otro addon (el detector) ya construyo sus hilos --
        # es el mismo momento en que el propio detector arranca su _Tunel
        # (ver detectar_m3u8.py, metodo running() del Detector).
        os.makedirs(DIR_ESTADO, exist_ok=True)

        import logging
        if self._manejador is None:
            self._manejador = _Eventos(ARCHIVO_EVENTOS)
            logging.getLogger("detector").addHandler(self._manejador)

        if self._hilo_estado is None:
            self._hilo_estado = threading.Thread(
                target=self._bucle_estado, name="nimbo-estado",
                daemon=True)
            self._hilo_estado.start()

        # Sin stdin (p.ej. consola sin pipe) no se arranca el hilo: mejor
        # no tener parada remota que pararse solo por un EOF inesperado.
        if self._hilo_stdin is None and sys.stdin is not None:
            self._hilo_stdin = threading.Thread(
                target=self._bucle_stdin, name="nimbo-stdin",
                daemon=True)
            self._hilo_stdin.start()

        _log("puente activo -- estado en " + ARCHIVO_ESTADO)

    def done(self):
        self._parar.set()
        if self._manejador is not None:
            import logging
            logging.getLogger("detector").removeHandler(self._manejador)
            self._manejador = None

    # -- estado en JSON ---------------------------------------------------

    def _bucle_estado(self):
        from mitmproxy import ctx
        det = None
        while not self._parar.is_set():
            if det is None:
                det = ctx.master.addons.get("detector")
                if det is None:
                    self._parar.wait(ESPERA_DETECTOR)
                    continue
            try:
                self._publicar(det)
            except Exception as e:
                # Esto es solo un espejo del estado real: perder un ciclo
                # no afecta a la grabacion. Nunca tirar el hilo por esto.
                _log("fallo publicando estado (sigue intentando): %r" % (e,))
            self._parar.wait(INTERVALO_ESTADO)

    def _sesiones_de(self, st):
        """Una entrada por variante actualmente abierta en disco (st.variantes),
        para el reproductor en vivo (ver vivo.py). Lista completa, no solo
        st.elegida: _cambiar() de detectar_m3u8.py fija st.elegida ANTES de
        que la Variante nueva exista en st.variantes, asi que hay una
        ventana en la que ese puntero no resuelve a nada -- el frontend lo
        tolera, pero igual necesita poder ver la variante vieja mientras
        tanto. Try/except propio: un cambio futuro en la dataclass Variante
        no debe llevarse puesto el resto de detector.json."""
        try:
            return [{
                "variante": nombre, "calidad": v.calidad, "codec": v.codec,
                "sesion": v.sesion, "carpeta": v.carpeta, "init": v.init,
                "segmentos": v.segmentos, "bytes": v.bytes, "inicio": v.inicio,
            } for nombre, v in st.variantes.items()]
        except Exception:
            return []

    def _publicar(self, det):
        ahora = time.time()
        cfg = det.seleccion.actual()
        filas = []
        with det.lock:
            for sid, st in det.streams.items():
                vivas = st.vivas(ahora)
                if st.grabando:
                    estado = "grabando"
                elif not vivas:
                    estado = "inactivo"
                elif st.privado:
                    estado = "privado"
                elif st.es_preview:
                    estado = "preview"
                else:
                    estado = "detectado"
                filas.append({
                    "id": sid,
                    "modelo": st.modelo,
                    "mejor": st.mejor_posible(cfg) or "",
                    "elegida": st.elegida,
                    "variantes": sorted(vivas),
                    "estado": estado,
                    "grabando": st.grabando,
                    "privado": st.privado,
                    "privado_desde": st.privado_desde or None,
                    "inicio": st.inicio or None,
                    "ultimo_visto": st.ultimo_visto,
                    # el detector ya lo mantiene (detectar_m3u8.py:674,
                    # sellado en el vigilante de @inactividad) -- nadie lo
                    # publicaba. Es la señal que el frontend usa para
                    # distinguir "grabando" de "grabando pero sin bytes".
                    "ultimo_segmento": st.ultimo_segmento or None,
                    "segmentos": st.segmentos,
                    "bytes": st.bytes,
                    "sesiones": self._sesiones_de(st),
                })
            tunel = getattr(det, "tunel", None)
            pedidos_tunel = getattr(det, "_por_tunel", 0)

        datos = {
            "generado": ahora,
            "tunel_activo": tunel is not None,
            "pedidos_tunel": pedidos_tunel,
            "streams": filas,
        }
        # A diferencia de activos.txt (que el detector solo reescribe si
        # el contenido cambio), aqui escribimos SIEMPRE: el mtime de este
        # archivo es el latido que le dice a Nimbo que el puente sigue
        # vivo. Es un JSON chico cada 2s; no cuesta nada medible.
        _escribir_atomico(ARCHIVO_ESTADO, json.dumps(datos, ensure_ascii=False))

    # -- parada ordenada por stdin -----------------------------------------

    def _bucle_stdin(self):
        # Importante para quien lance este proceso (ver nimbo/motor.py):
        # hay que abrir el hijo con stdin=PIPE y NO cerrar ese extremo hasta
        # que de verdad se quiera parar. Si el proceso hereda un stdin ya
        # cerrado o invalido, esto ve un EOF inmediato y para mitmdump al
        # instante -- por diseno: un stdin roto es indistinguible de "el
        # padre se fue", y seguir grabando sin que nadie lo sepa es peor.
        from mitmproxy import ctx
        try:
            for linea in sys.stdin:
                if linea.strip().lower() == "parar":
                    break
            else:
                pass   # el for se agoto solo = EOF, cae al aviso de abajo
        except (OSError, ValueError):
            return
        _log("orden de parada recibida -- apagando mitmdump "
             "(puede tardar si hay grabaciones uniendose)")
        ctx.master.shutdown()


if __name__ == "__main__":
    print("Esto es un addon de mitmproxy, no se ejecuta solo:\n"
          "  mitmdump -s detectar_m3u8.py -s puente.py")
else:
    addons = [Puente()]
