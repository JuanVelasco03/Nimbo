# -*- coding: utf-8 -*-
"""Sonda del login de chaturbate: medir que cookie marca "hay sesion", si
hace falta algo mas que cookies, de donde salen las seguidas, si hay
buscador con cuenta y si mitmproxy puede interceptar chaturbate.com --
los seis puntos de la Fase 0 de SPEC 21 (specs\\21-login_chaturbate.md).

Herramienta de diagnostico, NO parte del motor de captura. Calco de
pruebas\\sonda_chaturbate.py y pruebas\\sonda_directo.py (ver esos
archivos para el por que de cada restriccion): mide fase por fase contra
el sitio VIVO y deja la evidencia impresa para que el resto de SPEC 21 se
escriba con datos, no con supuestos.

Una sonda no se puede loguear sola -- SPEC 15 ya corto esa salida
(Cloudflare detecta CDP/Playwright, probado tres veces). Por eso esta
sonda toma la sesion como ENTRADA: un "Copiar como cURL (bash)" o
"Copiar como PowerShell" de un pedido real, capturado a mano desde una
pestana donde el usuario YA inicio sesion (--curl <archivo>). El parseo
de ese pegado se REUSA de nimbo\\sesion.py (_parsear_pegado) en vez de
duplicarlo -- mismo criterio que pruebas\\sondeo.py reusando nimbo\\vivo.py:
sesion.py no tiene ningun efecto secundario al importarse (solo clases y
regex, igual que mouflon.py), a diferencia de nucleo\\scripts\\
detectar_m3u8.py.

Medido YA (sin cuenta, 2026-11-10, request anonimo con curl): pedir
https://chaturbate.com/followed-cams/ sin cookies da 302 a
"/auth/login/?next=/followed-cams/", con Set-Cookie de "csrftoken" y
"sbr" (bot-management de Cloudflare, no es la cookie de sesion) pero
SIN ninguna cookie que se parezca a un "sessionid" de Django -- esa
cookie solo aparece despues de loguearse de verdad, que es exactamente
lo que esta sonda no puede hacer sola. De ahi el --curl.

Restricciones duras (las mismas que sonda_directo.py/sonda_chaturbate.py):
  - Solo libreria estandar (mas el import de nimbo.sesion, que tambien es
    stdlib puro).
  - Escribe solo bajo --salida (por defecto %TEMP%\\nimbo_sonda_login_cb\\).
    Nunca en estado\\, datos\\ ni nucleo\\.
  - NUNCA imprime el valor de una cookie o un header completo -- solo
    NOMBRES. El valor de una cookie de sesion es tan sensible como una
    contrasena (ver nimbo\\sesion.py, docstring: "sesion.json equivale en
    la practica a la contrasena de la cuenta").

Uso:
    python pruebas\\sonda_login_chaturbate.py --curl <archivo>
        [--fase N] [--salida CARPETA] [--proxy 127.0.0.1:8080]
        [--modelo nombre_en_vivo]

Cada fase se corre en orden y se aborta en la primera que falla. "--fase 0"
no pide red: documenta el criterio (no hay ningun esquema de cifrado que
verificar en esta sonda, a diferencia de MOUFLON -- igual que
sonda_chaturbate.py fase 0), no mide nada contra el sitio.
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "nimbo"))
import sesion as _sesion_mod  # noqa: E402  (ver docstring: reuso deliberado)

PAGINA_SEGUIDAS = "https://chaturbate.com/followed-cams/"

CABECERAS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

# El certificado que mitmproxy presenta en la fase 5 es el suyo propio
# (autofirmado, o el instalado por preflight.instalar_certificado en el
# almacen de Windows): no vale la pena depender de que el enum_certificates
# de Windows lo encuentre para una sonda de diagnostico -- mismo criterio
# que nimbo\sitio.py:_CONTEXTO_SSL.
_CONTEXTO_SSL_LAXO = ssl.create_default_context()
_CONTEXTO_SSL_LAXO.check_hostname = False
_CONTEXTO_SSL_LAXO.verify_mode = ssl.CERT_NONE

# Candidatos SIN CONFIRMAR -- la sonda los prueba y reporta cual (si
# alguno) responde JSON; no asume que ninguno exista de antemano.
CANDIDATOS_SEGUIDAS_JSON = [
    PAGINA_SEGUIDAS + "?format=json",
    "https://chaturbate.com/api/ts/follow/following/",
    PAGINA_SEGUIDAS + "load/",
]
CANDIDATOS_BUSCADOR = [
    "https://chaturbate.com/search/?q=a",
    PAGINA_SEGUIDAS + "?q=a",
    "https://chaturbate.com/api/ts/search/?q=a",
]


class Falla(Exception):
    pass


def ok(msg):
    print("  OK   %s" % msg)


def falla(msg):
    print("  FALLA %s" % msg)
    raise Falla(msg)


def _pedir(url, cookies, headers_extra=None, opener=None):
    """GET pelado con las cookies dadas. -> (logueado, status, url_final,
    cuerpo_bytes). 'logueado' es una heuristica LOCAL: 200 y la URL final
    (tras seguir redirects) no cayo en /auth/login/. Nunca lanza fuera de
    errores de red/timeout, que se propagan como excepcion de
    urllib/OSError -- el llamador decide si eso es fatal."""
    cab = dict(CABECERAS)
    if headers_extra:
        cab.update(headers_extra)
    if cookies:
        cab["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
    req = urllib.request.Request(url, headers=cab)
    abridor = opener.open if opener else urllib.request.urlopen
    try:
        with abridor(req, timeout=15.0) as r:
            url_final, status, cuerpo = r.geturl(), r.status, r.read()
    except urllib.error.HTTPError as e:
        url_final = e.geturl() if hasattr(e, "geturl") else url
        status, cuerpo = e.code, e.read()
    logueado = status == 200 and "/auth/login/" not in url_final
    return logueado, status, url_final, cuerpo


# --------------------------------------------------------------------------
# Fases
# --------------------------------------------------------------------------

def fase_0_criterio():
    print("[fase 0] verificar si hace falta un esquema de descifrado (sin red)")
    print("  chaturbate no envuelve nada de esto en un token cifrado propio: "
          "la sesion es una cookie comun y corriente, igual que cualquier "
          "sitio Django (SPEC 21, seccion B del 'Por que'). No hace falta "
          "ningun canario de descifrado.")
    ok("sin esquema de cifrado que verificar")


def fase_1_cookie_minima(cookies_completas: dict) -> dict:
    print("[fase 1] cual cookie marca 'hay sesion' (referencia: %s)" % PAGINA_SEGUIDAS)
    logueado, status, url_final, _ = _pedir(PAGINA_SEGUIDAS, cookies_completas)
    if not logueado:
        falla("con TODAS las cookies pegadas, el pedido de referencia no se "
              "ve logueado (status=%s, termino en %s) -- asegurate de que "
              "el --curl sea de una pestana donde YA iniciaste sesion"
              % (status, url_final))
    ok("con el set completo (%d cookies) el pedido se ve logueado" % len(cookies_completas))

    minimas = dict(cookies_completas)
    for nombre in list(cookies_completas):
        prueba = dict(minimas)
        del prueba[nombre]
        logueado, _, _, _ = _pedir(PAGINA_SEGUIDAS, prueba)
        if logueado:
            del minimas[nombre]
            print("  '%s' no hace falta -- se puede sacar" % nombre)
        else:
            print("  '%s' SI hace falta -- se queda" % nombre)
    if not minimas:
        falla("la reduccion se quedo sin ninguna cookie -- revisar a mano, "
              "algo no cierra con la heuristica de 'logueado'")
    print("  cookie(s) marcadora(s) minima(s): %s" % ", ".join(sorted(minimas)))
    ok("subconjunto minimo: %d de %d cookies" % (len(minimas), len(cookies_completas)))
    return minimas


def fase_2_extra(minimas: dict) -> None:
    print("[fase 2] si hace falta algo mas que cookies (Referer, X-Requested-With)")
    # El CSRF token solo protege pedidos que CAMBIAN estado (POST/PUT/
    # DELETE); leer la lista de seguidas es un GET, asi que no se prueba
    # aca -- si algun candidato de la fase 3/4 resulta ser un POST, hay
    # que medirlo ahi.
    variantes = [
        ("sin Referer (no se manda nunca)", {}),
        ("con Referer=" + PAGINA_SEGUIDAS, {"Referer": PAGINA_SEGUIDAS}),
        ("con X-Requested-With=XMLHttpRequest", {"X-Requested-With": "XMLHttpRequest"}),
    ]
    alguna_rompio = False
    for etiqueta, extra in variantes:
        logueado, status, _, _ = _pedir(PAGINA_SEGUIDAS, minimas, extra)
        print("  %-42s -> %s (status=%s)" % (
            etiqueta, "logueado" if logueado else "NO logueado", status))
        alguna_rompio = alguna_rompio or not logueado
    if alguna_rompio:
        ok("alguna variante SI necesito algo mas que cookies -- revisar arriba")
    else:
        ok("ninguna variante rompio el login con solo cookies")


def fase_3_seguidas(minimas: dict, carpeta: str) -> bool:
    print("[fase 3] de donde salen las seguidas: JSON o solo HTML")
    logueado, status, url_final, cuerpo = _pedir(PAGINA_SEGUIDAS, minimas)
    if not logueado:
        falla("la pagina de seguidas no se ve logueada con el subconjunto "
              "minimo (status=%s, %s)" % (status, url_final))
    os.makedirs(carpeta, exist_ok=True)
    ruta_html = os.path.join(carpeta, "followed-cams.html")
    with open(ruta_html, "wb") as f:
        f.write(cuerpo)
    print("  HTML completo guardado en %s (%d bytes) para inspeccion manual"
          % (ruta_html, len(cuerpo)))

    texto = cuerpo.decode("utf-8", "replace")
    pistas = sorted(set(re.findall(
        r'(page=\d+|offset=\d+|"next"\s*:\s*"[^"]*"|data-page="[^"]*")', texto)))
    if pistas:
        print("  pistas de paginacion en el HTML: %s" % ", ".join(pistas[:10]))
    else:
        print("  ninguna pista de paginacion obvia (puede ser scroll infinito via JS)")

    print("  probando candidatos JSON (sin confirmar de antemano):")
    algun_json = False
    for url in CANDIDATOS_SEGUIDAS_JSON:
        _, status_c, _, cuerpo_c = _pedir(
            url, minimas, {"X-Requested-With": "XMLHttpRequest"})
        tipo = "no es JSON (status=%s)" % status_c
        if cuerpo_c[:1] in (b"{", b"["):
            try:
                json.loads(cuerpo_c)
                tipo = "JSON valido"
                algun_json = True
            except ValueError:
                tipo = "parece JSON pero no parsea"
        print("    %-55s -> %s" % (url, tipo))
        if tipo == "JSON valido":
            ruta_json = os.path.join(carpeta, "seguidas_candidato.json")
            with open(ruta_json, "wb") as f:
                f.write(cuerpo_c)
            print("      guardado en %s -- revisar nombres de campo a mano" % ruta_json)

    if not algun_json:
        print("  AVISO: ningun candidato dio JSON valido -- probablemente hay "
              "que parsear el HTML de %s a mano (mismo camino que sitio.py "
              "reusando RE_ESTADO de supervisor.py, o los regex de "
              "nucleo\\scripts\\chaturbate.py)" % ruta_html)
    ok("HTML de seguidas guardado; %s" % (
        "se encontro JSON" if algun_json else "sin JSON, HTML a mano"))
    return algun_json


def fase_4_buscador(minimas: dict, carpeta: str) -> bool:
    print("[fase 4] si hay buscador con cuenta que junte nombre + estado online")
    algun_json = False
    for url in CANDIDATOS_BUSCADOR:
        _, status_c, _, cuerpo_c = _pedir(
            url, minimas, {"X-Requested-With": "XMLHttpRequest"})
        tipo = "no es JSON (status=%s)" % status_c
        if cuerpo_c[:1] in (b"{", b"["):
            try:
                json.loads(cuerpo_c)
                tipo = "JSON valido"
                algun_json = True
            except ValueError:
                tipo = "parece JSON pero no parsea"
        print("  %-55s -> %s" % (url, tipo))
        if tipo == "JSON valido":
            ruta = os.path.join(carpeta, "buscador_candidato.json")
            with open(ruta, "wb") as f:
                f.write(cuerpo_c)
            print("    guardado en %s -- revisar si trae nombre + online" % ruta)
    if algun_json:
        ok("candidato de buscador encontrado -- revisar el JSON guardado")
    else:
        print("  ningun candidato respondio JSON -- igual que con stripchat "
              "anonimo (sitio_chaturbate.py), el campo se queda como texto "
              "simple si esto se confirma a mano")
        ok("sin endpoint de buscador -- queda como texto simple")
    return algun_json


def fase_5_proxy(minimas: dict, proxy: str) -> None:
    print("[fase 5] si mitmproxy puede interceptar chaturbate.com (%s)" % proxy)
    logueado_d, status_d, url_d, _ = _pedir(PAGINA_SEGUIDAS, minimas)
    print("  directo: logueado=%s status=%s url=%s" % (logueado_d, status_d, url_d))

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": "http://" + proxy, "https": "http://" + proxy}),
        urllib.request.HTTPSHandler(context=_CONTEXTO_SSL_LAXO))
    try:
        logueado_p, status_p, url_p, _ = _pedir(PAGINA_SEGUIDAS, minimas, opener=opener)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        falla("el pedido por el proxy no se pudo completar (%s) -- revisa "
              "que mitmdump este corriendo en %s y que la CA este instalada "
              "(preflight.instalar_certificado)" % (e, proxy))
    print("  via proxy: logueado=%s status=%s url=%s" % (logueado_p, status_p, url_p))

    if logueado_p != logueado_d or status_p != status_d:
        falla("el pedido por el proxy se ve DISTINTO al directo -- Cloudflare "
              "puede estar cortando el certificado de mitmproxy para "
              "chaturbate.com; si esto falla, el boton 'Iniciar sesion' sale "
              "del scope y el pegado de cURL queda como unico camino")
    ok("el pedido via proxy se ve igual que el directo -- mitmproxy no rompe la sesion")


def fase_6_chatvideocontext(minimas: dict, nombre_modelo) -> None:
    print("[fase 6] (informativo, para un spec futuro) si /api/chatvideocontext/ "
          "responde distinto con cookies")
    if not nombre_modelo:
        print("  sin --modelo, se omite (no es bloqueante para este spec)")
        return
    url = "https://chaturbate.com/api/chatvideocontext/%s/" % nombre_modelo
    _, status_anon, _, cuerpo_anon = _pedir(url, {})
    _, status_auth, _, cuerpo_auth = _pedir(url, minimas)
    print("  anonimo:  status=%s (%d bytes)" % (status_anon, len(cuerpo_anon)))
    print("  logueado: status=%s (%d bytes)" % (status_auth, len(cuerpo_auth)))
    if cuerpo_anon == cuerpo_auth:
        print("  misma respuesta exacta con y sin cookies")
    else:
        print("  DISTINTA -- dato para un spec futuro, no se usa en este")
    ok("medicion informativa dejada arriba")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--curl", help="archivo con el 'Copiar como cURL (bash)' o "
                     "'Copiar como PowerShell' de un pedido a chaturbate.com "
                     "YA logueado (p.ej. abrir %s a mano y copiar ESE "
                     "pedido desde la pestana Red de las herramientas de "
                     "desarrollador)" % PAGINA_SEGUIDAS)
    ap.add_argument("--fase", type=int, default=6,
                     help="corre hasta esta fase inclusive (defecto: 6)")
    ap.add_argument("--salida", default=None,
                     help="carpeta de trabajo (defecto: %%TEMP%%\\nimbo_sonda_login_cb\\)")
    ap.add_argument("--proxy", default="127.0.0.1:8080",
                     help="proxy de mitmproxy para la fase 5 (defecto: 127.0.0.1:8080)")
    ap.add_argument("--modelo", default=None,
                     help="nombre de una modelo en vivo, solo para la fase 6 (informativo)")
    args = ap.parse_args()

    carpeta = args.salida or os.path.join(os.environ.get("TEMP", "."), "nimbo_sonda_login_cb")

    try:
        fase_0_criterio()
        if args.fase == 0:
            return 0

        if not args.curl:
            print("\nFalta --curl <archivo>: una sonda no se puede loguear "
                  "sola (ver docstring). Pega el 'Copiar como cURL (bash)' o "
                  "'Copiar como PowerShell' de un pedido a chaturbate.com "
                  "hecho DESPUES de iniciar sesion a mano en un navegador "
                  "normal, guardalo en un archivo, y pasa esa ruta con "
                  "--curl.")
            return 2
        with open(args.curl, encoding="utf-8") as f:
            texto_pegado = f.read()
        partes = _sesion_mod._parsear_pegado(texto_pegado)
        cookies_completas = partes["cookies"]
        if not cookies_completas:
            falla("el pegado no trajo ninguna cookie -- sin cookies no hay "
                  "nada que medir en un sitio que no usa headers de "
                  "autorizacion")
        print("cookies pegadas (%d, solo nombres): %s\n" % (
            len(cookies_completas), ", ".join(sorted(cookies_completas))))

        minimas = fase_1_cookie_minima(cookies_completas)
        if args.fase == 1:
            return 0

        fase_2_extra(minimas)
        if args.fase == 2:
            return 0

        fase_3_seguidas(minimas, carpeta)
        if args.fase == 3:
            return 0

        fase_4_buscador(minimas, carpeta)
        if args.fase == 4:
            return 0

        fase_5_proxy(minimas, args.proxy)
        if args.fase == 5:
            return 0

        fase_6_chatvideocontext(minimas, args.modelo)
        return 0
    except Falla as e:
        print("\nSonda abortada: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
