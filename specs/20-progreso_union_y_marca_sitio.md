# SPEC 20 — Progreso de la unión, y la marca de sitio al quitar

> **Estado:** Implementado
> **Depende de:** SPEC 06 (tarjetas de grabaciones), SPEC 12 (rutas de disco),
> SPEC 13 (freno de la unión), SPEC 18 (segundo sitio chaturbate), SPEC 19
> (unión secuencial entre todos los sitios)
> **Fecha:** 2026-11-08
> **Objetivo:** Que en Grabaciones se vea cuánto falta para que un `.mp4` quede
> terminado — fase, porcentaje, MB y ETA de la unión en curso — y que quitar un
> nombre de la lista limpie también su marca `@chaturbate=`.

---

## Por qué

### A — "veo los MB subir y no sé cuánto falta"

`unir_sesion()` (`detectar_m3u8.py:1483`) escribe el `.mp4` **directo al destino**:

```python
base  = os.path.join(GRABACIONES_DIR, limpiar(etiqueta))   # :1510
final = base + ".mp4"                                       # :1512
```

y `_remuxear_tubo()` le pasa ese `final` a ffmpeg tal cual (`:1652`). No hay `.part`, no
hay `os.replace`. O sea: desde el primer byte el archivo a medio armar ya está en
`datos\grabaciones\`, y `grabaciones.listar()` (`grabaciones.py:129`) lo lista como
cualquier otro `.mp4` porque su único filtro es `endswith(".mp4")` (`:151`). La tarjeta
aparece en la grilla con `formatoMB(g.tamano_total_bytes)` subiendo
(`vista_grabaciones.js:170`) y la duración en `"calculando..."` (`:63`). Eso es
exactamente lo que el usuario describe.

El dato que falta **ya se calcula**, y no sale de ningún lado:

| Dato | Dónde ya existe | Dónde se publica hoy |
|---|---|---|
| bytes totales a procesar | `_plan()` → `total`, `detectar_m3u8.py:1535` | un `log.info` (`:1539`) |
| bytes hechos | `os.path.getsize(salida)`, `:1578` | nada |
| el porcentaje | `100.0 * hechos / total`, `:1591` y `:1595` | solo un `log.warning` cuando ffmpeg se planta |
| la fase (`copia` / `remux`) | `_Freno.etiqueta`, `:1272` / `:1324` / `:1429` | una línea de log por transición del freno |
| la pasada de reparación | `intento`, `:1534` (hasta `MAX_REPARACIONES + 1` = 7) | nada |
| si el freno está puesto | `_Freno.frenar`, `:1277` | la línea de transición |
| qué sesión se une | `etiqueta` | el log |

Y no se puede mirar por el log, porque el padre lanza al hijo con
`capture_output=True` (`grabador_directo.py:341`): **toda** la salida del subproceso
`--unir` queda bufferizada hasta que termina. Durante la hora que puede tardar una unión
de 7 GB frenada, el padre no ve nada y la interfaz menos.

Hay además dos tramos completamente invisibles:

- La **copia al escenario** (`_preparar_escenario:1376`, SPEC 12): miles de archivos
  copiados al SSD antes de que el `.mp4` exista. Minutos sin tarjeta donde poner nada.
- La **mezcla de audio** de chaturbate (`grabador_chaturbate.py:_unir_doble:347`): dos
  uniones más un `_mezclar_audio`, tres fases largas seguidas.

Lo único que se publica hoy sobre unión es un contador de cola, y está mal:

```python
"uniendo": self.cola_union.qsize(),   # grabador_directo.py:1389, chaturbate.py:1161
```

`qsize()` **no cuenta la unión en vuelo** (ya se le hizo `get()`). Con una sola unión
corriendo y nada encolado, el contador dice `0`. Y su único consumidor
(`vista_grabando.js:298`) lo muestra solo junto a `parando`, así que una unión en
operación normal no aparece en ninguna parte de la interfaz.

### B — la marca `@chaturbate=` huérfana

`quitar_modelo()` (`lista.py:175`) hace `lineas.pop(i)` y nada más. Su docstring explica
por qué no toca `@vpn=`:

> *"si estaba marcada y se vuelve a agregar sin querer, el propio detector/supervisor ya
> avisan solos que la marca no sirve sin la linea suelta"*

Ese razonamiento **no se traslada** a `@chaturbate=`: no hay ningún aviso equivalente, y
el efecto no es cosmético. Como `leer()` arma `sitios` iterando `seleccion`
(`lista.py:114-115`), un nombre que quedó solo en `@chaturbate=` es **invisible** en
toda la interfaz. Sigue en el archivo. Si más adelante se agrega ese nombre desde Modelos
pensando en stripchat, `servidor.py:1496` toma la rama `else` → `agregar_modelo()`, que no
mira `@chaturbate=` → el nombre reaparece marcado y se graba contra el sitio equivocado,
en silencio. Es literalmente el riesgo que SPEC 18 declaró reportable
(`specs\18-segundo-sitio-chaturbate.md:475`).

El bug tiene tres piezas, y la tercera es la que de verdad lo arregla:

1. `vista_grabando.js:135` setea `b.dataset.nombre` en todos los `[data-accion]` pero el
   sitio solo en `verVivo` (`:184`) — aunque `el.dataset.sitio` ya está escrito dos
   líneas antes (`:169`) y `sitio` está en scope en `actualizarTarjeta` (`:164`).
2. `main.js:245` postea `{ nombre }` sin sitio. **Y `main.js:202` también** — la pestaña
   Modelos tiene el mismo bug: manda `sitio` al agregar (`:201`) y lo omite al quitar, con
   la variable `sitio` en scope dos líneas arriba (`:198`). SPEC 18 cubrió el lado
   `agregar` y dejó el lado `quitar` sin cubrir, coherente con que el backend no lo acepta.
3. `servidor.py:1498` ignora `cuerpo["sitio"]` y `quitar_modelo()` no tiene parámetro
   donde recibirlo.

La operación de desmarcado ya existe y es segura: `marcar_sitio(nombre, "stripchat")`
(`lista.py:218`) hace `discard` (idempotente) y, al no ser `activo`, **no** agrega la
línea de selección (`:244`) — se puede llamar sobre un nombre que ya no está en la lista.
Lo que falta es hacerlo **en la misma escritura** que el `pop`: dos llamadas sueltas son
dos reescrituras del archivo que el detector relee cada 2 s, o sea una ventana donde se ve
un estado intermedio.

---

## Scope

### Entra

**Parte A — progreso de la unión**

- Un módulo nuevo, `nucleo\scripts\progreso_union.py`: protocolo puro y **seguro de
  importar**, con el mismo criterio que `mouflon.py`, `chaturbate.py` y
  `cerrojo_union.py` (sin `addons = [...]`, sin estado de módulo mutable que importe,
  solo librería estándar). Escribe y lee `estado\union.json`.
- `estado\union.json`: **un** registro de la unión en curso. Un solo registro alcanza
  porque SPEC 19 ya garantiza una unión a la vez en toda la instalación — este spec se
  apoya en esa garantía y no agrega ninguna propia.
- Publicación desde `unir_sesion()` en `detectar_m3u8.py`, que es el **único** lugar por
  el que pasan los tres caminos de unión (detector en proceso, y el subproceso `--unir`
  del directo y de chaturbate): fase, bytes hechos de totales, pasada de reparación,
  estado del freno y ritmo medido.
- La fase `mezcla` publicada desde `grabador_chaturbate.py:_mezclar_audio`, que es la
  única que no pasa por `detectar_m3u8.py`.
- `NIMBO_UNION_SITIO`, variable de entorno que le dice al subproceso `--unir` de qué sitio
  es la sesión que está uniendo (calco de cómo SPEC 19 pasa `NIMBO_UNION_TURNO`).
- `uniendo` arreglado para contar **la en vuelo más las encoladas**, en los tres
  publicadores de estado (`grabador_directo.py`, `grabador_chaturbate.py`, y `detector.json`
  vía `puente.py` sobre `Detector.trabajos`).
- `grabaciones.listar()` cruza `estado\union.json` con el listado: marca la parte y el grupo
  que se está uniendo, y **saltea el sondeo de duración** de ese archivo.
- `/api/grabaciones` devuelve una clave `union` nueva, con la unión en curso y el total por
  unir.
- En `vista_grabaciones.js`: un banner arriba de la grilla y un badge con barra en la
  tarjeta del grupo que se está armando, los dos reconciliados **in place** (sin
  `innerHTML` en el camino del refresco, por la regla de `CLAUDE.md`).
- El lugar de `progreso_union.py` en el empaquetado: el caso exacto de `cerrojo_union.py`
  — abierto en disco (lo importa `detectar_m3u8.py`, que no se compila) y en `OCULTOS` de
  `compilar.py` porque lo importan los dos grabadores, que sí van al binario.

**Parte B — la marca de sitio al quitar**

- `lista.quitar_modelo(nombre, sitio="")`: con `sitio="chaturbate"` saca el nombre de
  `@chaturbate=` **en la misma escritura** que el `pop` de la línea suelta. Con `sitio=""`
  (el default) se comporta exactamente como hoy.
- `lista.agregar_modelo(nombre, sitio="")`: con `sitio="stripchat"` saca el nombre de
  `@chaturbate=`, también en una sola escritura. Cierra el otro lado: una marca huérfana
  que ya exista en el archivo no se hereda.
- `/api/lista/quitar` lee `cuerpo.get("sitio")` y lo pasa; `/api/lista/agregar` pasa
  `sitio` también por la rama de stripchat.
- `vista_grabando.js`: `b.dataset.sitio = sitio` en todos los `[data-accion]`, calcando
  `vista_modelos.js:600-609`.
- `main.js:245` (Grabando) **y** `main.js:202` (Modelos y el modal de previa) mandan
  `sitio` en el POST de quitar.

### No entra

- Un `.part` o un temporal para el `.mp4` de la unión. Cambiar dónde escribe ffmpeg toca el
  camino de las 7 pasadas de reparación, `_hay_espacio`, `MARGEN_DISCO` y el nombre con
  sufijo `_2`, `_3`. El progreso resuelve el síntoma sin tocar nada de eso, y un `.part` es
  su propio spec.
- Filtrar los `*.raw.mp4` del listado (hoy aparecen como un grupo "sin convención" con el
  nombre de archivo crudo, `grabaciones.py:169-173`). Es un camino de respaldo que solo se
  usa si la tubería falla; se deja anotado, no se arregla acá.
- Cancelar una unión en curso desde la interfaz. Mostrarla no es poder pararla.
- Barra de progreso de la **grabación** en curso. Cuánto falta para que la modelo deje de
  emitir no es calculable.
- Progreso por WebSocket. La pestaña Grabaciones ya se refresca sola cada 4 s
  (`vista_grabaciones.js:449`) y eso alcanza; no se agrega sección al delta de estado ni se
  toca `servidor.py:_bucle_estado`.
- Mostrar el progreso en la pestaña Grabando. Ahí solo se arregla el número de `uniendo`
  que ya se mostraba.
- Tocar `RITMO_UNION`, `MAX_REPARACIONES`, `FRACCION_MINIMA`, `MARGEN_DISCO`,
  `MARGEN_ESCENARIO` ni el propio `_Freno` más allá de colgarle el informe de progreso.
  Son los números con los que se midió SPEC 13.
- Tocar `cerrojo_union.py`. Es el punto de observación natural de "hay una unión en curso",
  pero su contrato dice **no escribe en disco** (`cerrojo_union.py:10-11`) y romperlo
  convertiría un módulo de protocolo puro en un escritor de estado.
- Limpiar `@vpn=` al quitar. Una `@vpn=` huérfana no da grabación doble: rutea por el túnel
  sin que se haya pedido. Es la misma clase de sorpresa pero de distinto tamaño, y
  `quitar_modelo()` tiene un docstring que justifica el estado actual — cambiarlo merece su
  propia decisión.
- **Hallazgo aparte, encontrado al revisar esto:** `vivo_temporal.abrir()`
  (`vivo_temporal.py:56`) llama `lista.agregar_modelo(nombre)` sin ningún concepto de
  sitio, así que "ver en vivo" de una modelo de chaturbate que no se está grabando le
  agrega la línea suelta **sin** la marca `@chaturbate=` — y la previa queda intentando
  contra stripchat. Es la misma familia de bug (un call site que ignora `sitio`) y no entra
  acá: `vivo_temporal` tiene su propio invariante duro ("nunca quita de `grabar.txt` un
  nombre que no haya agregado él mismo", 4 call sites, `:56`/`:88`/`:102`/`:120`) y
  cambiarle la firma pide pensar qué pasa con la marca al cerrar la previa.

---

## Modelo de datos

### `estado\union.json` (nuevo)

Lo escribe quien está uniendo. Varios procesos pueden ser el escritor en distintos
momentos — el detector, el subproceso `--unir` del directo, el de chaturbate, y
`grabador_chaturbate.py` para la fase de mezcla — pero **nunca dos a la vez**, porque el
turno de SPEC 19 lo garantiza. Lo lee `nimbo\grabaciones.py`.

```jsonc
{
  "generado": 1794191646.5,          // latido: se reescribe siempre, aunque no cambie nada
  "activa": {
    "etiqueta": "catalina__23_110618628_1080p_h264_20261108-211345",
    "archivo":  "catalina__23_110618628_1080p_h264_20261108-211345.mp4",
    // basename del .mp4 destino. "" durante la fase "copia", que corre antes
    // de que el archivo exista. Es la clave con la que grabaciones.listar()
    // encuentra la parte que se esta armando.
    "sitio":    "stripchat",         // de NIMBO_UNION_SITIO; default "stripchat"
    "fase":     "remux",             // "copia" | "remux" | "mezcla"
    "intento":      0,               // pasada de reparacion en curso
    "intentos_max": 6,               // MAX_REPARACIONES
    "bytes_hechos": 1234567,
    "bytes_total":  5678901,         // de _plan() para "remux", de total_origen para "copia"
    "frenado":   true,               // _Freno.frenar: explica un ETA largo
    "ritmo_bps": 25165824,           // medido sobre VENTANA_RITMO, no teorico
    "inicio":       1794191000.0,
    "inicio_fase":  1794191200.0
  }
}
```

Sin unión en curso: `{"generado": <ahora>, "activa": null}`.

Reglas del archivo:

- Escritura **atómica** (`.part` + `os.replace`), como todo lo que Nimbo escribe.
- Se reescribe cada `INTERVALO_UNION` segundos mientras hay unión, y una vez más con
  `activa: null` en el `finally` de `unir_sesion()`.
- El lector aplica frescura: con `generado` de más de `FRESCURA_UNION` segundos se trata
  como si no hubiera unión. Es la red contra un proceso que murió a mitad y dejó el archivo
  con una unión fantasma para siempre.
- Vive en `DIR_ESTADO` (`detectar_m3u8.py:1197`), el mismo que ya se resuelve para leer
  `directo.json` / `detector.json` / `chaturbate.json`.

### Variable de entorno

```
NIMBO_UNION_SITIO=chaturbate
#   La pone quien lanza el subproceso --unir, igual que SPEC 19 pone
#   NIMBO_UNION_TURNO. Solo chaturbate necesita ponerla; ausente = "stripchat",
#   que es el detector y el camino directo.
```

### Constantes nuevas

En `nucleo\scripts\progreso_union.py`:

```python
# Cada cuanto se reescribe union.json. 2s es el mismo ritmo con el que ya
# late todo lo demas (INTERVALO_ESTADO de puente.py y de los dos grabadores)
# y el mismo con el que _Freno.paso() ya consulta hay_grabaciones(), asi que
# el informe viaja gratis dentro de una comprobacion que ya se hacia.
INTERVALO_UNION = 2.0
# Ventana sobre la que se promedia el ritmo para el ETA. Mas corta y el numero
# salta con cada hipo del disco; mas larga y tarda en reflejar que el freno se
# acaba de soltar (o de poner), que es justo cuando el ETA cambia de verdad.
VENTANA_RITMO = 10.0
```

En `nimbo\grabaciones.py`:

```python
# Duplicada a mano de estado_captura.FRESCURA_S por el mismo motivo que
# detectar_m3u8.FRESCURA_ESTADO: que el lector no dependa de nada del otro
# lado. Un poco mas larga que INTERVALO_UNION * 2 para tolerar un hipo de
# disco sin declarar muerta una union viva.
FRESCURA_UNION = 15.0
```

### Lo que se agrega al payload de `/api/grabaciones`

```jsonc
{
  "grupos": [ /* ... como hoy, mas el campo "uniendo" en el grupo y en la parte */ ],
  "resumen": { "archivos": 0, "bytes": 0 },
  "union": {
    "activa": { /* el bloque "activa" de union.json, o null */ },
    "por_unir": 3          // la en vuelo + las encoladas, sumando los tres publicadores
  }
}
```

En el grupo y en la parte que se está armando:

```jsonc
{ "archivo": "...mp4", "tamano_bytes": 123, "duracion_seg": null, "uniendo": true }
```

`duracion_seg` queda `null` **sin haber corrido ffmpeg**: la parte marcada `uniendo` se
saltea el sondeo.

### `uniendo`, con el significado arreglado

Mismo nombre de clave en los tres JSON de estado, para no romper a
`vista_grabando.js:298`, pero ahora significa **trabajo pendiente total de ese proceso**:
la unión en vuelo más las encoladas.

| Archivo | Lo escribe | Cómo se calcula |
|---|---|---|
| `estado\directo.json` | `grabador_directo.py:1389` | `cola_union.qsize() + (1 if en vuelo)` |
| `estado\chaturbate.json` | `grabador_chaturbate.py:1161` | ídem |
| `estado\detector.json` | `puente.py` (clave nueva) | `det.trabajos.qsize() + (1 if en vuelo)` |

`estado_captura.fusionar()` ya propaga `uniendo` para directo y chaturbate
(`estado_captura.py:123` y `:131`); se suma la del detector.

---

## Plan de implementación

Cada paso deja el sistema funcionando. Los pasos 1 a 8 son la parte A; 9 a 11 la parte B;
el 12 y 13 son documentación y empaquetado.

1. **`nucleo\scripts\progreso_union.py`** (nuevo). Solo stdlib (`os`, `json`, `time`,
   `threading`). Expone:

   ```python
   ARCHIVO = "union.json"
   INTERVALO_UNION = 2.0
   VENTANA_RITMO = 10.0

   class Informe:
       """Publica el progreso de UNA union en estado\\union.json.

       Un solo registro alcanza: SPEC 19 garantiza una union a la vez en toda
       la instalacion. Si escribir falla (disco lleno, permisos), se traga el
       error y se sigue uniendo: esto es telemetria, no parte de la union.
       """
       def __init__(self, dir_estado, etiqueta, sitio="", archivo=""): ...
       def fase(self, nombre, bytes_total, intento=0, intentos_max=0): ...
       def avance(self, n, frenado=False): ...   # acumula y escribe cada INTERVALO_UNION
       def cerrar(self): ...                     # escribe activa: null

   def leer(dir_estado, frescura=15.0) -> dict | None:
       """El bloque "activa" fresco, o None. Para quien quiera leerlo del
       lado del nucleo; nimbo\\grabaciones.py lee el JSON por su cuenta."""
   ```

   `avance()` lleva el acumulado y una ventana de muestras `(t, bytes)` de
   `VENTANA_RITMO` segundos para el `ritmo_bps`.

   **Puerta:** `python -c "import progreso_union"` no construye ni arranca nada; un
   `Informe` de juguete deja un `union.json` bien formado y lo limpia al cerrar.

2. **`nucleo\scripts\detectar_m3u8.py` — colgar el informe de `_Freno`.** `_Freno.paso(n)`
   (`:1280`) ya corre en los dos bucles de disco (`_alimentar:1325` y
   `_preparar_escenario:1431`) y ya tiene un acelerador de 2 s para consultar
   `hay_grabaciones()`. Se le agrega un `informe=None` opcional y un acumulado propio (el
   `self.enviado` actual se resetea cuando el freno se suelta, así que no sirve), y dentro
   del mismo bloque acelerado llama `informe.avance(acumulado, self.frenar)`.

   **Puerta:** nada cambia todavía — sin `informe` el freno se comporta igual.

3. **`detectar_m3u8.py:unir_sesion()` — abrir, marcar fases y cerrar.** Crear el `Informe`
   al entrar (con `etiqueta`, `os.path.basename(final)` y `NIMBO_UNION_SITIO`), llamar
   `informe.fase("copia", total_origen)` antes de `_preparar_escenario` (`:1526`) y
   `informe.fase("remux", total, intento, MAX_REPARACIONES)` dentro del `for intento`
   (`:1534`, también en cada reintento, que es donde el `.mp4` se borra y vuelve a crecer),
   pasar el `informe` a `_preparar_escenario` y `_alimentar`, y `informe.cerrar()` en el
   `finally` que ya existe (`:1600`).

   **Puerta:** `python nucleo\scripts\detectar_m3u8.py --unir <sesión> --forzar` a mano
   deja `estado\union.json` latiendo cada 2 s con `fase`, `%` y `ritmo_bps` creciendo, y
   `activa: null` al terminar. Los tres caminos de unión lo heredan de una, porque los tres
   pasan por acá.

4. **`NIMBO_UNION_SITIO` y la fase `mezcla`.** `grabador_chaturbate.py:_unir_doble`
   (`:347`) agrega la variable al `env` del hijo (ya pasa `env=dict(os.environ)`, `:264`) y
   publica la fase `mezcla` alrededor de `_mezclar_audio` con su propio `Informe` — es la
   única fase que no pasa por `detectar_m3u8.py`.

   **Puerta:** una unión de chaturbate muestra `sitio: "chaturbate"` y las tres fases en
   orden, sin huecos de `activa: null` en el medio.

5. **`uniendo` que cuenta la en vuelo.** Un flag por grabador alrededor del trabajo que
   sacó de la cola (`grabador_directo.py:_bucle_union:1234`,
   `grabador_chaturbate.py:_bucle_union:1038`), sumado en `_publicar_estado`
   (`:1389` / `:1161`). Y la clave `uniendo` nueva en `detector.json`, que `puente.py`
   calcula desde fuera con `getattr(det, "trabajos", None)` y un flag que pone
   `_bucle_finalizador` (`:2826`) — mismo patrón `getattr` con el que ya lee `det.tunel`.
   `estado_captura.fusionar()` suma la del detector a las dos que ya propaga.

   Nota: las uniones de `recuperar_pendientes()` (`:1786`) no pasan por `trabajos`, así que
   no entran en ese conteo. Se documenta; aparecen igual en `union.json` cuando están en
   vuelo.

   **Puerta:** con una sola unión corriendo y nada encolado, `uniendo` vale `1`. Hoy vale `0`.

6. **`nimbo\grabaciones.py` — cruzar el listado con `union.json`.** `listar()` ya recibe
   `dir_estado` (`:129`), así que lee el JSON por su cuenta (sin importar nada del núcleo,
   mismo criterio que `nimbo\mp4.py`/`vivo.py`), aplica `FRESCURA_UNION`, y:

   - si `activa.archivo` coincide con el nombre de una parte, la marca `"uniendo": true` y
     **saltea `_duracion_de()`** para ella (`:124`), con lo que se corta la tormenta de
     `ffmpeg -i` contra un archivo sin `moov`;
   - propaga `"uniendo": true` al grupo que contiene esa parte;
   - devuelve el bloque `activa` en la clave `union` del resultado.

   **Puerta:** durante una unión, `GET /api/grabaciones` trae `union.activa` y el grupo
   marcado, y el Administrador de tareas no muestra ningún `ffmpeg` de sondeo nuevo cada
   4 s. Antes de este paso había uno por refresco.

7. **`nimbo\servidor.py:1287`** — sumarle `por_unir` al resultado de `listar()`, desde
   `app.estado` (la caché de `_bucle_estado`, que ya tiene `detector.directo.uniendo` y
   `detector.chaturbate.uniendo` fusionados). Sin tocar `_bucle_estado` ni el delta.

8. **`nimbo\web\js\vista_grabaciones.js`** — el banner y el badge:

   - Banner arriba de la grilla, creado una vez y actualizado in place: `uniendo
     <modelo> — <fase> 43% (1,2 de 2,8 GB) · faltan ~2 min · 3 por unir`, más `frenado`
     cuando `activa.frenado`, más `reintento 2 de 7` cuando `activa.intento > 0`. Con
     `activa: null` el banner lleva `.oculta` (no se saca del DOM).
   - En la tarjeta del grupo marcado: badge con la fase y una barra de progreso. La
     duración deja de decir `"calculando..."` y pasa a `"uniendo"` —
     `textoDuracionGrupo()` (`:62`) recibe el flag.
   - ETA en JS: `(bytes_total - bytes_hechos) / ritmo_bps`, formateado con
     `formato.js:formatoHMS`, siempre con `~`. Sin `ritmo_bps` (los primeros segundos de
     una fase) se muestra el `%` sin ETA.
   - Todo reconciliado in place, sin `innerHTML` en el camino del refresco: es la regla de
     `CLAUDE.md` que ya cumplen `vista_grabando.js` y `vista_modelos.js`.

   **Puerta:** con una unión en curso, la pestaña Grabaciones muestra el banner y la
   tarjeta marcada, y el `%` avanza solo cada 4 s sin tocar nada.

9. **`nimbo\lista.py` — las dos firmas nuevas.** `quitar_modelo(nombre, sitio="")` y
   `agregar_modelo(nombre, sitio="")`, cada una con **un solo** `_escribir()` bajo el
   `self._lock` que ya toman: el `pop`/`insert` de la línea suelta y el `discard` del set de
   `@chaturbate=` en la misma pasada. Con `sitio=""` (el default) ninguna de las dos toca
   ninguna directiva — es lo que preserva intacto a `vivo_temporal.py`, que llama a las dos
   sin concepto de sitio y tiene su propio invariante duro.

   Guard que conviene: si `@chaturbate=` no existía en el archivo, no crearla vacía solo
   para desmarcar (hoy `marcar_sitio` la crea, `lista.py:242`). Una directiva que aparece
   de la nada en un `grabar.txt` que nunca la tuvo es ruido en un archivo que el usuario
   edita a mano.

   **Puerta:** un `grabar.txt` con `@chaturbate=ana,beti` y las dos líneas sueltas; quitar
   `ana` con `sitio="chaturbate"` deja `@chaturbate=beti` y **una sola** reescritura del
   archivo. Quitarla con `sitio=""` deja la directiva intacta, como hoy.

10. **`nimbo\servidor.py:1489-1498`** — `/api/lista/quitar` pasa
    `cuerpo.get("sitio") or ""` (ese `or ""`, y no `or "stripchat"`, es a propósito: un
    cliente viejo que no manda sitio tiene que seguir comportándose como hoy y no tocar
    marcas). `/api/lista/agregar` pasa `sitio` también por la rama de stripchat, que hoy
    llama `agregar_modelo(nombre)` a secas.

11. **El frontend de la parte B.**

    - `vista_grabando.js:135` → el bloque de dos asignaciones de
      `vista_modelos.js:600-609` (`b.dataset.nombre` + `b.dataset.sitio`), con `sitio`
      pasado a `crearTarjeta` o seteado en `actualizarTarjeta` al lado de `:184` (más
      conservador: la tarjeta se reutiliza por nombre en el `Map` de `:192` y el sitio
      podría cambiar si el usuario reedita `@chaturbate=` a mano).
    - `main.js:245` y `main.js:202` → `{ nombre, sitio }`. En los dos casos `sitio` ya está
      a mano: en `:198` como variable, y en Grabando como `dataset.sitio` del botón, con el
      mismo `|| "stripchat"` que `ver-vivo` usa seis líneas arriba (`:239`).

    **Puerta:** marcar una modelo como chaturbate, cerrarla desde Grabando, y
    `@chaturbate=` ya no la tiene. Volver a agregarla desde Modelos como stripchat y
    sigue sin tenerla.

12. **`CLAUDE.md`** — la fila de `estado\union.json` en "Quién escribe qué" (con la nota de
    que tiene varios escritores posibles pero nunca dos a la vez, por el turno de SPEC 19),
    `progreso_union.py` en la lista de módulos seguros de importar y en la nota de
    empaquetado al lado de `cerrojo_union.py`, el significado nuevo de `uniendo`, y la
    corrección de que `quitar_modelo` sí toca `@chaturbate=` (su docstring actual dice lo
    contrario para `@vpn=` y hay que dejar claro que son dos criterios distintos, no un
    descuido).

13. **Empaquetado** — `progreso_union.py` es el caso de `cerrojo_union.py`: fuera de
    `construir.py:_NUCLEO_FUERA_COMPILADO`, dentro de `compilar.py:_ABIERTOS`, y además en
    `compilar.py:OCULTOS` porque lo importan los dos grabadores, que sí entran al binario y
    Nuitka no los descubre solo.

    **Puerta:** `python empaquetado\compilar.py --comprobar` nombra `progreso_union.py`
    entre los que quedan abiertos y no se queja.

---

## Criterios de aceptación

**Parte A**

- [ ] `python -c "import progreso_union"` no arranca ni construye nada, y el módulo no
      importa nada de `nimbo\`.
- [ ] Durante un `--unir --forzar` a mano, `estado\union.json` se reescribe cada ~2 s y
      `bytes_hechos` crece de forma monótona dentro de una misma fase.
- [ ] `activa.fase` pasa por `copia` y `remux` en ese orden cuando `%TEMP%` está en otro
      disco que `streams\`, y solo por `remux` cuando está en el mismo.
- [ ] Una unión de chaturbate pasa por `remux` (video), `remux` (audio) y `mezcla`, con
      `sitio: "chaturbate"` en las tres.
- [ ] Al terminar la unión, `estado\union.json` queda con `activa: null`.
- [ ] Matar el proceso que une a lo bruto deja un `union.json` viejo, y la pestaña
      Grabaciones **no** muestra banner (el lector lo descarta por `FRESCURA_UNION`).
- [ ] Con una unión corriendo y la cola vacía, `uniendo` vale `1` en el JSON del grabador
      que la está haciendo. Antes de este spec valía `0`.
- [ ] La pestaña Grabaciones, con una unión en curso: el banner nombra la modelo, la fase,
      el `%`, los MB y un ETA con `~`; la tarjeta de ese grupo tiene badge y barra; y su
      duración dice `uniendo`, no `calculando...`.
- [ ] Con el freno puesto (hay emisiones grabándose), el banner muestra `frenado` y el ETA
      es coherente con ~25 MB/s. Al soltarse el freno, el ETA baja dentro de
      `VENTANA_RITMO`.
- [ ] Una unión que entra en reparación (`intento > 0`): el banner dice `reintento N de 7`
      y el `%` vuelve a empezar sin que el banner desaparezca ni se duplique.
- [ ] Durante toda una unión con la pestaña Grabaciones abierta, no se lanza **ningún**
      `ffmpeg` de sondeo de duración contra el `.mp4` que se está armando. Antes de este
      spec se lanzaba uno por refresco de 4 s.
- [ ] Al terminar la unión, la duración de ese grupo se calcula una vez (ya con `moov`) y
      se cachea en `estado\duraciones.json` como siempre.
- [ ] Sin ninguna unión en curso, el payload de `/api/grabaciones` trae
      `union.activa == null` y la grilla se ve exactamente como antes del spec.
- [ ] `python empaquetado\compilar.py --comprobar` nombra `progreso_union.py` entre los que
      quedan abiertos en disco, y un paquete compilado lo tiene como `.py` en
      `nucleo\scripts\` sin que `auditar()` aborte.

**Parte B**

- [ ] Una modelo marcada `@chaturbate=` cerrada con "Parar / quitar" desde **Grabando**:
      desaparece de la línea suelta **y** de `@chaturbate=`, en una sola reescritura de
      `grabar.txt`.
- [ ] Lo mismo destildando el toggle "grabar" desde **Modelos** (el bug gemelo de
      `main.js:202`).
- [ ] Quitar una modelo de stripchat no toca `@chaturbate=` ni agrega la directiva si no
      existía.
- [ ] Con una marca `@chaturbate=ana` huérfana ya en el archivo, agregar `ana` desde
      Modelos como stripchat la deja **sin** la marca, y `ana` se graba por stripchat.
- [ ] Abrir y cerrar "ver en vivo" de una modelo marcada `@chaturbate=` que **ya estaba**
      en la lista no toca `@chaturbate=` (`vivo_temporal` no es dueño de esa línea y sigue
      llamando sin `sitio`).
- [ ] `grabar.txt` conserva byte a byte comentarios, orden y notas al final después de
      cualquiera de las operaciones de arriba.

---

## Decisiones

- **Sí:** un `estado\union.json` nuevo, con **un** registro. Rompe a medias el "un escritor
  por archivo" de la tabla de `CLAUDE.md` (hay cuatro escritores posibles), pero nunca dos
  a la vez: el turno de SPEC 19 es lo que lo hace seguro. Meterlo en
  `directo.json`/`chaturbate.json` sí habría roto el invariante de verdad, porque el
  subproceso `--unir` tendría que escribir el JSON de su propio padre; y el detector no
  tiene dónde, porque `detector.json` es de `puente.py`.
- **No:** un `union.json` por sitio. Mantendría un escritor por archivo, pero son tres
  archivos para algo que por SPEC 19 es uno solo a la vez, y la interfaz tendría que
  fusionarlos para mostrar un único banner.
- **Sí:** publicar desde `unir_sesion()`, en `detectar_m3u8.py`. Es el único punto por el
  que pasan los tres caminos de unión, así que un solo lugar instrumentado cubre al
  detector, al directo y a chaturbate. La fase `mezcla` es la única excepción y se publica
  aparte.
- **No:** publicar desde `cerrojo_union.py`, que es donde el turno ya sabe exactamente
  cuándo arranca y termina cada unión y con qué etiqueta. Su contrato dice "no escribe en
  disco" (`:10-11`), y sabe *que* hay una unión pero no *cuánto le falta* — el total de
  bytes solo lo conoce `_plan()`.
- **Sí:** colgar el informe de `_Freno.paso()`. Ya corre en los dos bucles de disco y ya
  tiene un acelerador de 2 s para consultar `hay_grabaciones()`, así que el informe viaja
  gratis dentro de una comprobación que ya se hacía. Un hilo aparte que mirara
  `os.path.getsize(final)` habría sido otro lector del disco que la unión está saturando.
- **Sí:** ETA medida sobre `VENTANA_RITMO`, no teórica a `RITMO_UNION`. El freno se pone y
  se suelta solo según si hay emisiones grabándose, así que una ETA calculada a 25 MB/s
  miente por un factor grande justo cuando nadie graba (que es la mitad del tiempo).
- **Sí:** mostrar `frenado` y `reintento N de 7` al lado del ETA. Son las dos razones por
  las que un número razonable se vuelve raro: sin ellas, un ETA que se duplica o un `%` que
  vuelve a cero parecen un bug de la interfaz.
- **Sí:** saltear el sondeo de duración del archivo que se está uniendo. No es una
  optimización cosmética: hoy, con la pestaña abierta, es un `ffmpeg -i` cada 4 s contra el
  mismo disco del que la unión está leyendo frenada a 25 MB/s — compite con exactamente lo
  que SPEC 13 protege, y nunca puede dar un resultado porque el `moov` no está escrito
  todavía (`-movflags +faststart`).
- **No:** escribir el `.mp4` a un temporal y hacer `os.replace` al final. Es la solución
  "limpia" al síntoma (el archivo a medias no aparecería en la grilla), pero toca el camino
  de las 7 pasadas de reparación, `_hay_espacio`, `MARGEN_DISCO` y el desambiguado con
  sufijo `_2`/`_3`, y además deja al usuario sin ninguna señal durante la hora que tarda.
  Mostrar el progreso es más información, no menos.
- **No:** progreso por WebSocket. La pestaña ya se refresca cada 4 s cuando está abierta y
  se apaga al salir (`entrarAGrabaciones`/`salirDeGrabaciones`), que es el mismo criterio
  con el que `_bucle_sitio` solo corre si alguien está suscrito. Agregar una sección al
  delta sería un latido más para algo que nadie mira la mayor parte del tiempo.
- **Sí:** arreglar `uniendo` para que cuente la en vuelo, conservando el nombre de la clave.
  Un contador que dice `0` mientras hay una unión corriendo es peor que no tenerlo, y
  cambiarle el nombre obligaría a tocar el consumidor que ya existe
  (`vista_grabando.js:298`) sin ganar nada.
- **Sí:** `sitio=""` como default de `quitar_modelo` y `agregar_modelo`, con el significado
  "no toques ninguna directiva". Es lo que deja intacto a `vivo_temporal.py`, que llama a
  las dos desde cuatro lugares sin ningún concepto de sitio y cuyo invariante es no tocar
  lo que no es suyo. El default `"stripchat"` habría hecho que una previa temporal
  desmarcara `@chaturbate=` al cerrarse.
- **Sí:** limpiar la marca también en `agregar_modelo`. Solo arreglar `quitar` evita
  huérfanas nuevas, pero una huérfana que ya está en el archivo hoy sigue dando grabación
  doble al re-agregar — y es invisible, porque `leer()` solo mira los nombres de
  `@chaturbate=` que están en `seleccion` (`lista.py:114-115`).
- **Sí:** una sola escritura de `grabar.txt` por operación. Llamar `quitar_modelo()` y
  después `marcar_sitio()` habría sido dos líneas de código y cero cambios de firma, pero
  son dos reescrituras de un archivo que el detector, el supervisor, el directo y
  chaturbate releen cada 1-2 s: hay una ventana real en la que se ve el estado intermedio.
- **No:** limpiar `@vpn=` al quitar. Una `@vpn=` huérfana rutea por el túnel sin que se
  haya pedido, que es molesto pero no es grabación doble, y `quitar_modelo()` tiene un
  docstring que argumenta a favor del estado actual. Cambiar eso es una decisión propia, no
  un arreglo de paso.
- **No:** filtrar los `*.raw.mp4` del listado. Es un wart real (aparecen como grupo "sin
  convención") pero de un camino de respaldo que solo corre si la tubería de ffmpeg falla.
  Se deja anotado.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Escribir `union.json` cada 2 s desde el proceso que une agrega escrituras al disco que el freno está protegiendo | Es un JSON de menos de 1 KB cada 2 s, el mismo costo que `puente.py` y los dos grabadores ya pagan como latido ("no cuesta nada medible", `puente.py:337`). Y va a `estado\`, que vive en la raíz de la instalación, no necesariamente en el disco de los segmentos |
| El informe falla (disco lleno, permisos) y rompe una unión que iba bien | `Informe` traga todos los `OSError`: es telemetría, no parte de la unión. Sin `union.json` la interfaz se ve como antes del spec y el `.mp4` sale igual |
| Un proceso muere a mitad y deja `union.json` con una unión fantasma para siempre | `FRESCURA_UNION = 15.0` en el lector: un `generado` viejo se trata como "no hay unión". Mismo criterio pegajoso-pero-con-vencimiento que `estado_captura.FRESCURA_S` |
| Dos uniones a la vez se pisan el `union.json` y el banner salta entre las dos | El turno de SPEC 19 lo impide. Si ese turno degradó a "sin turno" (`ctypes` falló, no es Windows), el banner puede saltar — es cosmético, no corrompe nada, y el aviso de degradación de `cerrojo_union.py` ya queda en el log |
| `activa.archivo` no matchea ninguna parte del listado y el badge no aparece nunca | Durante la fase `copia` eso es **lo esperado** (el `.mp4` todavía no existe) y para eso está el banner. En `remux` el nombre sale de `os.path.basename(final)`, el mismo string con el que ffmpeg escribe, así que no hay normalización que pueda desalinearlos |
| Cambiar la firma de `quitar_modelo` rompe a `vivo_temporal.py`, que la llama desde 4 lugares, y una previa temporal termina desmarcando `@chaturbate=` de una modelo que el usuario sí quería | `sitio=""` por defecto = no tocar ninguna directiva, o sea el comportamiento actual byte a byte. Hay un criterio de aceptación dedicado a eso |
| Un cliente viejo (pestaña sin recargar tras la actualización) postea `/api/lista/quitar` sin `sitio` y la marca queda huérfana igual | `or ""` en el servidor: se comporta como hoy, no peor. Y el otro lado del fix (`agregar_modelo`) limpia la huérfana cuando el nombre vuelva |
| El banner y el badge se repintan con `innerHTML` y vuelve el congelado de SPEC 01 | Reconciliación in place, con el nodo creado una vez y `.oculta` para prender/apagar. Es la regla de `CLAUDE.md` y ya hay dos vistas que la cumplen para calcar |

---

## Lo que **no** está en este spec

- Un `.part` o un temporal para el `.mp4` de la unión.
- Filtrar los `*.raw.mp4` del listado de Grabaciones.
- Cancelar una unión en curso desde la interfaz.
- Progreso de la unión por WebSocket, o en la pestaña Grabando.
- Estimar cuánto falta para que termine una **grabación** en curso.
- Recalibrar `RITMO_UNION`, `MAX_REPARACIONES` o cualquier constante de SPEC 13.
- Que `cerrojo_union.py` escriba estado.
- Limpiar `@vpn=` al quitar un nombre.
- Darle concepto de sitio a `vivo_temporal.py`, que hoy agrega la línea suelta de una
  modelo de chaturbate **sin** la marca `@chaturbate=`.

Cada una, si entra, va en su propio spec.
