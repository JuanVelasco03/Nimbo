# SPEC 15 — Iniciar sesión sin pegar nada

> **Estado:** Implementado
> **Depende de:** SPEC 09 (sesion.json cifrado con DPAPI), SPEC 01 (deltas de estado por WebSocket)
> **Fecha:** 2026-11-02
> **Objetivo:** Que iniciar sesión en la cuenta real sea un botón que abre una ventana de Edge ruteada por el proxy de grabación, donde el usuario se loguea a mano, y que Nimbo capture del propio tráfico las cookies y headers que hoy hay que pegar desde las herramientas de desarrollador.

---

## Por qué

Hoy el camino es: abrir otro navegador, loguearse, abrir F12, pestaña Red,
filtrar Fetch/XHR, navegar a Favoritas, encontrar el pedido `favorites`,
click derecho, "Copiar como cURL", volver a Nimbo, pegar
(`index.html:72-92`). Son nueve pasos, dos de ellos requieren saber qué son
las herramientas de desarrollador, y hay que repetirlos cada vez que vence el
JWT — `sesion.py` no tiene ningún mecanismo de renovación.

El diseño actual no fue un descuido: `sesion.py:5-21` deja escrito que se
probaron dos caminos y por qué terminó así.

1. **Playwright sobre un perfil propio**, el mismo truco que
   `supervisor.py --login` (`:778-791`). **No funcionó**: Cloudflare detectó
   CDP y cortó con "Actividad automática detectada", probado tres veces con
   tres variantes.
2. **Pegar el cURL de un pedido ya autenticado.** Sí funciona: un `urllib`
   común con esas cookies y esos headers es indistinguible de cualquier otro
   cliente HTTP.

La conclusión correcta de (1) no es "no se puede abrir un navegador", es **no
se puede controlar un navegador**. Lo que Cloudflare detecta es CDP, el
protocolo de automatización — no la ventana. Una ventana de Edge lanzada con
`subprocess.Popen`, sin `--remote-debugging-port` y sin nadie del otro lado,
es exactamente el navegador normal del usuario.

Y falta un dato que el diseño de entonces no aprovechó: **Nimbo ya tiene un
proxy que ve todo el tráfico**. `mitmdump` corre en `127.0.0.1:8080`
(`ajustes.py:79`), su CA ya se instala desde el banner de puesta a punto
(`preflight.instalar_certificado`), y `puente.py` ya es un addon que se carga
junto al detector. Si esa ventana sale por el proxy, el pedido autenticado a
`/api/front/models/favorites` pasa por adentro de Nimbo — con sus cookies y su
`Authorization` en el mismo lugar donde el navegador los puso. El copiar y
pegar del usuario era, literalmente, mover a mano un dato que ya iba a pasar
por casa.

## Scope

**In:**

