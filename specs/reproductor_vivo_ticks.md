# Reproductor en vivo: los ticks que quedan

Especificación de implementación. Estado: **el problema está medido contra
datos reales de una corrida en vivo** (ver §2); lo que falta es elegir entre
dos arreglos y hacerlo.

Punto de partida: commit `17c78dc` ("Arreglar el salto permanente del
reproductor en vivo y el `--unir` que unia sesiones vivas"). Todo lo de este
spec es **lo que sigue después** de ese commit, no un rehacer.

---

## 1. Por qué

Antes de `17c78dc` la previa en vivo se veía con saltos groseros y tramos
repetidos: `vivo.py:listar` devolvía `salto=true` en **cada** tick, el cliente
pedía `desde=None` siempre, `_arranque()` le devolvía la misma ventana y el
reproductor reappendeaba el mismo tramo cada 500 ms. Eso **ya está arreglado y
verificado** (§2.1).

Lo que quedó: **micro-ticks regulares**, reportados en vivo como "pequeños
ticks, pero no molesta mucho". No son pérdida de datos ni duplicación — las
dos cosas están descartadas con medición. Son un efecto de
`SourceBuffer.mode = "sequence"` sobre fragmentos CMAF partidos en partes de
0,5 s.

---

## 2. Evidencia medida (24/10/2026, 15:51–16:13, 4-5 modelos por camino directo)

Todo lo de esta sección está **verificado**, no inferido. Los scripts para
reproducirlo están en el apéndice.

### 2.1 Lo que ya NO pasa

Sondeo de 70 s contra `/api/vivo/segmentos` imitando exactamente al cliente
(mismo `limite=24`, `atras=3`, misma cadena de cursores), sobre
`catalina__23_110618628/1080p_h264_20261024-155115`:

```
ticks=137  entregados=152  duplicados=0  salto=0
aparecieron en disco durante el sondeo: 140
EN DISCO Y NUNCA ENTREGADOS (dentro de la ventana): 0
```

Ni un fragmento repetido, ni uno salteado, `salto` nunca. El camino
servidor↔cursor está sano.

### 2.2 Los fragmentos están impecables

240 fragmentos consecutivos (120 s de media) de la misma sesión:

```
discontinuidades de tfdt: 0
pista 1 (video, timescale 90000): 240 fragmentos, 120.0000 s
pista 2 (audio, timescale 48000): 240 fragmentos, 120.0000 s
desfasaje audio-video acumulado: 0.0000 s
```

Cero discontinuidades y los totales **coinciden exactamente**.

### 2.3 Pero cada parte NO cuadra, y el encoder lo compensa a futuro

Duraciones por parte de 0,5 s:

| pista | unidades | segundos |
|---|---|---|
| video | `45000` siempre | 0,5000 exacto |
| audio | `23552` ó `24576` | 0,4907 ó 0,5120 |

Por segmento de ~2 s (4 partes), el delta audio−video oscila y **se cancela**:

```
seq=2614  video=2.0000 s  audio=2.0053 s  delta=+5.3 ms
seq=2615  video=2.0000 s  audio=2.0053 s  delta=+5.3 ms
seq=2616  video=2.0000 s  audio=1.9840 s  delta=-16.0 ms   <-- compensa
seq=2617  video=2.0000 s  audio=2.0053 s  delta=+5.3 ms
```

El encoder mete cada tanto un fragmento de audio corto para devolver la cuenta
a cero. Esa compensación está diseñada para funcionar **sobre la línea de
tiempo absoluta** (los `tfdt`).

### 2.4 La causa de los ticks

`mode = "sequence"` recalcula el `timestampOffset` en **cada
`appendBuffer`**, tomando el final de presentación más alto de las pistas del
append. Como dentro de cada parte el video termina en 0,5000 s y el audio en
0,4907 ó 0,5120 s, cada append realinea sobre una pista y le deja a la otra
unos milisegundos de hueco o solape. A ~2 appends por segundo eso son **~2
realineaciones por segundo**, y la compensación del encoder **nunca llega a
cancelarse** porque cada append arranca de cero.

> **Qué está medido y qué no.** El desacople audio/video por parte y la
> contigüidad de los `tfdt` están medidos (§2.2, §2.3). Que los ticks salgan
> de la realineación de `sequence` es la explicación mejor sustentada, pero
> **no se instrumentó Chrome** para verlo. La puerta 1 de §7 es exactamente
> esa verificación que falta.

### 2.5 El comentario que justifica `sequence` ya no es cierto

`nimbo\web\js\reproductor_vivo.js:44-50` dice:

> *"esos timestamps vienen sucios a proposito (un reinicio de emision los
> vuelve a cero, una parte perdida salta hacia adelante)"*

