# SPEC 10 — Activar la licencia sin abrir una segunda ventana

> **Estado:** Aprobado
> **Depende de:** SPEC 07 (la puerta de licencia que levanta la pantalla), SPEC 02 (el empaquetado, donde esto se ve de verdad)
> **Fecha:** 2026-10-28
> **Objetivo:** Que al activar la clave la misma ventana de Edge navegue a Nimbo en vez de quedar abierta mientras se abre una segunda.

---

## Por qué

Hoy, cuando falta licencia, `app.py:224-227` abre la pantalla de activación
(`activacion.py:pantalla`) en su propia ventana de Edge, sobre un servidor
HTTP efímero con su propio puerto y token. Al activar, ese servidor se apaga
(`activacion.py:147-149`) y `app.py:230-263` sigue su curso lineal: carga los
ajustes otra vez, levanta el servidor real y llama `abrir_ventana(url)` **de
nuevo** (`app.py:263`). Nadie cierra la primera ventana ni le avisa a dónde
ir: `activacion.html:112-120` ya documenta que no hay nada más que pedirle a
un servidor que se está apagando. Resultado: el usuario ve dos ventanas de
Edge, la primera clavada en "Listo. Abriendo Nimbo...".

## Scope

**In:**

- `POST /activar` (`activacion.py:95-106`) espera a que exista la URL final
  del servidor real antes de responder, y la devuelve en el JSON.
- `activacion.html` navega la misma ventana a esa URL (`location.replace`) en
  vez de solo mostrar un mensaje.
- `app.py` entrega esa URL al servidor de activación antes de abrir una
  ventana nueva; solo abre una ventana propia si la entrega falla (timeout, o
  el usuario cerró la ventana de activación).
- La rama de instancia única (`app.py:232-237`) queda cubierta por el mismo
  mecanismo cuando corresponde.

**Out of scope (para otro spec):**

- Agregar un toolkit gráfico nativo para la pantalla de activación.
- Mover la activación a `servidor.py` — `activacion.py:1-18` ya documenta por
  qué no: levantar `servidor.py` implica levantar motor, WebSocket y bucles de
  estado para una pantalla que la mayoría de las corridas no ve.
- Cualquier cambio a `licencia.py` o `firma.py` (verificación de la clave en
  sí). Este spec solo cambia **a dónde navega la ventana**, no cómo se valida
  la licencia.
- Reusar el mismo puerto entre activación y servidor real salvo que el riesgo
  de navegación cross-origin (ver Riesgos) lo obligue.

## Modelo de datos

Este spec no introduce estructuras persistentes nuevas. Agrega un campo
`url` (string, opcional) al JSON que ya devuelve `POST /activar`:

```json
{"ok": true, "url": "http://127.0.0.1:54321/?t=<token-del-servidor-real>"}
```

Si `url` no viene (el servidor real no llegó a tiempo, o la ventana se
cerró), el cliente se queda con el comportamiento de hoy.

## Plan de implementación

1. **`nimbo\activacion.py`**: agregar `entregar_destino(url, tope_s=30) ->
   bool` y `cerrar()` al módulo (o a `pantalla()` según convenga el cierre).
   `_Pedido.do_POST` en la rama `/activar` (`:95-106`) deja de llamar
   `_Pedido.terminado.set()` apenas valida: en cambio, guarda que la clave es
   válida y bloquea el hilo de esa request (hay un hilo por pedido en
   `ThreadingHTTPServer`) esperando un segundo `threading.Event` que
   `entregar_destino()` dispara con la URL ya resuelta. Responde `{"ok":
   true, "url": ...}` y recién ahí libera `terminado` para que `pantalla()`
   pueda cerrar el servidor. Si el tope de 30 s vence antes de que
   `entregar_destino()` corra, responde `{"ok": true}` sin `url` (igual que
   hoy) y libera `terminado` de todas formas — nunca deja al usuario
   colgado. Sistema queda funcional: sin tocar `app.py` todavía, el campo
   `url` nunca se usa.
2. **`nimbo\web\activacion.html`**: en el handler de `activar()`
   (`:100-119`), si `datos.ok && datos.url`, hacer `location.replace(datos.url)`
   en vez de solo pintar "Listo. Abriendo Nimbo..."; si `datos.ok` sin `url`,
   dejar el mensaje de hoy (cubre el camino de fallback). Probar a mano
   simulando la respuesta en DevTools antes de tocar `app.py`.