- Botón "Iniciar sesión" en la barra de sesión de la pestaña Modelos, que
  abre una ventana de Edge en `https://stripchat.com/favorites` con
  `--proxy-server=127.0.0.1:8080` y un perfil propio, `perfil_login\`.
- Hook `request` nuevo en `nimbo\puente.py` que, **solo mientras hay una
  captura pedida**, guarda cookies + headers del primer pedido a
  `/api/front/` que traiga `Authorization`.
- Dos archivos de estado nuevos: `estado\captura_sesion.json` (Nimbo pide) y
  `estado\sesion_capturada.json` (el puente entrega, cifrado con DPAPI).
- Verificación antes de guardar: el candidato capturado se prueba con un
  pedido real a `/api/front/models/favorites?limit=1`, y solo si responde
  autenticado reemplaza a `sesion.json`.
- Precondiciones con el motivo escrito al lado cuando el botón no se puede
  usar: motor parado, CA de mitmproxy sin instalar, Edge no encontrado.
- Ventana de captura con tope de 5 minutos, cuenta regresiva en la interfaz y
  botón "Cancelar".
- Al terminar bien: se cierra la ventana de Edge, se borra
  `sesion_capturada.json` y se dispara `actualizar_siguiendo` solo.
- Al cerrar la ventana de Edge (termine bien, se cancele o venza el tope),
  se purga del perfil lo que es historial de navegacion (paginas
  visitadas, autocompletado de sitios, cache de favicons, restauracion de
  pestañas) -- nunca las cookies ni el local storage, que es lo que deja
  la sesion lista para la proxima vez sin loguearse de nuevo.
- El pegado de cURL/PowerShell de hoy se conserva, detrás de un "otra forma".
- `ajustes.localizar_edge()`, sacado de `app.py:_localizar_edge` para que lo
  puedan usar los dos.
- `perfil_login\` en `.gitignore` y en la auditoría de `construir.py`.

**Fuera de scope (para otro spec):**

- Usuario y contraseña dentro de Nimbo. No sabemos si ese endpoint tiene
  captcha, Turnstile o 2FA, y guardaría la contraseña además de la sesión.
- Leer el SQLite de cookies de Edge y descifrarlo. El `Authorization` (un
  JWT) no es una cookie: vive en `localStorage`, así que ese camino no
  alcanza para lo que `favoritas_en_linea()` necesita.
- Renovación automática y silenciosa de la sesión al vencer. El perfil queda
  logueado entre reinicios, así que renovar es apretar el mismo botón, pero
  hacerlo sin ventana es otro spec.
- Usar la sesión para algo más que `favoritas_en_linea()`. `sitio.py` sigue
  anónimo, con el mismo criterio de hoy.
- Levantar un `mitmdump` efímero para poder loguearse con el motor parado.
- Capturar cualquier otra cosa del tráfico. El hook mira un único patrón de
  URL y nada más.
- Cambiar `sitio.py`, el supervisor, su perfil o su `--login`.

## Modelo de datos

Dos archivos nuevos en `estado\`, los dos de vida corta. Ninguna clave nueva
en `config.json`.

**`estado\captura_sesion.json`** — lo escribe Nimbo, lo lee `puente.py`. Su
sola existencia con `vence` en el futuro es el permiso para capturar:

```json
{"pedida": 1762100000.0, "vence": 1762100300.0}
```

**`estado\sesion_capturada.json`** — lo escribe `puente.py`, lo lee y lo
borra Nimbo. Va cifrado con DPAPI, con el mismo encabezado `NIMBO-DPAPI-1\n`
que `sesion.json` (SPEC 09): es el mismo secreto, aunque viva segundos. El
claro de adentro:

```json
{
  "capturado": 1762100042.0,
  "url": "https://stripchat.com/api/front/models/favorites?offset=0&limit=60",
  "cookies": {"stripchat_com_sessionId": "...", "...": "..."},
  "headers": {"Authorization": "eyJ...", "User-Agent": "...", "...": "..."}
}
```

`cookies`, `headers` y `url` son **exactamente** la forma que
`guardar_desde_curl` ya arma (`sesion.py:208-214`), para que el resto de
`sesion.py` no sepa de dónde vino el dato.

`sesion.json` no cambia de forma.

Estado que la interfaz ve, publicado como sección `login` en el delta de
`_bucle_estado`:

```json
{"fase": "esperando", "vence": 1762100300.0, "motivo": null}
```

`fase` es una de `inactiva`, `esperando`, `verificando`, `ok`, `vencida`,
`cancelada`, `imposible`. `motivo` solo se llena con `imposible` y
`vencida`.

Y una duplicación deliberada, del mismo tipo que las de `mp4.py` y `vivo.py`:
`puente.py` lleva su propia copia de `_proteger()` (~25 líneas de `ctypes`,
stdlib puro) y de `PREFIJO_DPAPI`. No puede importar `sesion.py`: corre en el
Python empaquetado de `mitmdump`.

## Plan de implementación

1. **`nimbo\ajustes.py`** — mover `_localizar_edge` de `app.py:88-95` a
   `localizar_edge()` público, al lado de `localizar_python()`. Agregar
   `DIR_PERFIL_LOGIN = os.path.join(RAIZ, "perfil_login")`,
   `ESTADO_CAPTURA_SESION` y `ESTADO_SESION_CAPTURADA` en `DIR_ESTADO`, con
   sus properties. `app.py` pasa a llamar `ajustes_mod.localizar_edge()`.
   Prueba a mano: abrir Nimbo y ver que la ventana sigue saliendo en modo
   `--app`.
2. **`nimbo\puente.py`** — `PREFIJO_DPAPI`, `_proteger()` y
   `ARCHIVO_CAPTURA`/`ARCHIVO_CAPTURADA` nuevos arriba, con el comentario de
   por qué está duplicado. ~40 líneas, nada enganchado todavía.
3. **`nimbo\puente.py`** — método `request(self, flow)` nuevo, ~35 líneas.
   Sale enseguida si ya capturó algo en esta vuelta, si el host no termina en
   `stripchat.com`, si el path no empieza con `/api/front/` o si no hay
   header `Authorization`. Recién entonces lee `captura_sesion.json`
   (cacheado por `INTERVALO_ESTADO`, no un `open()` por flow) y, si `vence`
   está en el futuro, escribe `sesion_capturada.json` protegido. **Nunca
   loguea cookies, headers ni la URL con query**: la línea de log es
   `"[LOGIN] sesion capturada"` y nada más. Prueba a mano: pedir la captura
   escribiendo el JSON a mano, navegar a stripchat por el proxy y ver que
   aparece el archivo.
4. **`nimbo\sesion.py`** — sacar de `favoritas_en_linea()` el armado de
   cabeceras y el pedido de una página a un helper
   `_pedir_pagina(datos, offset, limite)`; `favoritas_en_linea()` queda
   igual por fuera. Prueba a mano: apretar "Actualizar" con una sesión
   pegada y ver el mismo resultado de antes.
5. **`nimbo\sesion.py`** — `verificar(datos) -> dict` (usa `_pedir_pagina`
   con `limite=1`, devuelve `{"autenticado": bool, "error": str|None}`, nunca
   lanza) y `guardar_desde_captura(datos) -> dict`, que arma el mismo dict
   que `guardar_desde_curl` (incluido `_exp_de_jwt` sobre el `Authorization`)
   y escribe. **Verificar no guarda nada**: así un candidato malo nunca pisa
   una `sesion.json` que funciona.
6. **`nimbo\login_sitio.py`** (nuevo, ~110 líneas) — cuatro funciones
   síncronas, todas para el pool: `precondiciones(ajustes, motor) -> dict`
   (motor vivo, `preflight._ca_en_el_store()`, `ajustes.localizar_edge()`);
   `pedir(ajustes, segundos)` escribe `captura_sesion.json` atómico;
   `abrir_ventana(ajustes) -> subprocess.Popen` lanza Edge con
   `--proxy-server`, `--user-data-dir=perfil_login`, `--no-first-run`,
   `--new-window` y la URL de favoritas; `recoger(ajustes) -> dict|None` lee
   y borra `sesion_capturada.json`, descifrando con DPAPI. `limpiar(ajustes)`
   borra los dos archivos. Una quinta, `cerrar_ventana(ajustes, proc)`:
   `terminate()` + `proc.wait()` con tope (los archivos del perfil quedan
   bloqueados mientras Edge sigue vivo) y recién ahí borra del perfil los
   archivos de historial/autocompletado/cache conocidos
   (`History`, `Visited Links`, `Top Sites`, `Favicons`,
   `Network Action Predictor`, `Shortcuts`, la carpeta `Sessions`, con sus
   `-journal`) -- nunca `Cookies`, `Network\Cookies` ni `Local Storage`.
   Prueba a mano: `python -c "import login_sitio..."` abre la ventana y
   sale por el proxy; cerrarla a mano y confirmar que `History` no
   reaparece pero el perfil sigue logueado en el proximo intento.
7. **`nimbo\servidor.py`** — corutina `_tarea_login(app)`: precondiciones →
   `pedir` → `abrir_ventana` → bucle de `await asyncio.sleep(1)` hasta
   `vence`, con `recoger` + `sesion.verificar` en cada vuelta → en el primer
   candidato autenticado, `guardar_desde_captura`, `proc.terminate()`,
   `limpiar`, fase `ok` y `actualizar_siguiendo`. Un candidato que no
   verifica se descarta y el bucle sigue. Nunca bloquea el pool más de un
   pedido por vez.
8. **`nimbo\servidor.py`** — `POST /api/sesion/login/iniciar` responde 202 y
   lanza `_tarea_login` con `_lanzar_tarea(app, "sesion_login", ...)`;
   `POST /api/sesion/login/cancelar` marca el `asyncio.Event` de cancelación.
   `TOPE_TAREA["sesion_login"] = 330.0`. `app.login = {...}` nuevo, publicado
   como sección `login` desde `_bucle_estado` comparando el dict. Prueba a
   mano: `curl` a los dos endpoints y ver las fases en el WebSocket.
9. **`nimbo\web\index.html:72-92`** — botón `#boton-login` nuevo como acción
   principal de la barra de sesión, `#estado-login` para la fase y la cuenta
   regresiva, `#boton-cancelar-login`. El `#boton-toggle-sesion` de hoy pasa
   a decir "otra forma" y queda al final; su `#panel-sesion` no cambia por
   dentro.
