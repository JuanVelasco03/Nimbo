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

Y hay un tercer proceso que nadie arranca a mano: wireproxy.exe, el tunel
de la VPN, que el detector abre como hijo suyo (clase _Tunel en
detectar_m3u8.py). Ese SI se queda huerfano: si el detector muere sin
pasar por su done() -- se lo mata por tardar en unir, se cae, o se para
Nimbo a los pocos segundos de arrancarlo -- wireproxy sigue vivo y
escuchando el puerto del tunel. Y el siguiente detector, en vez de abrir
el suyo, lo ADOPTA (_Tunel.run() ve el puerto ocupado, se da por listo y
termina): queda un tunel que ya nadie vigila, con el .conf de la corrida
anterior, y que tampoco se cerrara en la proxima parada. Por eso aqui hay
un watchdog de verdad -- se busca el proceso por nombre + dueno del puerto
y se mira si su padre sigue vivo -- en vez de confiar en la autocuracion
de _Tunel, que solo cubre el caso de que se le muera el hijo propio.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

try:
    import ctypes
    from ctypes import wintypes
except (ImportError, ValueError):   # no es Windows: el watchdog no hace nada
    ctypes = None
    wintypes = None

# Solo para ajustes.compilado() (ver _cmd_directo). El resto de motor.py
# recibe el objeto Ajustes ya armado, no lo carga el mismo.
import ajustes as ajustes_mod

_AQUI = os.path.dirname(os.path.abspath(__file__))
PUENTE = os.path.join(_AQUI, "puente.py")
LANZADOR_SUPERVISOR = os.path.join(_AQUI, "lanzador_supervisor.py")
LANZADOR_DIRECTO = os.path.join(_AQUI, "lanzador_directo.py")

CREAR_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ESPERA_PUERTO = 20.0             # a que el proxy empiece a escuchar
ESPERA_CIERRE_SUPERVISOR = 60.0  # cerrar paginas + Chromium no deberia tardar
# El propio detector espera hasta 900s a que termine de unir antes de
# darse por vencido (hilo_final.join(900) + escritor.detener(), ~930s en
# total) -- aqui el margen es mayor, para no cortarlo justo antes de que
# el termine solo.
ESPERA_CIERRE_DETECTOR = 960.0
# El camino directo delega la union a "detectar_m3u8.py --unir" como
# subproceso con su propio timeout de 1200s (grabador_directo.py:
# _unir_en_fondo); varias sesiones la corren en paralelo sobre el mismo
# pool, asi que el margen no se multiplica por cuantas haya.
ESPERA_CIERRE_DIRECTO = 1260.0
INTERVALO_VIGILANCIA = 2.0
# El watchdog del tunel no necesita ir al ritmo de los procesos propios:
# un wireproxy huerfano no hace dano por estar un rato de mas, lo que hace
# dano es que lo adopte el detector siguiente, y eso se corta al arrancar.
INTERVALO_TUNEL = 30.0
# Backoff del relanzado: un hijo que se cae suele volver al primer intento
# (mitmdump que no pudo abrir el puerto, Chromium con el perfil trabado);
# si no vuelve en tres, algo esta roto de verdad y hace falta un humano.
ESPERA_REINTENTO = (5.0, 15.0, 60.0)
# Rendido no es rendirse del todo: se avisa fuerte y se sigue probando a
# este ritmo. Una caida a las 3 AM no puede dejar la noche sin grabar.
ESPERA_RENDIDO = 900.0
# Vivo mas de esto = el arranque funciono; la proxima caida empieza el
# backoff de cero en vez de gastar el ultimo intento del presupuesto.
ESTABLE_S = 120.0
# Mismo tope que TOPE_BYTES_BARRIDO de registro.py: asi un archivo entero
# siempre entra en un barrido completo y un filtro por nivel no se rinde
# a mitad de camino.
MAX_EVENTOS = 8 * 1024 * 1024
# mitmdump no suelta el puerto en el mismo instante en que muere.
ESPERA_PUERTO_LIBRE = 20.0

