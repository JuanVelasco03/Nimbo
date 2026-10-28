# SPEC 09 — Los cinco hallazgos sueltos de la auditoría

> **Estado:** Aprobado
> **Depende de:** ninguno
> **Fecha:** 2026-10-28
> **Objetivo:** Cerrar los cinco hallazgos de seguridad que la auditoría encontró fuera de la licencia y del empaquetado: el XSS de las sugerencias, `sesion.json` en texto plano, la CA de mitmproxy que sobrevive al desinstalador, el proxy sin validar y la falta de chequeo de `Origin`.

---

## Por qué

La auditoría del 28/10/2026 encontró que la superficie local de Nimbo está
bien cerrada: servidor solo en `127.0.0.1`, token de `secrets.token_urlsafe(24)`
comparado con `compare_digest`, traversal cerrado con `realpath` +
`commonpath` en los tres resolvedores, SSRF de `/api/sitio/imagen` acotado por
lista blanca de hosts (`sitio.py:67`, `:259`), escrituras atómicas, borrado a
la Papelera, y cero `eval`/`exec`/`pickle`/`shell=True` en todo el repo.

Quedaron cinco cosas, ninguna crítica por separado, todas baratas:

1. **XSS en las sugerencias de búsqueda.** `vista_modelos.js:615`
   interpola `${m.username}` sin escapar dentro de `innerHTML`, y ese valor
   viene del endpoint no oficial de stripchat (`sitio.py:202-239`). La
   ventana tiene el token en la URL, así que un payload ahí puede llamar
   cualquier `/api/*`: escribir `grabar.txt`, borrar grabaciones, arrancar y
   parar el motor. Probabilidad baja —los usernames del sitio son
   alfanuméricos— pero es la única entrada de la interfaz donde texto
   remoto se convierte en HTML sin pasar por `RE_NOMBRE` (`lista.py:34`),
   que es lo que protege a todas las demás tarjetas.
2. **`sesion.json` en texto plano.** Equivale a la contraseña de la cuenta
   mientras la sesión viva, y el propio repo ya lo trata así (CLAUDE.md:
   "nunca loguear su contenido"). Vive en claro en la carpeta de
   instalación. La instalación per-user lo protege de otro usuario de
   Windows, no de cualquier proceso del mismo usuario.