10. **`nimbo\web\js\vista_modelos.js`** — `renderSesion` (`:584-625`) agrega
    las fases de `estado.login` antes de los cuatro casos de hoy: texto,
    cuenta regresiva en `vence`, botón deshabilitado con `motivo` cuando la
    fase es `imposible`. Mismo patrón in-place de siempre: no reconstruye la
    barra, solo escribe texto y clases. `main.js:87-89` suma `"login"` a las
    claves de `renderSesion` y engancha los dos botones nuevos.
11. **`.gitignore` y `empaquetado\construir.py`** — `perfil_login/` ignorado,
    y sumado a la lista de cosas que `auditar()` **aborta** si aparecen en el
    paquete, al lado de `sesion.json`. Es el mismo tipo de dato.
12. **`CLAUDE.md`** — el párrafo de `sesion.py` en "Detalles con historia"
    (hoy dice que el usuario pega un cURL y que Playwright no funciona: lo
    segundo sigue siendo cierto y hay que explicar por qué este camino no es
    lo mismo), y dos filas nuevas en "Quién escribe qué".

## Criterios de aceptación

- [ ] Con el motor corriendo, la CA instalada y Edge presente, "Iniciar
      sesión" abre una ventana de Edge en la página de favoritas de
      stripchat, con su propio perfil, y el sitio carga sin error de
      certificado.
