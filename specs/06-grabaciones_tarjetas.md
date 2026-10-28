# SPEC 06 — Grabaciones en tarjetas, con el fotograma real del video

> **Estado:** Implementado
> **Depende de:** SPEC 01 (reconciliación de tarjetas), SPEC 05 (fade de miniaturas)
> **Fecha:** 2026-10-28
> **Objetivo:** Que la pestaña Grabaciones muestre una grilla responsive de
> tarjetas (~8 por fila en ventana ancha) con un fotograma real del `.mp4`
> como portada, en vez de una lista de una sola columna con el avatar del
> sitio.

---

## Context / Por qué

Hoy `nimbo\web\js\vista_grabaciones.js` pinta una fila por grabación en una
columna única (`index.html:56`, `flex flex-col gap-2`), con una miniatura de
80×56 px al costado. Tres problemas concretos:

1. **La miniatura no es del video.** Es el avatar del sitio
   (`construirMiniatura(g.modelo)`, `vista_grabaciones.js:74` → `sitio.js:25`)
   y encima sale de `cacheSitio`, que solo se llena cuando el canal `sitio`
   del WebSocket está suscrito — y eso pasa únicamente en la pestaña Modelos
   (`main.js:170`). Así que en Grabaciones la mayoría de las filas muestran
   solo la inicial del nombre. No hay forma de reconocer una grabación de un
   vistazo.
2. **Una columna desperdicia la ventana.** Con 20 grabaciones hay que
   scrollear, mientras Grabando y Modelos ya usan grillas
   (`index.html:40` y `:95`).
3. **`renderGrabaciones()` reconstruye todo con `innerHTML` cada 4 s**
   (`vista_grabaciones.js:110`, `setInterval(cargar, 4000)` en `:294`). Eso
   recrea cada `<img>` y dispara un GET a `/api/sitio/imagen` por fila por
   tick — exactamente el patrón que SPEC 01 arregló en las otras dos vistas
   y que acá quedó pendiente.

Resultado buscado: una grilla de portadas donde se reconoce la grabación por
la imagen, el timecode se lee como en un deck de video, y el repintado de
cada 4 s no vuelve a pedir ni una imagen.

---

## Plan de diseño (y por qué no inventa un lenguaje nuevo)

El repo ya tiene una identidad visual decidida y documentada en
`tailwind.css:14-36`: panel de control de video, grafito sobre grafito,
IBM Plex Sans/Mono, y un vocabulario de **luces tally** donde cada estado
tiene siempre el mismo color. La tarjeta vertical con imagen grande arriba
ya existe y la comparten Modelos y Grabando (`.tarjeta-modelo`,
`tailwind.css:153-164`). Extender ese sistema es la decisión de diseño, no
la salida fácil: una pestaña con su propia paleta o su propio radio de
borde rompería la única cosa que hace que esta interfaz no parezca un
tablero genérico.

- **Color:** sin tokens nuevos. `bg-panel` la tarjeta, `bg-panel-alto` el
  hueco de la portada, `border-borde` en reposo y `border-tally-conectando`
  al foco/hover (mismo gesto que `.boton-chico:hover`). El ámbar
  `tally-privado` solo en el badge "N partes", que ya lo usa
  (`.etiqueta-partes`, `tailwind.css:433`).
- **Tipografía:** sin familias nuevas. La una nota fuerte es el
  **timecode**: la duración en IBM Plex Mono con `tabular-nums`, sobre un
  chip translúcido en la esquina inferior de la portada. Es lo que un
  operador busca primero en una grabación y es el único lugar donde la
  tipografía trabaja como elemento gráfico y no como etiqueta.
- **Layout:** `grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3` — ~8
  columnas en 1600 px, 4-5 a media pantalla, 2 en ventana angosta, mismo
  mecanismo que ya usa Grabando. Alineación a la izquierda, como todo el
  resto de la interfaz.
- **Principios:** la portada **es** el botón de reproducir (un `<button>`
  de verdad, no un `div` con `role`), así que no hay fila de acciones
  escondida detrás de un hover; `carpeta` y `eliminar` quedan visibles
  siempre abajo, en `.boton-chico`. Cero animación de entrada por tarjeta:
  el único movimiento es el fade de la portada al cargar, que ya existe y
  es una respuesta a un hecho real (la imagen llegó).

