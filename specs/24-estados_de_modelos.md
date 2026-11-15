# SPEC 24 — Estados de las modelos: cámara oculta, privados que faltan y una sola tabla

> **Estado:** Aprobado
> **Depende de:** SPEC 03 (grabador directo), SPEC 11 (interfaz de modelos y previa),
> SPEC 18 (segundo sitio, chaturbate), SPEC 21 (login chaturbate)
> **Fecha:** 2026-11-15
> **Objetivo:** Que Nimbo distinga un show público de verdad de uno con la cámara oculta
> (CrazyTicket / "Hidden Cam"), trate igual en todo el sistema cada estado del sitio y
> lo muestre con su nombre en la interfaz.

---

## Por qué

### A — "decía SHOW PÚBLICO y no grababa"

Una modelo en lista aparecía en Nimbo como **SHOW PÚBLICO** y no grababa. Entrando al
sitio: *"La cámara está oculta — CrazyTicket — 'Hidden Cam' show in progress"*.

Causa: todo Nimbo decide con un solo campo, `user.status` del sitio, y ese campo dice
`public` durante un show con cámara oculta. El JSON de `/api/front/v2/models/<id>/cam`
trae más (`cam.show`, `cam.isCamAvailable`): `mouflon.consultar_cam()`
(`nucleo\scripts\mouflon.py:155-181`) los lee y **nadie los usa**. Resultado en
`grabador_directo.py:_paso()`: `_publico()` da `True`, se piden master y playlist, no
llega video real, y la interfaz pinta SHOW PÚBLICO + EN LISTA/SIN SEÑAL.

No tenemos una muestra real del JSON en ese estado. En los logs
(`eventos_directo.jsonl*`) no aparece ningún `-> ticketShow`: el sitio no lo reporta
como ticket show por `status`.

### B — Estados que el núcleo no conoce

Transiciones vistas en los logs reales: `public`, `off`, `idle`, `p2p`, `groupShow`,
`private`, `virtualPrivate`. Pero:

- `ESTADOS_VIVOS` (`mouflon.py:64`, `supervisor.py:142`) **no tiene `virtualPrivate`
  ni `p2pVoice`**. Para el directo eso es "no está en vivo" → `_cerrar_todas()` cierra
  y une la sesión, en vez de congelarla como en `private`/`p2p`. Una modelo que pasa
  por `virtualPrivate` y vuelve fragmenta el `.mp4`.
- El frontend sí los conoce (`vista_modelos.js:222`), así que la UI y el grabador no
  coinciden.

### C — La tabla está copiada en seis lugares

`mouflon.py:64`, `supervisor.py:142`, `grabador_directo.py:154` (`ESTADOS_PUBLICOS`),
`vista_modelos.js:221-227`, `vista_grabando.js:17`, `pruebas\sonda_directo.py:257`.
Cada estado nuevo hay que agregarlo a mano en todos, y B es lo que pasa cuando falta
uno.

### D — Chaturbate: todo lo que no es `public` se ve igual

`chaturbate.py:62` trata cualquier `room_status` distinto de `public` como "nada que
grabar" (correcto para el grabador, SPEC 18). Pero la interfaz tampoco distingue:
`private`, `group`, `hidden`, `away`, `password protected` caen todos en EN LÍNEA /
SIN DATO, salvo `private` por coincidencia con el vocabulario de stripchat.

---

## Alcance

**Entra:**

1. **Sonda** `pruebas\sonda_estados.py`: vuelca el JSON crudo de `/cam` (stripchat) y
   de `/api/chatvideocontext/` (chaturbate) para fijar con datos reales el criterio de
   cámara oculta y los valores de `room_status`. Paso 1, bloqueante del resto.
2. **Cámara oculta en stripchat (camino directo)**: con `status` público pero la cámara
   oculta, el directo la trata **igual que un privado** — congela la sesión, deja de
   pedir la playlist, espera con `ESPERA_NO_PUBLICO`, reanuda al volver, y
   `@inactividad` la cierra si no vuelve. Mismo camino que `esperando_publico`.
3. **`virtualPrivate` y `p2pVoice`** pasan a contar como "en vivo, no público" en el
   núcleo (congelar, no cerrar), en el directo y en el supervisor.
