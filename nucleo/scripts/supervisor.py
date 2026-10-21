"""
supervisor.py

Abre y cierra las emisiones por vos. Mantiene un navegador headless
(Chromium via Playwright) con una pagina por modelo, de modo que el
reproductor de stripchat pida el stream y el proxy (detectar_m3u8.py) lo
grabe. No hay que tener ninguna pestana abierta a mano.

    Primera vez:   python supervisor.py --login
    Despues:       python supervisor.py

Por que un navegador y no una descarga directa: los segmentos de doppiocdn
vienen con las URLs ofuscadas (tags #EXT-X-MOUFLON) y solo el reproductor
del sitio sabe pedirlas. El navegador hace su trabajo normal; nosotros solo
lo abrimos, lo vigilamos y lo cerramos.

Que hace
    - Lee la MISMA lista que el proxy (grabar.txt): cada linea que no sea un
      numero ni una directiva @ se toma como nombre de modelo.
    - Consulta cada ~60s si la modelo esta emitiendo (status en el HTML de su
      pagina). Si esta offline, espera y abre sola cuando vuelve.
    - Cuando esta online abre una pagina headless y la deja reproduciendo.
    - Vigila que el <video> avance; si se frena, recarga la pagina.
    - Si la sacas de grabar.txt, cierra esa pagina (y el proxy cierra y une
      el mp4, como siempre).
    - Las modelos de @vpn= (geobloqueadas) llevan su pagina marcada con una
      cabecera, y el proxy saca por el tunel de la VPN solo esos pedidos. No
      hay que encender ni apagar nada, y las otras grabaciones no sufren el
      tiron de cada cambio de ruta.

Requisitos
    pip install playwright
    python -m playwright install chromium
    mitmdump -s detectar_m3u8.py --set flow_detail=0   (en la otra carpeta)
"""

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sys
import time

# Los scripts viven en <proyecto>\scripts; el perfil del navegador y las
# grabaciones en <proyecto>. Si no, al mover los scripts se perdia el perfil
# (sesion iniciada, aviso de edad aceptado) y stripchat volvia a tratarte como
# visitante -> playlist de publicidad y calidad recortada.
AQUI = os.path.dirname(os.path.abspath(__file__))
SUBCARPETAS_SCRIPT = {"scripts", "script", "bin", "src"}
BASE = (os.path.dirname(AQUI)
        if os.path.basename(AQUI).lower() in SUBCARPETAS_SCRIPT else AQUI)

PERFIL = os.environ.get("GRAB_PERFIL") or os.path.join(BASE, "perfil")
REGISTRO = os.path.join(BASE, "supervisor.log")


def _ruta_lista() -> str:
    """grabar.txt: primero al lado de este script, si no en la raiz."""
    aqui = os.path.join(AQUI, "grabar.txt")
    if os.path.exists(aqui):
        return aqui
    arriba = os.path.join(BASE, "grabar.txt")
    return arriba if os.path.exists(arriba) else aqui


# la lista de modelos es la misma que usa el proxy
GRABAR = os.environ.get("M3U8_GRABAR") or _ruta_lista()
# el proxy deja ahi el ID y el nombre de todo lo que detecta
ACTIVOS = os.environ.get("M3U8_ACTIVOS") or os.path.join(
    os.path.dirname(GRABAR), "activos.txt"
)

PROXY = os.environ.get("GRAB_PROXY", "127.0.0.1:8080")
SITIO = os.environ.get("GRAB_SITIO", "https://es.stripchat.com")
# "pegajoso": mientras el <video> avance, no cerrar la pagina aunque la
# consulta de estado falle. Sirve para las modelos geobloqueadas: abris
# con la VPN puesta y despues la apagas; la consulta empieza a dar 403 o
# "offline" pero el stream ya esta en marcha y se sigue grabando.
PEGAJOSO = os.environ.get("GRAB_PEGAJOSO", "") not in {"", "0", "no", "false"}

# Con esta cabecera se marcan los pedidos de las paginas de @vpn=. El proxy
# (detectar_m3u8.py, clase _Tunel) los saca por el tunel de la VPN y la borra
# antes de mandarlos al sitio. Marcar de mas no cuesta nada: si el proxy no
# tiene tunel, la cabecera se borra y el pedido sale directo, como siempre.
# El nombre esta en los dos archivos: si se cambia, cambiarlo en los dos.
CABECERA_TUNEL = "x-grab-tunel"

