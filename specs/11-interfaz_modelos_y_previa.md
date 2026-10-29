# SPEC 11 — Tres ajustes en Modelos y en la previa en vivo

> **Estado:** Aprobado
> **Depende de:** SPEC 01 (reconciliación in-place de las grillas), SPEC 04 (reproductor en vivo)
> **Fecha:** 2026-10-28
> **Objetivo:** Que el click en una sugerencia del buscador agregue a favoritas, que "no se pudo cargar la vista previa" nunca se pinte mientras el video en vivo está montado, y que una tarjeta que empieza a grabar suba sola a su grupo aunque haya filtros activos.

---

## Por qué

Tres fricciones de uso de la pestaña Modelos y del modal de previa, cada una
con causa raíz propia pero sin relación entre sí — por eso van juntas en un
spec chico de frontend en vez de en SPEC 10, que toca el arranque.

1. **El buscador solo autocompleta.** El handler del click en una sugerencia
   (`vista_modelos.js:683-688`) hace `campo.value = fila.dataset.nombre` y
   nada más. El usuario espera que un click alcance para agregarla.
2. **El mensaje de error se pinta encima del video.** `#modal-previa-sin-imagen`
   (`index.html:147`) es hermano **flex** de `#modal-previa-video`
   (`index.html:146`) dentro de `.modal-imagen` (`tailwind.css:189-203`).
   `pintarImagen()` (`vista_previa.js:288-316`) ya tiene la guarda `if
   (modoVivo()) return;` en sus callbacks `onload` (`:305`) y `onerror`
   (`:311`) — con un comentario que explica exactamente este problema — pero
   **no la tiene en la rama `!idAbierto`** (`:291-295`), que es justo la que
   corre cuando `resolverIdYArrancar()` (`:321-342`) no logra resolver un id
   numérico (`/api/sitio/estado` sin `id`, típico de una modelo agregada con
   "Grabar ahora" que nunca fue favorita) mientras el video ya está montado.
3. **La tarjeta no sube sola.** Esto pasa en la pestaña **Modelos** (los
   chips "Grabando" / "Show publico" son filtros de `vista_modelos.js:211,216`,
   no de Grabando). `renderFavoritas()` solo reubica nodos existentes cuando
   `pedirReordenModelos` está prendido (`:517-523`); una tarjeta que cambia de
   rango sin ese flag cae en la rama "no se toca" (`:536-538`). Es
   intencional — commit `0472331` lo agregó para no reacomodar la grilla
   entera bajo el cursor mientras se tildan varios conmutadores seguidos —
   pero `manejarAccionFavorita` (`main.js:193-212`) nunca distingue "esta
   tarjeta puntual la tocó el usuario": tildar grabar en una modelo del grupo
   de abajo no la sube ni ahora ni cuando el detector confirma la grabación
   segundos después.

## Scope

**In:**

- Click en una sugerencia del buscador agrega la modelo a `favoritas.json`
  (`POST /api/favoritas/agregar`) y deja el nombre en el campo.
- `pintarImagen()` nunca pinta el mensaje de error mientras `modoVivo()` es
  verdadero, en ninguna de sus ramas.
- Al tildar "grabar" (conmutador en la tarjeta, en el modal de previa, o
  "Grabar ahora") Nimbo sigue esa tarjeta puntual hasta 60 s y la reubica en
  su grupo apenas su rango cambie, sin tocar ninguna otra tarjeta.

**Out of scope (para otro spec):**

- Validar el nombre en `favoritas.py` (hoy solo hace `.strip()`, a diferencia
  de `RE_NOMBRE` en `lista.py:160`). El botón "+ favoritas" de hoy tiene el
  mismo comportamiento; este spec no lo cambia.
- Reordenar la grilla entera en cualquier cambio de estado — es justo lo que
  el commit `0472331` evitó a propósito.
- Tocar `app.css` / `tailwind.css`. Ninguno de los tres arreglos necesita CSS
  nuevo.
