# SPEC 21 — Login en chaturbate: seguidas y buscador con cuenta

> **Estado:** Aprobado
> **Depende de:** SPEC 09 (la sesión cifrada con DPAPI), SPEC 15 (login sin
> pegar: ventana de Edge ruteada por el proxy y captura desde `puente.py`),
> SPEC 18 (segundo sitio chaturbate)
> **Fecha:** 2026-11-10
> **Objetivo:** Que la sección de Chaturbate tenga su propia cuenta —
> capturada con el mismo botón "Iniciar sesión" de SPEC 15 — y que esa sesión
> traiga las modelos seguidas y, si el sitio lo expone, el autocompletado del
> buscador.

---

## Por qué

### A — la sección de Chaturbate está muerta de la cintura para arriba

SPEC 18 dejó el segundo sitio con alcance reducido a propósito, y lo escribió
en su propio "Fuera de scope": *"Login, cuenta, 'favoritos de la cuenta' o
cualquier equivalente de 'Siguiendo' en el sitio nuevo. Ninguna ruta
`/api/sesion/*` nueva."* El resultado se ve en la interfaz: el
`<details id="seccion-chaturbate">` de `nimbo\web\index.html:123` tiene un
campo de texto con el placeholder *"sin autocompletado"* y nada más — sin
barra de sesión, sin botón de login, sin sugerencias.

Eso cuesta dos cosas concretas:

1. **Hay que saber el nombre exacto de memoria.** `Chaturbate.buscar()`
   (`nimbo\sitio_chaturbate.py:76`) devuelve `[]` siempre, y su docstring dice
   por qué: *"a diferencia de `/api/front/v4/models/search/suggestion` de
   stripchat, chaturbate no expone (medido: no se encontró ninguno sin cuenta)
   un endpoint de búsqueda en vivo que junte nombre + estado online"*. La
   medición es de un cliente **anónimo**; con cuenta la respuesta puede ser
   otra, y nadie la midió.
2. **Cada favorita de chaturbate gasta un pedido saliente propio.**
   `servidor.py:776` lo deja escrito: *"`con_dato` solo cubre stripchat
   ('Siguiendo'/supervisor no existen para chaturbate, fuera de alcance): una
   favorita de chaturbate SIEMPRE pide su propio estado"*. En stripchat, la
   lista de seguidas trae nombre + estado online de todas de una sola vez y el
   servidor se ahorra ese pedido por modelo. Chaturbate no tiene de dónde.

### B — el portón de captura de `puente.py` es de stripchat, no genérico

El hook de captura (`nimbo\puente.py:387`) tiene cuatro condiciones y **tres
son del sitio**:

```python
if not host.endswith("stripchat.com"): return
ruta = flow.request.path.split("?", 1)[0]
if not ruta.startswith("/api/front/"): return
autorizacion = flow.request.headers.get("Authorization")
if not autorizacion: return
```

La tercera es la que importa. SPEC 15 la justificó así: *"es la señal de 'hay
sesión' y lo que `_exp_de_jwt` necesita; un pedido anónimo a `/api/front/`
tiene cookies pero no eso"*. Pero eso es verdad **de stripchat**: el
`Authorization` es un JWT que el sitio guarda en `localStorage`. Chaturbate no
manda ningún header de autorización — su sesión es una cookie, igual que la de
cualquier sitio Django. Con el portón actual, abrir la ventana de login contra
chaturbate no capturaría **nunca**, sin un solo error en el log: el hook
saldría en el primer `if`.

Dicho de otra forma: la maquinaria de SPEC 15 ya existe entera y funciona, y lo
único que la ata a un sitio son esas tres líneas más la URL que abre la ventana
(`login_sitio.py:88`, `ajustes["sitio"] + "/favorites"`). El resto —el permiso
con vencimiento, el cifrado DPAPI del candidato, la verificación con un pedido
real antes de guardar, la purga del historial del perfil, el tope de 5
minutos— es agnóstico del sitio y está probado en vivo.

### C — por qué la fase 0 es un paso y no un supuesto

De chaturbate no se sabe todavía: qué cookie marca "hay sesión", si las
seguidas salen por un JSON o solo por HTML, cuál es su contrato de paginación,
si hay buscador con cuenta, ni si mitmproxy puede interceptar
`chaturbate.com` sin que Cloudflare corte. SPEC 18 ya puso el precedente
(*"la fase 0 (sonda) es un paso del plan, no un supuesto"*) y SPEC 03 el
motivo: una premisa escrita en el código que nunca se verificó contra el sitio
real. Acá vale igual.

---

## Scope

### Entra

**Fase 0 — la sonda**

