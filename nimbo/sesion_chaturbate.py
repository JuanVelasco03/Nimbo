"""La cuenta real de chaturbate (SPEC 21): seguidas y el autocompletado
del buscador -- la contraparte de nimbo\\sesion.Sesion para el segundo
sitio, pero con un protocolo de sesion distinto de raiz.

Diferencia central con stripchat: alla la sesion de verdad viaja en el
header "Authorization" (un JWT), y las cookies son secundarias. Chaturbate
no manda ningun header de autorizacion -- su sesion es una cookie comun y
corriente, igual que cualquier sitio Django. De ahi
EXIGE_AUTORIZACION = False: un pegado con cookies y sin Authorization es
perfectamente valido aca (al reves que para Sesion).

Medido en vivo el 2026-11-10 (fase 0 de SPEC 21, cuenta real, via el
boton "Iniciar sesion" + inspeccion del bundle de la pagina):

  - La cookie que marca "hay sesion" es "sessionid" (la convencion Django
    de siempre) -- confirmado porque REGLAS_LOGIN de puente.py, que la usa
    como marcadora, capturo una sesion que despues verifico de verdad.
  - La lista de seguidas (y el buscador: es el MISMO endpoint) sale de
      GET https://chaturbate.com/api/ts/roomlist/room-list/
          ?follow=true&offset=<N>&limit=<N>          (seguidas)
          ?keywords=<consulta>&offset=0&limit=8      (buscador, catalogo
                                                        entero, no solo
                                                        seguidas)
    con la cabecera "X-Requested-With: XMLHttpRequest" y las cookies de
    la sesion -- encontrado en el bundle
    web2.static.mmcdn.com/cachebust/roomlist-prefetch.*.js que la propia
    pagina /followed-cams/ carga (la lista NO viene en el HTML inicial:
    ese endpoint es lo que la rellena del lado del cliente).
  - La respuesta es JSON: {"rooms": [...], "total_count": N,
    "all_rooms_count": N, ...}. "total_count" es cuantos hay en el
    filtro pedido (lo que importa para paginar); "all_rooms_count" en
    "follow=true" incluye tambien las seguidas que estan offline AHORA
    (no vienen en "rooms": este endpoint solo lista salas transmitiendo).
    Cada fila de "rooms" trae (entre otros) "username", "current_show",
    "img" (miniatura) e "is_following" (solo relevante para el buscador,
    no para "follow=true" que ya filtro por eso). NO trae ningun id
    numerico ni el uid alfanumerico de chaturbate.py -- de ahi que el
    "id" de las filas de aca abajo salga siempre None: pedirlo costaria
    un segundo pedido por modelo (a /api/chatvideocontext/), que es
    justo el gasto que esta lista evita.
  - Confirmado que offset/limit paginan de verdad (no se ignoran): dos
    pedidos con limit=2 y offset 0/2 devolvieron filas distintas.
  - "current_show" es BINARIO, a diferencia del vocabulario de stripchat
    (public/live/private/virtualprivate/p2p/p2pvoice/groupshow/ticketshow):
    sobre ~900 salas reales muestreadas (directorio general sin filtro,
    spy-on-cams, hidden-cams y las seguidas) solo aparecieron "public" y
    "private", nunca otro valor. Cruzado ademas contra
    chaturbate.consultar_estado() (el "room_status" de
    /api/chatvideocontext/, que es lo que usa el grabador) para 36 salas
    de la muestra: coincidio 100% de las veces. Chaturbate no tiene
    equivalente de p2p/group show/ticket show -- vista_modelos.js ya
    mapea "public"/"private" sin cambios (son los mismos literales que
    usa para stripchat), asi que las dos etiquetas que de verdad se ven
    en la seccion de Chaturbate son "SHOW PUBLICO" y "EN PRIVADO".

Lo que SI ya estaba confirmado desde SPEC 18 (pruebas\\sonda_chaturbate.py):
el snapshot publico de miniatura (jpeg.live.mmcdn.com) no pide cookies ni
cuenta -- _url_snapshot() de aca abajo lo reusa tal cual, y sirve de
respaldo si una fila de "rooms" viniera sin "img".
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from sesion import SesionBase, TOPE_PAGINA_S, _quitar_header

AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# Confirmado en vivo el 2026-11-10 -- ver docstring del modulo. Un solo
# endpoint para seguidas (follow=true) y para el buscador (keywords=...,
# ver sitio_chaturbate.Chaturbate.buscar).
ENDPOINT_ROOM_LIST = "https://chaturbate.com/api/ts/roomlist/room-list/"


class SesionChaturbate(SesionBase):
    SITIO = "chaturbate"
    EXIGE_AUTORIZACION = False   # cookies solas alcanzan (ver docstring)

    def _pedir_pagina(self, datos: dict, offset: int, limite: int) -> dict:
        """-> {"autenticado", "error", "modelos", "total"}. Nunca lanza.
        GET a ENDPOINT_ROOM_LIST con follow=true -- ver docstring del
        modulo para como se confirmo este contrato."""
        cookies = datos.get("cookies") or {}
        if not cookies:
            return {"autenticado": False, "error": "sesion guardada sin cookies",
                     "modelos": [], "total": None}

        cabeceras = dict(datos.get("headers") or {})
        _quitar_header(cabeceras, "Cookie")
        _quitar_header(cabeceras, "Accept-Encoding")
        cabeceras.setdefault("Accept", "*/*")
        cabeceras.setdefault("User-Agent", AGENTE)
        cabeceras["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
        cabeceras["X-Requested-With"] = "XMLHttpRequest"

        qs = urllib.parse.urlencode({"follow": "true", "offset": offset, "limit": limite})
        req = urllib.request.Request(ENDPOINT_ROOM_LIST + "?" + qs, headers=cabeceras)
        try:
            with urllib.request.urlopen(req, timeout=TOPE_PAGINA_S) as r:
                cuerpo = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return {"autenticado": False, "error": "sesion caducada",
                         "modelos": [], "total": None}
            return {"autenticado": True, "error": "http_%d" % e.code,
                     "modelos": [], "total": None}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"autenticado": True, "error": "error:%s" % type(e).__name__,
                     "modelos": [], "total": None}

        try:
            obj = json.loads(cuerpo)
        except ValueError:
            # Un 200 con HTML (no JSON) es la pinta de "redirigio al login
            # sin dar 401/403" -- se trata igual que sesion caducada.
            return {"autenticado": False, "error": "sesion caducada",
                     "modelos": [], "total": None}
        if not isinstance(obj, dict) or "rooms" not in obj:
            return {"autenticado": True, "error": "forma de respuesta inesperada",
                     "modelos": [], "total": None}

        return {"autenticado": True, "error": None,
                 "modelos": self._normalizar_modelos(obj),
                 "total": obj.get("total_count")}

    def _normalizar_modelos(self, obj) -> list:
        """obj es la respuesta de ENDPOINT_ROOM_LIST entera
        ({"rooms": [...], "total_count": ..., ...}) -- tolera cualquier
        forma rara (fila sin 'username', 'rooms' ausente) descartando en
        silencio, igual que sitio.buscar(). Cada fila presente en
        'rooms' ya esta transmitiendo (el endpoint no lista salas
        offline), asi que 'online' es siempre True aca."""
        if not isinstance(obj, dict):
            return []
        filas = obj.get("rooms")
        if not isinstance(filas, list):
            return []
        salida = []
        for m in filas:
            if not isinstance(m, dict):
                continue
            username = m.get("username")
            if not username:
                continue
            username = str(username)
            salida.append({
                "username": username,
                # Sin id numerico ni uid alfanumerico en este endpoint --
                # ver docstring del modulo. None, no "": que quede claro
                # que es un dato ausente y no una cadena vacia real.
                "id": None,
                "avatar": m.get("img") or self._url_snapshot(username),
                "online": True,
                "estado": m.get("current_show") or "",
            })
        return salida

    def _url_snapshot(self, modelo) -> str:
        """El snapshot publico y estable (sin cookies ni cuenta) -- medido
        en vivo en SPEC 18 (pruebas\\sonda_chaturbate.py), igual que
        sitio_chaturbate.Chaturbate.url_snapshot. 'modelo' puede ser el
        username pelado o un dict con 'username' -- las dos formas en que
        se llama desde _normalizar_modelos de aca arriba."""
        nombre = modelo.get("username") if isinstance(modelo, dict) else modelo
        if not nombre:
            return ""
        return "https://jpeg.live.mmcdn.com/stream?room=%s" % nombre