# VPN automatica para las modelos geobloqueadas (@vpn= en grabar.txt).
# Es tu rutina a mano, hecha sola: enciende la VPN, mira si esta emitiendo,
# abre la pagina, espera a que el video arranque de verdad y la apaga. Si el
# stream se cae o la modelo vuelve de privado, lo repite sin que hagas nada.
VPN_ON = os.environ.get("GRAB_VPN_ON", "")     # comando para conectar
VPN_OFF = os.environ.get("GRAB_VPN_OFF", "")   # comando para desconectar
# segundos de margen tras conectar/desconectar (que la ruta se asiente)
VPN_ESPERA = float(os.environ.get("GRAB_VPN_ESPERA", "8"))
INTERVALO_VPN = 300.0       # cada cuanto se levanta la VPN para reintentar
ESPERA_ARRANQUE = 40.0      # cuanto se espera a que el video avance, con VPN
# la VPN es de todo el PC: una modelo a la vez
VPN_LOCK: "asyncio.Lock | None" = None
# Encender o apagar la VPN cambia la ruta de todo el equipo: cualquier
# consulta en vuelo se queda colgada hasta agotar su timeout (30s tirados).
# Mientras haya una ventana de VPN abierta, las demas no preguntan.
VPN_LIBRE: "asyncio.Event | None" = None
ESPERA_VPN_LIBRE = 90.0     # tope por si la VPN se atasca
# al arrancar, que las que no necesitan VPN hagan su primera consulta
# antes de que se toque la ruta
RETRASO_VPN_INICIAL = 6.0

INTERVALO_LISTA = 5.0       # relectura de grabar.txt
INTERVALO_ESTADO = 60.0     # consulta de online/offline
REINTENTO_RED = 10.0        # tras un fallo de red, mirar otra vez enseguida
INTERVALO_SALUD = 20.0      # comprobacion de que el video avanza
REINTENTOS_SALUD = 2        # recargas seguidas antes de cerrar y reabrir
ESPERA_VIDEO = 45.0         # segundos para que arranque el video tras abrir
# en modo pegajoso, cuanto se protege la pagina desde el ultimo avance real
GRACIA_PEGAJOSO = 90.0

# sin esto stripchat contesta 406 a las peticiones que no parecen de navegador
CABECERAS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# 1920x1080: si el <video> es chico, el reproductor elige un nivel chico
VENTANA = {"width": 1920, "height": 1080}
AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

ESTADOS_VIVOS = {"public", "live", "groupShow", "p2p", "private", "ticketShow"}


def estado_incierto(estado: str) -> bool:
    """La consulta no pudo contestar: no dice nada de si emite o no.

    Encender o apagar la VPN corta la ruta de todo el PC unos segundos y el
    sitio contesta 502 a las consultas de TODAS las modelos a la vez. Tomarlo
    por "dejo de emitir" cerraba la pagina y costaba ~60s de grabacion hasta
    reabrirla: medidos 4 cortes de 50-64s en una hora, en las 3-4 modelos que
    grababan a la vez, ninguna de ellas la que pedia la VPN.
    """
    return (estado == "?" or estado.startswith("error:")
            or estado.startswith("http_5")
            or estado in {"http_408", "http_429"})

RE_ID = re.compile(r'"model":\{"id":(\d+)')
RE_ESTADO = re.compile(r'"model":\{"id":\d+.{0,6000}?"status":"([a-zA-Z]+)"', re.S)
RE_NOMBRE = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")
RE_FILA_ACTIVOS = re.compile(r"^(\d{4,})\s+([A-Za-z0-9_.\-]{2,40})\s")

log = logging.getLogger("supervisor")


def configurar_log() -> None:
    if log.handlers:
        return
    consola = logging.StreamHandler(sys.stderr)
    consola.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.addHandler(consola)
    try:
        archivo = logging.FileHandler(REGISTRO, encoding="utf-8")
        archivo.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        log.addHandler(archivo)
    except OSError:
        pass
    log.setLevel(logging.INFO)


# --------------------------------------------------------------------------
# Lista de modelos (grabar.txt, en caliente)
# --------------------------------------------------------------------------