- **`pruebas\sonda_login_chaturbate.py`**, calco de
  `pruebas\sonda_chaturbate.py` y `pruebas\sonda_directo.py`: stdlib puro, fase
  por fase, aborta en la primera que falla, `--fase 0` sin red como canario,
  escribe solo bajo `--salida`. Toma la sesión como entrada
  (`--curl <archivo>` con un "Copiar como cURL/PowerShell" de una pestaña ya
  logueada), porque una sonda no se puede loguear sola. Mide y deja impreso:
  1. **Qué cookie marca "hay sesión".** Repite el pedido autenticado quitando
     una cookie por vez y reporta el subconjunto mínimo que sigue
     respondiendo como usuario. Esa es la cookie que va a la tabla de reglas
     de `puente.py`.
  2. **Si hace falta algo más que cookies**: token CSRF, `Referer`,
     `X-Requested-With`.
  3. **De dónde salen las seguidas**: si hay un endpoint que devuelva JSON, o
     si solo está el HTML de la página de seguidas. En los dos casos reporta
     el contrato de paginación (offset/limit, página, o scroll infinito) y los
     nombres de campo de username, estado online y miniatura.
  4. **Si hay buscador con cuenta** que junte nombre + estado online en una
     respuesta, equivalente al `suggestion` de stripchat.
  5. **Si mitmproxy puede interceptar `chaturbate.com`**: el mismo pedido
     saliendo por `127.0.0.1:8080` con la CA de mitmproxy instalada, contra un
     pedido directo. Si acá aparece un desafío de Cloudflare, el camino del
     sniffing no sirve y el pegado de cURL pasa a ser el único.
  6. **Solo informativo, para un spec futuro**: si
     `/api/chatvideocontext/<nombre>/` responde distinto con cookies. No se
     usa en este spec; se deja el dato escrito.
- **Es punto de decisión.** Su resultado confirma o recorta lo que sigue: el
  punto 4 define si el autocompletado entra, y el punto 5 si el botón "Iniciar
  sesión" entra o si chaturbate se queda solo con "otra forma".

**La base de sesión compartida**

- **`nimbo\sesion.py` — extraer `SesionBase`**, mismo criterio y mismo patrón
  que SPEC 18 usó con `sitio.SitioBase` (`sitio.py:128`). A la base va todo lo
  que ya es agnóstico del sitio y está probado en vivo:
  - el envoltorio DPAPI (`PREFIJO_DPAPI`, `_proteger`, `_desproteger`) y la
    tabla de tres casos de `leer()` con su migración silenciosa del texto
    plano (SPEC 09);
  - `_escribir()` atómico, `borrar()`, `estado()`, `self._lock`;
  - `guardar_desde_curl()`, `guardar_desde_captura()`, `verificar()`;
  - el bucle de paginación de `favoritas_en_linea()` con `TOPE_PAGINAS`,
    `TOPE_PAGINA_S`, `TOPE_TOTAL_S` y el dedupe por `username.lower()`;
  - `actualizar_siguiendo(ruta_salida)`;
  - los parsers de pegado completos (`_parsear_pegado`, `_parsear_curl_bash`,
    `_parsear_powershell`, `_desescapar_ps`, `_buscar_header`,
    `_quitar_header`, `_agregar_cookies`, `_exp_de_jwt`, `_origen`).
- **Cinco ganchos** es todo lo que define una subclase: `SITIO` (el string que
  va en las filas y en el handshake), `EXIGE_AUTORIZACION` (si un pegado sin
  header de autorización se rechaza o no), `_pedir_pagina()`,
  `_normalizar_modelos()` y `_url_snapshot()`.
- **`nimbo\sesion.py:Sesion(SesionBase)`** queda con lo de stripchat y **nada
  más**: `/api/front/models/favorites` con `sortBy=bestMatch&mlfv=false`, la
  forma `models`/`totalCount`, los nombres de campo de
  `_normalizar_modelos()`, el snapshot de `img.doppiocdn.media`, y
  `EXIGE_AUTORIZACION = True`. Por fuera no cambia ni una firma: `servidor.py`
  la sigue usando igual.
- **`nimbo\sesion_chaturbate.py` (nuevo) — `SesionChaturbate(SesionBase)`**:
  los cinco ganchos con lo que midió la fase 0, `EXIGE_AUTORIZACION = False`
  (cookies solas alcanzan) y `estado()["expira"] = None` siempre, porque no hay
  JWT de dónde sacar un vencimiento.

**La captura**

- **`nimbo\puente.py` — reglas por sitio en el mismo hook `request`.** Una
  tabla de datos (`REGLAS_LOGIN`) con una entrada por sitio: sufijo de host,
  prefijo de path, header marcador, cookie marcadora. El hook **lee primero el
  permiso** y recién con su campo `sitio` elige la única regla a evaluar. Eso
  deja el camino caliente del detector (doppiocdn) saliendo en un acceso a
  atributo, sin tocar `pretty_host` — más barato que hoy, no más caro.
- El candidato que escribe `_escribir_captura()` suma el campo `sitio`. La
  línea de log sigue sin llevar ningún dato: `"[LOGIN] sesion capturada
  (<sitio>)"` y nada más.

**La orquestación**