La primera mitad **no pasa** dentro de una sesión: 0 discontinuidades en 240
fragmentos (§2.2). Un reinicio de emisión no ensucia la sesión: crea una
**carpeta nueva** (init nuevo → `_cerrar_variante` + sesión nueva), que el
cliente remonta por `sincronizarVivo()`. Se vio 8 veces en 22 minutos de
corrida. La segunda mitad sigue siendo cierta: una parte perdida deja un salto
hacia adelante — y eso es justo lo que `segments` convierte en un hueco real,
que hay que saber saltar (§4.2).

### 2.6 Salud general de la corrida (contexto, no es el problema)

- **Pérdida real 0,24%**: 7 huecos de secuencia en 2.908 segmentos ≈ 14 s
  perdidos en ~90 minutos-modelo de grabación.
- **Sin correlación con concurrencia**: 0,22% a 4 modelos, 0,32% a 5. Cruzando
  los fallos de todo el día contra modelos simultáneas, los timeouts/1000 van
  de 14,7 (1 modelo) a 19,1 (12) sin tendencia. **No hay experimento de
  concurrencia que valga la pena**: tocar `POOL_HILOS` o el `timeout` mediría
  ruido del CDN. Los 404 son ráfagas por modelo (un bucket de 10 min tuvo 447
  fallos, todos de dos modelos con el encoder reconectando).
- **9 fallos de descarga en 21 min** (5 timeouts, 4× 404).
- **El `[UNIR]` honesto de `17c78dc` pasó la prueba**: 7 uniones, todas con
  modelo + resumen del hijo, **cero `NO se unio`**.

---

## 3. Decisiones tomadas

1. **Se arregla la realineación, no los datos.** El grabador no se toca: los
   fragmentos que escribe son correctos (§2.2).
2. **Se hace A primero, B queda como arreglo de fondo.** A es de pocas líneas
   y comparable en vivo sobre el mismo modelo; B cambia el contrato de
   reproducción y necesita su propia tanda de pruebas.
3. **No se parte el grabador en partes más grandes.** Partir los multi-moof en
   `_partN` es lo que permite que el `Reencadenador` de la unión no los
   confunda con huecos (`mouflon.partir_fragmentos`); el reproductor se
   adapta, no al revés.
4. **La rotación de sesión por reconexión de encoder se trata aparte** (§5):
   es el otro artefacto visible, pero es un problema de continuidad entre
   sesiones, no de timestamps.

---

## 4. Los dos arreglos

### 4.1 A — appendear el grupo entero (recomendado para empezar)

Hoy `_tick()` baja cada parte y hace un `_encolarAppend(bytes)` por parte. En
cambio: agrupar por `(epoch, secuencia)` —el servidor ya manda `epoch`,
`secuencia` y `parte` en cada entrada de `/api/vivo/segmentos`— bajar las
partes del grupo, **concatenarlas** y hacer **un solo** `appendBuffer`.

- Las realineaciones bajan de ~2/s a ~0,5/s (una por borde de segmento).
- Dentro del append, Chrome respeta la cadena `tfdt` interna, así que la
  compensación del encoder **sí** se cancela dentro del segmento de 2 s.
- `ultimaClave` pasa a avanzar por grupo; mantener la regla de no retroceder.
- Un grupo incompleto en el borde vivo (las partes llegan en ráfaga, pero el
  tick puede caer en el medio) **no se appendea**: se espera al tick
  siguiente. Eso es un cambio de comportamiento a propósito y es lo que hace
  que el arreglo sirva — hoy esas partes entran de a una.
- `vivo.py:listar` ya garantiza no cortar un grupo salvo en el borde vivo
  (`17c78dc`), así que del lado del servidor no hay nada que cambiar.

Archivos: `nimbo\web\js\reproductor_vivo.js` solamente.

Alternativa descartada: un endpoint `/api/vivo/grupo` que concatene del lado
del servidor. Ahorra N-1 pedidos por grupo pero agrega superficie HTTP y
rompe el `Accept-Ranges: none` por archivo de `_servir_bytes`; el cliente ya
tiene todo lo que necesita para agrupar.

### 4.2 B — `mode = "segments"` (el arreglo de fondo)

Dejar que el navegador ubique cada fragmento por su timestamp real. Desaparece
toda realineación: el audio y el video caen donde el encoder los puso y su
compensación funciona como fue diseñada.

Lo que hay que resolver, en orden de riesgo:

1. **Sembrar `video.currentTime`.** En `segments` el timeline arranca donde
   diga el primer `tfdt`, que es absoluto y grande: el medido era `477180006`
   con timescale 90000 = **5302 s ≈ 88 min**. Sin
   `video.currentTime = buffered.start(0)` no se ve nada — el propio
   comentario del archivo avisa que es el error más común de MSE.
