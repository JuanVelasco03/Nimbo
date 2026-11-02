# SPEC 14 — Borrado definitivo de segmentos ya unidos

> **Estado:** Implementado
> **Depende de:** SPEC 12 (rutas de disco), SPEC 13 (freno de la unión)
> **Fecha:** 2026-11-01
> **Objetivo:** Que la cosecha de `datos\streams\` borre los segmentos ya unidos con `shutil.rmtree` en vez de mandarlos a la Papelera, para que mover los segmentos al SSD de sistema libere espacio de verdad.

---

## Por qué

SPEC 12 dejó listo poder elegir el disco de los segmentos. La configuración que
conviene en esta máquina es la que ese spec describe en su propio "Por qué":
segmentos en el SSD (escritura chica y constante), `.mp4` final en el HDD
(escritura secuencial grande). Como efecto lateral, unir pasa a **leer de C y
escribir a F** — discos distintos, cero competencia — en vez de pelear consigo
mismo en F como hoy.

Medido en la instalación real el 2026-11-01:

| Qué | Dato |
|---|---|
| C: libre | 142,1 GB |
| F: libre | 561,9 GB |
| Pico de material en un día | 36,79 GB (53 archivos, 2026-10-29) |
| `datos\streams\` ahora | 0 bytes (la cosecha está al día) |

Los segmentos pesan prácticamente lo mismo que el `.mp4`: el remux es copy. Con
`dias_segmentos: 1` sobreviven ~1 día **después** de unirse, así que el pico en
C sería dos días de material, ~75 GB sobre 142 libres. Entra, pero con poco
colchón.

El problema que bloquea la mudanza es otro. `barrer()` borra con
`grabaciones._a_la_papelera()` (`cosecha_streams.py:131`), y ese módulo lo dice
en su docstring: *"Nunca os.remove: son grabaciones de horas, no algo para
perder sin vuelta atrás por un click de más"*. Para un `.mp4` es exactamente la
decisión correcta. Para los segmentos crudos, en C, tiene tres consecuencias:

1. **El espacio no vuelve.** Cada sesión barrida se muda a la papelera de C y
   sigue ocupando hasta que Windows purgue por tope. El objetivo entero de
   mover los segmentos al SSD se pierde.
2. **El barrido miente.** `barrer()` devuelve `bytes` y la interfaz lo muestra
   como liberado. No lo está.
3. **Miles de archivos chicos por sesión** entran al índice de la papelera, que
   no está pensada para eso.

Y hay una asimetría de riesgo que conviene dejar escrita: nada chequea el
espacio libre del disco de segmentos. `_hay_espacio()`
(`detectar_m3u8.py:1319`) mide solo `GRABACIONES_DIR`. Mientras los segmentos
vivían en F, llenar el disco significaba "pierdo grabaciones". En C significa
romper Windows. Ese chequeo es otro spec; acá la mitigación es que el espacio
se libere de verdad y que la retención sea corta.

A diferencia del `.mp4`, un segmento ya unido es **regenerable mientras exista
el `.mp4`**, y ya pasó tres compuertas antes de llegar al borrado. Es el mismo
criterio con el que `miniaturas.py` borra su caché con `os.remove` y no a la
papelera.

## Scope

**In:**

- `cosecha_streams.barrer()` borra con `shutil.rmtree` en vez de
  `grabaciones._a_la_papelera()`.
- Orden de borrado nuevo: el contenido primero, `unido.json` **al final**.
- Fallo parcial: la sesión se reporta en `errores` y **conserva** su
  `unido.json`, así el barrido siguiente la reintenta.
- `confirm()` en el botón "Limpiar segmentos" avisando que es definitivo.
- Configuración de discos de esta instalación, con el motor parado:
  `dir_segmentos` a `C:\Nimbo\streams`, `dir_grabaciones` a
  `F:\Nimbo\datos\grabaciones`, `union_modo` en `auto`.
- `dias_segmentos: 0.25` (6 h) en el `config.json` de esta instalación.
- Docstring de `cosecha_streams.py` y las dos menciones de la papelera en
  `CLAUDE.md`, puestas al día.
- Opción `por nombre` en el desplegable de orden de la pestaña Grabaciones
  (`#grabaciones-orden`). Agregada a pedido durante la implementación; no tiene
  relación con el borrado de segmentos, pero toca el mismo archivo y entra acá
  en vez de abrir un spec de una línea.