- **`nimbo\login_sitio.py` — parámetro `sitio` en las cinco funciones.**
  `precondiciones()` no cambia de criterio (motor vivo, CA instalada, Edge
  encontrado). `pedir()` escribe el `sitio` en `captura_sesion.json`.
  `abrir_ventana()` saca la URL de una tabla por sitio: stripchat sigue en
  `ajustes["sitio"] + "/favorites"`, chaturbate va a
  `ajustes["sitio_chaturbate"]` + la página de seguidas que midió la fase 0.
  `recoger()` devuelve `None` si el candidato en disco es de otro sitio que el
  pedido — un archivo viejo del otro sitio nunca se verifica contra la cuenta
  equivocada. `cerrar_ventana()` y `limpiar()` no cambian.
- **Un solo `perfil_login\`** y un solo login a la vez. Lo garantiza el nombre
  de tarea compartido `sesion_login` con el dedupe que ya tiene
  `_lanzar_tarea_coro` (`servidor.py:1482`): un POST a `/iniciar` mientras
  corre el otro contesta `ya_corriendo`, igual que hoy contesta ante dos clicks
  seguidos.

**El servidor**

- **`nimbo\servidor.py`**: `app.sesion_chaturbate`, `_sesion_de(app, sitio)`
  (calco de `_sitio_de`, `:765`), dos secciones de estado nuevas
  (`sesion_chaturbate`, `siguiendo_chaturbate`) publicadas y diffeadas en
  `_bucle_estado` igual que las dos de hoy, `app.login` con campo `sitio`, y
  `_tarea_login(app, sitio)` parametrizada.
- Las cinco rutas `/api/sesion/*` aceptan `sitio` en el cuerpo, con default
  `"stripchat"` — misma convención que ya usan `/api/lista/*` y
  `/api/favoritas/*` (`servidor.py:1504-1527`), para que un cliente viejo siga
  funcionando idéntico.
- `sesion_actualizar` se desdobla por sitio (`sesion_actualizar` /
  `sesion_actualizar_chaturbate`) con su entrada en `TOPE_TAREA`: refrescar una
  lista no puede quedar deduplicada contra la otra. `sesion_login` queda
  **compartido** a propósito (ver más arriba).
- **`_nombres_sin_estado()`** suma los nombres de `siguiendo_chaturbate.json` a
  `con_dato`, y el comentario de `:776` se corrige. Es el ahorro de un pedido
  saliente por favorita de chaturbate en cada vuelta de `_bucle_sitio`.

**El catálogo**

- **`nimbo\sitio_chaturbate.py`**: `buscar()` deja de devolver `[]` y pasa a
  usar el endpoint que midió la fase 0, con las cookies de
  `sesion_chaturbate.json`, reusando la cache de búsqueda con TTL que
  `SitioBase` ya tiene. Si no hay sesión guardada, sigue devolviendo `[]` —
  el mismo "se degrada a nada" de hoy.
- **`estado()` y `url_snapshot()` siguen anónimos.** Hoy funcionan sin cuenta y
  delegan en `nucleo\scripts\chaturbate.py`, que es núcleo compartido con el
  grabador; mandarles cookies obligaría a tocarlo.

**La interfaz**

- **`nimbo\web\index.html`**: una segunda barra de sesión y su panel de pegado
  dentro del `<details id="seccion-chaturbate">`, con sufijo `-cb` en todos los
  ids — la convención que esa sección ya usa (`#nombre-nuevo-cb`,
  `#boton-agregar-favorita-cb`). Más `#sugerencias-cb`, solo si la fase 0
  encontró buscador.
- **`nimbo\web\js\vista_modelos.js`**: `renderSesion`, `pintarLogin`,
  `iniciarLogin` e `iniciarPanelSesion` pasan a recorrer una tabla de ids por
  sitio en vez de tener los ids clavados. **Estrictamente in place**:
  `textContent` y clases, nunca `innerHTML` — la regla de CLAUDE.md que evita
  el congelado de SPEC 01.
- `fusionarChaturbate()` (`:133`) deja de clavar `siguiendo: false` y mezcla
  `estado.siguiendo_chaturbate.modelos` con el mismo criterio que
  `fusionarFavoritas()`; `FILTROS_CHATURBATE` (`:296`) deja de sacar el filtro
  `siguiendo`; la etiqueta `SIGUIENDO` deja de esconderse en las filas de
  chaturbate.
- **`nimbo\web\js\main.js`**: `"sesion_chaturbate"` y `"siguiendo_chaturbate"`
  entran en las claves que repintan `renderSesion` (`:79`), y los cinco botones
  se enganchan por sección mandando su `sitio`.

**Ajustes, empaquetado y documentación**

- **`nimbo\ajustes.py`**: `ESTADO_SESION_CHATURBATE` y
  `ESTADO_SIGUIENDO_CHATURBATE` con sus properties, al lado de las que ya
  existen. Ninguna clave nueva en `DEFECTOS` — este spec no agrega
  configuración.
- **`empaquetado\construir.py`** (`_ARBOL`) y **`empaquetado\compilar.py`**:
  `nimbo\sesion_chaturbate.py` es un módulo de `nimbo\`, así que se compila y
  **no** va a `_PY_EN_DISCO_COMPILADO`. `pruebas\` no se empaqueta.
- **`CLAUDE.md`**: el párrafo de `sesion.py` en "Detalles con historia", y las
  filas de `estado\sesion_chaturbate.json` y `estado\siguiendo_chaturbate.json`
  en "Quién escribe qué", más la nota de que los dos archivos de vida corta del
  handshake ahora llevan `sitio`.

### No entra

- **Usuario y contraseña dentro de Nimbo.** SPEC 15 lo descartó por captcha,
  Turnstile y 2FA no verificados, y porque guardaría un secreto más grande y
  más duradero. El motivo vale igual acá; la decisión no se reabre.
- **Renovación automática de la sesión al vencer.** Mismo criterio de SPEC 15:
  *"abrir una ventana sola en medio de la noche es justamente lo que no se
  quiere"*.
- **Usar la sesión en el grabador.** `nucleo\scripts\chaturbate.py` y
  `nucleo\scripts\grabador_chaturbate.py` no se tocan: hoy no tienen ninguna
  noción de cookies y siguen grabando anónimos. Consecuencia directa: **una
  modelo que exija cuenta para ver el stream sigue sin poder grabarse**. Es un
  spec aparte, y además tiene que decidir cómo le llega una credencial a un
  proceso que hoy solo recibe variables de entorno (`motor.py:590`).
- **Usar la sesión en `estado()` o en el snapshot.** Funcionan sin cuenta hoy;
  cambiarlos obligaría a tocar el núcleo compartido con el grabador.
- **Dos logins en paralelo.** Un solo `perfil_login\` y un solo nombre de tarea
  `sesion_login`: el segundo POST contesta `ya_corriendo`.
- **VPN/túnel para chaturbate.** Sigue fuera de alcance desde SPEC 18: su
  tráfico de grabación no pasa por `GRAB_PROXY`.
- **Cualquier otra cosa de la cuenta**: tokens, propinas, chat, historial de
  salas visitadas.
- **Unificar la ubicación de los archivos de sesión.** `sesion.json` de
  stripchat vive en la raíz y el de chaturbate va a `estado\`. La asimetría es
  deliberada: migrar el de stripchat es un cambio de ruta en producción que no
  compra nada en este spec.
- **Un tercer sitio.** `SesionBase` se extrae contra dos casos reales, no
  contra una API de plugins diseñada a priori — mismo criterio que SPEC 18.

---

## Modelo de datos

### `estado\sesion_chaturbate.json` (nuevo)

Mismo envoltorio DPAPI que `sesion.json` (SPEC 09) y mismo esquema interno,
porque la base es la misma. En disco:

```
NIMBO-DPAPI-1\n<blob de CryptProtectData, base64>
```

En claro, adentro:

```jsonc
{
  "cookies": {"<la marcadora de la fase 0>": "...", "...": "..."},
  "headers": {"User-Agent": "...", "...": "..."},
  "url_origen": "https://chaturbate.com/...",  // de dónde salió la captura
  "guardado": 1794800000.0,
  "expira": null   // chaturbate no manda JWT: no hay vencimiento local
}
```

`expira: null` es la única diferencia de forma con stripchat, y arrastra una
consecuencia: `estado()["activa"]` de chaturbate significa **"hay credencial
guardada"**, no "la credencial sirve". La única señal real es el campo
`autenticado` de `siguiendo_chaturbate.json`, y `renderSesion` ya le da
prioridad a ese campo sobre `sesion.activa` (`vista_modelos.js:739-783`).

### `estado\siguiendo_chaturbate.json` (nuevo)

Lo escribe `SesionChaturbate.actualizar_siguiendo()`, único escritor, atómico.
Mismo nivel raíz que `siguiendo.json`:

```jsonc
{
  "generado": 1794800123.0,
  "autenticado": true,
  "error": null,
  "total": 37,
  "modelos": [
    {"username": "alguna_modelo", "id": "H349Y5V", "avatar": "https://...",
     "online": true, "estado": "public"}
  ]
}
```

Las claves de fila son las mismas cinco que ya produce
`_normalizar_modelos()` para stripchat, para que `fusionarChaturbate()` las
consuma con el mismo código que `fusionarFavoritas()`. El `id` es el token
alfanumérico de 7 caracteres de chaturbate, no un número — igual que el que ya
acepta `grabaciones.RE_NOMBRE` desde SPEC 18.

### `estado\captura_sesion.json` — campo nuevo

Lo escribe Nimbo (`login_sitio.pedir`), lo lee `puente.py`:

```jsonc
{"pedida": 1794800000.0, "vence": 1794800300.0, "sitio": "chaturbate"}
```

Sin `sitio`, se asume `"stripchat"` — un archivo de antes de este spec sigue
funcionando.

### `estado\sesion_capturada.json` — campo nuevo

Lo escribe `puente.py`, lo lee y lo borra Nimbo. Cifrado con DPAPI. En claro
adentro, igual que hoy más el `sitio`:

```jsonc
{
  "capturado": 1794800042.0,
  "sitio": "chaturbate",
  "url": "https://chaturbate.com/...",
  "cookies": {"...": "..."},
  "headers": {"...": "..."}
}
```

`login_sitio.recoger(ajustes, sitio)` devuelve `None` si el `sitio` del archivo
no es el pedido.

### `app.login` — campo nuevo

```jsonc
{"fase": "esperando", "vence": 1794800300.0, "motivo": null, "sitio": "chaturbate"}
```

Las siete fases no cambian (`inactiva`, `imposible`, `esperando`,
`verificando`, `ok`, `cancelada`, `vencida`). El `sitio` es lo que le dice a la
interfaz **cuál de las dos barras** muestra la cuenta regresiva.

### Las reglas de captura de `puente.py`

Tabla de datos, no código: una entrada por sitio, con el marcador de "hay
sesión" que corresponda a cada uno.

```python
REGLAS_LOGIN = {
    # stripchat: el JWT viaja en el header, no en una cookie (SPEC 15).
    "stripchat": {"host": "stripchat.com", "path": "/api/front/",
                  "header": "Authorization", "cookie": ""},
    # chaturbate: sesion de cookie, sin header de autorizacion.
    # La cookie exacta la fija la fase 0 de este spec.
    "chaturbate": {"host": "chaturbate.com", "path": "/",
                   "header": "", "cookie": "<fase 0>"},
}
```

### Los cinco ganchos de `SesionBase`

```python
SITIO = "stripchat"            # va en las filas y en el handshake
EXIGE_AUTORIZACION = True      # un pegado sin header de auth se rechaza

def _pedir_pagina(self, datos, offset, limite) -> dict: ...
    # -> {"autenticado", "error", "modelos", "total"}, nunca lanza
def _normalizar_modelos(self, obj) -> list: ...
def _url_snapshot(self, modelo) -> str: ...
```

Todo lo demás de `SesionBase` es compartido. No hay ninguna clave nueva en
`config.json`.

---

## Plan de implementación

Cada paso deja el sistema funcionando. El 1 es la fase 0 y es punto de
decisión; 2 a 4 son la base compartida (y el 3 no agrega nada de chaturbate: su
puerta es que stripchat siga igual); 5 a 7 la captura y la orquestación; 8 a 10
el servidor y el catálogo; 11 a 13 la interfaz; 14 y 15 empaquetado y
documentación.

1. **`pruebas\sonda_login_chaturbate.py` — la sonda, y correrla.** Los seis
   puntos de la fase 0 del scope, con `--fase 0` sin red como canario y
   `--curl <archivo>` para la sesión de entrada.
   **Puerta:** queda impreso, fase por fase, la cookie marcadora mínima, de
   dónde salen las seguidas con su contrato de paginación, si hay buscador con
   cuenta, y si el mismo pedido sobrevive saliendo por `127.0.0.1:8080`. Si el
   punto 5 falla, el resto del spec se recorta al pegado de cURL y se anota
   acá la medición.
2. **`nimbo\sesion.py` — mover a `SesionBase` todo lo agnóstico.** Las nueve
   piezas de la lista del scope, sin cambiar ninguna firma pública.
   **Puerta:** `nimbo\sesion.py` importa sin error y `Sesion` expone las mismas
   diez funciones públicas de antes.
3. **`nimbo\sesion.py` — `Sesion(SesionBase)` con los cinco ganchos.** Lo de
   stripchat y nada más.
   **Puerta:** con una `sesion.json` real, "Siguiendo" de stripchat lista las
   mismas modelos que antes del paso 2, y "Actualizar" las refresca. Este paso
   **no agrega nada de chaturbate** a propósito: si rompió algo, se ve acá.
4. **`nimbo\sesion_chaturbate.py` — `SesionChaturbate(SesionBase)`** con lo que
   midió la fase 0.
   **Puerta:** un script suelto que le pasa un pegado de cURL y llama
   `verificar()` + `favoritas_en_linea()` imprime la lista de seguidas, sin
   tocar `estado\` ni `grabar.txt`.
5. **`nimbo\ajustes.py` — `ESTADO_SESION_CHATURBATE` y
   `ESTADO_SIGUIENDO_CHATURBATE`** con sus properties.
   **Puerta:** Nimbo arranca y las dos properties devuelven rutas bajo
   `estado\`. `config.json` no cambia.
6. **`nimbo\puente.py` — `REGLAS_LOGIN` y el hook reordenado.** El permiso se
   lee primero, su `sitio` elige la regla, y recién después se mira host, path
   y marcador. `_escribir_captura()` suma el campo `sitio`.
   **Puerta:** navegar por el proxy **sin** haber apretado ningún botón no crea
   `sesion_capturada.json`; el login de stripchat sigue capturando igual que
   antes de este spec.
7. **`nimbo\login_sitio.py` — parámetro `sitio` en las cinco funciones**, con
   la tabla de URL por sitio y el filtro de `recoger()`.
   **Puerta:** `pedir(..., sitio="chaturbate")` deja un `captura_sesion.json`
   con ese campo, y `abrir_ventana(..., sitio="chaturbate")` abre Edge en la
   página de seguidas de chaturbate, ruteado por el proxy.
8. **`nimbo\servidor.py` — `app.sesion_chaturbate`, `_sesion_de()`, las dos
   secciones de estado y `_tarea_login(app, sitio)`.**
   **Puerta:** `GET /api/estado` trae `sesion_chaturbate` y
   `siguiendo_chaturbate`, y el login completo de chaturbate termina con fase
   `ok` y `estado\siguiendo_chaturbate.json` escrito.
9. **`nimbo\servidor.py` — `sitio` en las cinco rutas `/api/sesion/*`** y el
   desdoble de `sesion_actualizar` con su `TOPE_TAREA`.
   **Puerta:** un POST a `/api/sesion/actualizar` **sin** `sitio` hace
   exactamente lo de antes de este spec, y los dos sitios se pueden refrescar a
   la vez sin que uno conteste `ya_corriendo` por culpa del otro.
10. **`nimbo\servidor.py` — `_nombres_sin_estado()` cuenta
    `siguiendo_chaturbate`**, y el comentario de `:776` corregido.
    **Puerta:** con N favoritas de chaturbate seguidas en la cuenta,
    `_bucle_sitio` deja de pedir sus N estados individuales (verificable en
    `eventos_motor.jsonl` por la caída de pedidos salientes).
11. **`nimbo\sitio_chaturbate.py` — `buscar()` con cookies**, solo si la fase 0
    encontró endpoint. Sin sesión guardada devuelve `[]`.
    **Puerta:** `GET /api/sitio/buscar?q=xx&sitio=chaturbate` devuelve el mismo
    shape que la versión de stripchat, y sin sesión devuelve `[]` sin error.
12. **`nimbo\web\index.html` — segunda barra de sesión y panel de pegado**
    con sufijo `-cb`, más `#sugerencias-cb` si corresponde.
    **Puerta:** la sección de Chaturbate muestra la barra; la de Stripchat no
    cambió en nada.
13. **`nimbo\web\js\vista_modelos.js` + `main.js` — tabla de ids por sitio**,
    `fusionarChaturbate` mezclando las seguidas, `FILTROS_CHATURBATE` con el
    filtro `siguiendo` de vuelta, y la etiqueta `SIGUIENDO` visible en
    chaturbate.
    **Puerta:** las dos barras funcionan a la vez; el repintado de la grilla no
    vuelve a pedir ninguna miniatura (la regla in place de SPEC 01, verificable
    en la pestaña Red del navegador).
14. **`empaquetado\construir.py` (`_ARBOL`) y `empaquetado\compilar.py`.**
    **Puerta:** `python empaquetado\construir.py --descargar` deja
    `nimbo\sesion_chaturbate.py` adentro y `auditar()` no aborta; con build
    compilado, ese `.py` **no** quedó en disco.
15. **`CLAUDE.md`.**
    **Puerta:** lectura humana, sin comandos.

---

## Criterios de aceptación

**La base compartida**

- [ ] Tras extraer `SesionBase`, "Siguiendo" de stripchat lista las mismas
      modelos que antes y "Actualizar" las refresca.
- [ ] Una `sesion.json` en texto plano de antes de SPEC 09 se sigue leyendo y
      se reescribe cifrada, igual que hoy.
- [ ] Un pegado de "Copiar como cURL (bash)" y uno de "Copiar como PowerShell"
      siguen funcionando para stripchat, en los dos formatos.

**El login de chaturbate**

- [ ] Apretar "Iniciar sesión" en la sección de Chaturbate abre una ventana de
      Edge en la página de seguidas de chaturbate, y completar el login a mano
      deja `estado\sesion_chaturbate.json` escrito, sin abrir las devtools en
      ningún momento.
- [ ] Ese archivo, abierto con el Notepad, arranca con `NIMBO-DPAPI-1` y no
      muestra ninguna cookie.
- [ ] `estado\sesion_capturada.json` no existe cuando el login terminó, y
      mientras existió tampoco estaba en claro.
- [ ] `estado\captura_sesion.json` no existe tras éxito, cancelación ni
      vencimiento.
- [ ] Navegar por el proxy **sin** haber apretado ningún botón de login no crea
      `sesion_capturada.json`.
- [ ] Un candidato que no verifica se descarta y el bucle sigue esperando: una
      `sesion_chaturbate.json` que funcionaba no se pisa.
- [ ] Un login de chaturbate no toca `sesion.json` de stripchat, y al revés.
- [ ] Con un login de chaturbate en curso, apretar "Iniciar sesión" en la
      sección de Stripchat contesta `ya_corriendo` y **no** abre una segunda
      ventana.
- [ ] A los 5 minutos sin completar, la fase queda `vencida` y la cuenta
      regresiva se ve en la barra de **chaturbate**, no en la de stripchat.
- [ ] "Cancelar" deja fase `cancelada` y cierra la ventana.
- [ ] Los tres motivos de botón deshabilitado (motor parado, CA sin instalar,
      Edge ausente) se ven con su texto en la barra de chaturbate.
- [ ] `eventos_detector.jsonl` y `eventos_motor.jsonl` no tienen ninguna
      cookie, ningún header de autorización ni ninguna URL con query de la
      captura.

**Las seguidas y el catálogo**

- [ ] Tras el login, las modelos seguidas en chaturbate aparecen en la sección
      de Chaturbate con la etiqueta `SIGUIENDO`, y el filtro "Siguiendo" de esa
      sección las filtra.
- [ ] Una modelo seguida en chaturbate y una seguida en stripchat **con el
      mismo nombre** aparecen como dos tarjetas distintas, cada una en su
      sección.
- [ ] Con N favoritas de chaturbate que están en la lista de seguidas,
      `_bucle_sitio` deja de pedir sus N estados individuales.
- [ ] (Solo si la fase 0 encontró buscador) Escribir en el campo de Chaturbate
      muestra sugerencias con nombre y estado online; sin sesión guardada no
      muestra ninguna y no tira ningún error en consola.
- [ ] (Solo si la fase 0 **no** encontró buscador) El spec deja escrita la
      medición y el campo de Chaturbate sigue siendo texto simple.

**Compatibilidad**

- [ ] Un POST a cualquiera de las cinco rutas `/api/sesion/*` **sin** el campo
      `sitio` hace exactamente lo que hacía antes de este spec.
- [ ] `python nimbo\servidor.py` arranca sin traceback con y sin
      `sesion_chaturbate.json` presente.
- [ ] Con `chaturbate_activo` apagado, la barra de sesión de chaturbate sigue
      visible y el login sigue funcionando: la cuenta es de catálogo, no de
      grabación.
- [ ] Repintar la grilla de Modelos no vuelve a pedir ninguna miniatura.
- [ ] `construir.py --descargar` deja `nimbo\sesion_chaturbate.py` adentro y
      `auditar()` no aborta; con `--compilado`, ese `.py` no quedó en disco.

---

## Decisiones

- **Sí:** la fase 0 (sonda) es un paso del plan, no un supuesto. De chaturbate
  no se sabe qué cookie marca la sesión, de dónde salen las seguidas, ni si
  mitmproxy lo puede interceptar; SPEC 18 y SPEC 03 ya pusieron el precedente
  de medir antes de prometer.
- **Sí:** reusar entera la maquinaria de SPEC 15 (ventana de Edge sin CDP +
  captura desde el proxy). Está probada en vivo y lo único que la ataba a un
  sitio son tres líneas del hook y una URL.
- **No:** Playwright ni CDP en ninguna forma. `sesion.py:8-15` lo documenta
  probado tres veces y cortado por Cloudflare. **Esta decisión no se reabre.**
- **No:** usuario y contraseña dentro de Nimbo. Mismo motivo que SPEC 15:
  captcha/Turnstile/2FA no verificados, y guardaría un secreto más grande y más
  duradero que una cookie de sesión.
- **Sí:** archivo aparte (`estado\sesion_chaturbate.json`) en vez de convertir
  `sesion.json` en un dict por sitio. Un error en el formato nuevo no puede
  dejar sin sesión a stripchat, que es el sitio que graba 24/7; y `_escribir()`
  sigue siendo una escritura atómica de un archivo, sin lógica de merge.
- **Sí:** extraer `SesionBase` en vez de portar `sesion.py`. Es el mismo
  movimiento que SPEC 18 hizo con `sitio.SitioBase`, y acá —a diferencia de
  `grabador_directo.py`— lo que se generaliza **no corre en un proceso que
  graba**: es código de la interfaz, con un camino de verificación a mano
  (paso 3) que confirma que stripchat sigue igual antes de agregar nada nuevo.
- **No:** portar `sesion.py` a un `sesion_chaturbate.py` duplicado. Serían
  ~250 líneas de parsers de pegado y DPAPI duplicadas, sin ninguno de los
  motivos que justifican el porte de `grabador_directo.py`.
- **Sí:** reglas por sitio en el mismo hook `request` de `puente.py`, como
  tabla de datos. Un segundo hook duplicaría el permiso, el
  `_capturado_en_esta_vuelta` y la escritura del candidato para cambiar tres
  condiciones.
- **Sí:** el hook lee el permiso **antes** de mirar host y path. Es más barato
  que hoy (un acceso a atributo cacheado contra `pretty_host`), y sin permiso
  —el caso normal, el 100% del tiempo— el camino caliente del detector sale en
  la primera línea.
- **Sí:** campo `sitio` en los dos archivos de vida corta del handshake, en vez
  de dos archivos nuevos por sitio. Hay un solo login a la vez por
  construcción (un `perfil_login\`, un nombre de tarea `sesion_login`), y
  `recoger()` filtra por `sitio` como red contra un archivo viejo del otro
  sitio.
- **Sí:** un solo `perfil_login\` compartido. Edge no abre dos procesos contra
  el mismo `--user-data-dir`, y el dedupe de `sesion_login` ya garantiza que no
  haga falta: el segundo click contesta `ya_corriendo`, igual que hoy ante dos
  clicks seguidos.
- **Sí:** `sesion_actualizar` desdoblado por sitio. Es la única tarea que los
  dos sitios pueden querer a la vez, y compartir el nombre haría que refrescar
  una lista bloqueara la otra sin motivo.
- **Sí:** conservar el pegado de cURL/PowerShell también en chaturbate, detrás
  de "otra forma". Los parsers son 100% agnósticos del sitio y van a la base de
  todos modos, así que el costo es un textarea y un botón — y es la **única**
  salida si Cloudflare corta el certificado de mitmproxy, que es el riesgo nº1
  de SPEC 15.
- **Sí:** las cookies se usan solo en las seguidas y en el buscador.
  `estado()` y el snapshot funcionan sin cuenta y delegan en
  `nucleo\scripts\chaturbate.py`, que es núcleo compartido con el grabador:
  mandarles cookies obligaría a tocarlo, y eso contradice la decisión de diseño
  central del repo (el motor de captura no se toca, se lo mira desde afuera).
- **No:** pasarle la sesión al grabador. Hoy `grabador_chaturbate.py` recibe
  configuración **solo** por variables de entorno (`motor.py:590`), y una
  cookie de sesión no viaja por ahí sin decidir primero cómo. Va en su propio
  spec, junto con el caso real que lo justificaría (una modelo que exija cuenta
  para ver el stream).
- **Sí:** `estado()["expira"] = None` para chaturbate, y la verdad sobre si la
  sesión sirve sale de `autenticado` en `siguiendo_chaturbate.json`. No hay JWT
  de dónde sacar un vencimiento local, y `renderSesion` ya le da prioridad a
  ese campo.
- **Sí:** sufijo `-cb` en los ids de la segunda barra. Es la convención que la
  sección de Chaturbate ya usa (`#nombre-nuevo-cb`,
  `#boton-agregar-favorita-cb`), y los `render*` siguen escribiendo solo
  `textContent` y clases.
- **No:** mover `sesion.json` de la raíz a `estado\` para que los dos archivos
  queden juntos. Es un cambio de ruta en producción que no compra nada en este
  spec; la asimetría queda documentada.
- **No:** un tercer sitio. `SesionBase` se extrae contra dos casos reales, igual
  que `SitioBase` en SPEC 18.

---

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Cloudflare detecta el certificado de mitmproxy en `chaturbate.com` y corta el login | El punto 5 de la fase 0 lo mide **antes** de escribir una línea de producción; si corta, el botón "Iniciar sesión" sale del scope con la medición escrita y el pegado de cURL queda como único camino (por eso se conserva) |
| La cookie marcadora que fija la fase 0 cambia de nombre | Es un dato en la tabla `REGLAS_LOGIN` de `puente.py`, una línea; y la verificación con un pedido real antes de guardar nunca pisa una sesión que funciona: el síntoma es "no captura", no "se rompió la que había" |
| Chaturbate no expone las seguidas por JSON y hay que leer el HTML | Es el camino que el repo ya usa en otros dos lugares (`sitio.py` reusando `supervisor.RE_ESTADO`, y los regex de atributos de `nucleo\scripts\chaturbate.py`); si el HTML cambia, `favoritas_en_linea()` devuelve `autenticado: false` y la barra lo dice, sin romper nada más |
| Extraer `SesionBase` rompe el camino de stripchat, que hoy funciona | La extracción son los pasos 2 y 3, **sin nada de chaturbate adentro**, y la puerta del paso 3 es que "Siguiendo" de stripchat liste y refresque igual. Nada de chaturbate se agrega antes de pasar esa puerta |
| Una cookie o un header de autorización termina en un log | Criterio de aceptación explícito sobre los dos `.jsonl`, y la línea de log de `puente.py` sigue sin llevar dato (solo el nombre del sitio, que no es secreto) |
| Dos barras de sesión con ids globales se pisan entre sí | Sufijo `-cb` y una tabla de ids por sitio en `vista_modelos.js`; `app.login` lleva `sitio` para que la cuenta regresiva aparezca en la barra correcta |
| El usuario espera que el login le permita **grabar** modelos que exigen cuenta | Está explícito en "No entra" y en las Decisiones: el grabador no recibe la sesión en este spec. La cuenta es de catálogo |
| `nimbo\sesion_chaturbate.py` se olvida en `_ARBOL` de `construir.py` | La copia es por lista blanca y `auditar()` no aborta por lo que **falta**: el paso 14 tiene puerta explícita sobre el paquete, con y sin `--compilado` |

---

## Lo que **no** está en este spec

- Usuario y contraseña dentro de Nimbo.
- Renovación automática de la sesión al vencer.
- Usar la sesión en el grabador, y por lo tanto grabar modelos que exijan
  cuenta para ver el stream.
- Usar la sesión en `estado()` o en el snapshot de chaturbate.
- Dos logins en paralelo.
- VPN/túnel para chaturbate.
- Tokens, propinas, chat o cualquier otra cosa de la cuenta.
- Mover `sesion.json` de la raíz a `estado\`.
- Un tercer sitio.

Cada una, si entra, va en su propio spec.