4. **Una sola tabla por lado**: `mouflon.py` en el núcleo, `nimbo\web\js\estados.js` en
   el frontend.
5. **Interfaz**: badge propio **CAM OCULTA** (clase `privado`, nunca REC ni "Show
   público") con su filtro; badges con nombre para los estados de chaturbate.
6. `directo.json` publica el show oculto por modelo (campo nuevo, ver Modelo de datos).

**No entra:**

- Detectar cámara oculta en el **supervisor** (camino navegador). Hoy toda la lista
  stripchat va `@directo=1` con `@navegador=` vacío; el supervisor es respaldo. Solo
  recibe el arreglo de B. Si hace falta, otro spec.
- Detectar cámara oculta para modelos que **no** se graban (pestaña Modelos sin
  directo): `sitio.py:estado()` lee el HTML con `RE_ESTADO` y sigue igual. Un modelo
  fuera de la lista puede seguir diciendo SHOW PÚBLICO en ese caso.
- Comprar / ver el ticket con la cuenta logueada para grabar el show oculto.
- Cambiar el comportamiento de **grabación** de chaturbate (sigue "solo `public`
  graba"). Lo de chaturbate es solo interfaz.
- Tocar `pruebas\sonda_directo.py` (medidor, no runtime).

---

## Modelo de datos

### Núcleo — `nucleo\scripts\mouflon.py` (única fuente)

```python
ESTADOS_PUBLICOS = {"public", "live"}                       # movido desde grabador_directo.py
ESTADOS_NO_PUBLICOS = {"private", "virtualPrivate", "p2p", "p2pVoice",
                       "groupShow", "ticketShow"}
ESTADOS_VIVOS = ESTADOS_PUBLICOS | ESTADOS_NO_PUBLICOS        # suma virtualPrivate, p2pVoice

def show_oculto(crudo: dict) -> str | None:
    """Con user.status público: nombre del show que oculta la cámara
    (p. ej. "CrazyTicket"), o None si la cámara se ve. Criterio exacto
    fijado por la muestra del paso 1 (pruebas\\RESULTADOS.md)."""
```

`consultar_cam()` suma una clave al dict que ya devuelve: `"oculto": show_oculto(datos)`.
Función pura, sin estado de módulo: `mouflon.py` sigue siendo seguro de importar.

`grabador_directo.py` deja de definir `ESTADOS_PUBLICOS` y usa `mouflon.ESTADOS_PUBLICOS`.

### `estado\directo.json` — fila por modelo (`grabador_directo.py:fila_estado`)

| Campo | Tipo | Nuevo | Significado |
|---|---|---|---|
| `estado_cam` | str | no | `user.status` tal cual (sigue igual) |
| `cam_oculta` | str \| null | **sí** | nombre del show con cámara oculta (`"CrazyTicket"`), `null` si no |
| `esperando` | bool | no | ahora también `true` durante cámara oculta |

Nombre de clave nuevo, ningún consumidor existente cambia de significado.

### Frontend — `nimbo\web\js\estados.js` (nuevo, única fuente)

```js
// clave (minúscula) -> { clase: "publico"|"privado"|"offline", texto, filtro }
export const ESTADOS = {
  // stripchat
  public: {...}, live: {...},
  private: {...}, virtualprivate: {...}, p2p: {...}, p2pvoice: {...},
  groupshow: {...}, ticketshow: {...},
  idle: {...}, off: {...},
  // chaturbate (valores confirmados por la sonda del paso 1)
  group: {...}, hidden: {...}, away: {...}, "password protected": {...},
};
export const CAM_OCULTA = { clase: "privado", texto: "CAM OCULTA", filtro: "cam-oculta" };
export function infoEstado(estadoSitio, camOculta) { ... }   // camOculta gana
export function esPublico(...), esNoPublico(...), estaVivo(...)
```

`vista_modelos.js` (`ESTADOS_PUBLICOS`/`ESTADOS_PRIVADOS`/`TEXTO_*`/`FILTROS` de
estados) y `vista_grabando.js:17` importan de acá. `datosDeteccion()` suma
`camOculta: s && s.cam_oculta`.

Un estado desconocido sigue cayendo en EN LÍNEA (si `online`) / SIN DATO, como hoy.

### Muestras de la sonda

`pruebas\muestras_estados\<sitio>_<nombre>_<YYYYMMDD-HHMMSS>.json` — JSON crudo. Va en
`.gitignore` (datos de terceros); lo que se aprende va a `pruebas\RESULTADOS.md`.

---

## Plan de implementación

Cada paso deja el sistema andando. **No editar el núcleo con Nimbo grabando** (memoria:
recarga en caliente de mitmdump): pasos 3-5 con Nimbo parado.

1. **Sonda** `pruebas\sonda_estados.py` (solo lectura, librería estándar, importa
   `mouflon`/`chaturbate`):
   - `--una <nombre> [--sitio stripchat|chaturbate]`: un pedido, guarda el crudo,
     imprime `status`, `cam.show`, `isCamAvailable` y toda clave de `cam` con "show",
     "ticket", "hidden" o "active" en el nombre.
   - `--vigilar [--cada 60]`: recorre `grabar.txt` (stripchat y `@chaturbate=`) y
     guarda una muestra cada vez que cambia la firma
     `(status, cam.show, isCamAvailable)` de una modelo / `room_status` de una sala.
   - Dejarla corriendo hasta capturar al menos un caso de cámara oculta. Documentar en
     `pruebas\RESULTADOS.md` el campo exacto que la distingue y los `room_status` de
     chaturbate vistos. **Si no aparece el caso, el spec se frena acá** y se ajusta
     con lo que haya.
2. **Tabla del frontend** `estados.js` + migrar `vista_modelos.js` y
   `vista_grabando.js` a importarla, sin cambiar todavía ningún texto visible salvo los
   badges de chaturbate (D). Sin build: es un módulo ES más.
3. **Tabla del núcleo**: `ESTADOS_PUBLICOS`/`ESTADOS_NO_PUBLICOS`/`ESTADOS_VIVOS` en
   `mouflon.py` (con `virtualPrivate`, `p2pVoice`); `grabador_directo.py` los importa.
   Supervisor: `lanzador_supervisor.py` hace `supervisor.ESTADOS_VIVOS =
   mouflon.ESTADOS_VIVOS` antes de `supervisar()` (parchear desde afuera, mismo patrón
   que `Emision`; `supervisor.py:628` lo lee como global en cada llamada). Con esto B
   queda cerrado.
4. **`show_oculto()`** en `mouflon.py` con el criterio del paso 1; `consultar_cam()`
   devuelve `"oculto"`.
5. **Directo**: `_chequear_estado()` guarda `self.cam_oculta = cam["oculto"]` (log
   `[x] cámara oculta (CrazyTicket)` / `cámara visible otra vez` en cada transición);
   `_publico()` pasa a `estado_cam in ESTADOS_PUBLICOS and not self.cam_oculta`. Todo
   lo demás (congelar, `ESPERA_NO_PUBLICO`, `@inactividad`, `ESPERA_CIERRE_NO_PUBLICO`)
   sale solo por la rama `not self._publico()` existente. `fila_estado()` publica
   `cam_oculta`. Error de consulta → `cam_oculta` conserva el último valor (mismo
   criterio que `estado_incierto`).
6. **UI**: `infoEstado()` pone CAM OCULTA cuando `camOculta`; `sitioEsNoPublico()` da
   `true` → nada de REC, cuenta como EN LISTA; chip de filtro "Cam oculta" (no en
   chaturbate); tooltip del badge con el nombre del show. `npm run build:css` solo si
   hace falta clase nueva.
7. **CLAUDE.md**: actualizar la nota de `mouflon.py` (fuente única de estados, sumar
   `show_oculto`), la fila de `directo.json` y "Detalles con historia" (`status` miente
   durante una cámara oculta).

---

## Criterios de aceptación

- [ ] `pruebas\RESULTADOS.md` tiene la muestra de cámara oculta, el campo que la
      distingue y los `room_status` de chaturbate vistos.
- [ ] `mouflon.show_oculto()` devuelve el nombre del show con la muestra capturada y
      `None` con una muestra de show público normal (probado a mano con los dos JSON).
- [ ] Con una modelo en cámara oculta, `eventos_directo.jsonl` muestra
      `no esta en show publico ... congelo` y **ningún** segmento nuevo se pide hasta
      que la cámara vuelve.
- [ ] Al volver la cámara dentro de `@inactividad`, la grabación sigue en la **misma**
      carpeta de sesión (un solo `.mp4`).
- [ ] `estado\directo.json` trae `cam_oculta` con el nombre del show mientras dura, y
      `null` después.
- [ ] La tarjeta de esa modelo dice **CAM OCULTA**, no SHOW PÚBLICO ni REC; el filtro
      "Show público" no la incluye y "Cam oculta" sí.
- [ ] Una transición `public -> virtualPrivate -> public` en el directo **no** cierra
      la sesión (log `congelo`, no `la modelo no esta en vivo`).
- [ ] `grep` de `"groupShow"` en `nimbo\web\js\` y en `grabador_directo.py` /
      `lanzador_supervisor.py` da cero literales fuera de `estados.js` / `mouflon.py`.
- [ ] Una sala de chaturbate en `hidden`/`away`/`group` muestra un badge con ese
      nombre, no EN LÍNEA ni SIN DATO.
- [ ] `mouflon.verificar_canario()` sigue dando `True` e importar `mouflon` no arranca
      nada (sin efectos al importar).

---

## Decisiones tomadas y descartadas

| Decisión | Por qué |
|---|---|
| Sonda antes de escribir el criterio | No hay muestra real; escribir contra nombres de campo supuestos (`cam.show`, `isCamAvailable`) es adivinar. El paso 1 bloquea el 4. |
| Cámara oculta = privado (congelar y esperar) | Es exactamente "hay alguien en vivo pero no hay nada público que grabar". Reusa `esperando_publico` y `@inactividad` sin temporizador nuevo. |
| Descartado: intentar grabar igual | Sin ticket pago no llega video real; pedir playlist en vano es lo que generaba los reintentos y el SIN SEÑAL. |
| Badge propio CAM OCULTA, no reusar TICKET SHOW | `status` no dice `ticketShow`: mapearlo a ese texto mentiría sobre lo que reporta el sitio; el usuario reconoce "cámara oculta" de la página. |
| Descartado: estado sintético (`status="hiddenShow"`) inyectado en `estado_cam` | Rompe el "`estado_cam` es lo que dice el sitio" y los logs de transición. Campo aparte `cam_oculta`. |
| Una tabla por lado (núcleo / frontend), no una compartida | El frontend no puede importar Python y el núcleo no importa nada de `nimbo\`. Dos fuentes, cada una única en su lado. |
| Supervisor: parche desde `lanzador_supervisor.py`, no editar `supervisor.py` | Mismo criterio que `Emision` y el filtro de chaturbate: el núcleo del navegador se observa desde afuera. |
| Supervisor sin detección de cámara oculta | Es respaldo y hoy no graba a nadie (`@navegador=` vacío). Costo sin beneficio actual. |
| Chaturbate: solo interfaz | SPEC 18 cerró "solo `public` graba" y es correcto también para `hidden`. |

---

## Riesgos identificados

- **El caso no vuelve a aparecer mientras corre la sonda.** CrazyTicket depende de la
  modelo. Mitigación: `--vigilar` sobre toda la lista, corriendo en segundo plano
  días si hace falta; el resto del spec (pasos 2-3) no depende de la muestra.
- **Falso positivo de `show_oculto()`** congelaría un público real → horas sin grabar.
  Mitigación: el criterio exige `status` público **y** el marcador concreto de la
  muestra; log en cada transición para verlo rápido en Registro; una muestra de
  público normal entra en los criterios de aceptación.
- **El sitio cambia el JSON de `/cam`.** `show_oculto()` devuelve `None` ante cualquier
  clave faltante → se vuelve al comportamiento de hoy (graba o lo intenta), nunca a
  dejar de grabar.
- **`virtualPrivate` era a propósito "no vivo".** No hay comentario que lo justifique
  y el frontend ya lo trata como privado; si al probarlo aparece un motivo, se saca de
  `ESTADOS_NO_PUBLICOS` y se anota acá.