```
+---------------------------+
| [ fotograma 16:9        ] |   <- <button> portada, aria-label "Reproducir X"
| [            3 partes   ] |   <- badge ámbar, solo si hay varias
| [  1:50:48       2.4 GB ] |   <- timecode mono + tamaño, chip translúcido
+---------------------------+
| nombredelamodelo    1080p |   <- nombre truncado + calidad
| 19/10 10:07               |   <- mono atenuado
| [carpeta] [eliminar]      |
+---------------------------+
```

Revisión contra el brief antes de codear: lo que descarté por genérico fue
(a) el riel de color arriba de la tarjeta — una grabación terminada no tiene
estado tally que comunicar, sería decoración; (b) acciones reveladas por
hover, que es el default de toda grilla de cards y además deja la función
inalcanzable por teclado; (c) el gradiente de fondo. Lo que queda es un
chip de timecode y nada más.

---

## Scope

**In:**

- Grilla responsive de tarjetas en Grabaciones, reconciliadas por clave.
- Generación, cache y servido de un fotograma real del `.mp4` como portada.
- Las partes de una sesión se mueven al modal de reproducción, con sus
  acciones por parte (carpeta / eliminar) intactas.

**Out of scope (otro spec):**

- Paginación o virtualización de la grilla (hoy tampoco hay).
- Reemplazar `confirm()`/`alert()` por un modal propio.
- Reproducción continua entre partes (necesita MediaSource).
- Preview animado al pasar el mouse por la portada.
- Tocar la pestaña Modelos, Grabando, o el reproductor en vivo.

---

## Modelo de datos

Dos cosas nuevas:

1. **`datos\miniaturas\<nombre sin .mp4>.jpg`** — una portada por archivo
   `.mp4`, ~320 px de ancho, JPEG. Es cache regenerable: se borra con
   `os.remove`, **no** a la papelera (a diferencia de todo lo que toca
   `grabaciones.py`, que son horas de grabación).
2. **`estado\miniaturas.json`** — índice de validez, espejo exacto de
   `estado\duraciones.json` (`grabaciones.py:37-115`):
   `{ "<nombre.mp4>": {"mtime": float, "tam": int, "ok": bool} }`.
   `ok: false` es cache negativa: ffmpeg ya falló dos veces para ese
   archivo y no se vuelve a intentar hasta que cambie `mtime`/`tam`.

En `listar()`, cada **grupo** gana un campo `miniatura`: el nombre del
`.jpg` si existe y es válido, o `null`. El frontend no emite `<img>` si es
`null` — así no hay tormenta de pedidos a archivos que no están.

`config.json` no cambia (y no podría sin tocar `ajustes.DEFECTOS:72-81`, que
rechaza claves desconocidas).

---

## Plan de implementación

Cada paso deja el sistema funcionando.

### 1. `nimbo\ajustes.py` — dónde vive la cache

- Nueva property `dir_miniaturas` → `os.path.join(DIR_DATOS, "miniaturas")`,
  al lado de `streams` (L283) y `grabaciones` (L287).