3. **`nimbo\app.py`**: en la rama de `SinLicencia` (`:224-229`), después de
   que `activacion_mod.pantalla(...)` confirma la activación, no abrir
   ventana todavía — seguir cargando ajustes, levantar `Servidor`, y **antes**
   de la llamada a `abrir_ventana(url)` de `:263`, intentar
   `activacion_mod.entregar_destino(url)`. Si devuelve `True`, no abrir
   ventana nueva. Si devuelve `False` (venció el tope, o la ventana de
   activación ya se cerró), caer al `abrir_ventana(url)` de siempre. Requiere
   que `pantalla()` no bloquee la ejecución de `main()` más allá de la
   activación: el hilo del servidor de activación sigue vivo en segundo plano
   hasta que `entregar_destino()` lo complete o venza su propio tope.
4. **`nimbo\app.py`**: aplicar el mismo mecanismo a la rama de instancia única
   (`:232-237`) cuando la activación recién ocurrida coincide con una
   instancia que ya estaba corriendo — caso borde poco común (activar
   mientras otra copia de Nimbo ya está arriba), pero la pantalla de
   activación sigue abierta y merece el mismo destino final en vez de abrir
   una ventana extra contra la instancia existente.

## Criterios de aceptación

- [ ] Activar una clave válida deja **una sola** ventana de Edge abierta,
      mostrando Nimbo.
- [ ] La ventana que mostraba el formulario de activación es la que termina
      mostrando Nimbo (no una ventana nueva superpuesta).
- [ ] Si se cierra la ventana de activación antes de que el servidor real
      esté listo, Nimbo sigue arrancando y abre una ventana nueva como hoy.
- [ ] El token de la pantalla de activación nunca sirve como token contra el
      servidor real (son dos tokens distintos, como hoy).
- [ ] Si `entregar_destino()` vence su tope de 30 s, el formulario de
      activación muestra el mensaje de hoy ("Listo. Abriendo Nimbo...") y
      Nimbo abre su propia ventana sin quedar bloqueado.
- [ ] El camino de instancia única (`app.py:232-237`) no queda roto: abrir
      Nimbo cuando ya hay una instancia corriendo sigue abriendo una ventana
      contra ella.

## Decisiones

- **Sí:** bloquear el hilo de la request `/activar` en vez de agregar un
  endpoint de polling (`GET /destino`). Es la misma cantidad de código, sin
  que el cliente tenga que reintentar, y `ThreadingHTTPServer` ya da un hilo
  por pedido.
- **No:** polling desde `activacion.html`. Agrega latencia percibida y un
  endpoint nuevo para resolver lo mismo que una respuesta diferida.
- **Sí:** tope de 30 s en `entregar_destino()`. Sin tope, un `main()` que
  tarda en levantar el servidor (disco lento, puerto ocupado) deja la
  pantalla de activación congelada en "Validando..." para siempre.
- **No:** mover la activación a `servidor.py`. Ya descartado en
  `activacion.py:1-18`; este spec no reabre esa decisión.
- **Sí:** mantener dos tokens distintos (activación y servidor real). Ninguno
  de los problemas reportados pide unificarlos, y hacerlo ampliaría la
  superficie de qué puede usar el token de una pantalla efímera.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| `location.replace` hacia otro puerto puede ser tratado por Edge en modo `--app=` como navegación cross-origin y abrir una ventana nueva en vez de navegar la actual | Si se confirma en la práctica, pasarle a `Servidor.__init__` (`servidor.py:1254`, ya acepta `puerto=0` o uno fijo) el mismo puerto que usó `activacion.py`, para que la navegación sea same-origin |
| El hilo de la request `/activar` queda bloqueado hasta 30 s: si el usuario cierra la ventana en el medio, ese hilo sigue vivo esperando | `ThreadingHTTPServer` ya maneja hilos por conexión sin problema; el tope de 30 s limita cuánto puede durar, y el socket roto no afecta al resto del proceso |
| Un segundo intento de activación mientras el primero todavía espera destino | Fuera de alcance: la pantalla ya deshabilita el botón tras el primer POST (`activacion.html:102`, `botonActivar.disabled = true`) |

## Lo que **no** está en este spec

- Toolkit gráfico nativo para la pantalla de activación.
- Mover `activacion.py` a vivir dentro de `servidor.py`.
- Cambios a la verificación de la clave (`licencia.py`, `firma.py`).
- Unificar los tokens de activación y del servidor real.

Cada uno de estos, si se hace, va en su propio spec.
