"""Arranca, para y vigila los dos procesos del motor de captura: el
detector (mitmdump con nucleo\\scripts\\detectar_m3u8.py + puente.py) y el
supervisor (lanzador_supervisor.py, que importa nucleo\\scripts\\
supervisor.py sin tocarlo).

No hace nada que los .bat del grabador original (F:\\grabador\\arrancar\\)
no hicieran ya -- solo automatiza el orden, captura el log en vez de
mostrar una consola, y hace la parada ordenada esperando a que termine en
vez de dejarla a mano:

  Arrancar: primero el detector (si no hay quien grabe, de nada sirve
  abrir paginas), despues el supervisor. Igual que "1-grabar.bat antes
  que 2-paginas.bat".

  Parar: primero el supervisor (cierra cada pestana y el Chromium
  limpio), despues el detector (que puede tardar varios minutos si hay
  una grabacion uniendose: el propio detector espera hasta 900s antes de
  darse por vencido, asi que aqui se le da ese margen o mas). Igual que
  "Ctrl+C en la 2, despues en la 1", solo que con el progreso visible.

La orden de parada va por stdin (ver puente.py y lanzador_supervisor.py):
por eso los dos se abren siempre con stdin=PIPE y ese extremo no se cierra
hasta que de verdad se quiere parar. Matar el proceso a lo bruto es el
ultimo recurso, no el camino normal.
"""

import os
import socket
import subprocess
import sys
import threading
import time

_AQUI = os.path.dirname(os.path.abspath(__file__))
PUENTE = os.path.join(_AQUI, "puente.py")
LANZADOR_SUPERVISOR = os.path.join(_AQUI, "lanzador_supervisor.py")

CREAR_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ESPERA_PUERTO = 20.0             # a que el proxy empiece a escuchar
ESPERA_CIERRE_SUPERVISOR = 60.0  # cerrar paginas + Chromium no deberia tardar
# El propio detector espera hasta 900s a que termine de unir antes de
# darse por vencido (hilo_final.join(900) + escritor.detener(), ~930s en
# total) -- aqui el margen es mayor, para no cortarlo justo antes de que
# el termine solo.
ESPERA_CIERRE_DETECTOR = 960.0
INTERVALO_VIGILANCIA = 2.0


def _parsear_proxy(proxy: str):
    host, _, puerto = (proxy or "").rpartition(":")
    return (host or "127.0.0.1"), int(puerto) if puerto.isdigit() else 8080


