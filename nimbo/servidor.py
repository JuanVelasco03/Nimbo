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
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote, urlsplit

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

import ajustes as ajustes_mod
import cosecha_streams as cosecha_streams_mod
import estado_captura as estado_captura_mod
import favoritas as favoritas_mod
import grabaciones as grabaciones_mod
import inicio_automatico as inicio_mod
import lista as lista_mod
import motor as motor_mod
import preflight as preflight_mod
import registro as registro_mod
import sesion as sesion_mod
import sitio as sitio_mod
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
    "motor_arrancar": 300.0,
    "motor_parar": 1500.0,         # motor.ESPERA_CIERRE_DIRECTO = 1260s, con margen
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
CUPO_SITIO = 4                  # pedidos salientes simultaneos al sitio
CUPO_IMAGEN = 3                 # miniaturas simultaneas: el navegador puede
                                 # pedir N por segundo (una por tarjeta) y
                                 # cada una es un HTTPS saliente -- sin tope
                                 # se comen el pool entero y no responde
                                 # nada mas (ver specs\congelado_interfaz.md)

INTERVALO_PING = 15.0
TOPE_PONG = 20.0


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


async def _leer_detector_fusionado(app):
    """detector.json + directo.json fusionados (ver nimbo/estado_captura.py)
    -- el shape que ve el resto del codigo sigue siendo el de detector.json,
    asi que vivo.py/cosecha_streams.py no se enteran de donde salio cada
    sesion."""
    detector = await app.a_hilo(_leer_json, app.ajustes.estado_detector)
    directo = await app.a_hilo(_leer_json, app.ajustes.estado_directo)
    return estado_captura_mod.fusionar(detector, directo, time.time())


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
        self.sitio = sitio_mod.Sitio(ajustes)
        self.token = secrets.token_urlsafe(24)
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
        try:
            uso = shutil.disk_usage(self.ajustes.dir_datos)
            return {"libres": uso.free, "total": uso.total}
        except OSError:
            return None

    def estado_completo(self):
        """Snapshot sincrono completo -- se llama desde un hilo del
        ejecutor (ver a_hilo), nunca desde el loop. Arma self.estado al
        arrancar; de ahi en mas _bucle_estado la reemplaza como fuente
        del dia a dia, pero esto sigue siendo lo que responde /api/estado
        si self.estado todavia esta vacio (justo al conectar, antes del
        primer tick)."""
        detector = estado_captura_mod.fusionar(
            _leer_json(self.ajustes.estado_detector),
            _leer_json(self.ajustes.estado_directo))
        return {
            "motor": self.motor.estado(),
            "lista": self.lista.leer(),
            "favoritas": self.favoritas.leer(),
            "detector": detector,
            "supervisor": _leer_json(self.ajustes.estado_supervisor),
            "disco": self._disco(),
            "sesion": self.sesion.estado(),
            "siguiendo": _leer_json(self.ajustes.estado_siguiendo),
            "vivo": vivo_mod.sesiones_vivas(self.ajustes.streams, detector),
            "limpieza": self.ultima_limpieza,
        }


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

            motor = app.motor.estado()
            if motor != app.estado.get("motor"):
                delta["motor"] = motor

            if app.ultima_limpieza != app.estado.get("limpieza"):
                delta["limpieza"] = app.ultima_limpieza

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


def _nombres_sin_estado(estado):
    """Mismo calculo que hacia main.js:nombresSinEstado -- las favoritas
    puramente locales, sin dato de "siguiendo" ni del supervisor, son las
    unicas que de verdad necesitan golpear /api/sitio/estado."""
    con_dato = set()
    for m in (estado.get("siguiendo") or {}).get("modelos") or []:
        u = m.get("username")
        if u:
            con_dato.add(u.lower())
    for e in (estado.get("supervisor") or {}).get("emisiones") or []:
        n = e.get("nombre")
        if n and e.get("estado"):
            con_dato.add(n.lower())
    return [n for n in (estado.get("favoritas") or []) if n.lower() not in con_dato]


