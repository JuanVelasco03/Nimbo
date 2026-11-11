"""Servidor local de Nimbo: sirve la interfaz, la API de control
(arrancar/parar, la lista de modelos, los ajustes) y el canal en vivo por
WebSocket (/ws). Un solo asyncio.start_server en 127.0.0.1 -- un solo
hilo de red -- que demultiplexa por conexion: si la peticion trae
"Upgrade: websocket" se la re-alimenta a websockets.server.ServerProtocol
(la capa sans-I/O de la libreria: solo entiende bytes y eventos, no abre
sockets por su cuenta), si no se la atiende como HTTP comun.

Por que no server.websockets.asyncio.server.serve() para todo: esa API
solo sabe responder peticiones HTTP completas (process_request), no
transmitir un cuerpo en bloques -- y _servir_video (mas abajo) tiene que
poder mandar un .mp4 de varios GB sin cargarlo entero en memoria. El HTTP
de este archivo es propio por eso; el framing WebSocket, en cambio, se
reusa de la libreria porque ahi no hay nada que streamear bloque a
bloque: un frame se arma de una.

Solo escucha en 127.0.0.1 y exige un token en cada pedido (?t=... en la
URL, o en la query del handshake de /ws): el Chromium que graba sale por
el proxy de grabacion y no tiene por que poder manejar la aplicacion que
lo controla.

Lo que antes era SSE (/eventos, unidireccional) ahora es /ws: el estado
se empuja por SECCIONES -- solo las que cambiaron desde el ultimo tick,
ver _bucle_estado() -- y la pestana ya no necesita pedir /api/estado cada
2s para enterarse de nada (eso era, de lejos, lo mas caro que hacia la
version anterior: shutil.disk_usage + tres JSON + grabar.txt entero +
un recorrido de datos\\streams\\, repetido por cada pestana abierta). El
refresco de "Modelos" contra el sitio real tambien se movio para aca
(canal "sitio"): antes lo disparaba cada pestana con un setInterval que
nadie podia cancelar; ahora es una tarea del servidor, compartida, y se
apaga sola si nadie esta mirando esa pestana.

Los pedidos que pueden tardar (arrancar, parar, actualizar "siguiendo")
no bloquean la respuesta: se lanzan como tareas en el ejecutor y el
progreso real se ve por el canal "motor" del WebSocket (logs y avisos).
Todo lo que es I/O sincrono de verdad -- disco, subprocess, red saliente,
los locks de threading que ya tienen lista.py/sesion.py/sitio.py/etc --
pasa siempre por Aplicacion.a_hilo(), nunca se llama directo desde una
corutina: son hilos reales por debajo, asi que los locks existentes
siguen sirviendo tal cual.
"""

import asyncio
import contextlib
import email.parser
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote, urlsplit

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

import actualizaciones as actualizaciones_mod
import ajustes as ajustes_mod
import cosecha_streams as cosecha_streams_mod
import estado_captura as estado_captura_mod
import favoritas as favoritas_mod
import grabaciones as grabaciones_mod
import inicio_automatico as inicio_mod
import lan as lan_mod
import licencia as licencia_mod
import lista as lista_mod
import login_sitio as login_sitio_mod
import miniaturas as miniaturas_mod
import motor as motor_mod
import notificaciones as notificaciones_mod
import preflight as preflight_mod
import qr as qr_mod
import registro as registro_mod
import rutas as rutas_mod
import salud as salud_mod
import sesion as sesion_mod
import sesion_chaturbate as sesion_chaturbate_mod
import sitio as sitio_mod
import sitio_chaturbate as sitio_chaturbate_mod
import version as version_mod
import vivo as vivo_mod
import vivo_temporal as vivo_temporal_mod

EJECUTOR_HILOS = 12          # tope del pool: que el refresco del sitio no
                             # pueda dejar sin ranura al resto de la API
CANALES_VALIDOS = {"estado", "motor", "sitio"}

# Watchdog de _lanzar_tarea: cuanto se espera antes de dar una tarea por
# perdida y lanzar otra. No son comparables entre si -- una parada del
# motor TIENE que poder tardar los ~20 minutos medidos en vivo (@inactividad
# por sesion + la cola de union, secuencial), asi que un tope global chico
# la cortaria a mitad de camino.
TOPE_TAREA = {
    "sesion_actualizar": 90.0,     # sesion.TOPE_TOTAL_S = 45.0, con margen
    # SPEC 21: entrada propia, no comparte nombre con la de stripchat --
    # refrescar una lista no puede quedar deduplicada contra la otra.
    "sesion_actualizar_chaturbate": 90.0,
    "motor_arrancar": 300.0,
    "motor_parar": 1500.0,         # motor.ESPERA_CIERRE_DIRECTO = 1260s, con margen
    # Compartida a proposito entre los dos sitios (SPEC 21): un solo
    # perfil_login\ y un solo login a la vez.
    "sesion_login": 330.0,         # login_sitio.SEGUNDOS_CAPTURA = 300.0, con margen
}
TOPE_TAREA_DEFECTO = 120.0

INTERVALO_ESTADO_ACTIVO = 1.0   # con alguien mirando "estado"
INTERVALO_ESTADO_IDLE = 4.0     # sin nadie -- el tooltip de la bandeja
                                 # igual queda con dato de hasta 4s
INTERVALO_DISCO = 30.0          # shutil.disk_usage no es gratis
INTERVALO_COSECHA = 10.0        # mismo intervalo que tenia nimbo-cosecha
INTERVALO_LIMPIEZA = 600.0      # cada cuanto se barre datos\streams\ --
                                 # el listdir de dos niveles no es gratis
INTERVALO_SITIO = 15.0          # mismo intervalo que tenia el setInterval
                                 # del lado del cliente
INTERVALO_ACTUALIZACIONES = 86400.0   # 24 h (SPEC 17, C12) -- al arrancar y
                                 # despues cada dia; actualizaciones.verificar
                                 # ya cachea en RAM, asi que "buscar ahora"
                                 # (forzar=True) no espera a este reloj
INTERVALO_LICENCIA = 21600.0    # 6 h. La licencia se revisa al arrancar
                                 # (app.py) y despues cada tanto: el token
                                 # tiene dias de vida, no hay por que
                                 # golpear la red mas seguido
INTERVALO_MINIATURAS = 30.0     # cada cuanto se intenta generar portadas
                                 # faltantes de datos\grabaciones\ (ver
                                 # nimbo\miniaturas.py)
TOPE_MINIATURAS_POR_TICK = 4    # ffmpeg por tick: que no le compita el
                                 # disco a una grabacion en curso
INTERVALO_BARRIDO_MINIATURAS = 600.0   # cada cuanto se borran portadas
                                 # huerfanas (mismo ritmo que streams\)
CUPO_SITIO = 4                  # pedidos salientes simultaneos al sitio
CUPO_IMAGEN = 6                 # miniaturas simultaneas: el navegador puede
                                 # pedir N por segundo (una por tarjeta) y
                                 # cada una es un HTTPS saliente -- sin tope
                                 # se comen el pool entero y no responde
                                 # nada mas (ver specs\congelado_interfaz.md)

INTERVALO_PING = 15.0
TOPE_PONG = 20.0

# Acceso LAN (SPEC 17, C10): fuerza bruta sobre /api/lan/login.
LAN_MAX_INTENTOS = 5
LAN_BLOQUEO_S = 60.0


def _leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _leer_bytes(ruta):
    try:
        with open(ruta, "rb") as f:
            return f.read()
    except OSError:
        return None


_CLAVES_RUTAS = ("dir_segmentos", "dir_grabaciones", "union_modo", "dir_union")


def _rutas_snapshot(a):
    """Lo que necesita la seccion "Carpetas y discos" (SPEC 12): las
    cuatro claves configuradas, lo que resolvieron de verdad, el espacio
    libre de cada carpeta resuelta y las que cayeron al defecto."""
    resuelto = {"segmentos": a.streams, "grabaciones": a.grabaciones,
                "union": a.dir_union}
    espacio = {clave: rutas_mod.espacio(valor)
               for clave, valor in resuelto.items() if valor}
    return {
        "dir_segmentos": a["dir_segmentos"], "dir_grabaciones": a["dir_grabaciones"],
        "union_modo": a["union_modo"], "dir_union": a["dir_union"],
        "resuelto": resuelto, "espacio": espacio, "caidas": a.rutas_caidas,
    }


def _agregar_alerta_actualizacion(app, salud):
    """La alerta de actualizacion (SPEC 17, C12), mismo formato que las de
    salud.py pero aparte de salud.evaluar(): depende de una respuesta de
    red (actualizaciones_mod.verificar, cacheada en app.actualizacion por
    _bucle_actualizaciones) y no del estado de disco/motor/detector que
    esa funcion mira. Comparte esta logica estado_completo() (la foto
    inicial) y _bucle_estado (cada tick) -- asi las dos ven la misma
    alerta en vez de que la primera foto salga sin ella."""
    if app.actualizacion and app.actualizacion.get("hay_nueva"):
        salud.append({
            "clave": "actualizacion", "tipo": "actualizacion", "nivel": "aviso",
            "titulo": "Hay una version nueva (%s)" % app.actualizacion["version"],
            # Las notas de verdad y el link al release van en el panel
            # "Actualizaciones" de Ajustes (GET /api/actualizacion) -- la
            # franja de salud es texto fijo a proposito, mismo formato
            # que el ejemplo del spec.
            "texto": "notas del release -- ver el link en Ajustes",
        })


async def _leer_detector_fusionado(app):
    """detector.json + directo.json + chaturbate.json fusionados (ver
    nimbo/estado_captura.py) -- el shape que ve el resto del codigo sigue
    siendo el de detector.json, asi que vivo.py/cosecha_streams.py no se
    enteran de donde salio cada sesion."""
    detector = await app.a_hilo(_leer_json, app.ajustes.estado_detector)
    directo = await app.a_hilo(_leer_json, app.ajustes.estado_directo)
    chaturbate = await app.a_hilo(_leer_json, app.ajustes.estado_chaturbate)
    return estado_captura_mod.fusionar(detector, directo, chaturbate, time.time())


# ===========================================================================
# Aplicacion: todo el estado vivo -- ajustes, lista, motor, conexiones --
# ===========================================================================