- Crearla en `cargar()` (L364-368), junto a las otras.
- Verificar `.gitignore`: `datos\` debe quedar afuera del repo (ya lo está
  para `grabaciones\`/`streams\`); si el patrón es por subcarpeta, sumar
  `datos/miniaturas/`.

### 2. `nimbo\miniaturas.py` (nuevo) — generar y validar

Módulo nuevo, mismo estilo y mismos mecanismos que la maquinaria de
duraciones de `grabaciones.py` (no inventar otra forma de cachear):

- `_lock_cache = threading.Lock()`, lectura/escritura atómica `.part` +
  `os.replace` — copiar el patrón de `grabaciones.py:45-68`.
- `_CUPO = threading.Semaphore(1)`: **una** portada por vez. Duraciones
  permite 2 (`_CUPO_SONDEOS`), pero esto corre mientras el detector escribe
  miles de archivos chicos en el mismo disco; de a una alcanza.
- `nombre_cache(archivo)` → `<archivo sin extensión>.jpg`.
- `disponible(dir_miniaturas, dir_estado, archivo, mtime, tam)` → el nombre
  del `.jpg` si el índice lo marca válido para ese `mtime`/`tam` **y** el
  archivo está en disco; si no, `None`. No genera nada: es lo que llama
  `listar()` y no puede bloquear.
- `generar(ffmpeg_exe, ruta_mp4, destino, segundos)`:
  `[ffmpeg, "-nostdin", "-ss", str(segundos), "-i", ruta, "-frames:v", "1",
  "-vf", "scale=320:-2", "-q:v", "5", "-y", destino]`, `timeout=30`,
  `creationflags=CREATE_NO_WINDOW` (igual que `_duracion_ffmpeg`, L71-84).
  `-ss` **antes** de `-i` a propósito: seek por keyframe, no decodifica
  desde el principio.
- `_punto_de_corte(duracion)` → **10% de la duración**; si no se conoce la
  duración todavía, `60.0`; si ffmpeg no produjo archivo (grabación más
  corta que el corte), un segundo intento a `3.0`; si eso también falla,
  `ok: false` en el índice.
- `generar_faltantes(grupos, dir_grabaciones, dir_miniaturas, dir_estado,
  ffmpeg_exe, tope)` → genera como máximo `tope` portadas por llamada,
  recorriendo los grupos de más nuevo a más viejo (es el orden en que
  `listar()` ya los devuelve, `grabaciones.py:200`). Solo la **primera
  parte** de cada grupo lleva portada: la tarjeta es el grupo.
- `barrer(dir_miniaturas, nombres_vigentes)` → `os.remove` de los `.jpg`
  cuyo `.mp4` ya no está, y purga esas entradas del índice. Sin esto la
  cache crece para siempre cada vez que se borra una grabación.

Si `ffmpeg_exe` está vacío o no existe, todo devuelve `None` en silencio —
mismo criterio que las duraciones (ffmpeg no está en `_NUCLEO_REQUERIDO`
a propósito, `ajustes.py:88`).

### 3. `nimbo\grabaciones.py` — exponer el campo

- Firma: `listar(dir_grabaciones, dir_estado, ffmpeg_exe="", dir_miniaturas="")`.
- `import miniaturas as miniaturas_mod` (import plano, como todo `nimbo\`).
- En el armado del grupo (L179-198), agregar
  `"miniatura": miniaturas_mod.disponible(...)` usando la primera parte.
  Con `dir_miniaturas=""` devuelve `None` y nada se rompe.

### 4. `nimbo\servidor.py` — endpoint y bucle

- `INTERVALO_MINIATURAS = 30.0` y `TOPE_MINIATURAS_POR_TICK = 4` junto a las
  otras constantes (L97-107).
- `/api/grabaciones` (L817-820): pasar `a.dir_miniaturas` como cuarto
  argumento (`a_hilo` solo acepta posicionales, L187).
- **Nuevo** `GET /api/grabaciones/miniatura?archivo=<nombre.mp4>&v=<mtime>`:
  resolver con el mismo patrón anti-traversal de
  `grabaciones._resolver`/`ruta_absoluta` (L210-226) pero contra
  `dir_miniaturas`, leer los bytes en `a_hilo` y responder
  `Content-Type: image/jpeg` con
  `Cache-Control: public, max-age=86400, immutable`. Es la **segunda**
  excepción al `no-store` global (L608-616), legítima por el mismo motivo
  que `/api/sitio/imagen` (L770-791): el `&v=<mtime>` hace la URL
  inmutable. 404 si no está, sin generar nada en el camino del pedido.
- **Nuevo** `_bucle_miniaturas(app)`, calcado de `_bucle_cosecha`
  (L470-496): `await asyncio.sleep(INTERVALO_MINIATURAS)` primero,
  `contextlib.suppress(Exception)` por tick, todo el trabajo por `a_hilo`.
  Cada tick: listar (ya está en hilo), generar hasta 4 portadas faltantes,
  y cada ~10 min `barrer()`. Registrarlo en `Servidor._iniciar()`
  (L1199-1207) al lado de los otros cuatro bucles.
  El tope por tick es el punto importante: una instalación con 500
  grabaciones genera ~8 portadas por minuto en vez de clavar el disco
  mientras se está grabando.

### 5. `nimbo\web\tailwind.css` — la tarjeta

Reemplazar el bloque "pestana Grabaciones" (L418-446):

- **Fuera:** `.fila-grabacion`, `.fila-grabacion .miniatura-grande`,
  `.fila-grabacion .acciones-tarjeta`, `.partes-grupo` (ya no hay panel
  desplegable en la grilla).
- **Queda:** `.cuerpo-grabacion`, `.detalle-grabacion`,
  `.etiqueta-calidad`, `.etiqueta-partes`, y `.fila-parte` /
  `.parte-etiqueta` / `.parte-detalle`, que ahora se reusan **dentro del
  modal** sin cambios.
- **Nuevo** `.tarjeta-grabacion`: mismo cuerpo que `.tarjeta-modelo`
  (`relative flex flex-col overflow-hidden rounded-sm border border-borde
  bg-panel`) + `transition-colors`, y
  `:hover, :focus-within { @apply border-tally-conectando }`.
- **Nuevo** `.tarjeta-grabacion .miniatura-grande`:
  `@apply aspect-video h-auto w-full rounded-none text-[2rem]` — idéntico a
  lo que `.tarjeta-modelo` ya hace (L160-162). Reusar `.miniatura-grande`
  es deliberado: hereda gratis el `object-cover`, el
  `transition-opacity duration-200` y el `img.cargando { opacity: 0 }` de
  SPEC 05 (L256-257).
- **Nuevo** `.superficie-play`: el `<button>` que envuelve la portada,
  `@apply relative block w-full cursor-pointer border-none p-0 text-left`,
  con un glifo ▶ centrado que aparece en `:hover`/`:focus-visible`
  (`opacity-0` → `opacity-100`, 150 ms). Único hover decorativo y está
  justificado: es la señal de que la imagen es accionable.
- **Nuevo** `.marca-tiempo`: franja inferior de la portada con el
  degradado que ya usa `.transporte-vivo` (L209-212),
  `font-mono text-[0.72rem] tabular-nums`, timecode a la izquierda y
  tamaño a la derecha.
- **Nuevo** `.lista-partes`: contenedor de las partes en el modal,
  `flex flex-col gap-1 max-h-48 overflow-y-auto`.
- Correr `npm run build:css` y commitear `app.css` junto con
  `tailwind.css` (criterio de SPEC 05).

### 6. `nimbo\web\index.html`

- `#grabaciones-lista`: `flex flex-col gap-2` →
  `grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3`.