async def _refrescar_sitio(app, semaforo):
    favoritas = app.estado.get("favoritas") or []
    vpn = set((app.estado.get("lista") or {}).get("vpn") or [])
    sin_estado = _nombres_sin_estado(app.estado)
    if not favoritas and not sin_estado:
        return

    async def _buscar(nombre):
        async with semaforo:
            return nombre, "avatar", await app.a_hilo(app.sitio.buscar, nombre)

    async def _estado(nombre):
        async with semaforo:
            r = await app.a_hilo(app.sitio.estado_cacheado, nombre, nombre.lower() in vpn)
            return nombre, "estado", r

    tareas = [asyncio.ensure_future(_buscar(n)) for n in favoritas]
    tareas += [asyncio.ensure_future(_estado(n)) for n in sin_estado]

    resultados = {}
    for tarea in asyncio.as_completed(tareas):
        try:
            nombre, forma, r = await tarea
        except Exception:
            continue
        clave = nombre.lower()
        entrada = resultados.setdefault(clave, {})
        if forma == "avatar":
            exacto = next((m for m in r if m["username"].lower() == clave), None)
            if exacto:
                entrada["avatar"] = exacto.get("avatar", "")
                entrada["online"] = bool(exacto.get("online"))
        else:
            entrada["estado"] = r.get("estado", "")

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


async def _servir_video(app, writer, ruta, cabecera_rango):
    """GET con soporte de Range: sin esto el <video> puede arrancar pero
    no puede saltar a la mitad de un archivo de varios GB. Nunca carga el
    archivo entero en memoria -- lee y escribe en bloques de 1 MiB, cada
    uno en el ejecutor para no bloquear el loop con el read() sincrono."""
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
# HTTP: rutas
# ===========================================================================

async def _manejar_get(app, ruta, qs, cabeceras, writer):
    necesita_token = ruta in ("/", "/index.html") or ruta.startswith("/api/")
    if necesita_token and not _token_valido(app, qs):
        await _json(writer, 403, {"error": "token invalido"})
        return

    if ruta == "/api/estado":
        await _json(writer, 200, app.estado or await app.a_hilo(app.estado_completo))
    elif ruta == "/api/lista":
        await _json(writer, 200, await app.a_hilo(app.lista.leer))
    elif ruta == "/api/sitio/buscar":
        resultados = await app.a_hilo(app.sitio.buscar, _valor(qs, "q"))
        await _json(writer, 200, {"resultados": resultados})
    elif ruta == "/api/sitio/estado":
        nombre = _valor(qs, "nombre")
        if not nombre:
            await _json(writer, 400, {"error": "falta 'nombre'"})
            return
        lista_actual = await app.a_hilo(app.lista.leer)
        vpn = nombre.lower() in lista_actual["vpn"]
        resultado = await app.a_hilo(app.sitio.estado_cacheado, nombre, vpn)
        await _json(writer, 200, resultado)
    elif ruta == "/api/sitio/imagen":
        # El cupo es lo unico que acota de verdad cuanto del pool de 12
        # hilos puede comerse esto: la miniatura es un HTTPS saliente, y
        # getaddrinfo() no respeta el timeout (ver sitio.TOPE_IMAGEN).
        # Mismo criterio que CUPO_SITIO con _bucle_sitio.
        async with app.cupo_imagen:
            resultado = await app.a_hilo(app.sitio.imagen, _valor(qs, "url"))
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
        resultado = await app.a_hilo(grabaciones_mod.listar, a.grabaciones, a.dir_estado, a.ffmpeg)
        await _json(writer, 200, resultado)
    elif ruta == "/api/grabaciones/video":
        ruta_video = await app.a_hilo(
            grabaciones_mod.ruta_absoluta, app.ajustes.grabaciones, _valor(qs, "archivo"))
        if ruta_video is None:
            await _json(writer, 404, {"error": "no encontrado"})
            return
        await _servir_video(app, writer, ruta_video, cabeceras.get("Range"))
    elif ruta == "/api/preferencias":
        a = app.ajustes
        await _json(writer, 200, {
            "arrancar_al_abrir": a["arrancar_al_abrir"], "bandeja": a["bandeja"],
            "ver_navegador": a["ver_navegador"],
            "inicio_automatico": await app.a_hilo(inicio_mod.esta_activo),
        })
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
    existente = app._tareas.get(nombre)
    if existente is not None:
        tarea, inicio = existente
        if not tarea.done():
            tope = TOPE_TAREA.get(nombre, TOPE_TAREA_DEFECTO)
            if time.monotonic() - inicio < tope:
                return False
            app.motor._aviso(
                "la tarea %s lleva mas de %ds -- la doy por perdida y "
                "lanzo otra" % (nombre, int(tope)), nivel="error")
            app._tareas.pop(nombre, None)
    nueva = app.loop.create_task(app.a_hilo(fn, *args))
    nueva.add_done_callback(lambda t: _tarea_terminada(app, nombre, t))
    app._tareas[nombre] = (nueva, time.monotonic())
    return True