# -- el tunel de la VPN (wireproxy) --------------------------------------
# Portado a mano de nucleo\scripts\detectar_m3u8.py (lineas 165-170):
# TUNEL / WIREPROXY / CONF_GENERADA. No se importa ese modulo -- hacerlo
# construye un Detector entero vivo dentro de Nimbo (ver CLAUDE.md, "Los
# dos puentes"). Si alla cambian el puerto por defecto o el nombre de la
# conf generada, hay que cambiarlo aqui.
NOMBRE_TUNEL = "wireproxy.exe"
TUNEL_DEFECTO = "127.0.0.1:25345"
CONF_TUNEL_GENERADA = "tunel_wireproxy.conf"
# Lo que wireproxy escupe cada vez que alguien abre y cierra sin pedir
# nada (_Tunel.RUIDO, misma lista).
RUIDO_TUNEL = ("read request failed",)


def _cmd_directo(ajustes):
    """Como se arranca el camino directo.

    Compilado no hay lanzador_directo.py suelto que pasarle a un
    interprete: el binario se relanza a si mismo con --modo directo (ver
    nimbo\\modos.py y specs\\distribucion_instalador.md S5.2). Sin
    compilar, el camino de siempre.

    Es el unico hijo que cambia. El detector lo carga mitmdump por ruta
    (-s detectar_m3u8.py), el supervisor necesita Playwright con su
    driver de Node y la union es un `detectar_m3u8.py --unir` aparte: los
    tres siguen igual en los dos casos.
    """
    if ajustes_mod.compilado():
        return [sys.executable, "--modo", "directo"]
    return [ajustes["python"], LANZADOR_DIRECTO]


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


def _esperar_puerto_libre(host, puerto, tope):
    """El inverso de _esperar_puerto: True si el puerto quedo libre dentro
    del tope.

    TerminateProcess / mitmdump al morir vuelven antes de que Windows
    suelte el socket; arrancar ya mismo sobre ese puerto falla (o peor:
    _Tunel lo ve ocupado y adopta a un muerto).
    """
    fin = time.time() + tope
    while time.time() < fin:
        try:
            socket.create_connection((host, puerto), timeout=0.5).close()
        except OSError:
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# Mirar la tabla de procesos de Windows (sin psutil)
# --------------------------------------------------------------------------
#
# Nimbo corre con el Python de la ventana, que solo tiene stdlib +
# pystray/Pillow (ver CLAUDE.md, "Tres procesos"): nada de psutil. Alcanza
# con dos llamadas de la API, las dos por ctypes:
#
#   CreateToolhelp32Snapshot -> pid, padre y nombre de cada proceso
#   GetExtendedTcpTable      -> que pid tiene abierto cada puerto a la escucha
#
# Con las dos juntas se identifica el tunel por nombre + puerto (que es lo
# que lo distingue de cualquier otro wireproxy que el usuario tenga suyo) y
# se puede preguntar lo unico que importa: su padre, sigue vivo?

_WIN = sys.platform == "win32" and ctypes is not None

if _WIN:
    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]


def _procesos() -> dict:
    """{pid: (pid del padre, nombre del exe en minusculas)}.

    Dict vacio si no se pudo leer (o no es Windows): el watchdog se calla
    en vez de adivinar -- mejor dejar un huerfano vivo que matar a ciegas.
    """
    if not _WIN:
        return {}
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if snap in (0, -1, 0xFFFFFFFF):
        return {}
    tabla = {}
    try:
        e = _PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not k32.Process32First(snap, ctypes.byref(e)):
            return {}
        while True:
            tabla[int(e.th32ProcessID)] = (
                int(e.th32ParentProcessID),
                e.szExeFile.decode("latin-1", "replace").lower(),
            )
            if not k32.Process32Next(snap, ctypes.byref(e)):
                break
    finally:
        k32.CloseHandle(snap)
    return tabla


