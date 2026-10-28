# SPEC 05 — Fade-in de miniaturas y mas cupo para pedirlas

> **Estado:** Aprobado
> **Depende de:** SPEC 01
> **Fecha:** 2026-10-28
> **Objetivo:** Que las miniaturas de las pestañas Modelos y Grabando aparezcan con una transición suave en vez de pop-in brusco, y que lleguen más rápido subiendo CUPO_IMAGEN de 3 a 6.

---

## Por qué

Al apretar "Actualizar" en la pestaña Modelos con una lista de 20+ favoritas,
`renderFavoritas()` crea de golpe muchas tarjetas nuevas y cada una dispara un
`<img src="/api/sitio/imagen?...">` (`sitio.js:sincronizarMiniatura`). El
servidor solo atiende `CUPO_IMAGEN = 3` pedidos de miniatura a la vez
(`servidor.py`), así que van entrando de a poco durante varios segundos y cada
`<img>` aparece de golpe apenas carga — se ve entrecortado ("popping") y la
tarjeta se queda con la inicial de placeholder bastante tiempo antes de la
imagen real.

## Scope

**In:**

- Fade-in (transición de opacidad) al cargar la imagen en
  `sitio.js:sincronizarMiniatura`, compartido por Modelos
  (`vista_modelos.js`) y Grabando (`vista_grabando.js`), las dos únicas vistas
  que la usan.
- Subir `CUPO_IMAGEN` de 3 a 6 en `servidor.py`.
- Recompilar `app.css` desde `tailwind.css` con el cambio de estilo.

**Out of scope (para otro spec):**

- Priorización de las tarjetas visibles primero (todas se piden igual, sin
  orden por scroll).
- Skeleton loader o shimmer — el placeholder sigue siendo la inicial del
  nombre, como hoy.
- Tocar `TOPE_IMAGEN` (el timeout de 4s por pedido) o el problema de fondo de
  `getaddrinfo()` sin timeout real (ya documentado en SPEC 01).
- Cualquier cambio al snapshot del modal de previa (`vista_previa.js`), que no
  pasa por `sincronizarMiniatura`.

## Modelo de datos

Este spec no introduce estructuras nuevas. Solo agrega una clase CSS
transitoria (`cargando`) sobre el `<img>` que ya existe dentro de
`.miniatura-grande`.

## Plan de implementación

1. **`nimbo\web\tailwind.css`**: agregar a la regla de `.miniatura-grande img`
   `transition-opacity duration-200` y una regla `.miniatura-grande
   img.cargando { opacity: 0 }`. Correr `npm run build:css` para regenerar
   `nimbo\web\app.css`. El sistema sigue funcional: sin cambios de JS todavía,
   el CSS nuevo no se usa.
2. **`nimbo\web\js\sitio.js`**: en `sincronizarMiniatura`, al crear el `<img>`
   nuevo agregarle la clase `cargando` antes de fijar el `src`, y en un
   handler de `load` (además del `onerror` que ya existe) quitarle la clase.
   Cuando el `<img>` ya existe y solo cambia el `src` (avatar distinto),
   aplicar el mismo patrón: agregar `cargando`, fijar `src`, quitarla en
   `load`. Probar a mano: pestaña Modelos con 20+ favoritas, click
   "Actualizar", ver que las miniaturas entran con fade en vez de aparecer de
   golpe.
3. **`nimbo\servidor.py`**: subir `CUPO_IMAGEN` de `3` a `6`. Probar a mano:
   mismo escenario del paso 2, confirmar que el resto de `/api/*` (por
   ejemplo `/api/lista`) sigue respondiendo rápido durante la actualización.

## Criterios de aceptación

- [ ] Al crear una tarjeta nueva en Modelos o Grabando, el `<img>` de la
      miniatura arranca con opacidad 0 y pasa a 1 con transición al disparar
      su evento `load`.
- [ ] Si la miniatura falla (evento `error`), se remueve el `<img>` igual que
      antes, sin quedar una tarjeta con opacidad 0 permanente.
- [ ] `CUPO_IMAGEN` vale `6` en `servidor.py`.
- [ ] Con 20+ favoritas y un click en "Actualizar", las miniaturas aparecen
      con fade visible (sin pop-in brusco) y el conjunto termina de cargar en
      menos tiempo que antes del cambio.
- [ ] `/api/lista` sigue respondiendo durante una actualización con muchas
      miniaturas en vuelo (no se cuelga la pestaña Modelos).
- [ ] `nimbo\web\app.css` queda recompilado y en el commit (no se versiona
      `tailwind.css` sin su `app.css` correspondiente).

## Decisiones

- **Sí:** fade-in compartido entre Modelos y Grabando. Es el mismo código
  (`sincronizarMiniatura`); separar un caso del otro exigiría un parámetro
  sin ningún beneficio pedido.
- **No:** parámetro para desactivar el fade en una de las dos vistas. Nadie
  lo pidió y agrega una rama sin uso.
- **Sí:** `CUPO_IMAGEN = 6`. Duplica la concurrencia de miniaturas sin pasar
  la mitad del pool de 12 hilos (`EJECUTOR_HILOS`), dejando margen para
  `CUPO_SITIO` (4) y el resto de `/api/*`.
- **No:** `CUPO_IMAGEN = 8`. Dejaría solo 4 hilos libres de 12 para todo lo
  demás mientras la red esté sana, y mucho menos margen si el CDN se tranca
  (ver SPEC 01, sección "Por qué explota a partir de 6-8").
- **No:** priorizar miniaturas visibles por scroll. Pedido explícito del
  usuario de dejarlo afuera por ahora; se puede retomar en otro spec si el
  fade + CUPO_IMAGEN=6 no alcanzan.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Subir CUPO_IMAGEN a 6 deja menos margen del pool de 12 hilos si además corre `_bucle_sitio` (CUPO_SITIO=4) al mismo tiempo | Peor caso 10 de 12 hilos ocupados, quedan 2 libres para el resto de `/api/*`; no es 0 |
| El problema de fondo (`getaddrinfo()` sin timeout real, ver SPEC 01) sigue intacto: una racha de DNS caído puede agotar el pool igual que antes, ahora con el límite en 6 en vez de 3 | Fuera de alcance de este spec; ya documentado como riesgo conocido en SPEC 01 |

## Lo que **no** está en este spec

- Priorización de miniaturas por visibilidad en pantalla.
- Skeleton loader o shimmer.
- Cambios al timeout de pedido de imagen (`TOPE_IMAGEN`) o al problema de
  `getaddrinfo()` sin timeout.
- Cambios al reproductor en vivo o al snapshot del modal de previa.

Cada uno de estos, si se hace, va en su propio spec.