def _esperar_puerto(host, puerto, tope):
    fin = time.time() + tope
    while time.time() < fin:
        try:
            with socket.create_connection((host, puerto), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


class Motor:
    """Un Motor por aplicacion. ajustes es una instancia de
    ajustes.Ajustes; en_evento(tipo, datos) se llama con todo lo que
    convenga mostrar en la interfaz -- tipo "log" (una linea de alguno de
    los dos procesos) o "aviso" (algo que paso: arranco, se cerro solo,
    tarda en unir...). Si no se da, los eventos se pierden en silencio.
    """

    def __init__(self, ajustes, en_evento=None):
        self.ajustes = ajustes
        self._en_evento = en_evento or (lambda tipo, datos: None)
        self.detector = None       # subprocess.Popen o None
        self.supervisor = None
        self._lock = threading.Lock()
        self._parando = False
        threading.Thread(target=self._bucle_vigilancia, daemon=True,
                         name="nimbo-vigilancia").start()

    # -- estado --------------------------------------------------------

    @staticmethod
    def _vivo(proc):
        return proc is not None and proc.poll() is None

    def detector_vivo(self) -> bool:
        return self._vivo(self.detector)

    def supervisor_vivo(self) -> bool:
        return self._vivo(self.supervisor)

    def estado(self) -> dict:
        return {
            "detector_vivo": self.detector_vivo(),
            "supervisor_vivo": self.supervisor_vivo(),
            "parando": self._parando,
        }

    def _aviso(self, texto, **extra):
        datos = {"texto": texto}
        datos.update(extra)
        self._en_evento("aviso", datos)

    def _emitir_log(self, proceso, linea):
        self._en_evento("log", {"proceso": proceso, "linea": linea})

    # -- arranque --------------------------------------------------------

    def arrancar(self) -> bool:
        """Arranca lo que no este corriendo. True si arranco algo."""
        with self._lock:
            self._parando = False
            algo = False
            if not self.detector_vivo():
                self._arrancar_detector()
                algo = True
            if not self.supervisor_vivo():
                self._arrancar_supervisor()
                algo = True
            return algo

    def _leer_salida(self, proc, nombre):
        try:
            for linea in proc.stdout:
                self._emitir_log(nombre, linea.rstrip())
        except (OSError, ValueError):
            pass

    def _arrancar_detector(self):
        a = self.ajustes
        host, puerto = _parsear_proxy(a["proxy"])
        cmd = [a["mitmdump"], "-s", a.detector_py, "-s", PUENTE,
               "--listen-host", host, "--listen-port", str(puerto),
               "--set", "flow_detail=0"]
        env = dict(os.environ)
        env["NIMBO_ESTADO"] = a.dir_estado
        # Redirige streams\ y grabaciones\ a datos\ en vez de nucleo\: es
        # un mecanismo que detectar_m3u8.py ya trae de fabrica para probar
        # sin tocar los datos reales. No mueve grabar.txt ni activos.txt
        # -- esos siguen junto al script, en nucleo\scripts\.
        # setdefault, no asignacion directa: pruebas\prueba_motor.py fija
        # su propio M3U8_BASE aislado antes de llamar a Motor y no hay que
        # pisarlo con la carpeta real.
        env.setdefault("M3U8_BASE", a.dir_datos)
        self._aviso("arrancando el detector (mitmdump)...")
        self.detector = subprocess.Popen(
            cmd, cwd=a.scripts, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=CREAR_SIN_VENTANA,
        )
        threading.Thread(target=self._leer_salida,
                         args=(self.detector, "detector"), daemon=True).start()

        if _esperar_puerto(host, puerto, ESPERA_PUERTO):
            self._aviso("detector listo, escuchando en %s:%d" % (host, puerto))
        else:
            self._aviso("el detector no abrio el puerto %d a tiempo -- "
                       "revisa el log" % puerto, nivel="error")

    def _arrancar_supervisor(self):
        a = self.ajustes
        env = dict(os.environ)
        env["NIMBO_SCRIPTS"] = a.scripts
        env["NIMBO_ESTADO"] = a.dir_estado
        env["GRAB_PROXY"] = a["proxy"]
        env["GRAB_PERFIL"] = a.perfil
        env["GRAB_SITIO"] = a["sitio"]
        env["NIMBO_VER"] = "1" if a["ver_navegador"] else ""
        cmd = [a["python"], LANZADOR_SUPERVISOR]
        self._aviso("arrancando el supervisor (paginas)...")
        self.supervisor = subprocess.Popen(
            cmd, cwd=a.scripts, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=CREAR_SIN_VENTANA,
        )
        threading.Thread(target=self._leer_salida,
                         args=(self.supervisor, "supervisor"), daemon=True).start()

    # -- parada ordenada --------------------------------------------------

    def _mandar_parar(self, proc):
        try:
            proc.stdin.write("parar\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass    # el stdin ya no sirve: seguramente ya esta muriendo

    def _esperar_salida(self, proc, tope):
        try:
            proc.wait(timeout=tope)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _matar(self, proc):
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass

    def parar(self, espera_supervisor=ESPERA_CIERRE_SUPERVISOR,
              espera_detector=ESPERA_CIERRE_DETECTOR):
        """Bloquea hasta que los dos terminaron (o se forzaron). Llamar
        desde un hilo aparte si no se quiere congelar quien lo pida --
        el cierre del detector puede tardar minutos de verdad."""
        with self._lock:
            if self._parando:
                return
            self._parando = True
        try:
            if self.supervisor_vivo():
                self._aviso("cerrando las paginas...")
                self._mandar_parar(self.supervisor)
                if self._esperar_salida(self.supervisor, espera_supervisor):
                    self._aviso("paginas cerradas")
                else:
                    self._aviso("el supervisor no cerro a tiempo -- lo fuerzo",
                               nivel="error")
                    self._matar(self.supervisor)
            # Se suelta la referencia aqui, no solo cuando muere sola: si no,
            # el proximo paso de _bucle_vigilancia ve un Popen ya terminado
            # y lo reporta como "se cerro solo", aunque el cierre fue el que
            # pedimos nosotros mismos arriba.
            self.supervisor = None

            if self.detector_vivo():
                self._aviso(
                    "cerrando el detector -- si hay una grabacion "
                    "uniendose puede tardar varios minutos; no apagues "
                    "el PC")
                self._mandar_parar(self.detector)
                if self._esperar_salida(self.detector, espera_detector):
                    self._aviso("detector cerrado, todo guardado")
                else:
                    self._aviso(
                        "el detector no cerro a tiempo -- lo fuerzo (si "
                        "quedo una grabacion sin unir, se rehace con "
                        "--unir --forzar)", nivel="error")
                    self._matar(self.detector)
            self.detector = None
        finally:
            with self._lock:
                self._parando = False

    # -- vigilancia --------------------------------------------------------

    def _bucle_vigilancia(self):
        while True:
            time.sleep(INTERVALO_VIGILANCIA)
            with self._lock:
                if self._parando:
                    continue
            if self.detector is not None and not self.detector_vivo():
                self._aviso("el detector se cerro solo (codigo %s)" %
                           self.detector.returncode,
                           proceso="detector", tipo="murio")
                self.detector = None
            if self.supervisor is not None and not self.supervisor_vivo():
                self._aviso("el supervisor se cerro solo (codigo %s)" %
                           self.supervisor.returncode,
                           proceso="supervisor", tipo="murio")
                self.supervisor = None