def _duenos_puerto(puerto: int) -> set:
    """Los pid que tienen ese puerto TCP a la escucha."""
    if not _WIN:
        return set()
    try:
        iphlpapi = ctypes.windll.iphlpapi
    except (OSError, AttributeError):
        return set()
    tam = wintypes.DWORD(0)
    # AF_INET=2, TCP_TABLE_OWNER_PID_LISTENER=3. La primera llamada es solo
    # para que diga cuanto buffer hace falta (devuelve ERROR_INSUFFICIENT_BUFFER).
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(tam), False, 2, 3, 0)
    if not tam.value:
        return set()
    buf = ctypes.create_string_buffer(tam.value)
    if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(tam), False, 2, 3, 0) != 0:
        return set()
    cuantas = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    base = ctypes.sizeof(wintypes.DWORD)
    fila_tam = ctypes.sizeof(_MIB_TCPROW_OWNER_PID)
    duenos = set()
    for i in range(int(cuantas)):
        fila = _MIB_TCPROW_OWNER_PID.from_buffer_copy(
            buf, base + i * fila_tam)
        # dwLocalPort viene en orden de red en los 16 bits bajos
        if socket.ntohs(fila.dwLocalPort & 0xFFFF) == puerto:
            duenos.add(int(fila.dwOwningPid))
    return duenos


def _descendientes(raiz: int, procesos: dict) -> set:
    """raiz y todo lo que cuelgue de ella, segun la tabla dada."""
    arbol = {raiz}
    hijos = {}
    for pid, (padre, _nombre) in procesos.items():
        hijos.setdefault(padre, []).append(pid)
    pendientes = [raiz]
    while pendientes:
        for pid in hijos.get(pendientes.pop(), ()):
            if pid not in arbol:
                arbol.add(pid)
                pendientes.append(pid)
    return arbol