class Aplicacion:
    """Una instancia por servidor. self.estado es la cache de la ultima
    foto completa del estado (la mantiene _bucle_estado); self.loop se
    fija recien cuando el loop asyncio ya esta corriendo (ver
    Servidor._correr) -- antes de eso no hay a quien publicarle nada."""

    def __init__(self, ajustes, dir_web):
        self.ajustes = ajustes
        self.dir_web = dir_web
        self.lista = lista_mod.Lista(ajustes.grabar_txt)
        self.favoritas = favoritas_mod.Favoritas(
            os.path.join(ajustes_mod.RAIZ, "favoritas.json"))
        self.sesion = sesion_mod.Sesion(
            os.path.join(ajustes_mod.RAIZ, "sesion.json"))
        # SPEC 21: archivo aparte bajo estado\ (no sesion.json) -- un
        # error en el formato nuevo no puede dejar sin sesion a
        # stripchat, que es el sitio que graba 24/7.
        self.sesion_chaturbate = sesion_chaturbate_mod.SesionChaturbate(
            ajustes.estado_sesion_chaturbate)
        self.sitio = sitio_mod.Sitio(ajustes)
        self.sitio_chaturbate = sitio_chaturbate_mod.Chaturbate(ajustes)
        self.token = secrets.token_urlsafe(24)
        self.puerto = None           # lo fija Servidor._iniciar() apenas
                                      # abre el socket -- _origen_valido lo usa
        self.ejecutor = ThreadPoolExecutor(
            max_workers=EJECUTOR_HILOS, thread_name_prefix="nimbo-ej")
        # Se puede construir fuera del loop: desde 3.10 el Semaphore ya no
        # se ata a uno al crearse (_bucle_sitio crea el suyo adentro de la
        # corutina solo porque es local a ella).
        self.cupo_imagen = asyncio.Semaphore(CUPO_IMAGEN)
        self.motor = motor_mod.Motor(ajustes, en_evento=self._en_evento)
        self.conexiones = set()     # de _Conexion, solo se toca desde el loop
        self.loop = None
        self.estado = {}            # cache completa, la ve /api/estado y
                                     # _resumen_estado() de app.py
        self._tareas = {}           # nombre -> Task, para no duplicar
                                     # un 202 si llega dos veces seguidas
        self._sueltas = set()       # tareas sin dueno (el latido del WS):
                                     # asyncio solo guarda una referencia
                                     # debil, y una tarea que nadie sostiene
                                     # se la puede llevar el recolector a
                                     # mitad de camino
        self.ultima_limpieza = None  # ultimo barrido de datos\streams\,
                                      # lo pisa _bucle_cosecha
        # SPEC 15: fase de la captura de sesion en curso (o no). Mutado
        # solo por _tarea_login; _bucle_estado lo publica como cualquier
        # otra seccion, comparando contra el delta anterior. SPEC 21:
        # "sitio" le dice a la interfaz cual de las dos barras muestra la
        # cuenta regresiva (un solo login a la vez, ver TOPE_TAREA).
        self.login = {"fase": "inactiva", "vence": None, "motivo": None, "sitio": None}
        self._login_cancelar = asyncio.Event()
        # SPEC 16: construido ya con sink=None para que _bucle_estado (que
        # arranca antes de que app.py elija bandeja o no, ver
        # Servidor._iniciar) siempre tenga a quien llamarle; app.py solo le
        # asigna icono.notify si corresponde. Degradacion silenciosa, mismo
        # criterio que la bandeja entera.
        self.notificador = notificaciones_mod.Notificador()
        # Latch de la parada por disco critico: se suelta con histeresis,
        # cuando el espacio vuelve por encima del umbral de AVISO (no del
        # critico) -- ver _bucle_estado.
        self.parada_por_disco = False
        # Fuerza bruta de /api/lan/login (SPEC 17): contador en memoria por
        # IP, se pierde al reiniciar -- aceptable, el acceso LAN es nuevo
        # en cada arranque de todas formas.
        self.lan_intentos = {}      # ip -> fallos consecutivos
        self.lan_bloqueos = {}      # ip -> epoch hasta el que esa ip esta bloqueada
        # SPEC 17, C12: ultimo resultado de actualizaciones.verificar (o
        # None si nunca se pudo evaluar nada valido) -- lo pisa
        # _bucle_actualizaciones, lo lee _bucle_estado para la alerta de
        # salud y /api/actualizacion para el panel de Ajustes.
        self.actualizacion = None

    def fijar_loop(self, loop):
        self.loop = loop

    async def a_hilo(self, fn, *args):
        """Corre fn(*args) en el ejecutor propio. Todo lo sincrono (I/O
        de disco, subprocess, red saliente, cualquier modulo con su
        propio threading.Lock) pasa por aca -- nunca se llama directo
        desde una corutina."""
        return await self.loop.run_in_executor(self.ejecutor, fn, *args)

    # -- el puente motor -> WebSocket ----------------------------------

    def _en_evento(self, tipo, datos):
        """Motor llama esto desde HILOS PROPIOS (el de vigilancia, los
        lectores de stdout de cada hijo, o el que llamo parar()) -- nunca
        desde el loop. Tocar self.conexiones desde ahi seria una carrera
        de datos; call_soon_threadsafe cruza al loop antes de publicar.
        Mismo patron que ya usa lanzador_supervisor.py con su hilo de
        stdin."""
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(self._publicar, "motor", tipo, datos)

    def _publicar(self, canal, tipo, datos):
        """Nunca bloquea a quien publica y nunca deja que una pestana
        colgada afecte a otra: si la cola de una conexion esta llena, se
        descarta su mensaje mas viejo y se sigue -- calcado de la
        _Suscriptores de la version con SSE."""
        mensaje = json.dumps({"tipo": tipo, "datos": datos, "t": time.time()},
                              ensure_ascii=False)
        for con in list(self.conexiones):
            if canal not in con.canales:
                continue
            try:
                con.cola.put_nowait(mensaje)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    con.cola.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    con.cola.put_nowait(mensaje)

    # -- snapshots -------------------------------------------------------

    def _disco(self):
        """Espacio libre de cada disco en juego -- segmentos y
        grabaciones, deduplicados por unidad (SPEC 12). Con las dos
        carpetas en el mismo disco da una sola fila, igual que antes de
        que existiera "Carpetas y discos"."""
        filas = []
        vistos = set()
        for etiqueta, carpeta in (("segmentos", self.ajustes.streams),
                                  ("grabaciones", self.ajustes.grabaciones)):
            info = rutas_mod.espacio(carpeta)
            if not info or info["unidad"] in vistos:
                continue
            vistos.add(info["unidad"])
            filas.append({"etiqueta": etiqueta, **info})
        return filas

    def estado_completo(self):
        """Snapshot sincrono completo -- se llama desde un hilo del
        ejecutor (ver a_hilo), nunca desde el loop. Arma self.estado al
        arrancar; de ahi en mas _bucle_estado la reemplaza como fuente
        del dia a dia, pero esto sigue siendo lo que responde /api/estado
        si self.estado todavia esta vacio (justo al conectar, antes del
        primer tick)."""
        detector = estado_captura_mod.fusionar(
            _leer_json(self.ajustes.estado_detector),
            _leer_json(self.ajustes.estado_directo),
            _leer_json(self.ajustes.estado_chaturbate))
        base = {
            "motor": self.motor.estado(),
            "lista": self.lista.leer(),
            "favoritas": self.favoritas.leer(),
            "detector": detector,
            "supervisor": _leer_json(self.ajustes.estado_supervisor),
            "disco": self._disco(),
            "sesion": self.sesion.estado(),
            "siguiendo": _leer_json(self.ajustes.estado_siguiendo),
            # SPEC 21: misma forma que "sesion"/"siguiendo", para la
            # cuenta de chaturbate.
            "sesion_chaturbate": self.sesion_chaturbate.estado(),
            "siguiendo_chaturbate": _leer_json(self.ajustes.estado_siguiendo_chaturbate),
            "vivo": vivo_mod.sesiones_vivas(self.ajustes.streams, detector),
            "limpieza": self.ultima_limpieza,
            "login": self.login,
        }
        base["salud"] = salud_mod.evaluar(base, self.ajustes)
        _agregar_alerta_actualizacion(self, base["salud"])
        return base


# ===========================================================================
# El canal en vivo: una conexion WebSocket por pestana
# ===========================================================================

class _Conexion:
    def __init__(self, proto, writer):
        self.proto = proto
        self.writer = writer
        self.canales = set()
        self.cola = asyncio.Queue(maxsize=200)
        self.ultimo_pong = time.monotonic()


async def _ws_escritor(con):
    """Vacia con.cola hacia el socket; si no hay nada que mandar en
    INTERVALO_PING, manda un ping propio (la API alta de websockets daria
    esto gratis -- aca no, ServerProtocol es sans-I/O puro). Si el pong no
    llega en INTERVALO_PING + TOPE_PONG, pide el cierre."""
    while True:
        try:
            mensaje = await asyncio.wait_for(con.cola.get(), timeout=INTERVALO_PING)
        except asyncio.TimeoutError:
            if time.monotonic() - con.ultimo_pong > INTERVALO_PING + TOPE_PONG:
                con.proto.send_close(1000, "sin pong")
            else:
                con.proto.send_ping(b"")
        else:
            con.proto.send_text(mensaje.encode("utf-8"))
        saliente = con.proto.data_to_send()
        if not saliente:
            continue
        for fragmento in saliente:
            con.writer.write(fragmento)
        try:
            await con.writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            return


async def _latido_suelto(app, nombre):
    with contextlib.suppress(Exception):
        await app.a_hilo(vivo_temporal_mod.latido, app.ajustes.dir_estado, nombre)