**Fuera de scope (para otro spec):**

- Chequear el espacio libre de `STREAMS_DIR` y parar el grabador por disco
  lleno. Es el agujero que este spec deja abierto a propósito.
- Exponer `dias_segmentos` en la pestaña Ajustes. Hoy solo existe en
  `ajustes.py:84` y se edita a mano en `config.json`; sigue así.
- Cambiar el defecto de `dias_segmentos` en `ajustes.DEFECTOS`. Queda en `1`.
- Cambiar el borrado de `.mp4`. `grabaciones.eliminar()` sigue yendo a la
  Papelera, siempre.
- Mover material ya existente al cambiar las rutas. `datos\streams\` está en 0
  bytes, así que acá no hay nada que mover; lo viejo de `datos\grabaciones\` se
  queda donde está (igual que SPEC 12).
- Borrar los segmentos dentro de la unión, en `detectar_m3u8.py`.
- Tope de la papelera de C. Es configuración de Windows, no de Nimbo.
- `MARGEN_DISCO`, `MARGEN_ESCENARIO` y `_Freno`. No se tocan.

## Modelo de datos

Este spec no introduce estructuras nuevas. `barrer()` y `resumen()` devuelven
exactamente la misma forma que hoy, y no aparece ninguna clave nueva en
`config.json`: las cuatro de rutas existen desde SPEC 12 y `dias_segmentos`
desde antes.

Lo único que cambia es el **valor** de tres claves que ya existen, en el
`config.json` de esta instalación:

```json
{
  "dir_segmentos": "C:\\Nimbo\\streams",
  "dir_grabaciones": "F:\\Nimbo\\datos\\grabaciones",
  "union_modo": "auto",
  "dias_segmentos": 0.25
}
```

`dias_segmentos` acepta decimales sin ningún cambio: `_borrables()` hace
`dias * 86400` (`cosecha_streams.py:94`) y `Ajustes.completar()`
(`ajustes.py:398`) no coerciona tipos. `0.25` son 21.600 s.

Las tres compuertas de `_borrables()` no se tocan y siguen siendo la única
autorización para borrar:

1. la sesión tiene `unido.json`;
2. no aparece en `vivo.sesiones_vivas()`;
3. pasaron `GRACIA_MTIME_S` (300 s) desde el último archivo escrito.

## Plan de implementación

1. **`nimbo\cosecha_streams.py`** — función nueva `_borrar_definitivo(ruta)`,
   ~20 líneas. Borra `segmentos\`, `playlists\` y cualquier otro archivo de la
   carpeta de sesión; recién si todo eso salió bien borra `unido.json` y hace
   `os.rmdir(ruta)`. Si algo queda trabado, levanta `OSError` **sin** haber
   tocado el marcador. Agregar `import shutil`. Prueba a mano: carpeta de
   sesión de juguete con un `unido.json` y un archivo abierto por otro proceso
   → la función falla y el `unido.json` sigue ahí.
2. **`nimbo\cosecha_streams.py:barrer()`** — cambiar la llamada a
   `grabaciones_mod._a_la_papelera([s["ruta"]])` por
   `_borrar_definitivo(s["ruta"])`. El `try/except OSError` que ya está
   alrededor sigue sirviendo tal cual. Se cae
   `import grabaciones as grabaciones_mod` (queda sin uso). Reescribir el
   docstring del módulo (su primera línea dice "Borra a la Papelera") y el de
   `barrer()`, cuyo comentario sobre el fallo parcial de `_a_la_papelera` ya no
   aplica: ahora el motivo de ir de a una carpeta es que una trabada no impida
   borrar las demás.
3. **`nimbo\web\js\vista_grabaciones.js:limpiarStreams()`** — `confirm()` antes
   del `enviar("/api/streams/limpiar")`, armado con `ultimoStreams`, mismo
   patrón que `eliminarConConfirmacion()` (`:331`) pero con el texto al revés:
   que es definitivo, que no va a la Papelera, y que los `.mp4` no se tocan.
   Cancelar no manda el POST ni deshabilita el botón.
4. **`config.json`** — las tres claves de la sección "Modelo de datos", con el
   motor parado. Se puede hacer desde Ajustes → "Carpetas y discos" para las
   rutas (`POST /api/rutas` rechaza con 409 si el motor está vivo);
   `dias_segmentos` va a mano. Prueba a mano: arrancar el motor y ver que la
   carpeta de sesión nueva aparece en `C:\Nimbo\streams` y que
   `F:\Nimbo\datos\streams` no recibe nada.
5. **`CLAUDE.md`** — la fila de `datos\streams\` en "Quién escribe qué" (dice
   "borra a papelera lo ya unido") y el párrafo de `cosecha_streams.py` en
   "Reproductor en vivo", donde además hay que cambiar el valor del defecto
   mencionado de `dias_segmentos`.
6. **Orden por nombre en Grabaciones** — `<option value="nombre">por nombre</option>`
   en `#grabaciones-orden` (`nimbo\web\index.html`) y función `comparar(a, b)`
   nueva en `vista_grabaciones.js`, que `renderGrabaciones()` usa en vez del
   `(a, b) => claveOrdenar(a) - claveOrdenar(b)` inline. `claveOrdenar` no sirve
   para esto: devuelve un número y el orden alfabético necesita `localeCompare`.
   A igual modelo, de más reciente a más vieja.