def _matar_pid(pid: int, espera: float = 5.0) -> bool:
    """TerminateProcess a secas. wireproxy no tiene nada que guardar: no
    escribe archivos, solo mueve paquetes -- a diferencia del detector,
    matarlo no deja nada a medias."""
    if not _WIN:
        return False
    k32 = ctypes.windll.kernel32
    # PROCESS_TERMINATE | SYNCHRONIZE
    h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)
    if not h:
        return False
    try:
        if not k32.TerminateProcess(h, 1):
            return False
        k32.WaitForSingleObject(h, int(espera * 1000))
        return True
    finally:
        k32.CloseHandle(h)


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
        self.directo = None        # lanzador_directo.py (camino sin navegador)
        self._lock = threading.Lock()
        self._parando = False
        # Un wireproxy que levantamos nosotros para tapar el agujero que
        # deja un tunel adoptado (ver _vigilar_tunel). Normalmente None:
        # el tunel es cosa del detector.
        self._tunel_propio = None
        self._ajeno_avisado = set()   # pid ya reportados, para no repetir
        # -- relanzado automatico de un hijo que se cae solo --------------
        self._deseado = False          # el usuario pidio que esto corra
        self._parada_pedida = threading.Event()
        self._reiniciando = None       # "detector" | "supervisor" | "directo" | None
        self._reintentos = {"detector": 0, "supervisor": 0, "directo": 0}
        self._proximo = {"detector": 0.0, "supervisor": 0.0, "directo": 0.0}  # time.time()
        self._rendido = set()
        self._vivo_desde = {}          # "detector"/"supervisor"/"directo" -> monotonic
        # -- persistencia del log propio, ver "Los logs del motor" --------
        self._ruta_eventos = os.path.join(ajustes.dir_estado, "eventos_motor.jsonl")
        self._lock_eventos = threading.Lock()   # propio: no el de arranque/parada
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

    def directo_vivo(self) -> bool:
        return self._vivo(self.directo)

    def estado(self) -> dict:
        proximos = [t for t in self._proximo.values() if t > time.time()]
        return {
            "detector_vivo": self.detector_vivo(),
            "supervisor_vivo": self.supervisor_vivo(),
            "directo_vivo": self.directo_vivo(),
            "parando": self._parando,
            "tunel_propio": self._vivo(self._tunel_propio),
            "reiniciando": self._reiniciando,
            "rendido": sorted(self._rendido),
            "reintentos": dict(self._reintentos),
            "proximo_reintento": min(proximos) if proximos else None,
        }

    def _aviso(self, texto, **extra):
        datos = {"texto": texto}
        datos.update(extra)
        self._en_evento("aviso", datos)
        self._persistir((extra.get("nivel") or "info").upper(), "motor", texto)

    def _emitir_log(self, proceso, linea):
        self._en_evento("log", {"proceso": proceso, "linea": linea})
        self._persistir("INFO", proceso, linea)

    def _persistir(self, nivel, prefijo, mensaje):
        """Vuelca una linea a estado\\eventos_motor.jsonl, mismo esquema que
        los _Eventos de puente.py / lanzador_supervisor.py (ver
        registro.py:_parsear). Perder un evento nunca tira el hilo que
        llama -- el lector de stdout o el que avisa."""
        fila = {"t": time.time(), "nivel": nivel, "prefijo": prefijo,
                "mensaje": mensaje}
        try:
            linea = json.dumps(fila, ensure_ascii=False)
            with self._lock_eventos:
                try:
                    if os.path.getsize(self._ruta_eventos) > MAX_EVENTOS:
                        os.replace(self._ruta_eventos, self._ruta_eventos + ".1")
                except OSError:
                    pass   # no existe todavia, o no se pudo rotar: se sigue igual
                os.makedirs(os.path.dirname(self._ruta_eventos), exist_ok=True)
                with open(self._ruta_eventos, "a", encoding="utf-8") as f:
                    f.write(linea + "\n")
        except OSError:
            pass

    # -- arranque --------------------------------------------------------

    def arrancar(self) -> bool:
        """Arranca lo que no este corriendo. True si arranco algo."""
        # Un toque manual es un presupuesto nuevo: si un hijo habia
        # agotado sus reintentos y quedo "rendido", esto lo revive.
        self._deseado = True
        self._parada_pedida.clear()
        self._reintentos = {"detector": 0, "supervisor": 0, "directo": 0}
        self._proximo = {"detector": 0.0, "supervisor": 0.0, "directo": 0.0}
        self._rendido.clear()
        with self._lock:
            self._parando = False
            algo = False
            if not self.detector_vivo():
                self._arrancar_detector()
                algo = True
            if not self.supervisor_vivo():
                self._arrancar_supervisor()
                algo = True
            if not self.directo_vivo():
                self._arrancar_directo()
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
        # Antes de arrancar, el puerto del tunel tiene que estar libre: si
        # queda un wireproxy de la corrida anterior, este detector lo
        # adopta en vez de abrir el suyo y nos quedamos con un tunel que
        # nadie vigila ni cierra. Es el momento clave del watchdog.
        self._limpiar_tunel_previo()
        self._aviso("arrancando el detector (mitmdump)...")
        self.detector = subprocess.Popen(
            cmd, cwd=a.scripts, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=CREAR_SIN_VENTANA,
        )
        threading.Thread(target=self._leer_salida,
                         args=(self.detector, "detector"), daemon=True).start()
        self._vivo_desde["detector"] = time.monotonic()

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
        self._vivo_desde["supervisor"] = time.monotonic()

    def _arrancar_directo(self):
        a = self.ajustes
        env = dict(os.environ)
        env["NIMBO_SCRIPTS"] = a.scripts
        env["NIMBO_ESTADO"] = a.dir_estado
        # mismo mecanismo que usa el detector (ver _arrancar_detector):
        # redirige streams\ a datos\ sin tocar M3U8_GRABAR (grabar.txt
        # sigue junto al script, resuelto solo por grabador_directo.py).
        env.setdefault("M3U8_BASE", a.dir_datos)
        cmd = _cmd_directo(a)
        self._aviso("arrancando el camino directo (sin navegador)...")
        self.directo = subprocess.Popen(
            cmd, cwd=a.scripts, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=CREAR_SIN_VENTANA,
        )
        threading.Thread(target=self._leer_salida,
                         args=(self.directo, "directo"), daemon=True).start()
        self._vivo_desde["directo"] = time.monotonic()

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
              espera_detector=ESPERA_CIERRE_DETECTOR,
              espera_directo=ESPERA_CIERRE_DIRECTO):
        """Bloquea hasta que los tres terminaron (o se forzaron). Llamar
        desde un hilo aparte si no se quiere congelar quien lo pida --
        el cierre del detector (o de una union en curso del directo)
        puede tardar minutos de verdad. Orden: directo -> supervisor ->
        detector (el inverso del arranque)."""
        # Antes de pelear por el lock: si hay un _reiniciar() en curso
        # puede tenerlo tomado hasta decenas de segundos, y ese reinicio
        # tiene que poder enterarse del pedido de parada sin el lock.
        self._parada_pedida.set()
        self._deseado = False
        with self._lock:
            if self._parando:
                return
            self._parando = True
        try:
            if self.directo_vivo():
                self._aviso("cerrando el camino directo -- si hay una union "
                           "en curso puede tardar unos minutos")
                self._mandar_parar(self.directo)
                if self._esperar_salida(self.directo, espera_directo):
                    self._aviso("camino directo cerrado")
                else:
                    self._aviso("el camino directo no cerro a tiempo -- lo fuerzo",
                               nivel="error")
                    self._matar(self.directo)
            self.directo = None

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
            # Si en algun momento tuvimos que levantar el tunel nosotros,
            # se va con el detector: sin nadie grabando no hay nada que
            # enrutar, y dejarlo seria el huerfano que vinimos a evitar.
            self._parar_tunel_propio("ya no hay detector")
        finally:
            with self._lock:
                self._parando = False

    # -- watchdog del tunel (wireproxy huerfano) ---------------------------
    #
    # El tunel lo abre y lo cierra el detector (clase _Tunel), no Nimbo. Lo
    # que Nimbo hace aqui es lo que _Tunel no puede hacer: mirar desde
    # fuera si quedo uno de una corrida anterior. _Tunel solo se autocura
    # cuando se le muere SU hijo; si el que se muere es el detector, el
    # hijo sobrevive y el detector siguiente lo adopta (_Tunel.run(), rama
    # "ya habia un tunel escuchando"), con el .conf viejo y sin nadie que
    # lo vigile ni lo cierre.

    def _direccion_tunel(self):
        """(host, puerto) del tunel, o (None, 0) si esta desactivado.

        Se lee del entorno igual que lo hace detectar_m3u8.py, y es el
        mismo entorno que se le pasa al detector (env = dict(os.environ)
        en _arrancar_detector), asi que las dos puntas coinciden siempre.
        """
        destino = os.environ.get("M3U8_TUNEL", TUNEL_DEFECTO).strip()
        if not destino:
            return None, 0          # M3U8_TUNEL vacio = todo sale directo
        host, _, puerto = destino.rpartition(":")
        if not puerto.isdigit():
            return None, 0
        return (host or "127.0.0.1"), int(puerto)

    def _tuneles_escuchando(self, puerto, procesos):
        """Los pid de wireproxy.exe que tienen ese puerto a la escucha.

        Nombre Y puerto: con el nombre solo se mataria un wireproxy que el
        usuario tenga para lo suyo; con el puerto solo, cualquier cosa que
        se lo haya quedado.
        """
        return [pid for pid in _duenos_puerto(puerto)
                if procesos.get(pid, (0, ""))[1] == NOMBRE_TUNEL]

    def _clasificar_tunel(self, pid, procesos, arbol_detector):
        """'nuestro' | 'huerfano' | 'ajeno'."""
        if pid in arbol_detector:
            return "nuestro"        # hijo del detector vivo: su padre lo cuida
        if self._vivo(self._tunel_propio) and pid == self._tunel_propio.pid:
            return "nuestro"
        padre = procesos.get(pid, (0, ""))[0]
        if padre == os.getpid():
            # lo abrimos nosotros (_relanzar_tunel) pero ya no tenemos su
            # Popen: nadie lo vigila, asi que es un huerfano igual.
            return "huerfano"
        if padre and padre != pid and padre in procesos:
            # alguien vivo lo abrio y no somos nosotros: puede ser un
            # detector que el usuario corrio a mano desde una consola. No
            # se mata lo que no es nuestro.
            return "ajeno"
        return "huerfano"           # su padre ya no existe

    def _avisar_ajeno(self, pid, procesos, host, puerto):
        if pid in self._ajeno_avisado:
            return
        self._ajeno_avisado.add(pid)
        padre = procesos.get(pid, (0, ""))[0]
        self._aviso(
            "hay un %s que no es mio escuchando en %s:%d (pid %s, lo abrio "
            "el pid %s): no lo toco, pero el detector va a usar ESE tunel "
            "-- si no es a proposito, cerralo y volve a arrancar"
            % (NOMBRE_TUNEL, host, puerto, pid, padre), nivel="error")

    def _limpiar_tunel_previo(self):
        """Deja libre el puerto del tunel antes de arrancar el detector."""
        # El nuestro tambien estorba: dejarlo escuchando hace que el
        # detector nuevo lo adopte igual que a un huerfano.
        matados = int(self._parar_tunel_propio(
            "arranca un detector que abre el suyo"))
        host, puerto = self._direccion_tunel()
        if not puerto:
            return
        procesos = _procesos()
        if not procesos:
            return                  # no se pudo mirar: mejor no adivinar
        for pid in self._tuneles_escuchando(puerto, procesos):
            clase = self._clasificar_tunel(pid, procesos, set())
            if clase == "ajeno":
                self._avisar_ajeno(pid, procesos, host, puerto)
                continue
            if clase == "nuestro":
                continue
            if _matar_pid(pid):
                matados += 1
                self._aviso(
                    "habia un %s huerfano de una corrida anterior en "
                    "%s:%d (pid %s): lo cerre para que el detector abra "
                    "el suyo" % (NOMBRE_TUNEL, host, puerto, pid))
            else:
                self._aviso(
                    "no pude cerrar el %s huerfano (pid %s) que tiene "
                    "%s:%d: el detector lo va a adoptar"
                    % (NOMBRE_TUNEL, pid, host, puerto), nivel="error")
        if matados:
            # terminate/TerminateProcess vuelven antes de que Windows
            # suelte el socket; si arrancaramos ya mismo, _Tunel todavia
            # veria el puerto ocupado y adoptaria a un muerto.
            _esperar_puerto_libre(host, puerto, 5.0)
            self._ajeno_avisado.clear()

    def _vigilar_tunel(self):
        """Una pasada del watchdog. La llama _bucle_vigilancia."""
        host, puerto = self._direccion_tunel()
        if not puerto:
            return
        procesos = _procesos()
        if not procesos:
            return
        tuneles = self._tuneles_escuchando(puerto, procesos)
        if not tuneles:
            self._ajeno_avisado.clear()
            if self._tunel_propio is not None and not self._vivo(self._tunel_propio):
                self._tunel_propio = None
            return
        det = self.detector
        hay_detector = self._vivo(det)
        arbol = _descendientes(det.pid, procesos) if hay_detector else set()
        for pid in tuneles:
            clase = self._clasificar_tunel(pid, procesos, arbol)
            if clase == "nuestro":
                continue
            if clase == "ajeno":
                self._avisar_ajeno(pid, procesos, host, puerto)
                continue
            if not _matar_pid(pid):
                self._aviso("no pude cerrar el %s huerfano (pid %s) de %s:%d"
                            % (NOMBRE_TUNEL, pid, host, puerto), nivel="error")
                continue
            if not hay_detector:
                self._aviso("cerre un %s huerfano (pid %s) que seguia "
                            "escuchando %s:%d sin nadie grabando"
                            % (NOMBRE_TUNEL, pid, host, puerto))
                continue
            # Con el detector vivo, ese huerfano era el tunel que adopto al
            # arrancar: su _Tunel ya termino y no lo va a reabrir, asi que
            # el puerto lo tenemos que volver a levantar nosotros o las
            # @vpn= se quedan sin tunel hasta el proximo arranque.
            self._aviso("el detector estaba usando un %s huerfano (pid %s): "
                        "lo cerre y relanzo el tunel" % (NOMBRE_TUNEL, pid))
            self._relanzar_tunel(host, puerto)

    def _relanzar_tunel(self, host, puerto) -> bool:
        """Levanta wireproxy nosotros, con la conf que ya genero el
        detector (la del usuario + la seccion [http]; ver _generar_conf)."""
        a = self.ajustes
        exe = os.path.join(a.scripts, NOMBRE_TUNEL)
        conf = os.path.join(a.scripts, CONF_TUNEL_GENERADA)
        if not os.path.isfile(exe) or not os.path.isfile(conf):
            self._aviso(
                "no puedo relanzar el tunel: falta %s o %s en %s -- las "
                "modelos de @vpn= van a salir directas hasta que se vuelva "
                "a arrancar el motor"
                % (NOMBRE_TUNEL, CONF_TUNEL_GENERADA, a.scripts),
                nivel="error")
            return False
        try:
            proc = subprocess.Popen(
                [exe, "-s", "-c", conf], cwd=a.scripts,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                creationflags=CREAR_SIN_VENTANA,
            )
        except OSError as e:
            self._aviso("no pude relanzar el tunel: %s" % e, nivel="error")
            return False
        self._tunel_propio = proc
        threading.Thread(target=self._leer_tunel, args=(proc,),
                         daemon=True, name="nimbo-tunel").start()
        if _esperar_puerto(host, puerto, ESPERA_PUERTO):
            self._aviso("tunel de nuevo en %s:%d (lo mantiene Nimbo hasta "
                        "la proxima parada)" % (host, puerto))
            return True
        self._aviso("el tunel relanzado no acepto conexiones en %s:%d -- "
                    "revisa %s" % (host, puerto, CONF_TUNEL_GENERADA),
                    nivel="error")
        return False

    def _leer_tunel(self, proc):
        """Las quejas de wireproxy al log, igual que _Tunel._reflejar."""
        try:
            for linea in proc.stderr:
                texto = linea.strip()
                if not texto or any(x in texto for x in RUIDO_TUNEL):
                    continue
                self._emitir_log("tunel", texto[:200])
        except (OSError, ValueError):
            pass

    def _parar_tunel_propio(self, motivo="") -> bool:
        """True si habia uno andando y se cerro."""
        proc = self._tunel_propio
        self._tunel_propio = None
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self._matar(proc)
        self._aviso("cerre el tunel que mantenia Nimbo%s"
                    % (" (%s)" % motivo if motivo else ""))
        return True

    # -- relanzado automatico (un hijo que se cae solo) --------------------
    #
    # _parando / self.supervisor = None en parar() ya distinguen "lo
    # paramos nosotros" de "se murio solo" -- por eso aqui alcanza con
    # preguntar _parando (y, mientras dura, _reiniciando) antes de avisar
    # una muerte como tal.

    def _agendar_reinicio(self, cual):
        """Calcula cuando reintentar 'cual' ("detector"/"supervisor") y
        avisa. No hace nada si el usuario no pidio que esto corra."""
        if not self._deseado:
            return
        n = self._reintentos[cual]
        if n < len(ESPERA_REINTENTO):
            espera = ESPERA_REINTENTO[n]
            self._aviso("reintento del %s en %ds (intento %d de %d)" %
                       (cual, espera, n + 1, len(ESPERA_REINTENTO)))
        else:
            self._rendido.add(cual)
            espera = ESPERA_RENDIDO
            self._aviso(
                "el %s no vuelve a arrancar despues de %d intentos -- "
                "revisa el log; sigo probando cada %d min"
                % (cual, len(ESPERA_REINTENTO), int(ESPERA_RENDIDO // 60)),
                nivel="error")
        self._proximo[cual] = time.time() + espera
        self._reintentos[cual] += 1

    def _reiniciar(self, cual):
        """El relanzado de verdad. Corre en su propio hilo (bloquea
        decenas de segundos); _reiniciando lo mantiene unico y calla al
        resto de _bucle_vigilancia mientras dura."""
        self._reiniciando = cual
        try:
            if self._parada_pedida.is_set():
                return
            if cual == "detector":
                self._aviso("reiniciando el motor: cierro las paginas y "
                           "vuelvo a arrancar el detector")
                if self.supervisor_vivo():
                    self._mandar_parar(self.supervisor)
                    if not self._esperar_salida(self.supervisor,
                                                ESPERA_CIERRE_SUPERVISOR):
                        self._matar(self.supervisor)
                    self.supervisor = None
                if self._parada_pedida.is_set():
                    return
                host, puerto = _parsear_proxy(self.ajustes["proxy"])
                if not _esperar_puerto_libre(host, puerto, ESPERA_PUERTO_LIBRE):
                    self._aviso(
                        "el puerto %d sigue ocupado -- no puedo reiniciar "
                        "el detector todavia" % puerto, nivel="error")
                    self._agendar_reinicio("detector")
                    return
                with self._lock:
                    if self._parando or self._parada_pedida.is_set():
                        return
                    self._arrancar_detector()
                    self._arrancar_supervisor()
            elif cual == "supervisor":
                self._aviso("reiniciando el motor: vuelvo a arrancar el "
                           "supervisor")
                with self._lock:
                    if self._parando or self._parada_pedida.is_set():
                        return
                    self._arrancar_supervisor()
            else:
                # "directo": no depende del proxy (no pasa por el
                # detector), asi que relanzarlo solo no necesita tocar
                # nada mas -- a diferencia de una caida del detector, que
                # si se lleva al supervisor con ella.
                self._aviso("reiniciando el motor: vuelvo a arrancar el "
                           "camino directo")
                with self._lock:
                    if self._parando or self._parada_pedida.is_set():
                        return
                    self._arrancar_directo()
            self._aviso("motor reiniciado (intento %d)" %
                       self._reintentos[cual])
        finally:
            self._reiniciando = None

    # -- vigilancia --------------------------------------------------------

    def _bucle_vigilancia(self):
        proximo_tunel = 0.0
        while True:
            time.sleep(INTERVALO_VIGILANCIA)
            with self._lock:
                if self._parando:
                    continue
            if self._reiniciando:
                # Un reinicio en curso ya sabe que sus hijos van a morir y
                # nacer -- no hay que reportarlo como "se cerro solo" ni
                # agendar nada encima.
                continue
            murio_detector = False
            if self.detector is not None and not self.detector_vivo():
                if (time.monotonic() - self._vivo_desde.get("detector", 0)
                        > ESTABLE_S):
                    self._reintentos["detector"] = 0
                    self._rendido.discard("detector")
                self._aviso(
                    "el detector se cerro solo (codigo %s) -- si quedo "
                    "una sesion a medias en datos\\streams\\, el detector "
                    "la levanta solo al arrancar (recuperar_pendientes); "
                    "no se perdio la grabacion" % self.detector.returncode,
                    proceso="detector", tipo="murio")
                self.detector = None
                murio_detector = True
                self._agendar_reinicio("detector")
            if self.supervisor is not None and not self.supervisor_vivo():
                if (time.monotonic() - self._vivo_desde.get("supervisor", 0)
                        > ESTABLE_S):
                    self._reintentos["supervisor"] = 0
                    self._rendido.discard("supervisor")
                self._aviso("el supervisor se cerro solo (codigo %s)" %
                           self.supervisor.returncode,
                           proceso="supervisor", tipo="murio")
                self.supervisor = None
                self._agendar_reinicio("supervisor")
            if self.directo is not None and not self.directo_vivo():
                if (time.monotonic() - self._vivo_desde.get("directo", 0)
                        > ESTABLE_S):
                    self._reintentos["directo"] = 0
                    self._rendido.discard("directo")
                self._aviso("el camino directo se cerro solo (codigo %s)" %
                           self.directo.returncode,
                           proceso="directo", tipo="murio")
                self.directo = None
                self._agendar_reinicio("directo")
            if murio_detector:
                # Sin detector no hay nada que enrutar por el tunel.
                self._parar_tunel_propio("el detector se cerro solo")
            # El tunel se mira DESPUES de soltar las referencias: si el
            # detector se acaba de caer, su wireproxy quedo huerfano ya
            # mismo y no hay que esperar al turno siguiente para cerrarlo.
            if murio_detector or time.time() >= proximo_tunel:
                proximo_tunel = time.time() + INTERVALO_TUNEL
                try:
                    self._vigilar_tunel()
                except Exception as e:      # el watchdog no tira el bucle
                    self._aviso("el watchdog del tunel fallo: %s" % e,
                                nivel="error")
            # El detector tiene prioridad: si varios estan listos para
            # reintentar a la vez, se lanza primero el suyo (relanza
            # tambien al supervisor) y los demas quedan para el turno
            # siguiente. El directo va al final: no depende de los otros
            # dos ni ellos de el.
            if self._deseado and not self._parando and not self._reiniciando:
                for cual in ("detector", "supervisor", "directo"):
                    vivo = (self.detector_vivo() if cual == "detector"
                            else self.supervisor_vivo() if cual == "supervisor"
                            else self.directo_vivo())
                    if vivo or time.time() < self._proximo[cual]:
                        continue
                    threading.Thread(target=self._reiniciar, args=(cual,),
                                     daemon=True,
                                     name="nimbo-reinicio").start()
                    break