async def _despachar_mensaje(app, con, datos):
    try:
        msg = json.loads(bytes(datos).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(msg, dict):
        return
    tipo = msg.get("tipo")
    if tipo == "suscribir":
        canales = msg.get("canales")
        if isinstance(canales, list):
            con.canales |= {c for c in canales if c in CANALES_VALIDOS}
    elif tipo == "cancelar":
        con.canales.discard(msg.get("lote", ""))
    elif tipo == "latido":
        nombre = msg.get("nombre")
        if nombre:
            # Sin await a proposito: esto corre DENTRO de _ws_lector, y si
            # el pool esta ocupado un await aca deja de leer el socket --
            # no se procesan los PONG y _ws_escritor cierra la conexion por
            # "sin pong", llevandose puesto el canal de estado justo cuando
            # mas se lo necesita. El latido es best-effort por diseno:
            # vivo_temporal.cosechar() cierra sola a los 30s si no llega.
            tarea = app.loop.create_task(_latido_suelto(app, nombre))
            app._sueltas.add(tarea)
            tarea.add_done_callback(app._sueltas.discard)


async def _ws_lector(app, con, reader):
    while True:
        try:
            datos = await reader.read(65536)
        except (ConnectionResetError, OSError):
            return
        if not datos:
            return
        con.proto.receive_data(datos)
        saliente = con.proto.data_to_send()
        if saliente:
            for fragmento in saliente:
                con.writer.write(fragmento)
            try:
                await con.writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                return
        cerrado = False
        for ev in con.proto.events_received():
            if not isinstance(ev, Frame):
                continue
            if ev.opcode is Opcode.TEXT:
                await _despachar_mensaje(app, con, ev.data)
            elif ev.opcode is Opcode.PONG:
                con.ultimo_pong = time.monotonic()
            elif ev.opcode is Opcode.CLOSE:
                cerrado = True
        if cerrado:
            return


async def _atender_ws(app, proto, respuesta, reader, writer):
    proto.send_response(respuesta)
    for fragmento in proto.data_to_send():
        writer.write(fragmento)
    await writer.drain()

    con = _Conexion(proto, writer)
    # "estado" y "motor" de arranque: son los dos canales que la interfaz
    # siempre quiere (reemplazan al estado_inicial/log/aviso de antes).
    # "sitio" se suma solo cuando la pestana entra a "Modelos" (ver
    # main.js) -- ese es el que de verdad justifica la cancelacion.
    con.canales = {"estado", "motor"}
    app.conexiones.add(con)
    inicial = json.dumps(
        {"tipo": "estado_inicial", "datos": app.estado, "t": time.time()},
        ensure_ascii=False)
    with contextlib.suppress(asyncio.QueueFull):
        con.cola.put_nowait(inicial)

    tarea_escritor = asyncio.ensure_future(_ws_escritor(con))
    try:
        await _ws_lector(app, con, reader)
    finally:
        tarea_escritor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea_escritor
        app.conexiones.discard(con)
        with contextlib.suppress(Exception):
            writer.close()


# ===========================================================================
# Tareas de fondo: estado por secciones, cosecha de vivo_temporal, sitio
# ===========================================================================

async def _bucle_estado(app):
    """Reemplaza al hilo nimbo-cosecha de antes en lo que hace a difundir
    estado -- la cosecha de vivo_temporal sigue siendo _bucle_cosecha, por
    separado. Cada tick recalcula solo las secciones baratas de detectar
    que cambiaron, y publica apenas eso (no el estado entero) por el
    canal "estado". ojo con detector.json/supervisor.json: el puente los
    reescribe SIEMPRE cada 2s aunque no cambie nada (es su latido), asi
    que ahi hay que comparar el contenido ya parseado, no el mtime."""
    reloj_disco = 0.0
    firma_lista_vista = None   # aparte de app.estado: ese dict es el
                                # snapshot publico (se manda tal cual al
                                # cliente), esto es solo contabilidad interna
    while True:
        intervalo = (INTERVALO_ESTADO_ACTIVO
                     if any("estado" in c.canales for c in app.conexiones)
                     else INTERVALO_ESTADO_IDLE)
        await asyncio.sleep(intervalo)
        try:
            delta = {}

            firma_lista = await app.a_hilo(app.lista.firma)
            if firma_lista != firma_lista_vista:
                firma_lista_vista = firma_lista
                delta["lista"] = await app.a_hilo(app.lista.leer)

            detector = await _leer_detector_fusionado(app)
            detector_cambio = detector != app.estado.get("detector")
            if detector_cambio:
                delta["detector"] = detector

            supervisor = await app.a_hilo(_leer_json, app.ajustes.estado_supervisor)
            if supervisor != app.estado.get("supervisor"):
                delta["supervisor"] = supervisor

            siguiendo = await app.a_hilo(_leer_json, app.ajustes.estado_siguiendo)
            if siguiendo != app.estado.get("siguiendo"):
                delta["siguiendo"] = siguiendo

            favoritas = await app.a_hilo(app.favoritas.leer)
            if favoritas != app.estado.get("favoritas"):
                delta["favoritas"] = favoritas

            sesion = await app.a_hilo(app.sesion.estado)
            if sesion != app.estado.get("sesion"):
                delta["sesion"] = sesion

            sesion_cb = await app.a_hilo(app.sesion_chaturbate.estado)
            if sesion_cb != app.estado.get("sesion_chaturbate"):
                delta["sesion_chaturbate"] = sesion_cb

            siguiendo_cb = await app.a_hilo(_leer_json, app.ajustes.estado_siguiendo_chaturbate)
            if siguiendo_cb != app.estado.get("siguiendo_chaturbate"):
                delta["siguiendo_chaturbate"] = siguiendo_cb

            motor = app.motor.estado()
            if motor != app.estado.get("motor"):
                delta["motor"] = motor

            if app.ultima_limpieza != app.estado.get("limpieza"):
                delta["limpieza"] = app.ultima_limpieza

            if app.login != app.estado.get("login"):
                delta["login"] = app.login

            if detector_cambio or "lista" in delta:
                detector_para_vivo = detector if detector_cambio else app.estado.get("detector")
                vivo = await app.a_hilo(
                    vivo_mod.sesiones_vivas, app.ajustes.streams, detector_para_vivo)
                if vivo != app.estado.get("vivo"):
                    delta["vivo"] = vivo

            reloj_disco += intervalo
            if reloj_disco >= INTERVALO_DISCO or "disco" not in app.estado:
                reloj_disco = 0.0
                disco = await app.a_hilo(app._disco)
                if disco != app.estado.get("disco"):
                    delta["disco"] = disco

            # SPEC 16: vista fresca (estado publicado + lo que cambio este
            # tick) para que salud.evaluar() no dependa de que justo HOY
            # haya cambiado disco/motor/detector -- cualquiera de los tres
            # puede venir solo en delta todavia sin aplicar a app.estado.
            vista = {**app.estado, **delta}
            salud = await app.a_hilo(salud_mod.evaluar, vista, app.ajustes)
            _agregar_alerta_actualizacion(app, salud)
            if salud != app.estado.get("salud"):
                delta["salud"] = salud

            criticos = any(a["clave"].startswith("disco_critico:") for a in salud)
            if criticos:
                if app.ajustes["parar_por_disco"] and not app.parada_por_disco:
                    m = app.motor.estado()
                    if (m["detector_vivo"] or m["supervisor_vivo"]
                            or m["directo_vivo"] or m["chaturbate_vivo"]):
                        app.parada_por_disco = True
                        _lanzar_tarea(app, "motor_parar", app.motor.parar)
            elif not any(a["tipo"] == "disco" for a in salud):
                # histeresis: el latch se suelta solo cuando NINGUN disco
                # sigue ni siquiera en el umbral de aviso, no apenas cruza
                # de vuelta el critico -- si no, un espacio que oscila
                # justo alrededor del umbral para y arranca sin parar.
                app.parada_por_disco = False

            # sincronizar() puede terminar llamando a icono.notify() (una
            # API de Windows): pasa por a_hilo como cualquier otro
            # sincrono de verdad, nunca directo desde la corutina.
            await app.a_hilo(app.notificador.sincronizar, salud, app.ajustes)

            if delta:
                app.estado.update(delta)
                app._publicar("estado", "estado", delta)
        except Exception:
            pass    # un tick que fallo no tiene que tirar el bucle entero


async def _bucle_cosecha(app):
    """Mismo trabajo que hacia nimbo-cosecha: cerrar solas las vistas en
    vivo temporales cuyo navegador se cerro de golpe y nunca llego a
    mandar el cierre (ver vivo_temporal.cosechar). Ademas, cada
    INTERVALO_LIMPIEZA, barre datos\\streams\\ con cosecha_streams -- el
    listdir de dos niveles no es gratis, por eso no corre cada tick."""
    reloj_limpieza = 0.0
    while True:
        await asyncio.sleep(INTERVALO_COSECHA)
        with contextlib.suppress(Exception):
            await app.a_hilo(vivo_temporal_mod.cosechar, app.lista, app.ajustes.dir_estado)

        reloj_limpieza += INTERVALO_COSECHA
        if reloj_limpieza < INTERVALO_LIMPIEZA:
            continue
        reloj_limpieza = 0.0
        with contextlib.suppress(Exception):
            detector = await _leer_detector_fusionado(app)
            resultado = await app.a_hilo(
                cosecha_streams_mod.barrer, app.ajustes.streams, detector,
                app.ajustes["dias_segmentos"])
            if resultado["borradas"]:
                app.ultima_limpieza = {
                    "cuando": time.time(),
                    "sesiones": len(resultado["borradas"]),
                    "bytes": resultado["bytes"],
                }


async def _bucle_miniaturas(app):
    """Genera de a TOPE_MINIATURAS_POR_TICK las portadas que le falten a
    datos\\grabaciones\\ (ver nimbo\\miniaturas.py), de mas nueva a mas
    vieja -- grabaciones.listar() ya devuelve los grupos en ese orden. Cada
    INTERVALO_BARRIDO_MINIATURAS borra las portadas cuyo .mp4 ya no existe,
    mismo ritmo que el barrido de datos\\streams\\ en _bucle_cosecha."""
    reloj_barrido = 0.0
    while True:
        await asyncio.sleep(INTERVALO_MINIATURAS)
        with contextlib.suppress(Exception):
            listado = await app.a_hilo(
                grabaciones_mod.listar, app.ajustes.grabaciones,
                app.ajustes.dir_estado, app.ajustes.ffmpeg)
            await app.a_hilo(
                miniaturas_mod.generar_faltantes, listado["grupos"],
                app.ajustes.grabaciones, app.ajustes.dir_miniaturas,
                app.ajustes.dir_estado, app.ajustes.ffmpeg,
                TOPE_MINIATURAS_POR_TICK)

        reloj_barrido += INTERVALO_MINIATURAS
        if reloj_barrido < INTERVALO_BARRIDO_MINIATURAS:
            continue
        reloj_barrido = 0.0
        with contextlib.suppress(Exception):
            nombres_vigentes = await app.a_hilo(
                lambda: set(os.listdir(app.ajustes.grabaciones)))
            await app.a_hilo(miniaturas_mod.barrer, app.ajustes.dir_miniaturas, nombres_vigentes)
            await app.a_hilo(miniaturas_mod.purgar_indice, app.ajustes.dir_estado, nombres_vigentes)


async def _bucle_licencia(app):
    """Revisa la licencia cada INTERVALO_LICENCIA (si el paquete la trae).

    Si vence con grabaciones en curso **no se mata nada**: se avisa por el
    canal `motor` y se para por el camino ordenado (Motor.parar, que manda
    "parar\\n" por stdin). Matar a lo bruto deja grabaciones sin unir -- ver
    CLAUDE.md, "Cuatro procesos".
    """
    if not licencia_mod.activa():
        return                     # copia de desarrollo: nada que revisar
    while True:
        await asyncio.sleep(INTERVALO_LICENCIA)
        with contextlib.suppress(Exception):
            r = await app.a_hilo(licencia_mod.revisar, app.ajustes.dir_estado)
            if r.get("aviso"):
                app._publicar("motor", "aviso",
                              {"texto": "licencia: " + r["aviso"],
                               "nivel": "aviso"})
                await app.a_hilo(app.notificador.sincronizar, [{
                    "clave": "licencia", "tipo": "licencia", "nivel": "aviso",
                    "titulo": "Licencia por vencer", "texto": r["aviso"],
                }], app.ajustes)
            if r["ok"]:
                continue
            app._publicar("motor", "aviso",
                          {"texto": "la licencia vencio -- parando todo de "
                                    "forma ordenada",
                           "nivel": "error"})
            _lanzar_tarea(app, "motor_parar", app.motor.parar)


async def _bucle_actualizaciones(app):
    """Al arrancar y cada INTERVALO_ACTUALIZACIONES (SPEC 17, C12).
    actualizaciones.verificar ya corta solo si buscar_actualizaciones esta
    apagado, y ya degrada en silencio sin red -- aca solo hay que guardar
    el resultado para que _bucle_estado arme la alerta y
    /api/actualizacion tenga algo que mostrar."""
    while True:
        with contextlib.suppress(Exception):
            app.actualizacion = await app.a_hilo(actualizaciones_mod.verificar, app.ajustes)
        await asyncio.sleep(INTERVALO_ACTUALIZACIONES)


def _fijar_login(app, fase, vence, motivo, sitio="stripchat"):
    app.login = {"fase": fase, "vence": vence, "motivo": motivo, "sitio": sitio}


def _sesion_de(app, sitio: str):
    """app.sesion_chaturbate si sitio=="chaturbate", si no app.sesion (el
    default de siempre) -- calco de _sitio_de (SPEC 18), usado por las
    rutas /api/sesion/* y por _tarea_login (SPEC 21)."""
    return app.sesion_chaturbate if sitio == "chaturbate" else app.sesion


def _ruta_siguiendo_de(app, sitio: str) -> str:
    return (app.ajustes.estado_siguiendo_chaturbate if sitio == "chaturbate"
            else app.ajustes.estado_siguiendo)


def _tarea_actualizar_de(sitio: str) -> str:
    """Nombre de tarea de _lanzar_tarea para "actualizar siguiendo" --
    desdoblado por sitio (SPEC 21, ver TOPE_TAREA): refrescar una lista
    no puede quedar deduplicada contra la otra."""
    return "sesion_actualizar_chaturbate" if sitio == "chaturbate" else "sesion_actualizar"


async def _tarea_login(app, sitio: str = "stripchat"):
    """SPEC 15: abre la ventana de Edge por el proxy, pide la captura a
    puente.py y espera hasta que venza a que entregue un candidato que
    verifique de verdad -- recien ahi lo guarda en sesion.json (o
    sesion_chaturbate.json, SPEC 21) y cierra todo. Nunca bloquea el pool
    mas de un pedido por vez (cada paso sincrono pasa por a_hilo, uno a
    la vez). Un solo login a la vez para los dos sitios: el dedupe de
    "sesion_login" en _lanzar_tarea_coro ya lo garantiza (ver
    servidor.py:_despachar_post)."""
    pre = await app.a_hilo(login_sitio_mod.precondiciones, app.ajustes, app.motor)
    if not pre["ok"]:
        _fijar_login(app, "imposible", None, pre["motivo"], sitio)
        return

    permiso = await app.a_hilo(login_sitio_mod.pedir, app.ajustes, sitio,
                                login_sitio_mod.SEGUNDOS_CAPTURA)
    proc = await app.a_hilo(login_sitio_mod.abrir_ventana, app.ajustes, sitio)
    _fijar_login(app, "esperando", permiso["vence"], None, sitio)

    sesion_obj = _sesion_de(app, sitio)
    cancelar = app._login_cancelar
    cancelar.clear()
    try:
        while time.time() < permiso["vence"]:
            if cancelar.is_set():
                _fijar_login(app, "cancelada", None, None, sitio)
                return
            await asyncio.sleep(1.0)

            candidato = await app.a_hilo(login_sitio_mod.recoger, app.ajustes, sitio)
            if candidato is None:
                continue

            _fijar_login(app, "verificando", permiso["vence"], None, sitio)
            r = await app.a_hilo(sesion_obj.verificar, {
                "headers": candidato["headers"], "cookies": candidato["cookies"],
                "url_origen": candidato["url"]})
            if r["autenticado"]:
                await app.a_hilo(sesion_obj.guardar_desde_captura, candidato)
                _fijar_login(app, "ok", None, None, sitio)
                _lanzar_tarea(app, _tarea_actualizar_de(sitio), sesion_obj.actualizar_siguiendo,
                              _ruta_siguiendo_de(app, sitio))
                return
            # Candidato que no verifico (401, red): se descarta y el
            # bucle sigue esperando -- no pisa una sesion que andaba.
            _fijar_login(app, "esperando", permiso["vence"], None, sitio)

        _fijar_login(app, "vencida", None,
                     "pasaron 5 minutos sin completar el login -- proba de nuevo", sitio)
    finally:
        await app.a_hilo(login_sitio_mod.cerrar_ventana, app.ajustes, proc)
        await app.a_hilo(login_sitio_mod.limpiar, app.ajustes)


def _sitio_de(app, sitio: str):
    """app.sitio_chaturbate si sitio=="chaturbate", si no app.sitio (el
    default de siempre) -- usado por las tres rutas /api/sitio/* (SPEC 18)."""
    return app.sitio_chaturbate if sitio == "chaturbate" else app.sitio


def _nombres_sin_estado(estado):
    """Mismo calculo que hacia main.js:nombresSinEstado -- las favoritas
    puramente locales, sin dato de "siguiendo" ni del supervisor, son las
    unicas que de verdad necesitan golpear /api/sitio/estado.

    SPEC 18: favoritas.json ahora es [{"nombre","sitio"}, ...], y "sitio"
    de cada entrada va en la clave de salida. SPEC 21: con_dato ya cubre
    los dos sitios -- siguiendo_chaturbate.json le da a una favorita de
    chaturbate el mismo ahorro de pedido que siguiendo.json ya le daba a
    una de stripchat (antes de este spec, chaturbate no tenia
    "Siguiendo"/supervisor, asi que SIEMPRE pedia su propio estado; ver
    CLAUDE.md). -> [(nombre, sitio), ...].
    """
    con_dato = set()
    for m in (estado.get("siguiendo") or {}).get("modelos") or []:
        u = m.get("username")
        if u:
            con_dato.add(("stripchat", u.lower()))
    for m in (estado.get("siguiendo_chaturbate") or {}).get("modelos") or []:
        u = m.get("username")
        if u:
            con_dato.add(("chaturbate", u.lower()))
    for e in (estado.get("supervisor") or {}).get("emisiones") or []:
        n = e.get("nombre")
        if n and e.get("estado"):
            con_dato.add(("stripchat", n.lower()))
    salida = []
    for f in (estado.get("favoritas") or []):
        nombre = f.get("nombre") or ""
        if not nombre:
            continue
        sitio = f.get("sitio") or "stripchat"
        if (sitio, nombre.lower()) in con_dato:
            continue
        salida.append((nombre, sitio))
    return salida


async def _refrescar_sitio(app, semaforo):
    """SPEC 18: dos sitios, un solo cupo (CUPO_SITIO) -- el tope existe
    por getaddrinfo() sin timeout, y duplicarlo duplicaria el riesgo de
    agotar el pool de hilos del servidor (ver Decisiones del spec). Las
    claves del resultado publicado por el canal "sitio" llevan el sitio
    adelante ("sitio/nombre", ver sitio.js:claveCache) para que dos
    homonimas de sitios distintos no compartan cache de miniatura."""
    favoritas = [(f.get("nombre") or "", f.get("sitio") or "stripchat")
                 for f in (app.estado.get("favoritas") or []) if f.get("nombre")]
    vpn = set((app.estado.get("lista") or {}).get("vpn") or [])
    sin_estado = _nombres_sin_estado(app.estado)
    if not favoritas and not sin_estado:
        return

    async def _buscar(nombre):
        # Solo stripchat: sitio_chaturbate.Chaturbate.buscar() siempre da
        # [] (sin endpoint de catalogo sin cuenta, ver su docstring) -- no
        # tiene sentido pagar el pedido para chaturbate.
        async with semaforo:
            return nombre, "stripchat", "avatar", await app.a_hilo(app.sitio.buscar, nombre)

    async def _estado(nombre, sitio):
        async with semaforo:
            if sitio == "chaturbate":
                r = await app.a_hilo(app.sitio_chaturbate.estado_cacheado, nombre)
            else:
                r = await app.a_hilo(app.sitio.estado_cacheado, nombre, nombre.lower() in vpn)
            return nombre, sitio, "estado", r

    tareas = [asyncio.ensure_future(_buscar(n)) for n, s in favoritas if s == "stripchat"]
    tareas += [asyncio.ensure_future(_estado(n, s)) for n, s in sin_estado]

    resultados = {}
    for tarea in asyncio.as_completed(tareas):
        try:
            nombre, sitio, forma, r = await tarea
        except Exception:
            continue
        clave = sitio + "/" + nombre.lower()
        entrada = resultados.setdefault(clave, {})
        if forma == "avatar":
            exacto = next((m for m in r if m["username"].lower() == nombre.lower()), None)
            if exacto:
                entrada["avatar"] = exacto.get("avatar", "")
                entrada["online"] = bool(exacto.get("online"))
        else:
            entrada["estado"] = r.get("estado", "")
            if sitio == "chaturbate":
                # Sin buscar() que traiga un avatarUrl: el snapshot publico
                # (siempre la misma forma de URL) hace de miniatura.
                entrada["avatar"] = app.sitio_chaturbate.url_snapshot(nombre)
                entrada["online"] = r.get("estado") == "public"

    if resultados:
        app._publicar("sitio", "sitio", resultados)


async def _bucle_sitio(app):
    """El setInterval(..., 15000) que antes vivia en main.js, ahora
    compartido por todas las pestanas y apagado cuando nadie mira
    "Modelos" -- ver AskUserQuestion del plan: esto es lo que de verdad
    cancela el lote, no un AbortController del lado del cliente."""
    semaforo = asyncio.Semaphore(CUPO_SITIO)
    while True:
        if not any("sitio" in c.canales for c in app.conexiones):
            await asyncio.sleep(1.0)
            continue
        with contextlib.suppress(Exception):
            await _refrescar_sitio(app, semaforo)
        await asyncio.sleep(INTERVALO_SITIO)


# ===========================================================================
# HTTP: helpers de respuesta
# ===========================================================================

_RAZONES = {
    200: "OK", 202: "Accepted", 206: "Partial Content",
    400: "Bad Request", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
    431: "Request Header Fields Too Large", 500: "Internal Server Error",
    501: "Not Implemented",
}


async def _responder_cabeceras(writer, codigo, cabeceras_extra):
    cabeceras = {"Connection": "close", "Cache-Control": "no-store"}
    cabeceras.update(cabeceras_extra)
    texto = "HTTP/1.1 %d %s\r\n" % (codigo, _RAZONES.get(codigo, ""))
    for k, v in cabeceras.items():
        texto += "%s: %s\r\n" % (k, v)
    texto += "\r\n"
    writer.write(texto.encode("latin-1"))
    await writer.drain()


async def _responder(writer, codigo, cabeceras_extra, cuerpo=b""):
    cabeceras = dict(cabeceras_extra)
    cabeceras["Content-Length"] = str(len(cuerpo))
    await _responder_cabeceras(writer, codigo, cabeceras)
    if cuerpo:
        writer.write(cuerpo)
        await writer.drain()


async def _json(writer, codigo, datos):
    cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    await _responder(writer, codigo,
                      {"Content-Type": "application/json; charset=utf-8"}, cuerpo)


def _valor(qs, clave, defecto=""):
    return (qs.get(clave) or [defecto])[0]


def _token_valido(app, qs):
    return secrets.compare_digest(_valor(qs, "t"), app.token)


def _cookie(cabeceras, nombre):
    """El valor de una cookie del header "Cookie: a=1; b=2", o "" si no
    esta. email.message.Message.get() devuelve ese header tal cual, sin
    parsear -- no hay Cookie: en ningun otro lado del servidor todavia."""
    crudo = cabeceras.get("Cookie") or ""
    for parte in crudo.split(";"):
        if "=" not in parte:
            continue
        clave, valor = parte.strip().split("=", 1)
        if clave == nombre:
            return valor
    return ""


def _origen_valido(app, cabeceras):
    """Defensa en profundidad, no la puerta: el token sigue siendo lo
    que autoriza. Un GET same-origin no manda 'Origin' -- exigirla
    rompería la interfaz entera, asi que sin la cabecera esto da True.
    Con la cabecera, solo vale la propia ventana de Nimbo (o, con
    acceso_lan prendido, la propia IP de LAN -- ahi es donde de verdad
    escucha el socket, ver Servidor._iniciar)."""
    origen = cabeceras.get("Origin")
    if not origen:
        return True
    validos = ("http://127.0.0.1:%d" % app.puerto,
               "http://localhost:%d" % app.puerto)
    if origen in validos:
        return True
    if app.ajustes["acceso_lan"]:
        return origen == "http://%s:%d" % (lan_mod.ip_lan(), app.puerto)
    return False


# -- estaticos, con el traversal cerrado (recomendacion 6) ------------------

def _resolver_estatico(dir_web, ruta_rel):
    """-> ruta absoluta dentro de dir_web, o None. Mismo patron que
    grabaciones.py:_resolver y vivo.py:_resolver (realpath +
    commonpath): un componente con "\\" o ":" (la forma en que
    "/..\\..\\sesion.json" se cuela si solo se filtra por "/") se
    rechaza directo, antes de llegar a join()."""
    if not ruta_rel or ruta_rel == "/":
        ruta_rel = "/index.html"
    partes = [p for p in ruta_rel.split("/") if p]
    for p in partes:
        if p in (".", "..") or "\\" in p or ":" in p:
            return None
    destino = os.path.join(dir_web, *partes) if partes else dir_web
    base = os.path.realpath(dir_web)
    real = os.path.realpath(destino)
    if os.path.commonpath([base, real]) != base:
        return None
    return destino


async def _servir_estatico(app, ruta, writer):
    destino = _resolver_estatico(app.dir_web, ruta)
    if destino is None:
        await _json(writer, 404, {"error": "no encontrado"})
        return
    cuerpo = await app.a_hilo(_leer_bytes, destino)
    if cuerpo is None:
        await _json(writer, 404, {"error": "no encontrado"})
        return
    tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
    await _responder(writer, 200, {"Content-Type": tipo}, cuerpo)


# -- video con Range (206) y bytes CMAF sin Range ---------------------------

_RE_RANGO = re.compile(r"bytes=(\d*)-(\d*)")


def _abrir_en(ruta, inicio):
    f = open(ruta, "rb")
    f.seek(inicio)
    return f


async def _servir_video(app, writer, ruta, cabecera_rango, cabeceras):
    """GET con soporte de Range: sin esto el <video> puede arrancar pero
    no puede saltar a la mitad de un archivo de varios GB. Nunca carga el
    archivo entero en memoria -- lee y escribe en bloques de 1 MiB, cada
    uno en el ejecutor para no bloquear el loop con el read() sincrono.

    El chequeo de Origin ya lo hizo _manejar_get (la ruta empieza con
    /api/), pero se repite aca mismo: defensa en profundidad, mismo 403 y
    mismo cuerpo que los demas, por si el dia de mañana algo mas llama
    a esta funcion sin pasar por ese gate."""
    if not _origen_valido(app, cabeceras):
        await _json(writer, 403, {"error": "origen invalido"})
        return
    try:
        tam = await app.a_hilo(os.path.getsize, ruta)
    except OSError:
        await _json(writer, 404, {"error": "no encontrado"})
        return
    tipo = mimetypes.guess_type(ruta)[0] or "video/mp4"

    inicio, fin, codigo = 0, tam - 1, 200
    if cabecera_rango:
        m = _RE_RANGO.match(cabecera_rango.strip())
        if m:
            codigo = 206
            if m.group(1):
                inicio = int(m.group(1))
                fin = int(m.group(2)) if m.group(2) else tam - 1
            elif m.group(2):
                inicio = max(0, tam - int(m.group(2)))
                fin = tam - 1
    inicio = max(0, min(inicio, tam - 1 if tam else 0))
    fin = max(inicio, min(fin, tam - 1 if tam else 0))
    longitud = (fin - inicio + 1) if tam else 0

    cabeceras = {"Content-Type": tipo, "Accept-Ranges": "bytes",
                 "Content-Length": str(longitud)}
    if codigo == 206:
        cabeceras["Content-Range"] = "bytes %d-%d/%d" % (inicio, fin, tam)
    await _responder_cabeceras(writer, codigo, cabeceras)

    try:
        f = await app.a_hilo(_abrir_en, ruta, inicio)
        try:
            restante = longitud
            while restante > 0:
                bloque = await app.a_hilo(f.read, min(1024 * 1024, restante))
                if not bloque:
                    break
                writer.write(bloque)
                await writer.drain()
                restante -= len(bloque)
        finally:
            await app.a_hilo(f.close)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass   # el usuario cerro el modal o salto a otra parte a mitad


async def _servir_bytes(writer, datos, tipo):
    """Un fragmento CMAF entero, de una, sin Range: se appendea completo
    a un SourceBuffer -- un 206 parcial rompe appendBuffer."""
    await _responder(writer, 200, {"Content-Type": tipo, "Accept-Ranges": "none"}, datos)


# ===========================================================================
# Acceso LAN (SPEC 17, C10): pagina de login y fuerza bruta
# ===========================================================================
#
# El celular nunca llega a ver el token por la red: entra a "/" sin ?t=,
# recibe esta pagina minima, manda la contrasena a /api/lan/login y, si
# vale, se lleva una cookie HMAC propia (nimbo\lan.py) que en la vuelta
# siguiente a "/" alcanza para que el servidor lo mande con el token real
# -- api.js sigue leyendo ?t= de la URL exactamente igual que siempre.

_LOGIN_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nimbo</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background:#13161c; color:#e6e6e6;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
  form { background:#1c2029; padding:28px 32px; border-radius:8px; width:260px; }
  h1 { font-size:18px; margin:0 0 16px; }
  input { width:100%; box-sizing:border-box; padding:10px; margin-bottom:12px;
          border-radius:4px; border:1px solid #333; background:#0f1115; color:#e6e6e6; }
  button { width:100%; padding:10px; border:0; border-radius:4px; background:#3c78ff;
           color:#fff; font-weight:600; cursor:pointer; }
  p.error { color:#ff6b6b; font-size:13px; margin:0 0 12px; display:none; }
</style>
</head>
<body>
<form id="f">
  <h1>Nimbo</h1>
  <p class="error" id="error"></p>
  <input type="password" id="pw" placeholder="contrasena" autofocus autocomplete="current-password">
  <button type="submit">Entrar</button>
</form>
<script>
document.getElementById("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = document.getElementById("error");
  err.style.display = "none";
  try {
    const r = await fetch("/api/lan/login", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({pw: document.getElementById("pw").value}),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      err.textContent = d.error || "no se pudo entrar";
      err.style.display = "block";
      return;
    }
    location.href = "/";
  } catch (e) {
    err.textContent = "sin conexion con Nimbo";
    err.style.display = "block";
  }
});
</script>
</body>
</html>"""


async def _servir_login(writer):
    await _responder(writer, 200, {"Content-Type": "text/html; charset=utf-8"},
                      _LOGIN_HTML.encode("utf-8"))


def _ip_cliente(writer) -> str:
    info = writer.get_extra_info("peername")
    return info[0] if info else "?"


def _lan_bloqueada(app, ip) -> bool:
    hasta = app.lan_bloqueos.get(ip)
    return bool(hasta and time.time() < hasta)


def _lan_fallo(app, ip) -> None:
    n = app.lan_intentos.get(ip, 0) + 1
    if n >= LAN_MAX_INTENTOS:
        app.lan_bloqueos[ip] = time.time() + LAN_BLOQUEO_S
        app.lan_intentos[ip] = 0
    else:
        app.lan_intentos[ip] = n


def _lan_ok(app, ip) -> None:
    app.lan_intentos.pop(ip, None)
    app.lan_bloqueos.pop(ip, None)


async def _lan_login(app, cuerpo, writer):
    """POST /api/lan/login -- la unica ruta de este servidor que no pide
    ?t=: todavia no hay token que pedirle a un celular que recien entro.
    El gate es la contrasena (mas el bloqueo de fuerza bruta); igual se
    corta temprano si acceso_lan esta apagado, para no dejar una puerta
    que compare contra un hash vacio."""
    if not app.ajustes["acceso_lan"]:
        await _json(writer, 404, {"error": "acceso LAN apagado"})
        return
    ip = _ip_cliente(writer)
    if _lan_bloqueada(app, ip):
        await _json(writer, 403, {"error": "demasiados intentos -- espera un minuto"})
        return
    pw = cuerpo.get("pw") or ""
    ok = await app.a_hilo(lan_mod.verificar_password, pw,
                          app.ajustes["lan_pass_hash"], app.ajustes["lan_pass_sal"])
    if not ok:
        _lan_fallo(app, ip)
        await _json(writer, 403, {"error": "contrasena incorrecta"})
        return
    _lan_ok(app, ip)
    cookie = lan_mod.emitir_cookie(app.ajustes["lan_cookie_clave"])
    cabeceras = {
        "Content-Type": "application/json; charset=utf-8",
        "Set-Cookie": "nimbo_lan=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Lax"
                     % (cookie, 30 * 86400),
    }
    await _responder(writer, 200, cabeceras, json.dumps({"ok": True}).encode("utf-8"))


# ===========================================================================
# HTTP: rutas
# ===========================================================================

async def _manejar_get(app, ruta, qs, cabeceras, writer):
    # Acceso LAN: "/" sin token valido no es un 403 -- es la pagina de
    # login, o (con una cookie de sesion LAN vigente) un redirect directo
    # al token real. Nunca se activa con acceso_lan apagado: ahi "/" se
    # comporta exactamente igual que siempre, token o 403.
    if (ruta in ("/", "/index.html") and app.ajustes["acceso_lan"]
            and not _token_valido(app, qs)):
        cookie = _cookie(cabeceras, "nimbo_lan")
        payload = (lan_mod.verificar_cookie(app.ajustes["lan_cookie_clave"], cookie)
                  if cookie else None)
        if payload:
            await _responder(writer, 302, {"Location": "/?t=" + app.token})
        else:
            await _servir_login(writer)
        return

    necesita_token = ruta in ("/", "/index.html") or ruta.startswith("/api/")
    if necesita_token and not _token_valido(app, qs):
        await _json(writer, 403, {"error": "token invalido"})
        return
    if necesita_token and not _origen_valido(app, cabeceras):
        await _json(writer, 403, {"error": "origen invalido"})
        return

    if ruta == "/api/estado":
        await _json(writer, 200, app.estado or await app.a_hilo(app.estado_completo))
    elif ruta == "/api/lista":
        await _json(writer, 200, await app.a_hilo(app.lista.leer))
    elif ruta == "/api/sitio/buscar":
        # sitio= con default "stripchat" (SPEC 18): el cliente de hoy nunca
        # manda ese parametro, asi que sigue andando identico a como
        # andaba antes de este spec.
        sitio_obj = _sitio_de(app, _valor(qs, "sitio"))
        resultados = await app.a_hilo(sitio_obj.buscar, _valor(qs, "q"))
        await _json(writer, 200, {"resultados": resultados})
    elif ruta == "/api/sitio/estado":
        nombre = _valor(qs, "nombre")
        if not nombre:
            await _json(writer, 400, {"error": "falta 'nombre'"})
            return
        sitio = _valor(qs, "sitio") or "stripchat"
        if sitio == "chaturbate":
            resultado = await app.a_hilo(app.sitio_chaturbate.estado_cacheado, nombre)
        else:
            lista_actual = await app.a_hilo(app.lista.leer)
            vpn = nombre.lower() in lista_actual["vpn"]
            resultado = await app.a_hilo(app.sitio.estado_cacheado, nombre, vpn)
        await _json(writer, 200, resultado)
    elif ruta == "/api/sitio/imagen":
        # El cupo es lo unico que acota de verdad cuanto del pool de 12
        # hilos puede comerse esto: la miniatura es un HTTPS saliente, y
        # getaddrinfo() no respeta el timeout (ver sitio.TOPE_IMAGEN).
        # Mismo criterio que CUPO_SITIO con _bucle_sitio -- compartido
        # entre los dos sitios (SPEC 18, Decisiones).
        sitio_obj = _sitio_de(app, _valor(qs, "sitio"))
        async with app.cupo_imagen:
            resultado = await app.a_hilo(sitio_obj.imagen, _valor(qs, "url"))
        if resultado is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        tipo, datos = resultado
        # La UNICA respuesta cacheable de este servidor (el resto va con el
        # "no-store" de _responder_cabeceras, que este extra pisa via
        # .update()). La URL es snapshot/<id>/<snapshotTimestamp>: su
        # contenido no cambia nunca, y el timestamp solo se renueva cuando
        # el usuario aprieta Actualizar (sesion.py:_url_snapshot). Sin esto
        # el navegador re-bajaba la MISMA imagen una vez por segundo por
        # modelo -- ver specs\congelado_interfaz.md.
        await _responder(writer, 200, {
            "Content-Type": tipo,
            "Cache-Control": "public, max-age=86400, immutable",
        }, datos)
    elif ruta == "/api/licencia":
        # Solo la foto (nunca la clave: ver licencia.estado). En un
        # paquete sin licencia devuelve {"activa": false} y la interfaz
        # no muestra nada.
        await _json(writer, 200,
                    await app.a_hilo(licencia_mod.estado, app.ajustes.dir_estado))
    elif ruta == "/api/preflight":
        filas = await app.a_hilo(preflight_mod.comprobar, app.ajustes)
        await _json(writer, 200, {"filas": filas})
    elif ruta == "/api/registro":
        fuente = _valor(qs, "fuente", "detector")
        cursor = _valor(qs, "cursor") or None   # opaco: registro.leer() lo valida
        try:
            limite = min(int(_valor(qs, "limite", "400")), 1000)
        except ValueError:
            await _json(writer, 400, {"error": "limite invalido"})
            return
        try:
            resultado = await app.a_hilo(
                registro_mod.leer, app.ajustes.dir_estado, fuente, limite, cursor,
                _valor(qs, "nivel"), _valor(qs, "q"))
        except ValueError as e:
            await _json(writer, 400, {"error": str(e)})
            return
        await _json(writer, 200, resultado)
    elif ruta == "/api/grabaciones":
        a = app.ajustes
        resultado = await app.a_hilo(
            grabaciones_mod.listar, a.grabaciones, a.dir_estado, a.ffmpeg, a.dir_miniaturas)
        # SPEC 20: el total por unir (la en vuelo + las encoladas de los
        # tres grabadores) sale de app.estado, la cache de _bucle_estado,
        # que ya tiene los tres "uniendo" fusionados por
        # estado_captura.fusionar(). Sin tocar _bucle_estado ni el delta: el
        # progreso de la union NO va por WebSocket (esta pestana ya se
        # refresca sola cada 4s y se apaga al salir).
        det = (app.estado or {}).get("detector") or {}
        resultado.setdefault("union", {})["por_unir"] = (
            int(det.get("uniendo") or 0)
            + int((det.get("directo") or {}).get("uniendo") or 0)
            + int((det.get("chaturbate") or {}).get("uniendo") or 0))
        await _json(writer, 200, resultado)
    elif ruta == "/api/grabaciones/video":
        ruta_video = await app.a_hilo(
            grabaciones_mod.ruta_absoluta, app.ajustes.grabaciones, _valor(qs, "archivo"))
        if ruta_video is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _servir_video(app, writer, ruta_video, cabeceras.get("Range"), cabeceras)
    elif ruta == "/api/grabaciones/miniatura":
        # Cacheable (igual que /api/sitio/imagen): el nombre de la portada
        # viene de grabaciones.listar() y el cliente lo pide con &v=<mtime>
        # en la query, asi que si el .mp4 cambia la URL cambia con el.
        ruta_jpg = await app.a_hilo(
            miniaturas_mod.ruta_absoluta, app.ajustes.dir_miniaturas, _valor(qs, "archivo"))
        if ruta_jpg is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        datos = await app.a_hilo(_leer_bytes, ruta_jpg)
        if datos is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _responder(writer, 200, {
            "Content-Type": "image/jpeg",
            "Cache-Control": "public, max-age=86400, immutable",
        }, datos)
    elif ruta == "/api/preferencias":
        a = app.ajustes
        await _json(writer, 200, {
            "arrancar_al_abrir": a["arrancar_al_abrir"], "bandeja": a["bandeja"],
            "ver_navegador": a["ver_navegador"],
            "inicio_automatico": await app.a_hilo(inicio_mod.esta_activo),
            "parar_por_disco": a["parar_por_disco"],
            "notif_rendido": a["notif_rendido"], "notif_degradado": a["notif_degradado"],
            "notif_disco": a["notif_disco"], "notif_licencia": a["notif_licencia"],
            "chaturbate_activo": a["chaturbate_activo"],
        })
    elif ruta == "/api/salud":
        # Fuera del delta de 1 Hz a proposito (ver salud.canario): KEY_MAP
        # es estatica por build, se calcula una vez y se cachea.
        canario = await app.a_hilo(salud_mod.canario, app.ajustes.scripts)
        await _json(writer, 200, {"canario": canario})
    elif ruta == "/api/rutas":
        await _json(writer, 200, await app.a_hilo(_rutas_snapshot, app.ajustes))
    elif ruta == "/api/vivo/sesiones":
        detector = await _leer_detector_fusionado(app)
        sesiones = await app.a_hilo(vivo_mod.sesiones_vivas, app.ajustes.streams, detector)
        await _json(writer, 200, {"sesiones": sesiones})
    elif ruta == "/api/vivo/segmentos":
        sesion = _valor(qs, "sesion")
        desde = (qs.get("desde") or [None])[0]
        try:
            limite = min(int(_valor(qs, "limite", "40")), 200)
            atras = min(int(_valor(qs, "atras", "3")), 20)
        except ValueError:
            await _json(writer, 400, {"error": "limite/atras invalido"})
            return
        resultado = await app.a_hilo(vivo_mod.listar, app.ajustes.streams, sesion, desde, limite, atras)
        if not resultado:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _json(writer, 200, resultado)
    elif ruta == "/api/vivo/init":
        datos = await app.a_hilo(vivo_mod.leer_init, app.ajustes.streams, _valor(qs, "sesion"))
        if datos is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _servir_bytes(writer, datos, "video/mp4")
    elif ruta == "/api/vivo/segmento":
        datos = await app.a_hilo(
            vivo_mod.leer_segmento, app.ajustes.streams, _valor(qs, "sesion"), _valor(qs, "nombre"))
        if datos is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _servir_bytes(writer, datos, "video/mp4")
    elif ruta == "/api/streams":
        a = app.ajustes
        detector = await _leer_detector_fusionado(app)
        resultado = await app.a_hilo(
            cosecha_streams_mod.resumen, a.streams, detector, a["dias_segmentos"])
        await _json(writer, 200, resultado)
    elif ruta == "/api/lan/estado":
        await _json(writer, 200, {
            "acceso_lan": app.ajustes["acceso_lan"],
            "puerto_lan": app.ajustes["puerto_lan"],
            "configurada": bool(app.ajustes["lan_pass_hash"]),
            "ip_lan": await app.a_hilo(lan_mod.ip_lan),
        })
    elif ruta == "/api/lan/qr":
        url = "http://%s:%d/" % (await app.a_hilo(lan_mod.ip_lan), app.ajustes["puerto_lan"])
        try:
            png = await app.a_hilo(qr_mod.generar_png, url)
        except ValueError as e:
            await _json(writer, 400, {"error": str(e)})
            return
        await _responder(writer, 200, {"Content-Type": "image/png"}, png)
    elif ruta == "/api/actualizacion":
        resultado = {"version_actual": version_mod.VERSION,
                    "buscar_actualizaciones": app.ajustes["buscar_actualizaciones"]}
        if app.actualizacion:
            resultado.update(app.actualizacion)
        await _json(writer, 200, resultado)
    elif ruta.startswith("/api/"):
        await _json(writer, 404, {"error": "no encontrado"})
    else:
        await _servir_estatico(app, ruta, writer)


def _tarea_terminada(app, nombre, tarea):
    """done_callback: saca la tarea de app._tareas (si no, queda
    referenciada para siempre, aunque ya termino) y avisa si fallo --
    antes la Task nunca se awaiteaba, asi que una excepcion de disco en
    sesion.actualizar_siguiendo() se perdia entera, sin log ni aviso en la
    ventana."""
    app._tareas.pop(nombre, None)
    if tarea.cancelled():
        return
    exc = tarea.exception()
    if exc is not None:
        app.motor._aviso("la tarea %s fallo: %s" % (nombre, exc), nivel="error")


def _lanzar_tarea(app, nombre, fn, *args):
    """Dedup de los 202+hilo: si ya hay una tarea con este nombre
    corriendo, un segundo POST no lanza una segunda -- motor.arrancar()/
    parar() ya tienen su propio lock, pero asi ni se llega a pedir el
    hilo de mas.

    -> True si lanzo, False si lo descarto por duplicado. Quien llama TIENE
    que pasarle ese dato al cliente: antes devolvia 202 {"aceptado": true}
    igual, asi que "Actualizar" apretado mientras la vuelta anterior
    todavia corria (hasta 45s, ver sesion.TOPE_TOTAL_S) no hacia nada y no
    avisaba nada -- se veia como un boton roto.

    Watchdog: si la tarea existente lleva mas de TOPE_TAREA[nombre] sin
    terminar, se la da por perdida y se lanza una nueva de todas formas --
    sin esto, un hilo del pool colgado en getaddrinfo() (que NO respeta el
    timeout= de urllib, ver sitio.py:TOPE_IMAGEN) dejaba "ya_corriendo":
    true para SIEMPRE, hasta reiniciar Nimbo: ni sesion.actualizar ni
    motor.arrancar/parar volvian a correr nunca mas.

    OJO, esto es mitigacion y no cura: libera la BANDERA, no el hilo. El
    hilo colgado en getaddrinfo() sigue perdido en el pool -- con
    EJECUTOR_HILOS=12, unos pocos cuelgues sucesivos igual dejan el
    servidor sin ranuras. La cura real es que cada operacion saliente
    tenga su propio tope efectivo (sesion.py ya lo tiene con
    TOPE_TOTAL_S, chequeado entre paginas); esto solo evita que un cuelgue
    deje la bandera trabada para siempre."""
    if _tarea_duplicada(app, nombre):
        return False
    nueva = app.loop.create_task(app.a_hilo(fn, *args))
    _registrar_tarea(app, nombre, nueva)
    return True


def _tarea_duplicada(app, nombre) -> bool:
    """-> True si ya hay una tarea 'nombre' corriendo (y no vencio el
    watchdog de TOPE_TAREA). Extraido de _lanzar_tarea para que
    _lanzar_tarea_coro (SPEC 15, _tarea_login es una corutina propia, no
    un fn sincrono para el pool) comparta el mismo dedupe."""
    existente = app._tareas.get(nombre)
    if existente is None:
        return False
    tarea, inicio = existente
    if tarea.done():
        return False
    tope = TOPE_TAREA.get(nombre, TOPE_TAREA_DEFECTO)
    if time.monotonic() - inicio < tope:
        return True
    app.motor._aviso(
        "la tarea %s lleva mas de %ds -- la doy por perdida y "
        "lanzo otra" % (nombre, int(tope)), nivel="error")
    app._tareas.pop(nombre, None)
    return False


def _registrar_tarea(app, nombre, nueva):
    nueva.add_done_callback(lambda t: _tarea_terminada(app, nombre, t))
    app._tareas[nombre] = (nueva, time.monotonic())


def _lanzar_tarea_coro(app, nombre, coro):
    """Mismo dedupe y mismo watchdog que _lanzar_tarea, pero para una
    corutina ya async (_tarea_login): no pasa por app.a_hilo porque no es
    una funcion sincrona para el pool, usa await asyncio.sleep() propio."""
    if _tarea_duplicada(app, nombre):
        coro.close()
        return False
    nueva = app.loop.create_task(coro)
    _registrar_tarea(app, nombre, nueva)
    return True


def _buscar_actualizacion(app):
    app.actualizacion = actualizaciones_mod.verificar(app.ajustes, forzar=True)


async def _despachar_post(app, ruta, cuerpo):
    """-> (codigo, datos) | None (None = ruta desconocida, 404)."""
    if ruta == "/api/lista/agregar":
        # sitio="chaturbate" (SPEC 18): marcar_sitio ya suma la linea de
        # seleccion si todavia no existia (calco de marcar_vpn), asi que
        # alcanza con UN pedido para "agregar y marcar" -- no dos.
        if (cuerpo.get("sitio") or "stripchat") == "chaturbate":
            await app.a_hilo(app.lista.marcar_sitio, cuerpo["nombre"], "chaturbate")
        else:
            # SPEC 20: con "stripchat" explicito, agregar_modelo saca el
            # nombre de @chaturbate= en la misma escritura -- una marca
            # huerfana que ya estuviera en el archivo no se hereda.
            await app.a_hilo(app.lista.agregar_modelo, cuerpo["nombre"], "stripchat")
    elif ruta == "/api/lista/quitar":
        # `or ""` y NO `or "stripchat"` a proposito (SPEC 20): un cliente
        # viejo (una pestana sin recargar tras la actualizacion) que no
        # manda sitio tiene que seguir comportandose como hoy y no tocar
        # ninguna marca. El otro lado del fix (agregar_modelo) limpia la
        # huerfana cuando el nombre vuelva.
        await app.a_hilo(app.lista.quitar_modelo, cuerpo["nombre"],
                          cuerpo.get("sitio") or "")
    elif ruta == "/api/lista/vpn":
        await app.a_hilo(app.lista.marcar_vpn, cuerpo["nombre"], bool(cuerpo.get("activo")))
    elif ruta == "/api/lista/directiva":
        await app.a_hilo(app.lista.establecer_directiva, cuerpo["clave"], cuerpo["valor"])
    elif ruta == "/api/favoritas/agregar":
        await app.a_hilo(app.favoritas.agregar, cuerpo["nombre"], cuerpo.get("sitio") or "stripchat")
        return 200, {"favoritas": await app.a_hilo(app.favoritas.leer)}
    elif ruta == "/api/favoritas/quitar":
        await app.a_hilo(app.favoritas.quitar, cuerpo["nombre"], cuerpo.get("sitio") or "stripchat")
        return 200, {"favoritas": await app.a_hilo(app.favoritas.leer)}
    elif ruta == "/api/sesion/guardar":
        # SPEC 21: "sitio" en el cuerpo, default "stripchat" -- misma
        # convencion que /api/lista/* y /api/favoritas/* para que un
        # cliente viejo siga funcionando idéntico.
        sesion_obj = _sesion_de(app, cuerpo.get("sitio") or "stripchat")
        resultado = await app.a_hilo(sesion_obj.guardar_desde_curl, cuerpo["curl"])
        return 200, {"sesion": resultado}
    elif ruta == "/api/sesion/olvidar":
        sitio = cuerpo.get("sitio") or "stripchat"
        sesion_obj = _sesion_de(app, sitio)
        await app.a_hilo(sesion_obj.borrar)

        def _quitar_siguiendo():
            with contextlib.suppress(OSError):
                os.remove(_ruta_siguiendo_de(app, sitio))

        await app.a_hilo(_quitar_siguiendo)
        return 200, {"sesion": await app.a_hilo(sesion_obj.estado)}
    elif ruta == "/api/sesion/login/iniciar":
        sitio = cuerpo.get("sitio") or "stripchat"
        # Nombre de tarea COMPARTIDO a proposito (ver TOPE_TAREA): un solo
        # perfil_login\ y un solo login a la vez, para los dos sitios --
        # el segundo POST mientras el primero corre contesta ya_corriendo.
        lanzada = _lanzar_tarea_coro(app, "sesion_login", _tarea_login(app, sitio))
        return 202, {"aceptado": lanzada, "ya_corriendo": not lanzada}
    elif ruta == "/api/sesion/login/cancelar":
        app._login_cancelar.set()
        return 200, {"ok": True}
    elif ruta == "/api/sesion/actualizar":
        # 202 y no 409 aunque se descarte: un 409 hace que api.js:enviar()
        # tire excepcion y el footer lo muestre como "error: ...", que es la
        # lectura equivocada para un doble click.
        sitio = cuerpo.get("sitio") or "stripchat"
        sesion_obj = _sesion_de(app, sitio)
        lanzada = _lanzar_tarea(app, _tarea_actualizar_de(sitio),
                                 sesion_obj.actualizar_siguiendo,
                                 _ruta_siguiendo_de(app, sitio))
        return 202, {"aceptado": lanzada, "ya_corriendo": not lanzada}
    elif ruta == "/api/vivo/abrir":
        if not app.motor.estado()["supervisor_vivo"]:
            return 409, {"error": "el motor no esta corriendo"}
        resultado = await app.a_hilo(
            vivo_temporal_mod.abrir, app.lista, app.ajustes.dir_estado, cuerpo["nombre"])
        return 200, resultado
    elif ruta == "/api/vivo/latido":
        await app.a_hilo(vivo_temporal_mod.latido, app.ajustes.dir_estado, cuerpo["nombre"])
        return 200, {"ok": True}
    elif ruta == "/api/vivo/cerrar":
        await app.a_hilo(
            vivo_temporal_mod.cerrar, app.lista, app.ajustes.dir_estado, cuerpo["nombre"])
        return 200, {"ok": True}
    elif ruta == "/api/motor/arrancar":
        lanzada = _lanzar_tarea(app, "motor_arrancar", app.motor.arrancar)
        return 202, {"aceptado": lanzada, "ya_corriendo": not lanzada}
    elif ruta == "/api/motor/parar":
        lanzada = _lanzar_tarea(app, "motor_parar", app.motor.parar)
        return 202, {"aceptado": lanzada, "ya_corriendo": not lanzada}
    elif ruta == "/api/preferencias":
        clave = cuerpo["clave"]
        if clave not in ("arrancar_al_abrir", "bandeja", "ver_navegador",
                          "parar_por_disco", "notif_rendido", "notif_degradado",
                          "notif_disco", "notif_licencia", "acceso_lan",
                          "buscar_actualizaciones", "chaturbate_activo"):
            return 400, {"error": "preferencia desconocida"}
        app.ajustes[clave] = bool(cuerpo["valor"])
        await app.a_hilo(app.ajustes.guardar)
        return 200, {clave: app.ajustes[clave]}
    elif ruta == "/api/rutas":
        clave = cuerpo["clave"]
        if clave not in _CLAVES_RUTAS:
            return 400, {"error": "clave desconocida"}
        a = app.ajustes
        if clave in ("dir_segmentos", "dir_grabaciones"):
            m = app.motor.estado()
            if (m["detector_vivo"] or m["supervisor_vivo"] or m["directo_vivo"]
                    or m["chaturbate_vivo"] or m["parando"]):
                return 409, {"error": "el motor esta corriendo -- parala "
                                      "antes de cambiar esta carpeta"}
        valor = cuerpo["valor"]
        if clave == "union_modo":
            if valor not in ("auto", "carpeta", "apagado"):
                return 400, {"error": "modo invalido"}
        else:
            valor = (valor or "").strip()
            if valor and valor.lower() != "auto":
                r = await app.a_hilo(rutas_mod.validar, valor)
                if not r["ok"]:
                    return 400, {"error": r["motivo"]}
        a[clave] = valor
        await app.a_hilo(a.guardar)
        await app.a_hilo(a.resolver_rutas)
        return 200, _rutas_snapshot(a)
    elif ruta == "/api/rutas/examinar":
        clave = cuerpo["clave"]
        titulos = {
            "dir_segmentos": "Elegir carpeta de segmentos",
            "dir_grabaciones": "Elegir carpeta de grabaciones",
            "dir_union": "Elegir carpeta del escenario de union",
        }
        if clave not in titulos:
            return 400, {"error": "clave desconocida"}
        inicial = app.ajustes[clave] or ""
        elegida = await app.a_hilo(rutas_mod.elegir_carpeta, titulos[clave], inicial)
        return 200, {"ruta": elegida}
    elif ruta == "/api/preflight/certificado":
        # El unico "arreglo" que ofrece el banner de puesta a punto (ver
        # preflight.instalar_certificado): certutil es un subprocess, va
        # al pool como todo lo sincrono.
        return 200, await app.a_hilo(preflight_mod.instalar_certificado)
    elif ruta == "/api/sistema/inicio_automatico":
        if cuerpo.get("activo"):
            await app.a_hilo(inicio_mod.activar)
        else:
            await app.a_hilo(inicio_mod.desactivar)
        return 200, {"inicio_automatico": await app.a_hilo(inicio_mod.esta_activo)}
    elif ruta == "/api/grabaciones/borrar":
        archivos = cuerpo["archivos"]
        if not isinstance(archivos, list) or not archivos:
            return 400, {"error": "falta 'archivos' (lista)"}
        resultado = await app.a_hilo(grabaciones_mod.eliminar, app.ajustes.grabaciones, archivos)
        return 200, resultado
    elif ruta == "/api/grabaciones/abrir_carpeta":
        ok = await app.a_hilo(
            grabaciones_mod.abrir_carpeta, app.ajustes.grabaciones, cuerpo.get("archivo", ""))
        return 200, {"abierto": ok}
    elif ruta == "/api/streams/limpiar":
        a = app.ajustes
        detector = await _leer_detector_fusionado(app)
        resultado = await app.a_hilo(
            cosecha_streams_mod.barrer, a.streams, detector, a["dias_segmentos"])
        if resultado["borradas"]:
            app.ultima_limpieza = {
                "cuando": time.time(),
                "sesiones": len(resultado["borradas"]),
                "bytes": resultado["bytes"],
            }
        return 200, resultado
    elif ruta == "/api/lan/configurar":
        # Primera vez (o cambio de contrasena) desde el panel de Ajustes --
        # autenticado con el token normal, a diferencia de /api/lan/login
        # (ver _lan_login). lan_cookie_clave se genera una sola vez: si ya
        # existe, cambiar la contrasena no invalida las sesiones ya
        # abiertas (para eso esta "Cerrar todas las sesiones").
        pw = (cuerpo.get("pw") or "").strip()
        if not pw:
            return 400, {"error": "falta la contrasena"}
        a = app.ajustes
        hash_, sal = await app.a_hilo(lan_mod.hashear, pw)
        a["lan_pass_hash"] = hash_
        a["lan_pass_sal"] = sal
        if not a["lan_cookie_clave"]:
            a["lan_cookie_clave"] = lan_mod.generar_clave()
        await app.a_hilo(a.guardar)
        return 200, {"ok": True}
    elif ruta == "/api/lan/puerto":
        try:
            puerto = int(cuerpo["valor"])
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "puerto invalido"}
        if not (1 <= puerto <= 65535):
            return 400, {"error": "puerto invalido"}
        a = app.ajustes
        a["puerto_lan"] = puerto
        await app.a_hilo(a.guardar)
        return 200, {"puerto_lan": puerto}
    elif ruta == "/api/lan/cerrar_sesiones":
        # Rota lan_cookie_clave: invalida de una cualquier cookie emitida
        # antes, sin llevar una lista de sesiones vivas.
        a = app.ajustes
        a["lan_cookie_clave"] = lan_mod.generar_clave()
        await app.a_hilo(a.guardar)
        return 200, {"ok": True}
    elif ruta == "/api/actualizacion/buscar":
        # "buscar ahora" del panel de Ajustes: 202 + tarea de fondo, mismo
        # patron que sesion_actualizar -- es un pedido de red (hasta
        # actualizaciones.ESPERA_RED) y no tiene por que colgar la
        # respuesta HTTP.
        lanzada = _lanzar_tarea(app, "actualizacion_buscar", _buscar_actualizacion, app)
        return 202, {"aceptado": lanzada, "ya_corriendo": not lanzada}
    else:
        return None
    return 200, await app.a_hilo(app.lista.leer)


async def _leer_cuerpo(cabeceras, reader, writer):
    """-> dict, o None si ya se respondio un 400 al cliente."""
    try:
        n = int(cabeceras.get("Content-Length") or 0)
    except ValueError:
        await _json(writer, 400, {"error": "Content-Length invalido"})
        return None
    try:
        crudo = await reader.readexactly(n) if n else b""
    except asyncio.IncompleteReadError:
        await _json(writer, 400, {"error": "cuerpo incompleto"})
        return None
    try:
        return json.loads(crudo) if crudo else {}
    except ValueError:
        await _json(writer, 400, {"error": "json invalido"})
        return None


async def _manejar_post(app, ruta, qs, cabeceras, reader, writer):
    if ruta == "/api/lan/login":
        # La unica ruta POST sin ?t=: ver _lan_login.
        cuerpo = await _leer_cuerpo(cabeceras, reader, writer)
        if cuerpo is None:
            return
        await _lan_login(app, cuerpo, writer)
        return

    if not _token_valido(app, qs):
        await _json(writer, 403, {"error": "token invalido"})
        return
    if not _origen_valido(app, cabeceras):
        await _json(writer, 403, {"error": "origen invalido"})
        return
    cuerpo = await _leer_cuerpo(cabeceras, reader, writer)
    if cuerpo is None:
        return

    try:
        resultado = await _despachar_post(app, ruta, cuerpo)
    except (KeyError, ValueError) as e:
        await _json(writer, 400, {"error": str(e)})
        return

    if resultado is None:
        await _json(writer, 404, {"error": "no encontrado"})
        return
    codigo, datos = resultado
    await _json(writer, codigo, datos)


# ===========================================================================
# El despachador de conexiones: HTTP comun o /ws
# ===========================================================================

def _parsear_preambulo(crudo: bytes):
    """-> (metodo, destino, cabeceras). cabeceras es un email.message.Message
    (lookup case-insensitive via .get(), igual que BaseHTTPRequestHandler
    .headers de la version anterior)."""
    fin_linea = crudo.find(b"\r\n")
    if fin_linea == -1:
        raise ValueError("sin linea de peticion")
    linea = crudo[:fin_linea].decode("latin-1")
    resto = crudo[fin_linea + 2:]
    partes = linea.split(" ")
    if len(partes) != 3:
        raise ValueError("linea de peticion invalida")
    metodo, destino, _version = partes
    cabeceras = email.parser.BytesHeaderParser().parsebytes(resto)
    return metodo, destino, cabeceras


async def _manejar_ws(app, preambulo, destino, cabeceras, reader, writer):
    qs = parse_qs(urlsplit(destino).query)
    if not _token_valido(app, qs):
        await _responder(writer, 403, {"Content-Type": "application/json"},
                          json.dumps({"error": "token invalido"}).encode())
        return
    if not _origen_valido(app, cabeceras):
        await _responder(writer, 403, {"Content-Type": "application/json"},
                          json.dumps({"error": "origen invalido"}).encode())
        return
    proto = ServerProtocol()
    proto.receive_data(preambulo)
    peticiones = [e for e in proto.events_received() if isinstance(e, Request)]
    if not peticiones:
        return
    respuesta = proto.accept(peticiones[0])
    await _atender_ws(app, proto, respuesta, reader, writer)


async def _manejar_http(app, metodo, destino, cabeceras, reader, writer):
    s = urlsplit(destino)
    ruta = unquote(s.path)
    qs = parse_qs(s.query)
    if metodo == "GET":
        await _manejar_get(app, ruta, qs, cabeceras, writer)
    elif metodo == "POST":
        await _manejar_post(app, ruta, qs, cabeceras, reader, writer)
    else:
        await _responder(writer, 501, {}, b"")


async def _atender(app, reader, writer):
    """Una conexion = una peticion (igual que la version con
    BaseHTTPRequestHandler de antes, que nunca puso protocol_version en
    "HTTP/1.1" y por eso tampoco hacia keep-alive): se lee el preambulo,
    se despacha, y se cierra -- salvo que sea /ws, que se queda abierta
    mientras dure la pestana."""
    try:
        preambulo = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError,
             ConnectionResetError, OSError):
        with contextlib.suppress(Exception):
            writer.close()
        return

    try:
        metodo, destino, cabeceras = _parsear_preambulo(preambulo)
    except ValueError:
        with contextlib.suppress(Exception):
            await _responder(writer, 400, {}, b"")
        with contextlib.suppress(Exception):
            writer.close()
        return

    es_ws = (destino.split("?", 1)[0] == "/ws"
             and (cabeceras.get("Upgrade") or "").lower() == "websocket")
    try:
        if es_ws:
            await _manejar_ws(app, preambulo, destino, cabeceras, reader, writer)
        else:
            await _manejar_http(app, metodo, destino, cabeceras, reader, writer)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        if not es_ws:
            with contextlib.suppress(Exception):
                writer.close()
        # si era /ws, _atender_ws ya cerro el writer en su propio finally


# ===========================================================================
# Servidor: el loop asyncio corriendo en su propio hilo
# ===========================================================================

class Servidor:
    """Levanta el loop en un hilo daemon aparte (el hilo principal de
    app.py lo necesita libre para icono.run() de pystray, que bloquea) y
    expone arrancar()/parar() sincronos para quien no vive en asyncio.
    arrancar() no vuelve hasta que el socket esta escuchando, asi que
    .puerto ya esta disponible para instancia.json apenas retorna."""

    def __init__(self, ajustes, dir_web, puerto=0):
        self.app = Aplicacion(ajustes, dir_web)
        self._puerto_pedido = puerto
        self._loop = None
        self._hilo = None
        self._servidor_asyncio = None
        self.puerto = None

    @property
    def token(self):
        return self.app.token

    def arrancar(self):
        listo = threading.Event()
        self._hilo = threading.Thread(target=self._correr, args=(listo,),
                                       name="nimbo-loop", daemon=True)
        self._hilo.start()
        listo.wait()

    def _correr(self, listo):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self.app.fijar_loop(loop)
        loop.run_until_complete(self._iniciar())
        listo.set()
        try:
            loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(self._cerrar())
            loop.close()

    async def _iniciar(self):
        self.app.estado = await self.app.a_hilo(self.app.estado_completo)
        # Acceso LAN (SPEC 17): el bind no cambia en caliente -- hace falta
        # reiniciar Nimbo para que el toggle de Ajustes tome efecto (ver
        # Decisiones del spec). Con acceso_lan apagado, exactamente lo de
        # siempre: loopback, puerto al azar.
        if self.app.ajustes["acceso_lan"]:
            host, puerto_pedido = "0.0.0.0", self.app.ajustes["puerto_lan"]
        else:
            host, puerto_pedido = "127.0.0.1", self._puerto_pedido
        self._servidor_asyncio = await asyncio.start_server(
            lambda r, w: _atender(self.app, r, w), host, puerto_pedido)
        self.puerto = self._servidor_asyncio.sockets[0].getsockname()[1]
        self.app.puerto = self.puerto    # _origen_valido lo necesita
        self._loop.create_task(_bucle_estado(self.app))
        self._loop.create_task(_bucle_cosecha(self.app))
        self._loop.create_task(_bucle_miniaturas(self.app))
        self._loop.create_task(_bucle_sitio(self.app))
        self._loop.create_task(_bucle_licencia(self.app))
        self._loop.create_task(_bucle_actualizaciones(self.app))

    async def _cerrar(self):
        if self._servidor_asyncio is not None:
            self._servidor_asyncio.close()
            with contextlib.suppress(Exception):
                await self._servidor_asyncio.wait_closed()
        self.app.ejecutor.shutdown(wait=False)

    def parar(self):
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._hilo:
            self._hilo.join(timeout=30)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    # Este entrypoint es para depurar con la consola a la vista, no para
    # el usuario final -- sin pantalla de activacion (A3,
    # specs\07-licencia_inviolable.md): si la puerta de ajustes.cargar()
    # no deja pasar, se imprime el motivo y se sale.
    try:
        a = ajustes_mod.cargar()
    except licencia_mod.SinLicencia as e:
        print("[NIMBO] licencia: " + str(e.motivo))
        sys.exit(3)
    servidor = Servidor(a, ajustes_mod.DIR_WEB)
    servidor.arrancar()
    print("Nimbo escuchando en http://127.0.0.1:%d/?t=%s" % (servidor.puerto, servidor.token))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        servidor.parar()