- En el modal (L186), `#modal-grabacion-partes` pasa de
  `class="partes-selector"` a `class="lista-partes"`.

### 7. `nimbo\web\js\vista_grabaciones.js` — reconciliar, no reconstruir

Copiar la receta de `vista_grabando.js:138-239` (y no reinventarla):

- `const tarjetas = new Map()` clave `g.clave` → elemento.
- `crearTarjeta(g)` arma el `innerHTML` una sola vez, con los nodos
  opcionales (badge de partes, etiqueta de calidad) **siempre presentes** y
  apagados con `.oculta`, y cachea los nodos en `el._nodos`.
- `actualizarTarjeta(el, g)` escribe solo lo que cambió, con el helper
  `poner(nodo, texto)` que compara antes de asignar.
- El loop de render: `Set` de vistos → `get` o `crear` → `actualizar` →
  `cont.appendChild(el)` (que **mueve** el nodo, por eso el `<img>` no se
  vuelve a pedir y el orden cambia sin recrear nada) → remover los no
  vistos. Caso vacío con `tarjetas.clear()` antes del `innerHTML`, y
  `if (cont.querySelector(".vacio")) cont.innerHTML = ""` al volver.
- **`sincronizarPoster(cont, g)`** local al módulo: si `g.miniatura` es
  `null`, mostrar `inicialDe(g.modelo)` (ya existe en `formato.js:43`); si
  no, comparar con `img.getAttribute("src")` (no `img.src`, que es
  absoluto — mismo detalle que `sitio.js:44-46`) y solo si cambió:
  `classList.add("cargando")`, `onload` la quita, `onerror` remueve el
  `<img>`, `loading="lazy"` + `decoding="async"`, y recién entonces el
  `src` = `conToken("/api/grabaciones/miniatura?archivo=…&v=" + mtime)`.