def nombres_conocidos() -> dict:
    """id -> nombre, de lo que el proxy fue detectando (activos.txt)."""
    mapa = {}
    try:
        with open(ACTIVOS, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                m = RE_FILA_ACTIVOS.match(linea.strip())
                if m and m.group(2) != "desconocido":
                    mapa[m.group(1)] = m.group(2)
    except OSError:
        pass
    return mapa


class Lista:
    """Los nombres de grabar.txt. Un ID numerico se traduce a nombre con
    activos.txt (lo que el proxy ya detecto); si no esta, se avisa."""

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._firma = None
        self._nombres = []
        self._avisado_ids = set()
        self._avisado_vpn = set()
        self.vpn = set()          # nombres en minuscula que piden VPN

    def actual(self):
        try:
            st = os.stat(self.ruta)
            firma = (st.st_mtime, st.st_size)
        except OSError:
            return self._nombres          # ilegible: conservar lo ultimo bueno
        if firma == self._firma or st.st_size == 0:
            return self._nombres

        nombres, ids = [], []
        conocidos = nombres_conocidos()
        try:
            with open(self.ruta, "r", encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except OSError:
            return self._nombres

        vpn = set()
        for linea in lineas:
            linea = linea.split("#", 1)[0].strip()
            if linea.startswith("@"):
                clave, _, valor = linea[1:].partition("=")
                if clave.strip().lower() == "vpn":
                    # se suman: dos lineas @vpn= no se pisan
                    vpn |= {v.strip().lower()
                            for v in valor.split(",") if v.strip()}
                continue
            if not linea or linea in {"*", "all", "todo", "todos"}:
                continue
            if linea.isdigit():
                nombre = conocidos.get(linea)
                if nombre:
                    if nombre not in nombres:
                        nombres.append(nombre)
                    self._avisado_ids.discard(linea)
                else:
                    ids.append(linea)
            elif RE_NOMBRE.match(linea) and linea not in nombres:
                nombres.append(linea)

        self._firma = firma
        if vpn != self.vpn:
            self.vpn = vpn
            log.info("[LISTA] con VPN: %s", ", ".join(sorted(vpn)) or "-")
        # @vpn= solo marca; el nombre tiene que estar ademas como linea
        # suelta. Marcarla y olvidarse de la linea no grababa nada y no
        # avisaba: es el error facil de cometer.
        sueltos = vpn - {n.lower() for n in nombres}
        for n in sorted(sueltos):
            if n not in self._avisado_vpn:
                self._avisado_vpn.add(n)
                log.warning(
                    "[LISTA] '%s' esta en @vpn= pero no figura como linea "
                    "suelta: no se graba. Agregala tambien por su cuenta.", n)
        self._avisado_vpn &= sueltos
        if nombres != self._nombres:
            log.info("[LISTA] modelos: %s", ", ".join(nombres) or "-")
            self._nombres = nombres
        for i in ids:
            if i not in self._avisado_ids:
                self._avisado_ids.add(i)
                log.warning(
                    "[LISTA] %s es un ID que todavia no vi en %s. Pone el "
                    "nombre de usuario, o abri su pagina una vez para que el "
                    "proxy aprenda el nombre.", i, os.path.basename(ACTIVOS)
                )
        return self._nombres


# --------------------------------------------------------------------------
# VPN: encender, hacer lo que haya que hacer, apagar
# --------------------------------------------------------------------------

async def _ejecutar(comando: str, que: str) -> bool:
    """Corre un comando de shell y espera a que termine."""
    if not comando:
        return False
    try:
        proc = await asyncio.create_subprocess_shell(
            comando,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        salida, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        log.error("[VPN] %s: el comando no termino en 120s -- %s", que, comando)
        return False
    except Exception as e:
        log.error("[VPN] %s: %s", que, e)
        return False
    texto = (salida or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        log.error("[VPN] %s fallo (codigo %s): %s", que, proc.returncode,
                  texto[-300:] or comando)
        return False
    log.info("[VPN] %s: ok%s", que, (" -- " + texto[-200:]) if texto else "")
    return True


def _asegurar_vpn() -> None:
    """Crea el candado y el aviso si no existen.

    Tienen que nacer dentro del bucle de eventos, asi que no se pueden
    crear al importar. supervisar() los crea; esto cubre cualquier otro
    camino que acabe llamando a con_vpn().
    """
    global VPN_LOCK, VPN_LIBRE
    if VPN_LOCK is None:
        VPN_LOCK = asyncio.Lock()
    if VPN_LIBRE is None:
        VPN_LIBRE = asyncio.Event()
        VPN_LIBRE.set()


@contextlib.asynccontextmanager
async def con_vpn(nombre: str):
    """Levanta la VPN, cede el control, y la apaga pase lo que pase.

    Cede True si quedo conectada. Si no hay comandos configurados cede
    False y no toca nada: el resto sigue funcionando igual que siempre.
    """
    if not VPN_ON:
        yield False
        return
    _asegurar_vpn()
    async with VPN_LOCK:               # la VPN es de todo el PC
        VPN_LIBRE.clear()              # que las demas no pregunten ahora
        try:
            log.info("[VPN] %s: conectando...", nombre)
            ok = await _ejecutar(VPN_ON, "conectar")
            if ok and VPN_ESPERA > 0:
                await asyncio.sleep(VPN_ESPERA)
            try:
                yield ok
            finally:
                if VPN_OFF:
                    await _ejecutar(VPN_OFF, "desconectar")
                    if VPN_ESPERA > 0:
                        await asyncio.sleep(VPN_ESPERA)
                else:
                    log.warning("[VPN] sin GRAB_VPN_OFF: queda conectada")
        finally:
            VPN_LIBRE.set()


# --------------------------------------------------------------------------
# Una modelo
# --------------------------------------------------------------------------

class Emision:
    def __init__(self, nombre: str, contexto):
        self.nombre = nombre
        self.contexto = contexto
        self.url = "%s/%s" % (SITIO, nombre)
        self.pagina = None
        self.estado = ""
        self.sid = ""
        self.ultimo_t = -1.0
        self.fallos = 0
        self.abierta_en = 0.0
        self.tarea = None
        self.errores_red = 0
        self.alto = 0               # ultima resolucion vista en el <video>
        self.ultimo_avance = 0.0    # cuando sumo tiempo el <video> por ultima vez
        self.aviso_pegajoso = False
        self.necesita_vpn = False   # esta en @vpn= de grabar.txt

    # -- consulta de estado sin abrir pagina ------------------------------

    async def consultar(self):
        """-> (estado, id) leyendo el HTML de la pagina de la modelo.

        Las geobloqueadas se preguntan por el tunel: sin eso la respuesta es
        403 o "offline" y no hay manera de saber si esta emitiendo. Antes esto
        obligaba a levantar la VPN de todo el PC cada 5 minutos solo para
        preguntar.
        """
        cabeceras = dict(CABECERAS)
        if self.necesita_vpn:
            cabeceras[CABECERA_TUNEL] = "1"
        try:
            r = await self.contexto.request.get(
                self.url, timeout=30000, headers=cabeceras
            )
            if not r.ok:
                return "http_%d" % r.status, ""
            html = await r.text()
        except Exception as e:
            self.errores_red += 1
            if self.errores_red == 3 and PROXY:
                log.error(
                    "[RED] %s: no consigo abrir la pagina. Esta corriendo el "
                    "proxy en %s?  (mitmdump -s detectar_m3u8.py)",
                    self.nombre, PROXY,
                )
            return "error:%s" % type(e).__name__, ""
        self.errores_red = 0
        m = RE_ID.search(html)
        sid = m.group(1) if m else ""
        m = RE_ESTADO.search(html)
        return (m.group(1) if m else "?"), sid

    # -- pagina ------------------------------------------------------------

    async def abrir(self) -> bool:
        if self.pagina:
            return True
        try:
            self.pagina = await self.contexto.new_page()
            if self.necesita_vpn:
                # va antes del goto para que la marca ya viaje en el primer
                # pedido. Es por pestana y aguanta las recargas, asi que la
                # pagina se queda en el tunel mientras exista.
                await self.pagina.set_extra_http_headers(
                    {CABECERA_TUNEL: "1"})
            await self.pagina.goto(self.url, wait_until="domcontentloaded",
                                   timeout=60000)
            await self.cerrar_avisos()
            self.abierta_en = time.time()
            self.ultimo_t = -1.0
            self.fallos = 0
            # recien abierta cuenta como viva: el video tarda en arrancar
            self.ultimo_avance = self.abierta_en
            log.info("[ABRE] %s (id %s) -- reproduciendo en segundo plano",
                     self.nombre, self.sid or "?")
            return True
        except Exception as e:
            log.error("[ABRE] %s: %s", self.nombre, e)
            await self.cerrar("fallo al abrir")
            return False

    async def cerrar_avisos(self) -> None:
        """Acepta el aviso de edad / cookies si aparece."""
        textos = ["Soy mayor de 18", "I am 18", "Aceptar", "Accept",
                  "Entrar", "Continuar"]
        for t in textos:
            try:
                boton = self.pagina.get_by_role("button", name=re.compile(t, re.I))
                if await boton.count() and await boton.first.is_visible():
                    await boton.first.click(timeout=3000)
                    log.info("[AVISO] %s: '%s'", self.nombre, t)
                    await self.pagina.wait_for_timeout(1000)
            except Exception:
                pass

    async def cerrar(self, motivo: str) -> None:
        if self.pagina:
            log.info("[CIERRA] %s -- %s", self.nombre, motivo)
            try:
                await self.pagina.close()
            except Exception:
                pass
        self.pagina = None
        self.ultimo_t = -1.0
        self.fallos = 0
        self.alto = 0
        self.ultimo_avance = 0.0
        self.aviso_pegajoso = False

    async def video(self):
        """-> dict con el estado del <video> o None."""
        if not self.pagina:
            return None
        try:
            return await self.pagina.evaluate(
                """() => {
                    const v = document.querySelector('video');
                    if (!v) return null;
                    return {t: v.currentTime, pausado: v.paused,
                            listo: v.readyState, w: v.videoWidth,
                            h: v.videoHeight};
                }"""
            )
        except Exception:
            return None

    async def esperar_arranque(self) -> bool:
        """Espera a ver el <video> avanzar de verdad. -> True si arranco.

        Es el paso 2 de la rutina a mano ('veo que se esta reproduciendo'):
        hasta que no avanza no se puede apagar la VPN, porque el player
        todavia esta pidiendo el master y los primeros segmentos.
        """
        limite = time.time() + ESPERA_ARRANQUE
        anterior = -1.0
        while time.time() < limite:
            v = await self.video()
            if v and not v.get("pausado"):
                t = float(v.get("t") or 0.0)
                if anterior >= 0 and t > anterior + 0.05:
                    self.ultimo_avance = time.time()
                    log.info("[VPN] %s: el video ya avanza (%dx%d) -- se puede "
                             "apagar la VPN", self.nombre,
                             int(v.get("w") or 0), int(v.get("h") or 0))
                    return True
                anterior = t
            elif v and v.get("pausado"):
                try:
                    await self.pagina.evaluate(
                        "() => { const v=document.querySelector('video');"
                        " if (v) { v.muted = true; v.play(); } }"
                    )
                except Exception:
                    pass
            await asyncio.sleep(1.0)
        log.warning("[VPN] %s: el video no arranco en %ds; apago la VPN igual",
                    self.nombre, int(ESPERA_ARRANQUE))
        return False

    def viva(self) -> bool:
        """El <video> dio senales de vida hace poco."""
        return time.time() - self.ultimo_avance <= GRACIA_PEGAJOSO

    async def revisar_salud(self) -> None:
        """Si el video no avanza, recarga; si insiste, cierra y reabre."""
        v = await self.video()
        if v is None:
            if time.time() - self.abierta_en > ESPERA_VIDEO:
                await self.recargar("no aparece el reproductor")
            return

        if v["pausado"]:
            try:
                await self.pagina.evaluate(
                    "() => { const v=document.querySelector('video');"
                    " if (v) { v.muted = true; v.play(); } }"
                )
                log.info("[SALUD] %s: estaba en pausa, reanudando", self.nombre)
            except Exception:
                pass
            return

        t = float(v.get("t") or 0.0)
        if self.ultimo_t >= 0 and t <= self.ultimo_t + 0.05:
            if time.time() - self.abierta_en > ESPERA_VIDEO:
                await self.recargar("el video no avanza (%.1fs)" % t)
                return
        else:
            if self.ultimo_t >= 0:            # hubo muestra anterior: avanzo
                self.ultimo_avance = time.time()
            self.fallos = 0
        self.ultimo_t = t

        # avisar de los cambios de calidad del reproductor: si baja sola, el
        # recorte del master (@forzar) no esta llegando
        alto = int(v.get("h") or 0)
        if alto and alto != self.alto:
            if not self.alto:
                log.info("[VIDEO] %s: %dx%d reproduciendo",
                         self.nombre, int(v.get("w") or 0), alto)
            elif alto < self.alto:
                log.warning("[CALIDAD] %s: el reproductor BAJO de %dp a %dp",
                            self.nombre, self.alto, alto)
            else:
                log.info("[CALIDAD] %s: subio de %dp a %dp",
                         self.nombre, self.alto, alto)
            self.alto = alto

    async def recargar(self, motivo: str) -> None:
        self.fallos += 1
        if self.fallos > REINTENTOS_SALUD:
            await self.cerrar("%s (tras %d reintentos)" % (motivo, self.fallos))
            return
        log.warning("[SALUD] %s: %s -- recargando (%d/%d)",
                    self.nombre, motivo, self.fallos, REINTENTOS_SALUD)
        if PEGAJOSO:
            log.warning("[PEGAJOSO] %s: la recarga vuelve a pedir la pagina; "
                        "si esta geobloqueada necesitas la VPN encendida "
                        "otra vez", self.nombre)
        try:
            await self.pagina.reload(wait_until="domcontentloaded", timeout=60000)
            await self.cerrar_avisos()
            self.abierta_en = time.time()
            self.ultimo_t = -1.0
        except Exception as e:
            log.error("[SALUD] %s: no pude recargar: %s", self.nombre, e)
            await self.cerrar("error al recargar")

    # -- bucle propio ------------------------------------------------------

    async def paso_estado(self) -> bool:
        """Consulta si esta emitiendo y abre o cierra segun corresponda.

        -> False si la consulta fallo por red (conviene reintentar antes).
        """
        estado, sid = await self.consultar()
        if sid:
            self.sid = sid
        if estado != self.estado:
            log.info("[ESTADO] %s: %s -> %s (id %s)", self.nombre,
                     self.estado or "?", estado, self.sid or "?")
            self.estado = estado
        if estado in ESTADOS_VIVOS:
            self.aviso_pegajoso = False
            if not self.pagina:
                await self.abrir()
            return True
        elif self.pagina:
            # La consulta de estado pide la pagina de la modelo:
            # geobloqueada da 403 o "?" aunque el stream siga llegando.
            # Si el video avanza, la que miente es la consulta.
            if estado_incierto(estado):
                # No ha dejado de emitir: es que no se pudo preguntar. Cerrar
                # aqui cuesta un minuto de grabacion y no arregla nada. Si de
                # verdad se cayo, revisar_salud() la cierra igual en cuanto el
                # video deje de avanzar.
                if not self.aviso_pegajoso:
                    self.aviso_pegajoso = True
                    log.info("[ESTADO] %s: no pude preguntar (%s) -- dejo la "
                             "pagina abierta", self.nombre, estado)
            elif (PEGAJOSO or self.necesita_vpn) and self.viva():
                if not self.aviso_pegajoso:
                    self.aviso_pegajoso = True
                    log.info(
                        "[PEGAJOSO] %s: la consulta dice '%s' pero el video "
                        "sigue avanzando -- la dejo abierta",
                        self.nombre, estado,
                    )
            else:
                await self.cerrar("la modelo dejo de emitir (%s)" % estado)
            return True
        # sin pagina y sin emitir: solo es un fallo si no se pudo preguntar
        return not estado_incierto(estado)

    async def bucle(self, parar: asyncio.Event) -> None:
        proximo_estado = 0.0
        primera_vpn = True
        while not parar.is_set():
            try:
                if self.pagina:
                    await self.revisar_salud()

                ahora = time.time()
                if ahora >= proximo_estado:
                    if self.necesita_vpn and not self.pagina and VPN_ON:
                        if primera_vpn:
                            # dejar que las que no llevan VPN pregunten
                            # antes de tocar la ruta de todo el equipo
                            primera_vpn = False
                            await asyncio.sleep(RETRASO_VPN_INICIAL)
                        # Tu rutina, sola: VPN -> mirar -> abrir -> esperar a
                        # que el video avance -> VPN fuera. Mientras siga
                        # avanzando no hace falta tocarla nunca mas; si se
                        # cae o se va a privado, este mismo camino la reabre.
                        proximo_estado = time.time() + INTERVALO_VPN
                        async with con_vpn(self.nombre) as conectada:
                            if conectada:
                                await self.paso_estado()
                                if self.pagina:
                                    await self.esperar_arranque()
                        proximo_estado = time.time() + (
                            INTERVALO_ESTADO if self.pagina else INTERVALO_VPN)
                    else:
                        # no preguntar con una ventana de VPN abierta: la
                        # consulta moriria en el cambio de ruta
                        if VPN_LIBRE is not None and not VPN_LIBRE.is_set():
                            try:
                                await asyncio.wait_for(VPN_LIBRE.wait(),
                                                       ESPERA_VPN_LIBRE)
                            except asyncio.TimeoutError:
                                log.warning("[VPN] %s: la ventana de VPN no cierra; pregunto igual", self.nombre)
                        ok = await self.paso_estado()
                        proximo_estado = time.time() + (
                            INTERVALO_ESTADO if ok else REINTENTO_RED)
            except Exception as e:
                log.error("[%s] %s", self.nombre, e)

            try:
                await asyncio.wait_for(parar.wait(), timeout=INTERVALO_SALUD)
            except asyncio.TimeoutError:
                pass
        await self.cerrar("cierre del supervisor")


# --------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------

ARGS_CHROMIUM = [
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-media-suspend",
    "--disable-ipc-flooding-protection",
    # IntensiveWakeUpThrottling frena los timers de las pestanas de fondo a 1
    # por minuto: el ABR del reproductor lo lee como "red lenta" y baja.
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=%d,%d" % (VENTANA["width"], VENTANA["height"]),
    # tambien a nivel navegador, para que los client hints (Sec-CH-UA) no
    # delaten "HeadlessChrome" mientras el contexto dice otra cosa
    "--user-agent=" + AGENTE,
    "--mute-audio",
]

# El reproductor de stripchat baja la calidad cuando cree que nadie mira:
# pestana oculta, ventana sin foco, o <video> chico (los players tipo hls.js
# limitan el nivel al tamano del elemento). Aqui se le miente en los tres
# frentes: la pagina siempre esta visible, con foco, y en 1920x1080.
GUION_VISIBLE = """
(() => {
  const dec = (obj, prop, get) => {
    try { Object.defineProperty(obj, prop, {get, configurable: true}); }
    catch (e) {}
  };
  dec(Document.prototype, 'visibilityState', () => 'visible');
  dec(Document.prototype, 'webkitVisibilityState', () => 'visible');
  dec(Document.prototype, 'hidden', () => false);
  dec(Document.prototype, 'webkitHidden', () => false);
  document.hasFocus = () => true;
  dec(Object.getPrototypeOf(navigator), 'webdriver', () => undefined);

  // que no se entere de que la ventana perdio el foco
  const tragar = e => { e.stopImmediatePropagation(); };
  for (const ev of ['visibilitychange', 'webkitvisibilitychange',
                    'blur', 'pagehide', 'freeze']) {
    window.addEventListener(ev, tragar, true);
    document.addEventListener(ev, tragar, true);
  }
})();
"""


async def abrir_navegador(pw, headless: bool):
    ctx = await pw.chromium.launch_persistent_context(
        PERFIL,
        headless=headless,
        proxy={"server": "http://" + PROXY} if PROXY else None,
        ignore_https_errors=True,      # el certificado del proxy
        args=ARGS_CHROMIUM,
        viewport=dict(VENTANA),
        screen=dict(VENTANA),
        # el UA de headless dice "HeadlessChrome": el sitio lo reconoce
        user_agent=AGENTE,
        locale="es-ES",
    )
    await ctx.add_init_script(GUION_VISIBLE)
    return ctx


async def login() -> int:
    from playwright.async_api import async_playwright
    log.info("[LOGIN] abriendo navegador visible con el perfil %s", PERFIL)
    log.info("[LOGIN] acepta el aviso de edad y entra a tu cuenta si queres.")
    log.info("[LOGIN] cuando termines, cerra la ventana.")
    async with async_playwright() as pw:
        ctx = await abrir_navegador(pw, headless=False)
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(SITIO, wait_until="domcontentloaded", timeout=60000)
        cerrado = asyncio.Event()
        ctx.on("close", lambda _: cerrado.set())
        await cerrado.wait()
    log.info("[LOGIN] perfil guardado")
    return 0


async def supervisar(headless: bool) -> int:
    from playwright.async_api import async_playwright

    _asegurar_vpn()                # dentro del bucle de eventos ya creado
    lista = Lista(GRABAR)
    parar = asyncio.Event()
    emisiones = {}

    log.info("=" * 70)
    log.info("[SUPERVISOR] lista=%s", GRABAR)
    log.info("[SUPERVISOR] proxy=%s | perfil=%s | headless=%s | pegajoso=%s",
             PROXY or "-", PERFIL, headless,
             "si" if PEGAJOSO else "solo las de @vpn")
    log.info("[SUPERVISOR] las de @vpn= van marcadas (%s) para que el proxy "
             "las saque por el tunel", CABECERA_TUNEL)
    if VPN_ON:
        log.info("[SUPERVISOR] vpn on=%r off=%r espera=%ss reintento=%ss",
                 VPN_ON, VPN_OFF or "(ninguno)", int(VPN_ESPERA),
                 int(INTERVALO_VPN))
        if not VPN_OFF:
            log.warning("[SUPERVISOR] sin --vpn-off/GRAB_VPN_OFF la VPN "
                        "queda encendida tras abrir cada modelo")
    log.info("=" * 70)
    if not os.path.exists(GRABAR):
        log.warning("[SUPERVISOR] no existe %s", GRABAR)

    async with async_playwright() as pw:
        ctx = await abrir_navegador(pw, headless=headless)
        for p in list(ctx.pages):            # la pestana en blanco inicial
            try:
                await p.close()
            except Exception:
                pass
        try:
            while not parar.is_set():
                nombres = set(lista.actual())

                for nombre in nombres - set(emisiones):
                    em = Emision(nombre, ctx)
                    emisiones[nombre] = em
                    em.tarea = asyncio.create_task(em.bucle(parar))

                # @vpn= se puede editar en caliente, como todo lo demas.
                # Marcada = su pagina y su consulta de estado salen por el
                # tunel del proxy; no hace falta ningun comando de VPN.
                for nombre, em in emisiones.items():
                    em.necesita_vpn = nombre.lower() in lista.vpn

                for nombre in set(emisiones) - nombres:
                    em = emisiones.pop(nombre)
                    log.info("[LISTA] %s salio de grabar.txt", nombre)
                    em.tarea.cancel()
                    await em.cerrar("quitada de grabar.txt")

                await asyncio.sleep(INTERVALO_LISTA)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            parar.set()
            for em in emisiones.values():
                if em.tarea:
                    em.tarea.cancel()
            for em in emisiones.values():
                await em.cerrar("cierre del supervisor")
            try:
                await ctx.close()
            except Exception:
                pass
    log.info("[FIN] listo")
    return 0


def main(argv) -> int:
    configurar_log()
    p = argparse.ArgumentParser(description="Abre las emisiones por vos.")
    p.add_argument("--login", action="store_true",
                   help="abre el navegador visible para aceptar el aviso de "
                        "edad / iniciar sesion una vez")
    p.add_argument("--ver", action="store_true",
                   help="no usar headless (para mirar que hace)")
    p.add_argument("--estado", action="store_true",
                   help="consulta el estado de cada modelo y sale")
    p.add_argument("--vpn-on", metavar="CMD", default="",
                   help="comando que conecta la VPN (para las modelos de "
                        "@vpn= en grabar.txt)")
    p.add_argument("--vpn-off", metavar="CMD", default="",
                   help="comando que desconecta la VPN")
    p.add_argument("--pegajoso", action="store_true",
                   help="no cerrar la pagina mientras el video avance, "
                        "aunque la consulta de estado falle (modelos "
                        "geobloqueadas: abris con VPN y despues la apagas)")
    args = p.parse_args(argv)
    global PEGAJOSO, VPN_ON, VPN_OFF
    if args.pegajoso:
        PEGAJOSO = True
    if args.vpn_on:
        VPN_ON = args.vpn_on
    if args.vpn_off:
        VPN_OFF = args.vpn_off

    try:
        import playwright  # noqa: F401
    except ImportError:
        log.error("falta playwright:  pip install playwright")
        log.error("y despues:         python -m playwright install chromium")
        return 2

    if args.login:
        return asyncio.run(login())
    if args.estado:
        return asyncio.run(estado_de_todas())
    try:
        return asyncio.run(supervisar(headless=not args.ver))
    except KeyboardInterrupt:
        return 0


async def estado_de_todas() -> int:
    from playwright.async_api import async_playwright
    lista = Lista(GRABAR)
    nombres = lista.actual()
    if not nombres:
        log.info("no hay nombres de modelo en %s", GRABAR)
        return 0
    async with async_playwright() as pw:
        ctx = await abrir_navegador(pw, headless=True)
        for nombre in nombres:
            em = Emision(nombre, ctx)
            # las de @vpn= hay que preguntarlas por el tunel o contestan 403
            em.necesita_vpn = nombre.lower() in lista.vpn
            estado, sid = await em.consultar()
            log.info("%-24s id=%-12s %s", nombre, sid or "?", estado)
        await ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
