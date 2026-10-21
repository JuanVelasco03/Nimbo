"""Sesion de la cuenta real de stripchat (favoritas remotas, "Siguiendo"
en la interfaz) -- separado a proposito de sitio.py, que habla con el
sitio de forma anonima.

Como se llego a este diseno (vale la pena dejarlo escrito: se probaron
dos caminos antes y los dos fallaron en la practica):

  1. Un perfil de Chromium propio, manejado por Playwright, donde el
     usuario inicia sesion a mano en una ventana real -- el mismo truco
     que ya usa nucleo\\scripts\\supervisor.py (su "--login"). NO
     funciono: Cloudflare detecto la automatizacion (CDP, el protocolo
     que CUALQUIER libreria de automatizacion usa para controlar el
     navegador, sea Chromium empaquetado o el Edge de verdad instalado
     en el sistema) y corto con un desafio irresoluble ("Actividad
     automatica detectada"), probado tres veces con tres variantes.
  2. Pegar el "Copiar como cURL (bash)" de un pedido ya autenticado,
     capturado a mano desde las herramientas de desarrollador del
     navegador normal del usuario (sin automatizar nada). Esto SI
     funciona: confirmado en vivo con un pedido urllib comun y
     corriente -- sin Playwright, sin CDP, sin ventana, el sitio no
     tiene forma de distinguirlo de cualquier otro cliente HTTP.

Por eso esta clase no abre ningun navegador: solo guarda lo que el
usuario pega (cookies + headers, tal cual los mando su navegador real)
y hace pedidos HTTP comunes con eso, igual de "manual" en espiritu que
sitio.py -- la unica diferencia es que ESTOS pedidos llevan una sesion
de verdad.

Dato sensible: sesion.json equivale en la practica a la contrasena de
la cuenta mientras la sesion sea valida. Atomico (.part + os.replace,
igual que favoritas.py), nunca en el repo (ver .gitignore), y nunca se
imprime su contenido en ningun log.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

TOPE_PAGINAS = 10   # 10 * limite(60) = 600 favoritas en linea: de sobra
LIMITE_DEFECTO = 60


class Sesion:
    def __init__(self, ruta: str):
        self.ruta = ruta
        self._lock = threading.Lock()

    # -- persistencia -------------------------------------------------

    def leer(self):
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
            return datos if isinstance(datos, dict) else None
        except (OSError, ValueError):
            return None

    def _escribir(self, datos) -> None:
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        tmp = self.ruta + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.ruta)

    def borrar(self) -> bool:
        with self._lock:
            try:
                os.remove(self.ruta)
                return True
            except OSError:
                return False

    # -- guardar lo pegado ---------------------------------------------

    def guardar_desde_curl(self, texto_curl: str) -> dict:
        """Parsea lo pegado desde las herramientas de desarrollador --
        "Copiar como cURL (bash)" o "Copiar como PowerShell" (algunos
        navegadores, como Opera, hacen mas facil copiar uno que el
        otro; se aceptan los dos) -- y lo guarda. Levanta ValueError
        con un mensaje legible si no se entiende -- servidor.py ya
        convierte eso en un 400 para la interfaz."""
        partes = _parsear_pegado(texto_curl)
        expira = _exp_de_jwt(_buscar_header(partes["headers"], "Authorization") or "")
        guardado = {
            "cookies": partes["cookies"],
            "headers": partes["headers"],
            "url_origen": partes["url"],
            "guardado": time.time(),
            "expira": expira,
        }
        with self._lock:
            self._escribir(guardado)
        return self.estado()

    def estado(self) -> dict:
        """-> {"activa", "expira", "guardado"}. 'activa' es una
        estimacion LOCAL (mira el 'exp' del JWT si habia uno) -- la
        unica confirmacion real es si favoritas_en_linea() de verdad
        funciona, que es lo que decide 'autenticado' en siguiendo.json."""
        datos = self.leer()
        if not datos:
            return {"activa": False, "expira": None, "guardado": None}
        expira = datos.get("expira")
        vencida = expira is not None and expira < time.time()
        return {"activa": not vencida, "expira": expira,
                "guardado": datos.get("guardado")}

    # -- la llamada autenticada de verdad -------------------------------

    def favoritas_en_linea(self, limite: int = LIMITE_DEFECTO) -> dict:
        """-> {"autenticado": bool, "error": str|None, "modelos": [...]}.
        Nunca lanza -- misma filosofia que sitio.buscar(): si el sitio
        cambio algo o la sesion caduco, se degrada a un resultado vacio
        con el motivo adentro, no a una excepcion."""
        datos = self.leer()
        if not datos:
            return {"autenticado": False, "error": "sin sesion guardada", "modelos": []}

        sitio = _origen(datos.get("url_origen") or "")
        if not sitio:
            return {"autenticado": False, "error": "sesion guardada sin URL valida", "modelos": []}

        cabeceras = dict(datos.get("headers") or {})
        _quitar_header(cabeceras, "Cookie")      # las cookies van aparte, no duplicar
        # el "Accept-Encoding" pegado (gzip/br/zstd) hace que el sitio
        # comprima la respuesta -- urllib, a diferencia de un navegador,
        # NO descomprime solo, asi que json.loads() se rompia sobre los
        # bytes crudos (confirmado en vivo: el cuerpo empezaba con el
        # magic number de gzip, \x1f\x8b). Mejor pedir sin comprimir.
        _quitar_header(cabeceras, "Accept-Encoding")
        cabeceras.setdefault("Accept", "*/*")
        cookies = datos.get("cookies") or {}
        if cookies:
            cabeceras["Cookie"] = "; ".join(
                "%s=%s" % (k, v) for k, v in cookies.items())

        todos, vistos = [], set()
        offset = 0
        for _ in range(TOPE_PAGINAS):
            qs = urlencode({"offset": offset, "limit": limite,
                            "sortBy": "bestMatch", "mlfv": "false"})
            url = sitio + "/api/front/models/favorites?" + qs
            req = urllib.request.Request(url, headers=cabeceras)
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    cuerpo = json.loads(r.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return {"autenticado": False, "error": "sesion caducada", "modelos": todos}
                return {"autenticado": True, "error": "http_%d" % e.code, "modelos": todos}
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
                return {"autenticado": True, "error": "error:%s" % type(e).__name__, "modelos": todos}

            pagina = _normalizar_modelos(cuerpo)
            for m in pagina:
                clave = m["username"].lower()
                if clave in vistos:
                    continue
                vistos.add(clave)
                todos.append(m)

            total = cuerpo.get("totalCount") if isinstance(cuerpo, dict) else None
            offset += limite
            if not pagina or total is None or offset >= total:
                break

        return {"autenticado": True, "error": None, "modelos": todos}

    def actualizar_siguiendo(self, ruta_salida: str) -> dict:
        """Pide favoritas_en_linea() y escribe el resultado (mas
        'generado') en ruta_salida de forma atomica -- mismo contrato
        que detector.json/supervisor.json: Nimbo solo LEE ese
        archivo (ver servidor.py), esto es lo unico que lo escribe."""
        resultado = self.favoritas_en_linea()
        datos = {"generado": time.time(), **resultado}
        os.makedirs(os.path.dirname(ruta_salida) or ".", exist_ok=True)
        tmp = ruta_salida + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
        os.replace(tmp, ruta_salida)
        return datos


# -- parseo de lo pegado -----------------------------------------------
# Dos formatos aceptados, los dos salen del mismo boton "Copiar" de la
# pestana Red de las herramientas de desarrollador -- cual esta mas a
# mano depende del navegador (en Opera, por ejemplo, "como PowerShell"
# a veces es mas facil de encontrar que "como cURL").

def _parsear_pegado(texto: str) -> dict:
    t = (texto or "").strip()
    if not t:
        raise ValueError("pega el texto completo, esta vacio")
    primera = t.split(None, 1)[0] if t.split() else ""
    if primera == "curl":
        return _parsear_curl_bash(t)
    if "Invoke-WebRequest" in t or "New-Object System.Net.Cookie" in t or "$session" in t:
        return _parsear_powershell(t)
    raise ValueError(
        "no reconozco el formato pegado -- copia el pedido desde la "
        "pestana Red de las herramientas de desarrollador con "
        "\"Copiar - Copiar como cURL (bash)\" o \"Copiar - Copiar como "
        "PowerShell\" (el que te deje copiar)")


def _parsear_curl_bash(texto: str) -> dict:
    import shlex

    texto = (texto or "").strip()
    try:
        tokens = shlex.split(texto, posix=True)
    except ValueError as e:
        raise ValueError("no se pudo interpretar el texto pegado (%s)" % e)
    if not tokens or tokens[0] != "curl":
        raise ValueError(
            "tiene que empezar con 'curl ...' -- copia el pedido con "
            "\"Copiar como cURL (bash)\" desde la pestana Red de las "
            "herramientas de desarrollador")

    url = None
    headers = {}
    cookies = {}
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-H", "--header"):
            i += 1
            if i < len(tokens):
                nombre, _, valor = tokens[i].partition(":")
                if nombre:
                    headers[nombre.strip()] = valor.strip()
        elif t in ("-b", "--cookie"):
            i += 1
            if i < len(tokens):
                _agregar_cookies(cookies, tokens[i])
        elif t in ("-A", "--user-agent"):
            i += 1
            if i < len(tokens):
                headers["User-Agent"] = tokens[i]
        elif not t.startswith("-") and url is None and t.startswith(("http://", "https://")):
            url = t
        i += 1

    if not url:
        raise ValueError("no encontre la URL en el comando pegado")

    # algunos navegadores meten las cookies como header "Cookie: ..." en
    # vez de -b/--cookie -- se las pasa igual al dict de cookies.
    valor_cookie = _quitar_header(headers, "Cookie")
    if valor_cookie:
        _agregar_cookies(cookies, valor_cookie)

    if not cookies and _buscar_header(headers, "Authorization") is None:
        raise ValueError(
            "no encontre ninguna cookie ni cabecera de autorizacion -- "
            "asegurate de copiar el pedido DESPUES de iniciar sesion")

    return {"url": url, "headers": headers, "cookies": cookies}


# El "Copiar como PowerShell" de un navegador Chromium arma un script
# $session = New-Object ...WebRequestSession; $session.Cookies.Add(...)
# por cada cookie, mas Invoke-WebRequest -Uri "..." -Headers @{...}.
# No hace falta ejecutar nada de esto: alcanza con leer los literales
# con regex, igual de "manual" en espiritu que leer un cURL a mano.
_RE_PS_COOKIE = re.compile(
    r'New-Object\s+System\.Net\.Cookie\(\s*"([^"]*)"\s*,\s*"([^"]*)"')
_RE_PS_HEADER_LINEA = re.compile(r'^\s*"([^"]+)"\s*=\s*"(.*)"\s*$')
_RE_PS_URI = re.compile(r'-Uri\s+"([^"]+)"')
_RE_PS_AGENTE = re.compile(r'\$session\.UserAgent\s*=\s*"([^"]*)"')


def _desescapar_ps(texto: str) -> str:
    """PowerShell usa la comilla inversa (`) como caracter de escape
    DENTRO de un string con comillas dobles: `" es una comilla literal,
    `$ un signo pesos literal, etc. Sin desescapar esto, un header como
    sec-ch-ua (que trae comillas adentro) queda con comillas inversas
    sueltas -- invalido como header HTTP, y una cookie como _ga_* con un
    `$ adentro queda con el valor corrompido. Confirmado en vivo: esto
    rompia /api/front/models/favorites (la respuesta dejaba de ser JSON)."""
    salida = []
    i = 0
    while i < len(texto):
        if texto[i] == "`" and i + 1 < len(texto):
            salida.append(texto[i + 1])
            i += 2
        else:
            salida.append(texto[i])
            i += 1
    return "".join(salida)


def _parsear_powershell(texto: str) -> dict:
    cookies = {}
    for m in _RE_PS_COOKIE.finditer(texto):
        nombre = _desescapar_ps(m.group(1))
        if nombre:
            cookies[nombre] = _desescapar_ps(m.group(2))

    headers = {}
    en_headers = False
    for linea in texto.splitlines():
        if not en_headers:
            if "-Headers" in linea and "@{" in linea:
                en_headers = True
            continue
        if linea.strip() in ("}", "} `"):
            en_headers = False
            continue
        m = _RE_PS_HEADER_LINEA.match(linea)
        if m:
            headers[_desescapar_ps(m.group(1).strip())] = _desescapar_ps(m.group(2).strip())

    m_agente = _RE_PS_AGENTE.search(texto)
    if m_agente:
        headers.setdefault("User-Agent", _desescapar_ps(m_agente.group(1)))

    m_url = _RE_PS_URI.search(texto)
    if not m_url:
        raise ValueError("no encontre la URL (-Uri) en el script pegado")

    if not cookies and _buscar_header(headers, "Authorization") is None:
        raise ValueError(
            "no encontre ninguna cookie ni cabecera de autorizacion en el "
            "script -- asegurate de copiarlo DESPUES de iniciar sesion")

    return {"url": m_url.group(1), "headers": headers, "cookies": cookies}


def _buscar_header(headers: dict, nombre: str):
    """Los headers HTTP no distinguen mayusculas/minusculas, pero un
    dict si -- esto busca 'nombre' sin importar como vino capitalizado
    en el cURL pegado (DevTools a veces los manda todos en minuscula)."""
    clave = next((k for k in headers if k.lower() == nombre.lower()), None)
    return headers.get(clave) if clave is not None else None


def _quitar_header(headers: dict, nombre: str):
    clave = next((k for k in headers if k.lower() == nombre.lower()), None)
    return headers.pop(clave) if clave is not None else None


def _agregar_cookies(cookies: dict, texto: str) -> None:
    for par in texto.split(";"):
        if "=" in par:
            k, v = par.split("=", 1)
            k = k.strip()
            if k:
                cookies[k] = v.strip()


def _exp_de_jwt(valor_autorizacion: str):
    """-> epoch de expiracion del JWT en 'Authorization: Bearer <jwt>',
    o None si no hay JWT o no se pudo leer. Nunca lanza: esto es solo
    para avisarle al usuario con tiempo, no algo critico."""
    try:
        # a veces viene "Bearer <jwt>", a veces el JWT pelado --
        # confirmado en vivo: stripchat lo manda sin el prefijo.
        token = (valor_autorizacion or "").strip()
        if token.lower().startswith("bearer "):
            token = token[len("bearer "):].strip()
        carga_b64 = token.split(".")[1]
        carga_b64 += "=" * (-len(carga_b64) % 4)
        datos = json.loads(base64.urlsafe_b64decode(carga_b64))
        return float(datos["exp"]) if "exp" in datos else None
    except Exception:
        return None


def _origen(url: str) -> str:
    try:
        partes = urlsplit(url)
        if partes.scheme and partes.netloc:
            return "%s://%s" % (partes.scheme, partes.netloc)
    except ValueError:
        pass
    return ""


def _normalizar_modelos(obj) -> list:
    """Tolera cualquier forma rara del lado del sitio: si algo no
    calza, esa fila (o la respuesta entera) se descarta en silencio en
    vez de romper el resto -- mismo espiritu que sitio.buscar()."""
    if not isinstance(obj, dict):
        return []
    modelos = obj.get("models")
    if not isinstance(modelos, list):
        return []
    salida = []
    for m in modelos:
        if not isinstance(m, dict):
            continue
        username = m.get("username")
        if not username:
            continue
        salida.append({
            "username": str(username),
            "id": m.get("id"),
            "avatar": _url_snapshot(m) or m.get("previewUrlThumbSmall") or "",
            "online": bool(m.get("isOnline") or m.get("isLive")),
            "estado": m.get("status") or "",
        })
    return salida


def _url_snapshot(m: dict) -> str:
    """El snapshot en vivo de verdad (lo que se ve actualizarse en el
    sitio) no es 'previewUrlThumbSmall' -- es esta URL, confirmada a
    mano comparando el id+snapshotTimestamp de un modelo real contra
    la miniatura que el propio sitio muestra para el (mismo patron
    "descubierto a mano" que el resto de sesion.py/sitio.py)."""
    id_ = m.get("id")
    snap = m.get("snapshotTimestamp")
    if not id_ or not snap:
        return ""
    return "https://img.doppiocdn.media/snapshot/%s/%s" % (id_, snap)
