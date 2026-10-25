# -*- coding: utf-8 -*-
r"""Mide la carga que la propia interfaz le mete al servidor, y si lo tranca.

Herramienta de medicion, NO parte del motor de captura ni de Nimbo. Solo
lee: GET contra la API local y lectura de estado\siguiendo.json. No
escribe nada, no toca grabar.txt ni estado\. El puerto y el token salen de
instancia.json, igual que pruebas\sondeo.py y que cualquier pestana.

QUE IMITA
---------
Hasta el 25/10/2026 (ver specs\congelado_interfaz.md): cada delta de
estado -- uno por segundo -- hacia que main.js repintara todo, y
renderTarjetas/renderFavoritas reconstruian su contenedor con
innerHTML="". Cada reconstruccion recrea los <img> de las miniaturas, y
cada <img> es un GET a /api/sitio/imagen, que del lado del servidor era un
HTTPS saliente al CDN ocupando un hilo del pool de 12 (EJECUTOR_HILOS).
Con N modelos en la grilla eso es N pedidos de imagen por segundo, para
siempre.

Este script reproduce esa cadencia -- N pedidos/s con URLs de avatar
REALES, por como maximo `--sockets` conexiones a la vez.

QUE MIDE, Y QUE NO
------------------
Mide el lado SERVIDOR (puntos 3 y 4 del arreglo: cache en memoria de
sitio.imagen y CUPO_IMAGEN). El lado CLIENTE (puntos 1 y 2: Cache-Control
y la reconciliacion sin innerHTML) no se puede medir desde aca, porque
este script ES el cliente y pide siempre: eso se mide en la consola de la
ventana de Nimbo, con

    performance.getEntriesByType("resource")
      .filter(r => r.name.includes("sitio/imagen")).length

que antes crecia ~N por segundo y despues queda clavado.

HONESTIDAD SOBRE LA LINEA DE BASE
---------------------------------
Con la red sana y el CDN rapido (~0.1 s por miniatura) la saturacion NO se
reproduce: medido el 25/10/2026 contra la instalacion real, 24 pedidos/s
por 24 sockets dejaban /api/lista en p95 = 15 ms, intacto. La aritmetica
de por que igual se congela esta en el cuarto medidor de abajo
(hilos-segundo): el pool de 12 aguanta N/s mientras cada pedido tarde poco,
y revienta cuando el CDN o el DNS se trancan y cada uno se come su timeout
entero (hay 2391 "getaddrinfo failed" en estado\eventos_directo.jsonl: en
esta maquina el DNS si falla). 8 pedidos/s x 10 s de timeout = 80 hilos
pedidos contra 12 que hay, con la cola de run_in_executor sin tope: de ahi
que no se recupere solo.

Por eso la puerta principal de este script NO es la latencia de la API
sino la latencia de la miniatura: si baja a ~0 ms es que se sirve de RAM
y NO hay pedido saliente, y entonces el escenario de arriba no puede
pasar, ande la red como ande.

LAS PUERTAS (specs\congelado_interfaz.md)
-----------------------------------------
    miniatura p50 ........... < 5 ms      (sale de la cache, sin red)
    /api/lista p95 .......... < 250 ms    (guarda de regresion)
    hueco maximo entre deltas < 3 s       (guarda de hambreado del pool)

USO
---
    python pruebas\carga_api.py                       (8 imagenes/s, 30 s)
    python pruebas\carga_api.py --imagenes 8 --seg 30
    python pruebas\carga_api.py --solo-estado         (linea de base sin carga)
    python pruebas\carga_api.py --imagenes 24 --sockets 24    (estres)

    --url "http://127.0.0.1:PUERTO/?t=TOKEN"   si no hay instancia.json
          (sacala de la consola de la ventana de Nimbo: location.href)
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INTERVALO_SONDA_S = 0.5
TOPE_PEDIDO_S = 30.0        # si un GET tarda mas que esto, ya es un congelado
HILOS_POOL = 12             # EJECUTOR_HILOS de servidor.py, para la aritmetica

PUERTA_MINIATURA_MS = 5.0
PUERTA_LISTA_MS = 250.0
PUERTA_HUECO_S = 3.0


# -- de donde salen el puerto, el token y las URLs reales ------------------

def instancia(url_manual=None):
    """-> (base, token). Igual que pruebas\\sondeo.py:instancia(), con la
    salida manual para cuando no hay instancia.json (por ejemplo si Nimbo
    se arranco con `python nimbo\\servidor.py`, que no lo escribe)."""
    if url_manual:
        partes = urllib.parse.urlsplit(url_manual)
        token = urllib.parse.parse_qs(partes.query).get("t", [""])[0]
        if not token:
            raise SystemExit("la --url tiene que traer ?t=<token>")
        return "%s://%s" % (partes.scheme, partes.netloc), token
    ruta = os.path.join(RAIZ, "instancia.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        raise SystemExit(
            "no se pudo leer %s -- Nimbo no esta corriendo, o se arranco con\n"
            "  python nimbo\\servidor.py (que no escribe ese archivo).\n"
            "Pasa la URL a mano:\n"
            "  python pruebas\\carga_api.py --url \"http://127.0.0.1:PUERTO/?t=TOKEN\"\n"
            "(en la consola de la ventana de Nimbo: location.href)" % ruta)
    return "http://127.0.0.1:%d" % d["puerto"], d["token"]


def avatares():
    """Las URLs de miniatura REALES que la ventana pide hoy: el snapshot de
    cada modelo seguida (ver sesion.py:_url_snapshot). Medir con URLs
    inventadas no mediria nada -- sitio.imagen() descarta cualquier host
    fuera de HOSTS_IMAGEN_PERMITIDOS antes de intentar el pedido."""
    ruta = os.path.join(RAIZ, "estado", "siguiendo.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [m["avatar"] for m in (d.get("modelos") or []) if m.get("avatar")]


def pedir(base, token, ruta):
    """-> (segundos, tam_bytes | None). None en tam = fallo."""
    url = base + ruta + ("&" if "?" in ruta else "?") + "t=" + token
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=TOPE_PEDIDO_S) as r:
            datos = r.read()
        return time.monotonic() - t0, len(datos)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError, ValueError):
        return time.monotonic() - t0, None


# -- los tres hilos de la medicion ----------------------------------------

class Carga:
    """N pedidos de imagen por segundo, por como maximo `sockets`
    conexiones a la vez -- la ventana reconstruyendo N tarjetas a 1 Hz."""

    def __init__(self, base, token, urls, por_segundo, sockets):
        self.base, self.token = base, token
        self.urls = urls
        self.por_segundo = por_segundo
        self.pool = ThreadPoolExecutor(max_workers=max(1, sockets),
                                        thread_name_prefix="carga-img")
        self.parar = threading.Event()
        self.lock = threading.Lock()
        self.latencias = []
        self.ok = 0
        self.fallos = 0
        self.bytes = 0
        self.pedidos = 0

    def _uno(self, url):
        ruta = "/api/sitio/imagen?url=" + urllib.parse.quote(url, safe="")
        seg, tam = pedir(self.base, self.token, ruta)
        with self.lock:
            self.latencias.append(seg)
            if tam is None:
                self.fallos += 1
            else:
                self.ok += 1
                self.bytes += tam

    def correr(self):
        """Un tick por segundo, N pedidos por tick: la cadencia exacta de
        _bucle_estado -> delta -> pintarEstado -> innerHTML."""
        i = 0
        while not self.parar.is_set():
            t0 = time.monotonic()
            for _ in range(self.por_segundo):
                if not self.urls:
                    break
                url = self.urls[i % len(self.urls)]
                i += 1
                with self.lock:
                    self.pedidos += 1
                self.pool.submit(self._uno, url)
            # el tick siguiente sale a 1 s del anterior aunque los pedidos
            # del tick anterior todavia esten en vuelo -- justo como el
            # navegador, que no espera a que carguen las imagenes para
            # repintar en el delta siguiente
            self.parar.wait(max(0.0, 1.0 - (time.monotonic() - t0)))
        self.pool.shutdown(wait=False, cancel_futures=True)


class SondaLista:
    """GET /api/lista: el camino que SI pasa por el pool (a_hilo ->
    lista.leer() -> disco). Es lo que devuelve cada conmutador de las
    tarjetas, asi que lo que tarda esto es lo que tarda un boton en
    responder.

    Ojo: /api/estado NO sirve de sonda -- se responde desde app.estado, la
    cache que mantiene _bucle_estado, sin tocar el pool (servidor.py:685).
    Medido con el, cualquier saturacion queda invisible."""

    def __init__(self, base, token):
        self.base, self.token = base, token
        self.parar = threading.Event()
        self.latencias = []
        self.fallos = 0

    def correr(self):
        while not self.parar.is_set():
            t0 = time.monotonic()
            seg, tam = pedir(self.base, self.token, "/api/lista")
            self.latencias.append(seg)
            if tam is None:
                self.fallos += 1
            self.parar.wait(max(0.0, INTERVALO_SONDA_S - (time.monotonic() - t0)))


class SondaWS:
    """Mide el HUECO entre deltas de estado por /ws, no su tasa: el bucle
    publica solo cuando algo cambio, asi que con pocas modelos la tasa en
    reposo ya es ~0.5/s (medido) y no dice nada. Lo que delata el pool
    hambreado es un hueco grande entre dos deltas seguidos."""

    def __init__(self, base, token):
        self.url = base.replace("http://", "ws://") + "/ws?t=" + token
        self.parar = threading.Event()
        self.deltas = 0
        self.inicial = False
        self.hueco_max = 0.0
        self.error = None

    def correr(self):
        try:
            from websockets.sync.client import connect
        except ImportError as e:      # websockets es obligatoria, pero igual
            self.error = "sin websockets: %s" % e
            return
        try:
            with connect(self.url, open_timeout=10) as ws:
                ultimo = time.monotonic()
                while not self.parar.is_set():
                    try:
                        crudo = ws.recv(timeout=1.0)
                    except TimeoutError:
                        self.hueco_max = max(self.hueco_max,
                                              time.monotonic() - ultimo)
                        continue
                    try:
                        msg = json.loads(crudo)
                    except (ValueError, TypeError):
                        continue
                    if msg.get("tipo") == "estado":
                        ahora = time.monotonic()
                        self.hueco_max = max(self.hueco_max, ahora - ultimo)
                        ultimo = ahora
                        self.deltas += 1
                    elif msg.get("tipo") == "estado_inicial":
                        self.inicial = True
                        ultimo = time.monotonic()
        except Exception as e:        # la sonda nunca tiene que tirar la corrida
            self.error = "%s: %s" % (type(e).__name__, e)


# -- informe ---------------------------------------------------------------

def pct(valores, p):
    if not valores:
        return float("nan")
    orden = sorted(valores)
    i = min(len(orden) - 1, max(0, int(round((p / 100.0) * (len(orden) - 1)))))
    return orden[i]


def ms(x):
    return "nan" if x != x else "%.0f ms" % (x * 1000.0)


def informe(args, carga, sonda, ws, transcurrido):
    print()
    print("=" * 64)
    print("  %d imagenes/s por %d sockets, %.0f s" %
          (args.imagenes if carga else 0, args.sockets, transcurrido))
    print("=" * 64)

    puertas = []
    p50_img = float("nan")

    if carga:
        with carga.lock:
            pedidos, ok, fallos = carga.pedidos, carga.ok, carga.fallos
            lat, tot = list(carga.latencias), carga.bytes
        p50_img, p95_img = pct(lat, 50), pct(lat, 95)
        print("  /api/sitio/imagen  (la carga que genera la ventana)")
        print("    pedidos ............ %d (%.1f/s, %.0f/min)"
              % (pedidos, pedidos / transcurrido, pedidos / transcurrido * 60))
        print("    completados ........ %d ok, %d fallidos" % (ok, fallos))
        print("    bytes servidos ..... %.1f MB (%.1f MB/min)"
              % (tot / 1048576.0, tot / 1048576.0 / transcurrido * 60))
        print("    latencia p50/p95 ... %s / %s   [puerta p50: < %.0f ms]"
              % (ms(p50_img), ms(p95_img), PUERTA_MINIATURA_MS))
        if lat:
            # Lo que de verdad decide si el pool de 12 alcanza: cuantos
            # hilos-segundo por segundo pide esta carga. >= HILOS_POOL es
            # saturacion, y la cola de run_in_executor no tiene tope.
            demanda = sum(lat) / transcurrido
            print("    demanda del pool ... %.1f hilos de %d%s"
                  % (demanda, HILOS_POOL,
                      "  <-- SATURADO" if demanda >= HILOS_POOL else ""))
            print("    si cada pedido se fuera a su timeout (10 s): %.0f hilos"
                  % (pedidos / transcurrido * 10.0))
        if len(lat) < pedidos * 0.9:
            print("    !! %d pedidos quedaron en vuelo al cortar: la cola no"
                  % (pedidos - len(lat)))
            print("       daba abasto -- eso es el congelado")
        puertas.append(("miniatura p50",
                         p50_img == p50_img and p50_img * 1000.0 < PUERTA_MINIATURA_MS))

    p95 = pct(sonda.latencias, 95)
    print("  /api/lista  (lo que tarda un boton en responder)")
    print("    muestras ........... %d (%d fallidas)"
          % (len(sonda.latencias), sonda.fallos))
    print("    p50 / p95 .......... %s / %s   [puerta p95: < %.0f ms]"
          % (ms(pct(sonda.latencias, 50)), ms(p95), PUERTA_LISTA_MS))
    if sonda.latencias:
        print("    maximo ............. %s" % ms(max(sonda.latencias)))
    puertas.append(("/api/lista p95", p95 == p95 and p95 * 1000.0 < PUERTA_LISTA_MS))

    print("  /ws  (que la ventana siga enterandose del estado)")
    if ws.error:
        print("    sonda caida ........ %s" % ws.error)
    else:
        print("    estado_inicial ..... %s" % ("si" if ws.inicial else "NO"))
        print("    deltas ............. %d (%.2f/s, informativo)"
              % (ws.deltas, ws.deltas / transcurrido))
        print("    hueco maximo ....... %.1f s   [puerta: < %.1f s]"
              % (ws.hueco_max, PUERTA_HUECO_S))
        puertas.append(("hueco de deltas", ws.hueco_max < PUERTA_HUECO_S))

    print("-" * 64)
    for nombre, pasa in puertas:
        print("  %-20s %s" % (nombre, "PASA" if pasa else "NO PASA"))
    print("=" * 64)
    return all(pasa for _, pasa in puertas)


def main():
    p = argparse.ArgumentParser(
        description="Mide la carga que la interfaz de Nimbo le mete al servidor.")
    p.add_argument("--imagenes", type=int, default=8,
                    help="pedidos de miniatura por segundo (= modelos en la "
                         "grilla). Defecto 8, el caso que congelaba.")
    p.add_argument("--sockets", type=int, default=6,
                    help="conexiones simultaneas. 6 = el tope por host de "
                         "Chromium (imitacion fiel de la ventana).")
    p.add_argument("--seg", type=float, default=30.0, help="duracion. Defecto 30.")
    p.add_argument("--solo-estado", action="store_true",
                    help="sin carga de imagenes: la linea de base.")
    p.add_argument("--url", default=None,
                    help="URL con ?t=<token> si no hay instancia.json.")
    args = p.parse_args()

    base, token = instancia(args.url)
    print("Nimbo en %s" % base)

    urls = []
    if not args.solo_estado:
        urls = avatares()
        if not urls:
            raise SystemExit(
                "estado\\siguiendo.json no trae avatares -- pega la sesion y\n"
                "apreta Actualizar en la pestana Modelos, o corre con --solo-estado.")
        print("%d avatares reales de estado\\siguiendo.json" % len(urls))

    seg, tam = pedir(base, token, "/api/lista")
    if tam is None:
        raise SystemExit("no responde /api/lista -- revisa el puerto y el token")
    print("primer /api/lista: %s" % ms(seg))

    carga = None
    hilos = []
    sonda = SondaLista(base, token)
    ws = SondaWS(base, token)
    hilos.append(threading.Thread(target=sonda.correr, name="sonda-lista"))
    hilos.append(threading.Thread(target=ws.correr, name="sonda-ws"))
    if urls:
        carga = Carga(base, token, urls, args.imagenes, args.sockets)
        hilos.append(threading.Thread(target=carga.correr, name="carga"))

    print("midiendo %.0f s..." % args.seg)
    t0 = time.monotonic()
    for h in hilos:
        h.daemon = True
        h.start()
    try:
        time.sleep(args.seg)
    except KeyboardInterrupt:
        print("\ncortado a mano")
    transcurrido = time.monotonic() - t0

    sonda.parar.set()
    ws.parar.set()
    if carga:
        carga.parar.set()
    for h in hilos:
        h.join(timeout=5)

    return 0 if informe(args, carga, sonda, ws, transcurrido) else 1


if __name__ == "__main__":
    sys.exit(main())