## Criterios de aceptación

- [ ] Una sesión unida que pasó las tres compuertas desaparece de
      `dir_segmentos` tras un barrido y **no** aparece en la Papelera.
- [ ] El espacio libre de C: que reporta el encabezado sube por el mismo valor
      que `barrer()` devolvió en `bytes`, sin vaciar la papelera.
- [ ] Una sesión con un archivo abierto por otro proceso queda listada en
      `errores`, conserva su `unido.json`, y el barrido siguiente la vuelve a
      intentar.
- [ ] Una sesión sin `unido.json` no se toca.
- [ ] Una sesión que aparece en `vivo.sesiones_vivas()` no se toca.
- [ ] Con `dias_segmentos: 0.25`, una sesión unida hace 2 h no se borra y una
      unida hace 7 h sí.
- [ ] Arrancado el motor, las carpetas de sesión nuevas aparecen en
      `C:\Nimbo\streams` y el `.mp4` final en `F:\Nimbo\datos\grabaciones`.
- [ ] El log del detector registra que saltea la copia al escenario por estar
      `%TEMP%` en el mismo disco que los segmentos (los dos en C:).
- [ ] "Limpiar segmentos" pide confirmación, y cancelar no borra nada ni deja
      el botón deshabilitado.
- [ ] Eliminar un `.mp4` desde la pestaña Grabaciones sigue yendo a la
      Papelera y sigue mostrando su confirmación de hoy.
- [ ] El desplegable de orden de Grabaciones tiene una quinta opción,
      `por nombre`, y al elegirla las tarjetas quedan alfabéticas por modelo,
      sin distinguir mayúsculas ni acentos, y las de una misma modelo de más
      reciente a más vieja.
- [ ] Las otras cuatro opciones de orden siguen dando el mismo resultado que
      antes del cambio.

## Decisiones

- **Sí:** `shutil.rmtree`, sin clave de escape. Un segmento ya unido es
  regenerable mientras exista el `.mp4` y ya pasó tres compuertas. Mismo
  criterio que `miniaturas.py` con su caché.
- **No:** una clave `segmentos_a_papelera` en `config.json` para volver al
  comportamiento de hoy. Una clave más que documentar y mantener para un
  camino que, si se usa, reintroduce el problema entero.
- **Sí:** `unido.json` se borra al final. Si se fuera primero y el resto
  quedara trabado, esa carpeta no volvería a aparecer en `sesiones_unidas()` y
  quedaría huérfana ocupando C para siempre. Es el detalle que hace falta para
  que el reintento del barrido siguiente funcione.
- **Sí:** `dias_segmentos: 0.25`. Baja el pico en C de ~75 a ~50 GB y deja una
  mañana para abrir el `.mp4` y verificarlo antes de que los segmentos se
  vayan. Los segmentos son la única forma de rehacer un `.mp4` cortado con
  `--unir --forzar`, y en este repo esa red se usó: `MAX_REPARACIONES = 6`
  existe porque ffmpeg se atasca, y el 2026-10-29 cinco sesiones perdieron
  entre 430 y 856 s (SPEC 13).