- [ ] Logueándose a mano en esa ventana, la barra de sesión pasa a "sesión
      activa · N en línea" sin que el usuario pegue nada ni abra F12.
- [ ] La ventana de Edge se cierra sola al guardarse la sesión.
- [ ] `estado\sesion_capturada.json` no existe después de un login exitoso, y
      mientras existió no estaba en claro (empieza con `NIMBO-DPAPI-1`).
- [ ] `estado\captura_sesion.json` no existe después de un login exitoso, ni
      después de cancelar, ni después del tope de 5 minutos.
- [ ] Navegar a stripchat por el proxy **sin** haber apretado el botón no
      crea `sesion_capturada.json`.
- [ ] Con una `sesion.json` válida guardada, apretar "Iniciar sesión" y
      cerrar la ventana sin loguearse deja la sesión anterior intacta.
- [ ] Un candidato que responde 401 no reemplaza a `sesion.json` y el bucle
      sigue esperando hasta el tope.
- [ ] Con el motor parado, el botón está deshabilitado y el motivo dice que
      hay que arrancar la captura.
- [ ] Sin la CA en el store, el botón está deshabilitado y el motivo apunta
      al banner de puesta a punto.
- [ ] Sin Edge instalado, el botón está deshabilitado y lo dice.
- [ ] "Cancelar" cierra la ventana, deja la fase en `cancelada` y no toca
      `sesion.json`.
- [ ] Después de cerrar la ventana (login exitoso, cancelado o vencido),
      `perfil_login\Default\History` no existe (ni `Visited Links`, ni
      `Top Sites`), pero las cookies de sesión sobreviven: abrir la
      ventana de nuevo (otro click en "Iniciar sesión") muestra el sitio
      ya logueado, sin pedir credenciales.