2. **Saltar huecos.** Una parte perdida deja un hueco real y Chrome **se
   clava** en el borde del rango. Hace falta un vigilante: si el video está
   detenido por falta de datos y existe un rango posterior en `buffered`,
   saltar a su `start`. Hoy eso no existe porque `sequence` pegaba todo sin
   huecos (ocultando la pérdida).
3. **`_recortar` y `_vigilarDeriva`** ya trabajan sobre `buffered`, pero
   asumen **un solo** rango (`buffered.start(0)` / `end(0)`). Con huecos puede
   haber varios: usar el último rango para el borde vivo y el primero para el
   recorte.
4. **`alVivo()`** ídem: `buffered.end(buffered.length - 1)`.

Archivos: `nimbo\web\js\reproductor_vivo.js`. Es el mismo archivo que A, pero
A **no** es prerrequisito de B: si B sale bien, A sobra por el lado de los
timestamps (conviene igual appendear por grupo, por cantidad de pedidos).

---

## 5. Secundario: la rotación de sesión corta la previa

No es un tick, es un corte de varios segundos, y en la corrida medida fue el
artefacto más visible:

- `lolykush` rotó de sesión **4 veces en 15 min** (una sesión duró 21 s).
- `scofty` cambió `1440p → 540p → 720p → off` en 7 min.
- `aritoo_` tuvo **un minuto entero** de `media playlist dio 404` (16:10:40 →
  16:11:42) antes de reconectar el encoder: durante ese minuto no bajó nada,
  así que la previa se congela y después salta.

Cada rotación crea una carpeta nueva y el cliente la remonta desde cero
(`montar()` → nuevo `MediaSource`, `init`, `_arranque` 3 grupos atrás). El
corte es inevitable en parte —es un `init` distinto, no se puede appendear al
mismo `SourceBuffer`— pero se puede acortar:

- Hoy la rotación se detecta recién cuando `estado\directo.json` la publica
  (cada 2 s) y el delta llega por WebSocket. `vivo.py:listar` ya devuelve
  `vivo: false` cuando no hay escrituras hace `UMBRAL_VIVO_S = 10` s: el
  cliente podría pedir la sesión hermana más nueva en cuanto `vivo` se cae,
  sin esperar el delta.
- Si el `init` nuevo fuera **byte a byte igual** al anterior, se podría seguir
  en el mismo `SourceBuffer` sin remontar. Medir primero cuántas rotaciones
  cumplen eso antes de escribir una línea: en la corrida medida los nombres
  eran distintos (`init_152586700_init_SxTndQCsYWflH0Vz.mp4` →
  `..._orCcbM0PoARofbDJ.mp4`), pero el nombre no prueba que el contenido
  difiera.

Prioridad: después de A. Es más molesto que los ticks pero también más
invasivo.

---

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| A agrega latencia: hay que esperar el grupo completo | Son 4 partes escritas en ráfaga por el mismo hilo del `Escritor`; el margen es `MARGEN_VIVO_S = 3.0` s, holgado contra un grupo de 2 s |
| B se clava en el primer hueco si falta el vigilante | El vigilante de huecos es parte de B, no un extra. Sin él, B es peor que hoy |
| B expone la pérdida real que `sequence` tapaba | Es lo correcto: 0,24% medido. No es regresión, es dejar de mentir |
| Tocar `_tick()` otra vez puede reintroducir duplicados | El sondeo del apéndice mide duplicados y salteos de forma directa; correrlo antes y después |

---

## 7. Orden de trabajo

1. **Verificación que falta: instrumentar el navegador.** Antes de tocar nada,
   confirmar la hipótesis de §2.4 con dos números del cliente, logueados por
   tick: `sourceBuffer.buffered.length` y, si es > 1, el tamaño de los huecos
   entre rangos.
   **Puerta:** o aparecen huecos/realineaciones a ~2/s y la hipótesis queda
   confirmada, o no aparecen y **este spec se replantea** antes de escribir el
   arreglo.
2. **A — append por grupo.** Agrupar, concatenar, un `appendBuffer`.
   **Puerta:** el sondeo del apéndice sigue dando `duplicados=0` y `en disco y
   nunca entregados=0`, y los ticks bajan o desaparecen a ojo sobre el mismo
   modelo.
3. **Medición de A** (§8). Si los ticks desaparecen, el trabajo termina acá y
   B queda documentado como opcional.