- **No:** `0` (borrar apenas se une). El barrido corre cada
  `INTERVALO_LIMPIEZA = 600 s` (`servidor.py:99`), así que con `0` la ventana
  para rehacer un `.mp4` queda en ~15 min. Demasiado poco para algo que corre
  de noche.
- **No:** cambiar el defecto de `ajustes.DEFECTOS`. Vale `1` para una
  instalación con segmentos y grabaciones en el mismo disco grande, que es el
  caso por defecto. `0.25` es una decisión de **esta** configuración.
- **No:** borrar los segmentos dentro de la unión, en `detectar_m3u8.py`. Las
  tres compuertas no viven todas ahí: el detector sabe si la sesión está unida,
  pero solo Nimbo sabe si alguien la está mirando en vivo
  (`vivo.sesiones_vivas()`). Es el motivo por el que la cosecha vive en Nimbo
  desde el principio, y CLAUDE.md lo dice explícito.
- **Sí:** confirmación en "Limpiar segmentos". La acción pasa a ser
  irreversible y el botón es el único lugar donde la dispara una persona.
- **No:** confirmación en el barrido automático de `_bucle_cosecha`. Corre solo
  cada 10 min por diseño; preguntar no es una opción ahí.
- **No:** mover los segmentos que ya existen al cambiar `dir_segmentos`.
  `datos\streams\` está en 0 bytes, así que es trabajo sin objeto. Igual que en
  SPEC 12, lo viejo se queda donde está.
- **Sí:** el orden por nombre como una opción más del desplegable que ya
  existe. Es una línea de HTML y una función de seis, y no agrega ningún
  control nuevo a una barra que ya tiene cinco.
- **No:** un desplegable aparte con la lista de modelos, ni secciones con
  encabezado por modelo en la grilla. Las dos se consideraron y las dos son
  bastante más trabajo: la segunda rompe la reconciliación por clave de
  `renderGrabaciones()` (hoy un solo contenedor plano con un `Map`
  clave→elemento, y las secciones obligarían a manejar contenedores por
  modelo sin recrear los `<img>`). Si alguna vez hay tantas modelos que el
  orden alfabético no alcanza, va en su propio spec.
- **No:** sacar el buscador de texto (`#grabaciones-buscar`). Filtra, no
  ordena: son dos cosas distintas y conviven sin pisarse.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Llenar C: rompe Windows, no solo Nimbo. Nada chequea el espacio libre de `STREAMS_DIR` (`_hay_espacio` mira solo `GRABACIONES_DIR`). | Parcial: `dias_segmentos: 0.25` baja el pico a ~50 GB sobre 142 libres, y el borrado definitivo hace que el espacio vuelva de verdad. El chequeo real es otro spec, declarado fuera de scope. |
| La ventana para rehacer un `.mp4` cortado baja de 1 día a 6 h. | Las miniaturas de `datos\grabaciones\` se generan solas (`_bucle_miniaturas`) de más nueva a más vieja: una portada negra o faltante es la señal temprana de un `.mp4` roto, visible en la pestaña Grabaciones antes de las 6 h. |
| `rmtree` deja la carpeta a medio borrar y la sesión queda huérfana. | `unido.json` se borra al final: mientras el marcador siga ahí, la sesión vuelve a aparecer en `sesiones_unidas()` y el barrido siguiente la reintenta. |
| El antivirus o el indexador de Windows tienen un archivo abierto en C: y el borrado falla seguido. | La sesión queda en `errores` con el motivo y se reintenta cada 10 min. Si pasa sistemáticamente, aparece en el resumen de la pestaña Grabaciones como sesiones "sin limpiar" que no bajan. |
| Un error de rutas manda los segmentos a un disco que no responde. | SPEC 12 ya lo cubre: `resolver_rutas()` cae al defecto `<raíz>\datos\streams`, con fila roja en el banner y aviso nivel `error`. |

## Lo que **no** está en este spec

- Chequear el espacio libre del disco de segmentos y parar el grabador por
  disco lleno.
- Exponer `dias_segmentos` en la pestaña Ajustes.
- Cambiar cómo se borran los `.mp4`.
- Mover material ya grabado de un disco a otro.
- Tocar `_Freno`, `MARGEN_DISCO` o `MARGEN_ESCENARIO`.

Cada una, si llega, va en su propio spec.