3. **La CA de mitmproxy sobrevive al desinstalador.** `nimbo.iss:95-105`
   borra `perfil_ventana\`, `nucleo\perfil\`, `estado\`, `registro\`,
   `runtime\` e `instancia.json`, y no toca el store de certificados.
   Desinstalar Nimbo deja una CA raíz de confianza huérfana en el store del
   usuario. El repo ya razonó muy bien el lado de la instalación
   (`preflight.py:70-78`: botón explícito, no un paso silencioso del
   instalador, "un instalador que agrega una CA raíz sin decirlo hace
   exactamente lo que hace un malware") — falta el lado simétrico.
4. **El proxy no se valida.** `motor.py:134` saca host y puerto de
   `config.json["proxy"]`, con default `127.0.0.1`. Un `0.0.0.0:8080` ahí
   deja un proxy MITM abierto a la red local, con la CA ya confiada en esa
   máquina.
5. **Sin chequeo de `Origin`.** El token es la única puerta de `/api/*` y
   `/ws`. Alcanza (no es adivinable, y no se filtra por `Referer` porque la
   página no hace ningún pedido externo), pero un chequeo de `Origin` es
   defensa en profundidad de diez líneas.

---

## Scope

**In:**

- `renderSugerencias` deja de construir HTML con `innerHTML`.
- `sesion.json` cifrado con DPAPI, con migración de los existentes.
- `certutil -user -delstore ROOT mitmproxy` al desinstalar.
- `_parsear_proxy` fuerza loopback y avisa si se le pidió otra cosa.
- Chequeo de `Origin` en `/api/*` y `/ws`.

**Out of scope (otro spec):**

- Nada de la licencia (SPEC 07) ni del empaquetado (SPEC 08).
- Cifrar `config.json`, `favoritas.json` o `estado\siguiendo.json`: no
  tienen credenciales.
- Cifrar `grabar.txt`: son datos de usuario, pero el detector y el
  supervisor lo leen desde otros procesos y otros intérpretes (cada 2 s, 1 s
  y 2 s respectivamente, CLAUDE.md "Quién escribe qué"); cifrarlo
  significaría cifrar el núcleo copiado.
- Borrar `~\.mitmproxy\` al desinstalar. Ver Decisiones.
- Cabeceras CSP sobre la interfaz, `Permissions-Policy`, o pasar el token a
  una cookie `Secure`/`HttpOnly`. El token en la query es lo que permite que
  los estáticos vayan sin token (`servidor.py:787`) y cambiarlo es un
  rediseño del handshake, no un endurecimiento.
- Reemplazar `confirm()`/`alert()` (ya diferido por SPEC 06).

---

## Modelo de datos

Un solo cambio de formato, en `sesion.json`.

Hoy es JSON en claro. Pasa a ser un blob DPAPI con un encabezado que lo hace
reconocible, para que `leer()` pueda distinguir los tres casos sin adivinar:

```
NIMBO-DPAPI-1\n<blob de CryptProtectData, base64>
```

`leer()` decide por el prefijo:

| Lo que hay en el archivo | Qué hace |
|---|---|
| JSON en claro (`{`) | lo usa **y lo reescribe cifrado** — migración silenciosa |
| `NIMBO-DPAPI-1` que descifra | lo usa |
| `NIMBO-DPAPI-1` que no descifra (otra máquina u otro usuario) | devuelve `{}`, el usuario pega el cURL de nuevo |

Es el mismo contrato que ya tiene hoy una sesión vencida, así que ni
`sesion.estado()` ni la pestaña "Siguiendo" necesitan un estado nuevo.

El resto de los archivos de estado no cambia.

---

## Plan de implementación

Los cinco puntos son independientes: cada paso se puede commitear solo y en
cualquier orden.

### 1. `nimbo\web\js\vista_modelos.js` — sugerencias sin `innerHTML`

Copiar el criterio que `vista_registro.js:43` ya tiene escrito con todas las
letras ("Sin innerHTML: el mensaje es texto ajeno"):

- `renderSugerencias(resultados)` (`:610-623`) pasa a crear nodos:
  `document.createElement`, `el.dataset.nombre = m.username` (atributo, no
  HTML), y `.textContent` para el nombre y la inicial.
- La miniatura sigue siendo un `<img>` con
  `src = urlImagenProxy(m.avatar)`, que ya escapa bien
  (`sitio.js:22`, `encodeURIComponent`), creado con `createElement` y
  `setAttribute` en vez de interpolado.
- `title` del punto de estado por propiedad (`.title = ...`), no por
  atributo en una plantilla.
- El caso vacío (`sin resultados`) puede seguir siendo `textContent` sobre un
  `div` creado a mano.
- No se toca la delegación de clicks: sigue leyendo `data-nombre`.

No hay otro lugar que necesite el mismo arreglo: las tarjetas de Modelos,
Grabando y Grabaciones pintan nombres que ya pasaron por `RE_NOMBRE`
(`lista.py:34`) o por el detector, y SPEC 01/06 ya las dejaron
reconciliando por clave en vez de reconstruyendo.

### 2. `nimbo\sesion.py` — DPAPI

Dos funciones nuevas y dos métodos tocados. `sesion.py` ya concentra todo el
acceso al archivo en `leer()` (`:62`) y `_escribir()` (`:70`), los dos bajo
`self._lock`, así que no hay más puntos que cubrir.

- `_proteger(datos: bytes) -> bytes` y `_desproteger(blob: bytes) -> bytes`:
  `CryptProtectData` / `CryptUnprotectData` de `crypt32.dll` por `ctypes`,
  con `DATA_BLOB` y `LocalFree` del buffer devuelto. Sin dependencias nuevas
  —el repo ya hace esto para `SHFileOperationW` en `grabaciones.py` y para
  `CreateToolhelp32Snapshot` en `motor.py`.
- `CRYPTPROTECT_UI_FORBIDDEN` (0x1) en el flag: esto corre en procesos sin
  consola ni ventana propia y un diálogo del sistema ahí cuelga el arranque.
- `leer()` implementa la tabla de tres casos del modelo de datos. La
  migración (reescribir cifrado lo que estaba en claro) va **dentro** del
  mismo `with self._lock` que ya tiene, para que no se cruce con un
  `guardar_desde_curl()`.
- `_escribir()` cifra siempre antes del `.part` + `os.replace`.
- Si `crypt32` no está disponible o falla (otro sistema operativo), las dos
  funciones devuelven la entrada tal cual y se escribe en claro, con un
  aviso una vez. Nimbo es una app de Windows (`sys.platform == "win32"` ya
  guarda `id_maquina()` y la Papelera), pero fallar al guardar la sesión es
  peor que guardarla como hoy.
- `borrar()` (`:77`) no cambia.

### 3. `empaquetado\nimbo.iss` — sacar la CA al desinstalar

- Sección `[UninstallRun]` nueva:

  ```
  Filename: "certutil.exe"; Parameters: "-user -delstore ROOT mitmproxy"; \
    Flags: runhidden skipifdoesntexist; RunOnceId: "QuitarCA"
  ```

- Solo el store **del usuario**. Es la distinción que `preflight.py:46-57`
  ya documenta: el instalador de mitmproxy pone la CA en el store de la
  máquina (con elevación) y Nimbo la pone en el del usuario. Borrar el del
  usuario deshace lo que hizo Nimbo y no toca lo que el usuario instaló por
  su cuenta.
- Comentario arriba explicando la simetría con
  `preflight.instalar_certificado`, igual que el resto del `.iss` explica el
  por qué de cada bloque.

### 4. `nimbo\motor.py` — el proxy es loopback

- `_parsear_proxy` (`:132-134`) devuelve además si el host pedido era
  loopback.
- `_arrancar_detector` (`:431`): si no lo era, usar `127.0.0.1`, emitir
  `self._aviso("el proxy configurado (%s) no es local -- se usa 127.0.0.1",
  nivel="error")` y seguir. El aviso ya llega a la ventana y al tooltip de
  la bandeja por el mismo camino que "rendido".
- Loopback = `{"127.0.0.1", "localhost", "::1", ""}`.
- Sin clave de configuración para permitirlo: nada de Nimbo necesita el
  proxy expuesto. El supervisor abre Chromium en la misma máquina y el
  camino directo no usa el proxy. Agregar el opt-out además obligaría a
  tocar `ajustes.DEFECTOS:72`, que rechaza claves desconocidas.

### 5. `nimbo\servidor.py` — chequeo de `Origin`

- `_origen_valido(app, cabeceras)`: si no hay `Origin`, True (un GET
  same-origin no lo manda). Si hay, True solo si es
  `http://127.0.0.1:<app.puerto>` o `http://localhost:<app.puerto>`.
- Aplicarlo junto al chequeo de token, en los tres lugares que ya lo hacen:
  `_manejar_get`/`_manejar_post` (`:787-789`), el handshake de `/ws`
  (`:1092`) y `_servir_video` (`:1147`). Mismo 403 y mismo cuerpo.
- Es defensa en profundidad, no la puerta: el token sigue siendo lo que
  autoriza. Va con el comentario que lo diga, para que nadie lo lea como un
  permiso para relajar el token.

---

## Criterios de aceptación

- [ ] Con `sitio.buscar()` devolviendo a mano un `username` de
      `<img src=x onerror=alert(1)>`, escribir en el buscador de Modelos
      muestra ese texto **literal** en la sugerencia y no ejecuta nada
      (consola limpia, sin `alert`).
- [ ] Click en esa sugerencia sigue agregando el nombre correcto (o lo
      rechaza por `RE_NOMBRE`, que es el comportamiento de hoy).
- [ ] `sesion.json` abierto con el Notepad no muestra ninguna cookie ni
      ningún token: arranca con `NIMBO-DPAPI-1`.
- [ ] Un `sesion.json` en claro de antes de este cambio: la pestaña
      "Siguiendo" sigue funcionando sin pegar nada, y a los pocos segundos el
      archivo ya está cifrado.
- [ ] Copiar ese `sesion.json` cifrado a otra cuenta de Windows y abrir
      Nimbo: "Siguiendo" pide el cURL de nuevo, sin traceback y sin romper
      nada más.
- [ ] Desinstalar Nimbo y correr `certutil -user -store ROOT`: no aparece
      ningún certificado de mitmproxy.
- [ ] Desinstalar Nimbo **no** borra la CA del store de la máquina si estaba
      (caso de la instalación de desarrollo, que tiene mitmproxy instalado
      aparte).
- [ ] Poner `"proxy": "0.0.0.0:8080"` en `config.json` y arrancar: el
      detector escucha en `127.0.0.1:8080`, aparece el aviso rojo en la
      ventana, y la grabación funciona igual.
- [ ] Un `fetch` a `/api/lista?t=<token>` desde una página de otro origen
      (p. ej. `http://localhost:9999`) devuelve 403.
- [ ] La ventana de Nimbo sigue funcionando entera: las seis pestañas, el
      WebSocket conectado, el modal de previa reproduciendo, y un `.mp4`
      grande con `Range` saltando dentro del video.

---

## Decisiones

- **Sí:** `renderSugerencias` sin `innerHTML`, en vez de un helper
  `escaparHtml()`. Elimina la clase de bug en lugar de taparla, son ~15
  líneas, y el repo ya tiene el precedente razonado en `vista_registro.js:43`.
  Un helper hay que acordarse de usarlo cada vez, y este caso existe
  precisamente porque alguien no se acordó.
- **No:** validar `username` contra `RE_NOMBRE` en `sitio.buscar()`. Cerraría
  también a cualquier consumidor futuro, pero ocultaría modelos con nombres
  legítimos que el patrón no cubre, y el buscador dejaría de encontrar a
  alguien por un motivo invisible. La validación de `lista.py` sigue siendo
  la puerta de `grabar.txt`, que es donde importa.
- **Sí:** DPAPI por `ctypes`. Ata el secreto al usuario + la máquina, no
  necesita que el usuario recuerde nada, y no agrega dependencias — igual
  criterio que `firma.py` evitando `cryptography` (su docstring, `:6-13`).
- **Sí:** migrar al leer y degradar a "sin sesión" si no descifra. El caso
  "no descifra" es indistinguible de "la sesión venció", que ya está
  manejado; no hace falta un estado nuevo en la interfaz.
- **No:** pedirle al usuario una contraseña maestra. Nimbo arranca solo con
  Windows (`inicio_automatico.py`) y graba de noche sin nadie delante; una
  contraseña al arranque rompe justo el caso de uso.
- **Sí:** `certutil -user -delstore` al desinstalar, solo el store del
  usuario. Es la simetría exacta de lo que instala
  `preflight.instalar_certificado`.
- **No:** borrar `~\.mitmproxy\` al desinstalar. Esa carpeta la genera
  mitmdump y la puede compartir con un mitmproxy que el usuario instaló por
  su cuenta; borrarla le rompería su herramienta. La CA en el store es lo que
  tiene consecuencias de seguridad, y es lo que se saca.
- **Sí:** forzar loopback en el proxy sin opt-out. Ningún componente de
  Nimbo necesita el proxy expuesto, y un proxy MITM abierto en una máquina
  que ya confía en su CA es el peor default posible.
- **Sí:** el chequeo de `Origin` permite el caso "sin `Origin`". Un GET
  same-origin no manda la cabecera; exigirla rompería la interfaz entera.
- **No:** mover el token de la query a una cookie, o agregar CSP. El token en
  la query es lo que permite que los estáticos vayan sin token
  (`servidor.py:787-789`, y los imports del navegador usan URLs relativas
  sin query); cambiarlo es un rediseño del handshake, no un endurecimiento.
- **Asumido:** el XSS de hoy es poco probable (los usernames de stripchat son
  alfanuméricos). Se arregla igual porque el arreglo es de 15 líneas y la
  consecuencia, si pasa, es que la página con el token hace lo que quiera.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| DPAPI falla en algún entorno (política de grupo, cuenta de servicio) y la sesión deja de guardarse | Las dos funciones degradan a "escribir en claro" con un aviso; nunca se pierde la funcionalidad por no poder cifrar |
| El usuario mueve la carpeta de instalación a otra máquina y pierde la sesión | Es inherente a DPAPI y está en los criterios de aceptación: se comporta igual que una sesión vencida, que ya es un camino conocido |
| `certutil -delstore` saca una CA de mitmproxy que el usuario usaba para otra cosa en su store de usuario | Solo Nimbo escribe en el store del usuario (`preflight.py:46-57` lo documenta); el instalador de mitmproxy usa el de la máquina, que no se toca |
| Forzar loopback rompe una instalación que de verdad necesitaba el proxy en otra interfaz | No existe ese caso en el repo (supervisor y camino directo son locales), y si apareciera, el aviso dice exactamente qué pasó en vez de fallar en silencio |
| El chequeo de `Origin` rompe el WebSocket en alguna versión de Edge | El criterio de aceptación exige el WebSocket conectado y la previa reproduciendo; si rompe, se revierte sin tocar nada más (el token sigue siendo la puerta real) |

---

## Verificación

```bat
rem servidor solo, con consola y log a la vista
python nimbo\servidor.py

rem sin tocar la instalación real
set NIMBO_BASE=%TEMP%\nimbo_prueba
python nimbo\servidor.py

rem la CA, antes y después de desinstalar
certutil -user -store ROOT | findstr /i mitmproxy
certutil -store ROOT | findstr /i mitmproxy
```

A mano:

1. **XSS.** Parchar temporalmente `sitio.buscar()` para que devuelva
   `{"username": "<img src=x onerror=alert(1)>", "avatar": "", "online": true}`,
   escribir tres letras en el buscador de Modelos, y confirmar que se ve el
   texto crudo y la consola queda limpia. Revertir el parche.
2. **Migración de `sesion.json`.** Partir de un `sesion.json` en claro, abrir
   la pestaña "Siguiendo", confirmar que lista las favoritas en línea, y
   después abrir el archivo con el Notepad: tiene que empezar con
   `NIMBO-DPAPI-1`.
3. **Otra cuenta.** Copiar ese archivo cifrado a un
   `%TEMP%\nimbo_prueba` corriendo bajo otro usuario de Windows: "Siguiendo"
   tiene que pedir el cURL, sin traceback en la consola.
4. **Desinstalar.** Instalar con `NimboSetup.exe`, apretar el botón de
   instalar el certificado en el banner de puesta a punto, confirmar con
   `certutil -user -store ROOT`, desinstalar, y confirmar que ya no está.
5. **Proxy.** `"proxy": "0.0.0.0:8080"` en `config.json`, Arrancar todo, y
   confirmar con `netstat -ano | findstr 8080` que escucha en `127.0.0.1` y
   que el aviso rojo aparece en la ventana.
6. **Origin.** Con Nimbo abierto, en otra pestaña del navegador sobre
   cualquier origen distinto, `fetch("http://127.0.0.1:<puerto>/api/lista?t=<token>")`
   tiene que dar 403.
7. **Que no se rompió nada.** Las seis pestañas, el WebSocket conectado (el
   resumen de estado se actualiza), el modal de previa reproduciendo video, y
   saltar dentro de un `.mp4` de varios GB en el modal de grabaciones.

No hay suite de tests en el repo; esto es verificación manual, como el resto
de los specs.

---

## Archivos tocados

| Archivo | Qué |
|---|---|
| `specs\09-endurecer_app.md` | **nuevo** — este documento |
| `nimbo\web\js\vista_modelos.js` | `renderSugerencias` con nodos y `textContent` |
| `nimbo\sesion.py` | `_proteger`/`_desproteger` con DPAPI; `leer()` migra, `_escribir()` cifra |
| `empaquetado\nimbo.iss` | `[UninstallRun]` con `certutil -user -delstore ROOT mitmproxy` |
| `nimbo\motor.py` | `_parsear_proxy` + `_arrancar_detector` fuerzan loopback y avisan |
| `nimbo\servidor.py` | `_origen_valido` aplicado en GET/POST, `/ws` y `_servir_video` |
| `CLAUDE.md` | una línea en "Detalles con historia" por `sesion.json` cifrado con DPAPI |

---

## Lo que **no** está en este spec

- La licencia (SPEC 07) y el empaquetado (SPEC 08).
- Cifrar `config.json`, `favoritas.json`, `siguiendo.json` o `grabar.txt`.
- Borrar `~\.mitmproxy\` al desinstalar.
- CSP, `Permissions-Policy`, o mover el token a una cookie.
- Reemplazar `confirm()`/`alert()` por un modal propio (ya diferido por
  SPEC 06).

Cada uno, si entra, va en su propio spec.