- **Eliminar** `construirMiniatura` de los imports, `expandidos`,
  `filaParte()`, `filaGrupo()` y la acción `expandir`. Grabaciones deja de
  depender del canal `sitio` por completo — de paso se va la tormenta de
  `/api/sitio/imagen` descrita en "Por qué".
- Acciones de la tarjeta: `reproducir-grupo` pasa a ser el `<button>` de la
  portada; quedan `carpeta` (primera parte) y `eliminar-grupo`. La
  delegación de clicks en `#grabaciones-lista` sigue igual.
- `pintarSelectorPartes()` pasa a pintar la **lista** de partes en el
  modal: por parte, una `.fila-parte` con `.parte-etiqueta` ("parte N"),
  `.parte-detalle` (`duración · tamaño`), y los botones
  `seleccionar-parte` / `carpeta` / `eliminar-parte`. La parte en
  reproducción lleva `.activa`. El listener de
  `#modal-grabacion-partes` (L273-276) se amplía a esas tres acciones —
  `eliminar-parte` reusa `eliminarConConfirmacion` tal cual, y si se borra
  la última parte del grupo, cerrar el modal.

---

## Criterios de aceptación

- [ ] Grabaciones muestra una grilla: ~8 tarjetas por fila en una ventana
      de 1600 px, 4-5 a media pantalla, 2 en ventana angosta, sin scroll
      horizontal.
- [ ] Cada tarjeta muestra un fotograma real del `.mp4` (no el avatar del
      sitio), tomado al 10% de la duración, con fade al cargar.
- [ ] Una grabación sin portada todavía generada muestra la inicial del
      nombre y **no** dispara ningún pedido de imagen.
- [ ] `datos\miniaturas\` se llena solo, de a 4 portadas por tick de 30 s,
      arrancando por las grabaciones más nuevas.
- [ ] Borrar una grabación y esperar un barrido deja `datos\miniaturas\` sin
      su `.jpg` huérfano.
- [ ] Click (o Enter con foco) en la portada abre el modal y reproduce.
- [ ] Una sesión de varias partes muestra el badge "N partes" y, en el
      modal, la lista con `parte N` / duración · tamaño / carpeta /
      eliminar. Borrar una parte sola sigue funcionando.
- [ ] Con las DevTools abiertas en la pestaña Grabaciones durante 60 s
      (15 ticks de refresco): cero pedidos nuevos de
      `/api/grabaciones/miniatura` y cero de `/api/sitio/imagen`.
- [ ] `/api/lista` sigue respondiendo rápido mientras el bucle de
      miniaturas genera portadas.
- [ ] `app.css` recompilado y en el commit junto a `tailwind.css`.

---

## Decisiones

- **Sí:** fotograma al **10% de la duración**. El arranque de un `.mp4` del
  detector suele ser el peor cuadro posible (reconexión, pantalla de carga).
  Cae a 60 s si la duración todavía no está calculada y a 3 s si el archivo
  es más corto.
- **Sí:** cache en `datos\miniaturas\` + índice en `estado\miniaturas.json`.
  Binarios en `datos\` (como `grabaciones\` y `streams\`), metadatos en
  `estado\` (como `duraciones.json`). Dos lugares, misma lógica que ya hay.
- **Sí:** borrar portadas con `os.remove`. Son regenerables en segundos; la
  papelera es para material irrecuperable.
- **Sí:** generación en un bucle del servidor con tope por tick, no dentro
  del pedido HTTP. Un `GET /api/grabaciones` que dispare 200 ffmpeg deja la
  pestaña colgada y le compite el disco a las grabaciones en curso.
- **No:** generar solo mientras la pestaña está abierta (como `_bucle_sitio`
  con su canal). Grabaciones no usa el WebSocket, habría que inventarle una
  suscripción, y con tope de 4 por tick el costo de tenerlo siempre
  prendido es despreciable.
- **Sí:** partes en el modal. En una tarjeta de 190 px no entra un panel
  desplegable, y el modal ya tiene el selector de partes — se agranda a una
  lista y no se pierde ninguna acción.
- **No:** preview animado al hover. Es N procesos ffmpeg más y un segundo
  formato de cache para un adorno que nadie pidió.
- **No:** paginación ni virtualización. Con reconciliación por clave el
  costo por tick ya deja de escalar con la cantidad de tarjetas; si con
  cientos de grabaciones el primer pintado molesta, va en su propio spec.
- **No:** riel de color arriba de la tarjeta. Una grabación terminada no
  tiene estado tally; el riel pasaría de información a decoración.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| ffmpeg generando portadas le compite el disco al detector mientras graba | Tope de 4 por tick de 30 s y semáforo de 1; es ~8 ffmpeg por minuto de 1 cuadro cada uno, contra los 2 concurrentes que ya permite el sondeo de duraciones |
| Primera corrida con cientos de grabaciones tarda (~1 h para 500) | Va de más nueva a más vieja, así que lo que el usuario mira primero aparece primero; mientras tanto la tarjeta muestra la inicial, nunca un hueco roto |
| `-ss` antes de `-i` cae en un keyframe cercano, no exacto | Irrelevante para una portada; es lo que la hace barata |
| `Cache-Control: immutable` en una portada regenerada (si se reemplaza el `.mp4`) | La URL lleva `&v=<mtime>`: si el archivo cambia, cambia la URL |
| 8 tarjetas por fila dejan el nombre muy corto | `truncate` + `title` con el nombre completo, como ya hace `.nombre` hoy |

---

## Verificación

```bat
rem 1. CSS
npm run build:css