- Cualquier cambio a `vista_grabando.js` o al reproductor (`reproductor_vivo.js`).
  Las tarjetas de Grabando no tienen `<video>` propio; el mensaje en cuestión
  es del modal de previa.
- Priorizar visualmente qué tan "nueva" es una reubicación (animación,
  resaltado). Solo se pide que suba; cómo se ve subiendo no está pedido.

## Modelo de datos

Una sola estructura nueva, en memoria, en `vista_modelos.js`:

```js
// nombre (lowercase) -> timestamp de vencimiento (Date.now() + 60_000)
const reubicarHasta = new Map();
const TOPE_REUBICAR_MS = 60_000;
```

No persiste entre recargas. Se limpia una entrada cuando vence su plazo o
cuando la tarjeta correspondiente deja de existir en `visibles`.

## Plan de implementación

1. **`vista_previa.js:288-295`**: agregar `if (modoVivo()) return;` al tope
   de `pintarImagen()`, antes de la rama `!idAbierto`. Cubre de una las tres
   ramas (la de `!idAbierto` y las dos que ya tenían la guarda duplicada en
   `onload`/`onerror`, que pueden simplificarse pero no es obligatorio).
   Revisar también `:341` (`arrancarReloj()` tras resolver el id): si
   `modoVivo()` es verdadero ahí, no reactivar el worker de snapshots —
   `montarVivo()` ya lo paró en `:154` y hoy se reactiva igual, pidiendo
   `/api/sitio/imagen` cada segundo en vano durante todo el modo vivo.
   Probar a mano: abrir "ver en vivo" desde Grabando sobre una modelo que no
   sea favorita (sin `id` resuelto) y confirmar que el mensaje nunca aparece
   sobre el video.
2. **`vista_modelos.js:683-688`**: en el handler del click sobre
   `#sugerencias`, además de `campo.value = fila.dataset.nombre`, emitir el
   pedido de agregar a favoritas. Como `iniciarBuscador()` no importa
   `enviar`/`refrescar` a propósito (comentario en `:655-657`), el listener
   que hace el POST va en `main.js`, agregado como un cuarto listener sobre
   `#sugerencias` (no reemplaza al de autocompletar, que sigue ocultando y
   fijando el campo). Reusa `enviar("/api/favoritas/agregar", {nombre})` +
   `refrescar()`, igual patrón que `manejarAccionFavorita` (`main.js:205-206`).
   Probar a mano: tipear un nombre, click en una sugerencia, confirmar que
   aparece en la grilla de favoritas y que el campo conserva el nombre.
3. **`vista_modelos.js`**: agregar el `Map` `reubicarHasta` y
   `export function marcarReubicarModelo(nombre)` que le pone
   `Date.now() + TOPE_REUBICAR_MS`. En el bucle de `renderFavoritas()`
   (`:506-539`), para una fila que **no** es nueva (`!esNueva`) y
   `!pedirReordenModelos`: si `reubicarHasta.has(nombre)` y no venció, y
   `fila._rango !== el._rango` (cambió de grupo desde la última vez),
   reubicarla con el mismo `insertBefore` por rango que ya usa el camino de
   tarjeta nueva (`:530-534`) y borrar su entrada del `Map` (ya cumplió su
   propósito). Si venció el plazo, borrar la entrada sin reubicar. Importar
   `marcarReubicarModelo` desde `main.js` y llamarlo en la rama
   `toggle-grabar` de `manejarAccionFavorita` cuando `ev.target.checked`
   (`main.js:198`) y en el handler de "Grabar ahora" (`main.js:256-264`). El
   conmutador del modal de previa (`vista_previa.js:125`) queda cubierto
   gratis: dispara el mismo `data-accion="toggle-grabar"` que ya maneja
   `manejarAccionFavorita`. Probar a mano: con "Grabando" + "Show publico"
   activos, tildar grabar en una modelo del grupo de abajo y confirmar que
   sube sola a "Grabando" en cuanto el detector confirma, sin mover ninguna
   otra tarjeta.