- [ ] Pasados 5 minutos sin login, la fase queda `vencida` y el botón vuelve
      a estar disponible.
- [ ] Dos POST seguidos a `/api/sesion/login/iniciar` no abren dos ventanas.
- [ ] `eventos_detector.jsonl` y `eventos_motor.jsonl` no contienen ninguna
      cookie, ningún `Authorization` ni ninguna URL con query de
      `/api/front/`.
- [ ] El pegado de cURL/PowerShell sigue funcionando igual, desde "otra
      forma", con los dos formatos.
- [ ] "Actualizar" sigue dando el mismo resultado que antes del spec con una
      sesión pegada a mano.
- [ ] La ventana de la app (`--app`, `perfil_ventana\`) sigue abriéndose
      igual después de mover `_localizar_edge`.
- [ ] `python empaquetado\construir.py --descargar` aborta si se cuela
      `perfil_login\`.

## Decisiones

- **Sí:** capturar del proxy que ya existe. Es el mismo dato que el usuario
  movía a mano, sin automatizar nada del navegador. El proxy, la CA y el
  addon ya estaban; esto no agrega ningún proceso.
- **No:** Playwright o CDP, en ninguna forma. `sesion.py:8-15` lo documenta
  probado tres veces y cortado por Cloudflare. Esta decisión no se reabre.
- **Sí:** `subprocess.Popen` de Edge, sin `--remote-debugging-port` y sin
  nadie del otro lado. Lo que Cloudflare detecta es el protocolo de
  automatización, no la ventana; esta ventana es el navegador normal del
  usuario con un proxy puesto.
- **No:** usuario y contraseña en Nimbo. Sería la UX más simple, pero no está
  verificado que el endpoint de login sea alcanzable desde `urllib`
  (captcha / Turnstile / 2FA) y obligaría a guardar la contraseña además de
  la sesión, que es un secreto más grande y más duradero.
- **No:** leer el SQLite de cookies de Edge. El `Authorization` no es una
  cookie — vive en `localStorage` — y `favoritas_en_linea()` lo usa. Además
  AES-GCM no está en stdlib.
- **Sí:** un perfil propio, `perfil_login\`. Queda logueado entre reinicios,
  así que renovar la sesión es un click con la ventana ya autenticada. No
  mezcla cookies del sitio con `perfil_ventana\` ni toca `nucleo\perfil\`,
  que es exclusivo del supervisor.
- **No:** el Edge personal del usuario. Edge se niega a abrir un segundo
  proceso con un perfil ya abierto, y rutear la navegación personal por el
  proxy de grabación es un efecto lateral que nadie pidió.
- **Sí:** exigir el motor arrancado. El addon vive en `puente.py`, que se
  carga junto al detector: sin detector no hay proxy ni hook. Deshabilitar el
  botón con el motivo escrito es más honesto que levantar un cuarto proceso.
- **No:** un `mitmdump` efímero para loguearse con el motor parado. Sería un
  proceso más que lanzar, vigilar y matar, con su propio addon, para ahorrar
  un click de "Arrancar".
- **Sí:** verificar antes de guardar, con un pedido real de una página. Es lo
  que el docstring de `estado()` ya dice: la única confirmación real es que
  `favoritas_en_linea()` funcione. Y evita que un candidato malo pise una
  `sesion.json` que andaba.
- **Sí:** el candidato va cifrado con DPAPI aunque viva segundos. Es el mismo
  secreto que `sesion.json`, y SPEC 09 lo cifró justamente por eso. Escribir
  en claro en `estado\` sería deshacer ese spec por comodidad.
- **Sí:** duplicar `_proteger()` en `puente.py`. Ese addon corre en el Python
  de `mitmdump` y usa solo stdlib — `ctypes` lo es. Mismo criterio y mismo
  costo que las constantes duplicadas de `mp4.py` y `vivo.py`.
- **Sí:** exigir header `Authorization` para considerar un pedido. Es la
  señal de "hay sesión" y es lo que `_exp_de_jwt` necesita para el `expira`.
  Un pedido anónimo a `/api/front/` tiene cookies pero no eso.
- **Sí:** dirigir la ventana directo a la página de favoritas. Después del
  login el sitio dispara él mismo el pedido que queremos capturar, sin que el
  usuario tenga que navegar a ningún lado.
- **Sí:** el patrón de captura es `/api/front/` y no solo
  `/api/front/models/favorites`. Si el sitio renombra ese endpoint, cualquier
  otro pedido autenticado sirve igual: las cookies y el JWT son los mismos.
- **Sí:** tope de 5 minutos. Una captura abierta para siempre es un hook que
  siempre está mirando; el permiso tiene que caducar solo.
- **Sí:** conservar el pegado de cURL/PowerShell. Son ~140 líneas ya
  probadas en vivo y son la única salida si el sniffing falla (Cloudflare,
  un CDN nuevo, Edge sin CA). Pasa a "otra forma", no desaparece.
- **No:** renovación automática al vencer el JWT. Con el perfil logueado es
  casi gratis, pero abrir una ventana sola en medio de la noche es
  justamente lo que no se quiere. Si llega, va en su propio spec.
- **No:** usar la sesión en `sitio.py`. Sigue anónimo: así la búsqueda y los
  estados funcionan con el motor parado y sin cuenta, como hoy.
- **Sí:** purgar el historial de navegación del perfil al cerrar la
  ventana (verificado con el usuario tras la primera implementación: le
  preocupaba que quedara registro de qué modelo se visitó). Se mantiene el
  perfil persistente -- la alternativa (`--inprivate`, sin perfil) pierde
  la ventaja de "renovar es un click" que ya se decidió más arriba -- pero
  se le borra lo que es historial/autocompletado/cache después de cada
  cierre. Nunca se toca `Cookies` ni `Local Storage`: son lo que mantiene
  la sesión lista para la próxima vez, y borrarlos deshacía la ventaja de
  tener un perfil propio.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Cloudflare detecta el proxy TLS (certificado de mitmproxy) y corta el login | El proxy ya intercepta todo el tráfico del supervisor y de `sitio.py` desde siempre, sin desafíos. Si igual pasa, el pegado de cURL queda como respaldo y es exactamente el camino de hoy. |
| La sesión del sitio queda atada a la IP y el pedido del proxy sale por otra ruta que el `urllib` posterior | `favoritas_en_linea()` va directo, sin proxy (`sesion.py:271`), y la verificación del paso 5 usa el mismo camino: si esa combinación no sirve, el candidato no se guarda y la fase nunca llega a `ok`. |
| El `terminate()` no cierra la ventana porque Edge dejó procesos hijos | El perfil es nuevo y exclusivo, así que el PID lanzado **es** el proceso del navegador. Si igual queda una ventana, la interfaz dice que se puede cerrar a mano y la sesión ya está guardada. |
| El hook `request` le agrega latencia a cada flow del detector | Sale en el primer `if` por host y por path, antes de leer ningún archivo; el JSON de permiso se relee como máximo cada `INTERVALO_ESTADO`. El camino caliente del detector es doppiocdn, que ni entra. |
| Una cookie o un JWT terminan en un log | Criterio de aceptación explícito, y la línea de log del puente no lleva ningún dato. Misma regla que `sesion.py` ya tiene escrita en su docstring. |
| `perfil_login\` se cuela en el paquete y viaja una sesión real a otra máquina | `auditar()` de `construir.py` aborta, al lado de `sesion.json` y los `.conf`. |
| El usuario cierra la ventana a mitad y el permiso de captura queda vivo | `vence` corta a los 5 minutos sin que nadie haga nada, y la tarea borra los dos archivos en su `finally`. |

## Lo que **no** está en este spec

- Usuario y contraseña dentro de Nimbo.
- Leer y descifrar el SQLite de cookies de Edge.
- Renovación automática de la sesión al vencer.
- Un `mitmdump` propio para loguearse con el motor parado.
- Usar la sesión para `sitio.py` o para cualquier cosa que no sea
  `favoritas_en_linea()`.
- Tocar el supervisor, su perfil o su `--login`.

Cada uno, si llega, va en su propio spec.