async def _despachar_post(app, ruta, cuerpo):
    """-> (codigo, datos) | None (None = ruta desconocida, 404)."""
    if ruta == "/api/lista/agregar":
        await app.a_hilo(app.lista.agregar_modelo, cuerpo["nombre"])
    elif ruta == "/api/lista/quitar":
        await app.a_hilo(app.lista.quitar_modelo, cuerpo["nombre"])
    elif ruta == "/api/lista/vpn":
        await app.a_hilo(app.lista.marcar_vpn, cuerpo["nombre"], bool(cuerpo.get("activo")))
    elif ruta == "/api/lista/directiva":
        await app.a_hilo(app.lista.establecer_directiva, cuerpo["clave"], cuerpo["valor"])
    elif ruta == "/api/favoritas/agregar":
        await app.a_hilo(app.favoritas.agregar, cuerpo["nombre"])
        return 200, {"favoritas": await app.a_hilo(app.favoritas.leer)}
    elif ruta == "/api/favoritas/quitar":
        await app.a_hilo(app.favoritas.quitar, cuerpo["nombre"])
        return 200, {"favoritas": await app.a_hilo(app.favoritas.leer)}
    elif ruta == "/api/sesion/guardar":
        resultado = await app.a_hilo(app.sesion.guardar_desde_curl, cuerpo["curl"])
        return 200, {"sesion": resultado}
    elif ruta == "/api/sesion/olvidar":
        await app.a_hilo(app.sesion.borrar)

        def _quitar_siguiendo():
            with contextlib.suppress(OSError):
                os.remove(app.ajustes.estado_siguiendo)

        await app.a_hilo(_quitar_siguiendo)
        return 200, {"sesion": await app.a_hilo(app.sesion.estado)}
    elif ruta == "/api/sesion/actualizar":
        # 202 y no 409 aunque se descarte: un 409 hace que api.js:enviar()
        # tire excepcion y el footer lo muestre como "error: ...", que es la
        # lectura equivocada para un doble click.
        lanzada = _lanzar_tarea(app, "sesion_actualizar",
                                 app.sesion.actualizar_siguiendo,
                                 app.ajustes.estado_siguiendo)
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
        if clave not in ("arrancar_al_abrir", "bandeja", "ver_navegador"):
            return 400, {"error": "preferencia desconocida"}
        app.ajustes[clave] = bool(cuerpo["valor"])
        await app.a_hilo(app.ajustes.guardar)
        return 200, {clave: app.ajustes[clave]}
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
    else:
        return None
    return 200, await app.a_hilo(app.lista.leer)


async def _manejar_post(app, ruta, qs, cabeceras, reader, writer):
    if not _token_valido(app, qs):
        await _json(writer, 403, {"error": "token invalido"})
        return
    try:
        n = int(cabeceras.get("Content-Length") or 0)
    except ValueError:
        await _json(writer, 400, {"error": "Content-Length invalido"})
        return
    try:
        crudo = await reader.readexactly(n) if n else b""
    except asyncio.IncompleteReadError:
        await _json(writer, 400, {"error": "cuerpo incompleto"})
        return
    try:
        cuerpo = json.loads(crudo) if crudo else {}
    except ValueError:
        await _json(writer, 400, {"error": "json invalido"})
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
        self._servidor_asyncio = await asyncio.start_server(
            lambda r, w: _atender(self.app, r, w), "127.0.0.1", self._puerto_pedido)
        self.puerto = self._servidor_asyncio.sockets[0].getsockname()[1]
        self._loop.create_task(_bucle_estado(self.app))
        self._loop.create_task(_bucle_cosecha(self.app))
        self._loop.create_task(_bucle_sitio(self.app))

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
    a = ajustes_mod.cargar()
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