4. **B — `segments`**, solo si A no alcanza: sembrado de `currentTime`,
   vigilante de huecos, y `_recortar` / `_vigilarDeriva` / `alVivo()` sobre
   múltiples rangos.
   **Puerta:** arranca y se ve video (el sembrado), y una sesión con huecos
   conocidos (forzar borrando una parte en una **copia**) no clava el video.
5. **Secundario: rotación de sesión** (§5), empezando por medir cuántos
   `init` nuevos son idénticos al anterior.
6. **`CLAUDE.md` + el comentario de `reproductor_vivo.js`** (§10), commit
   aparte, solo documentación.

---

## 8. Plan de medición

Para cada variante (hoy / A / B), sobre **la misma modelo** y ≥ 5 min:

1. **Integridad del canal** — `sondeo.py` (apéndice): `duplicados` y `en disco
   y nunca entregados` tienen que ser **0** en los tres casos. Si A o B los
   mueve, es un bug nuevo.
2. **Realineaciones** — el contador de la puerta 1 de §7: huecos en `buffered`
   por minuto. Es la métrica que A tiene que bajar ~4× y B llevar a 0.
3. **A ojo** — 5 min continuos mirando, con el modelo de bitrate más alto
   disponible (en la corrida medida, `scofty` a 1440p era el peor caso para el
   presupuesto de 500 ms por tick).
4. **Que no haya regresión de pérdida** — `huecos.py` (apéndice) antes y
   después: el % de huecos de secuencia no depende del reproductor, así que
   tiene que quedar igual. Si cambia, algo del cliente está afectando al
   grabador (no debería: son procesos distintos).

---

## 9. Lo que NO se hace

- **No se toca el grabador** (`grabador_directo.py`, `mouflon.py`,
  `detectar_m3u8.py`). Los datos están bien.
- **No se toca `POOL_HILOS` ni el `timeout` de `mouflon.pedir`.** §2.6: la
  pérdida no correlaciona con concurrencia.
- **No se mete hls.js ni ninguna dependencia de reproducción.** Sigue siendo
  MSE a mano; todo este spec son constantes y ~50 líneas de un archivo.
- **No se intenta "coser" sesiones consecutivas en un solo `.mp4`.** Las
  rotaciones de encoder cambian el `init`; eso es otro problema (y otro spec).
- **No se cambia el layout en disco.**

---

## 10. Documentación a corregir

- **`nimbo\web\js\reproductor_vivo.js:44-50`** — el comentario de `MODO_SB`
  afirma que los timestamps vienen sucios dentro de una sesión. Es falso
  (§2.5): 0 discontinuidades medidas en 240 fragmentos. Corregirlo diga lo que
  diga el resultado de §7, porque la afirmación está mal hoy.
- **`CLAUDE.md`, "Reproductor en vivo"** — ya documenta el conteo por grupo de
  `17c78dc`. Agregar, cuando se haga A o B, por qué el append va por grupo y
  no por parte.

---

## Apéndice: scripts de medición

Los tres se corrieron contra la instalación real sin tocarla (solo leen disco
y la API local) y sacan el puerto y el token de `instancia.json`. Guardarlos
en `pruebas\` al implementar.

### `sondeo.py <rel> <segundos>` — integridad del canal

Imita al cliente: pide `/api/vivo/segmentos` con `limite=24`, `atras=3` y la
misma cadena de cursores (incluido el `desde=None` cuando viene `salto`),
anota cada clave entregada, y al final compara contra lo que quedó en disco
dentro de esa ventana. Reporta `ticks`, `entregados`, `duplicados`, `salto` y
**lo que está en disco y nunca se entregó** — que es exactamente lo que el
reproductor nunca pudo appendear. Produjo §2.1.

### `huecos.py [minimo]` — pérdida real y concurrencia

Recorre `datos\streams\*\*\segmentos`, saca `(secuencia, epoch, parte)` de
cada nombre con el mismo regex que `vivo.py:RE_SEGMENTO`, y por sesión calcula
`(max-min+1) - distintos` = huecos de secuencia. Estima la concurrencia de
cada sesión por solape temporal (del epoch del nombre) contra las demás y
agrupa el % de pérdida por nivel de concurrencia. Produjo §2.6.

### `drift.py <rel> <n>` — timestamps y desfasaje A/V

Usa `nimbo\mp4.py:tiempos_del_fragmento` (que ya devuelve
`[(pista, tfdt, duracion)]` por `traf`) y `pistas_del_init` para los
timescales — ojo que las claves del dict son `pista` y `escala`, no `id` ni
`timescale`. Verifica que `tfdt[n+1] == tfdt[n] + duracion[n]` por pista
(discontinuidades) y acumula la duración de cada pista para medir el
desfasaje audio-video, total y por grupo de 4 partes. Produjo §2.2 y §2.3.