rem 2. servidor solo, con consola y log a la vista
python nimbo\servidor.py
rem abrir la URL con ?t=<token> que imprime (o leerla de instancia.json)

rem 3. sin tocar la instalación real, con datos de juguete
set NIMBO_BASE=%TEMP%\nimbo_prueba
python nimbo\servidor.py
```

A mano, en la pestaña Grabaciones:

1. **Grilla:** achicar y agrandar la ventana; contar columnas (≈8 en 1600 px,
   2 en angosta) y confirmar que no aparece scroll horizontal.
2. **Portadas:** mirar `datos\miniaturas\` llenándose; a los ~2 minutos las
   primeras tarjetas tienen que mostrar el fotograma con fade. Comparar un
   `.jpg` contra el video abierto en el modal para confirmar que el cuadro
   corresponde al 10%.
3. **Sin pedidos repetidos:** DevTools → Red, filtrar por `miniatura`,
   dejar la pestaña abierta 60 s: después de la carga inicial, cero pedidos
   nuevos. Repetir filtrando por `sitio/imagen`: cero siempre.
4. **Teclado:** Tab hasta una portada, Enter abre el modal; Escape cierra
   (y no cierra si hay otro elemento en pantalla completa).
5. **Partes:** buscar una sesión con `-seg2` en el nombre, abrir el modal,
   saltar de parte, borrar una parte y confirmar que va a la Papelera y que
   el grupo se actualiza.
6. **Carga del servidor:** mientras el bucle genera portadas, pedir
   `/api/lista` (la sonda de saturación, no `/api/estado`) y confirmar que
   responde sin demora.
7. **Barrido:** borrar una grabación y esperar el barrido (o reiniciar el
   servidor) → su `.jpg` ya no está en `datos\miniaturas\` ni en
   `estado\miniaturas.json`.

No hay suite de tests en el repo; esto es verificación manual, como el
resto de los specs.

---

## Archivos tocados

| Archivo | Qué |
|---|---|
| `specs\06-grabaciones_tarjetas.md` | **nuevo** — este documento |
| `nimbo\miniaturas.py` | **nuevo** — generar / validar / barrer portadas |
| `nimbo\ajustes.py` | property `dir_miniaturas` + crearla en `cargar()` |
| `nimbo\grabaciones.py` | campo `miniatura` por grupo en `listar()` |
| `nimbo\servidor.py` | endpoint `/api/grabaciones/miniatura`, `_bucle_miniaturas`, constantes |
| `nimbo\web\index.html` | grilla en `#grabaciones-lista`, `.lista-partes` en el modal |
| `nimbo\web\tailwind.css` | bloque de Grabaciones reescrito |
| `nimbo\web\app.css` | recompilado |
| `nimbo\web\js\vista_grabaciones.js` | reconciliación por clave, portada, partes en el modal |
| `CLAUDE.md` | una línea en "Quién escribe qué" por `datos\miniaturas\` y `estado\miniaturas.json` |
| `.gitignore` | verificar que `datos\miniaturas\` quede afuera |