## Criterios de aceptación

- [ ] Click en una sugerencia del buscador deja la modelo en
      `favoritas.json` y el campo de texto conserva su nombre.
- [ ] Un click repetido en la misma sugerencia no duplica la entrada (dedupe
      ya existente en `favoritas.py:35`).
- [ ] Con el video en vivo montado (`modoVivo()` verdadero) y
      `/api/sitio/estado` sin `id`, el mensaje "no se pudo cargar la vista
      previa" no aparece en ningún momento.
- [ ] En modo vivo no se disparan pedidos a `/api/sitio/imagen` (el worker de
      snapshots queda detenido mientras dura el video).
- [ ] Con filtros "Grabando" + "Show publico" activos, tildar el conmutador
      de grabar en una modelo del grupo de abajo la sube sola a "Grabando" en
      ≤60 s, sin mover ninguna otra tarjeta de la grilla.
- [ ] Tildar cinco conmutadores seguidos (uno detrás de otro, antes de que el
      primero termine de confirmar) reubica cada tarjeta por separado sin
      reacomodar las que no se tocaron.
- [ ] Si pasan los 60 s sin que el detector confirme la grabación, la
      tarjeta se queda donde está (no sube más tarde sola, sin aviso).

## Decisiones

- **Sí:** la reubicación por tarjeta puntual, nunca la grilla entera.
  Preserva el motivo del commit `0472331` (no mover tarjetas bajo el cursor
  mientras se tildan varias) y resuelve lo pedido sin reabrir ese problema.
- **No:** reordenar toda la grilla en cualquier cambio de rango. Es lo que
  SPEC previo evitó a propósito.
- **Sí:** ventana de 60 s para seguir la tarjeta. El conmutador solo escribe
  `grabar.txt`; el rango "Grabando" llega recién cuando el detector confirma
  la sesión, unos segundos después — sin ventana no hay forma de saber
  cuánto esperar antes de soltarla.
- **No:** seguir la tarjeta sin tope hasta que llegue a "Grabando". Si la
  modelo nunca llega a grabar (se fue a privado, el detector no la agarra),
  la tarjeta quedaría marcada para saltar sola en cualquier momento futuro,
  sin relación con una acción reciente del usuario.
- **Sí:** el click en sugerencia deja el nombre en el campo en vez de
  limpiarlo. Conserva el atajo de apretar "Grabar ahora" sobre la misma
  modelo sin volver a tipear.
- **No:** que el click en sugerencia también agregue a `grabar.txt` (grabar
  de una). Un click accidental en la lista de sugerencias no tiene que poder
  arrancar una grabación.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El detector tarda más de 60 s en confirmar la sesión (VPN lenta, modelo que demora en salir al aire) | La tarjeta se queda quieta, igual que cualquier otra no seguida; el usuario puede volver a entrar a la pestaña Modelos, que ya dispara `marcarReordenModelos()` y reacomoda todo |
| Un click accidental en una sugerencia agrega una modelo no deseada a favoritas | Se saca con el botón "quitar" que ya existe en la tarjeta (`vista_modelos.js:372`) |
| Quitar el reactivado indebido de `arrancarReloj()` en modo vivo (paso 1) deja el snapshot desactualizado si el usuario vuelve al modo imagen | No aplica: `_volverASnapshot()` (`vista_previa.js:168-184`) ya llama `pintarImagen()` y `arrancarReloj()` por su cuenta al salir del modo vivo |

## Lo que **no** está en este spec

- Validación de nombre en `favoritas.py`.
- Reordenar la grilla entera ante cualquier cambio de estado.
- Cambios a `app.css` / `tailwind.css`.
- Cambios a `vista_grabando.js` o al reproductor en vivo (`reproductor_vivo.js`).
- Animaciones o resaltado visual de la reubicación.

Cada uno de estos, si se hace, va en su propio spec.
