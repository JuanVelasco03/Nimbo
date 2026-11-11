# SPEC 22 — Unión con audio en chaturbate: una sola pasada, un solo mp4

> **Estado:** Implementado
> **Depende de:** SPEC 18 (segundo sitio chaturbate, que trajo el audio como pista
> separada), SPEC 19 (el turno único de unión), SPEC 20 (el progreso de la unión, cuya
> fase `mezcla` este spec retira)
> **Fecha:** 2026-11-10
> **Objetivo:** Que una grabación de chaturbate salga de una sola unión como un único
> `.mp4` con las dos pistas adentro y en sincronía exacta, en vez de dos uniones, un
> `.mp4` de audio intermedio y un ffmpeg de mezcla al final.

---

## Por qué

### A — "es un poco feo y me trajo muchos bugs"

Hoy una sesión de chaturbate se une **tres veces**: `--unir` de la pista de video,
`--unir` de la pista de audio, y un ffmpeg propio que mete el segundo adentro del primero
(`grabador_chaturbate.py:393-432`, `_unir_doble` / `_mezclar_audio`). Escribe ~3x el
tamaño final en disco, ocupa el turno único de unión el triple de tiempo, y abre cuatro
caminos de falla que el usuario ya vio los cuatro:

| Síntoma | Dónde nace |
|---|---|
| Audio desincronizado | `Reencadenador` corre dos veces, una por pista, cada una cerrando sus propios huecos; después `-c copy -map 0:v:0 -map 1:a:0` las pega desde `t=0` sin offset y sin comparar duraciones (`detectar_m3u8.py:1099-1165`, `grabador_chaturbate.py:348-351`) |
| Video mudo | `_unir_doble:409-415` devuelve el video solo si la unión del audio falla; `_mezclar_audio:343-346` lo mismo si no está ffmpeg — *"mudo es mejor que nada"* |
| `*_audio.mp4` suelto en la grilla | `:430-432` borra el `.mp4` de audio **solo si** la mezcla salió bien; mientras exista no matchea `grabaciones.RE_NOMBRE` (`grabaciones.py:31-45`) y aparece como grupo `sin-convencion` (`:249`) |
| *"la app lo da como que quedó bien unido"* y sale mudo | `recuperar_pendientes()` (`detectar_m3u8.py:1856-1885`) une **toda** carpeta con segmentos y sin `unido.json`, incluidas las `..._audio`: les deja marcador y un `.mp4` de audio que nadie va a mezclar jamás, porque la mezcla solo existe adentro de `_unir_doble`. El video queda mudo y los dos archivos quedan "unidos" |

### B — por qué no se arregla bajando muxado

Medido en vivo el 2026-11-10 contra tres salas públicas (`monika_reed1`,
`bunnydollstella`, `sweet_littleee`): **las cinco rendiciones del master declaran grupo de
audio**, sin excepción.

```
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio_aac_128",...,URI=".../chunklist_5_audio_..._llhls.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=5128000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="audio_aac_128"
```

El nombre de la chunklist de video lo dice todo: `chunklist_3_video_..._llhls.m3u8`. El
origen está demuxado. Probé los caminos hermanos por si había una salida Wowza clásica
muxada — `sin _llhls`, `chunklist_w<N>`, `chunklist.m3u8`, `playlist.m3u8`,
`playlist_llhls.m3u8`: **404**; y los del master (`playlist.m3u8`, `playlist_sfm4s.m3u8`,
`playlist_dvr.m3u8`): **403**, porque el JWT está atado al path `llhls.m3u8`. No hay
variante muxed que pedir. La separación es del sitio, no de Nimbo, y la descarga se queda
como está: dos chunklists, dos carpetas de segmentos. Lo que cambia es que de la unión
para adelante haya **una sola** grabación.

### C — por qué la desincronización es estructural y no un ajuste

`Reencadenador` está escrito para los fragmentos **muxados** de stripchat, y su propio
comentario lo dice: *"Un corte afecta a todas las pistas a la vez, así que primero se mide
en segundos y después se corrigen todas juntas. Comprobado que cada fragmento de este CDN
trae siempre video y audio"* (`detectar_m3u8.py:1121-1124`). En chaturbate cada fragmento
trae **una sola** pista, y encima en dos uniones distintas: si el video perdió 10 s y el
audio no, el video cierra el hueco (*"el video corre seguido y dura eso menos"*, `:1636-1639`)
y el audio no, así que de ahí en adelante el sonido va 10 s adelante. Ningún valor que se
pueda tocar arregla eso: hay que unir las dos pistas **en la misma pasada y sobre el mismo
reloj**.

---

## Scope

### Entra

**El núcleo de la unión** (`nucleo\scripts\detectar_m3u8.py`)

- `unir_sesion()` acepta una **pista de audio acompañante** y produce un solo `.mp4` con
  las dos adentro, en un solo ffmpeg.
- El emparejado es **automático por convención de carpeta** (`<sesión>` ↔
  `<sesión>_audio`): sin flag nuevo de CLI, así que `--unir <ref>`,
  `--unir --forzar`, el `--unir` del cierre y `recuperar_pendientes()` quedan todos
  cubiertos por el mismo cambio.
- Una carpeta `..._audio` **deja de ser una sesión unible por sí sola** mientras exista su
  hermana de video: ni `--unir` laxo ni `recuperar_pendientes()` la toman. Eso cierra el
  camino de *"la app dice que quedó unido y sale mudo"*.
- Sincronía: con audio acompañante, el reencadenado **solo corrige reinicios de emisión**
  (el reloj que vuelve atrás, que si no deja el archivo roto) y **no cierra huecos**; las
  dos pistas conservan su tiempo absoluto y el único corrimiento es el `make_zero` único
  de la salida, que mueve las dos por igual.
- Las **dos** carpetas de sesión reciben `unido.json` apuntando al mismo `.mp4`, para que
  `cosecha_streams.py` pueda barrer también los segmentos de audio.

**El grabador de chaturbate** (`nucleo\scripts\grabador_chaturbate.py`)

- `_unir_doble()` se reduce a una sola llamada a `_unir_en_fondo()`.
- Se retiran `_mezclar_audio()`, `_vigilar_mezcla()`, `ESPERA_MEZCLA` y el `FFMPEG` propio
  del módulo.

**El progreso de la unión** (`nucleo\scripts\progreso_union.py`)

- La fase `"mezcla"` queda sin publicador y se retira. Los bytes del audio entran en el
  total de las fases `copia` y `remux` que ya existen.

**Verificación**

- Una medición nueva en `pruebas\` que mida, sobre un `.mp4` ya terminado, el desfase
  audio-video al principio y al final y la diferencia de duración entre las dos pistas.

### No entra

- **La previa en vivo.** `vivo.py` (`:120-163`, el descubrimiento de la carpeta `_audio` por
  sufijo), `reproductor_vivo.js` y `vista_previa.js` quedan **exactamente** como están: el
  CDN sigue demuxado, así que la previa sigue necesitando dos `SourceBuffer`. Este spec no
  toca ni una línea del frontend.
- **Juntar los segmentos en una sola carpeta.** La carpeta `_audio` hermana se queda: es
  lo que mantiene la previa en vivo y el `Escritor` sin cambios.
- **Lo ya grabado.** Ni se barren los `*_audio.mp4` viejos ni se rehacen las grabaciones
  desincronizadas que estén en disco. Una sesión vieja que todavía tenga sus segmentos se
  puede rehacer a mano con `--unir --forzar`.
- **Stripchat.** El camino sin audio acompañante tiene que quedar byte a byte como hoy —
  es el que graba 24/7.
- **Selección de calidad en chaturbate.** Sigue siendo `mejor_rendicion()` por bandwidth,
  sin `@calidad=`/`@codec=`.

---

## Modelo de datos

No introduce ninguna estructura nueva en disco. Dos cambios en lo que ya existe:

### `datos\streams\<modelo>_<uid>\<variante>_<sello>_audio\unido.json` — pasa a existir siempre

Hoy esa carpeta recibe el marcador porque la une un `--unir` propio, apuntando a su propio
`.mp4` de audio. Después de este spec la une la misma pasada que el video y el marcador
apunta al **mismo** archivo final:

```jsonc
{
  "salida": "F:\\...\\datos\\grabaciones\\ana_H349Y5V_1080p_h264_20261110-213000.mp4",
  "segmentos": 1240,          // los de ESTA pista (la de audio)
  "fecha": "2026-11-10 22:05:11",
  "pista": "audio"            // nuevo: para distinguirlo del marcador de la hermana
}
```

El campo `pista` es informativo (nadie lo lee hoy): lo que importa es que el marcador
**exista**, porque es la primera de las tres compuertas de `cosecha_streams._borrables()`
(`cosecha_streams.py:70-72`, `:100-112`) y sin él esos segmentos de audio no se barren
nunca.

### `estado\union.json` — una fase menos

`fase` pasa de `"copia" | "remux" | "mezcla"` a `"copia" | "remux"`. Ningún otro campo
cambia, y el lector (`grabaciones.py:142-163`) no necesita tocarse: ya pinta la fase que
venga.

---

## Plan de implementación

Cada paso deja el sistema funcionando. Los pasos 1 a 5 son el núcleo de la unión, el 6 el
grabador, el 7 el progreso, y el 8 la medición.

1. **`detectar_m3u8.py` — reconocer la pista acompañante.** Una función
   `_hermana_audio(ruta_sesion)` que devuelve `ruta_sesion + "_audio"` si es un directorio
   con `segmentos\` no vacío, y `None` si no. `SesionDisco` gana un campo
   `acompanante: bool` (`True` si el nombre de la carpeta termina en `_audio` **y** existe
   la hermana de video), que `sesiones_en_disco()` rellena. El listado a mano lo muestra
   como tal.
   **Puerta:** `python nucleo\scripts\detectar_m3u8.py --listar` lista las carpetas
   `_audio` marcadas como acompañantes y el resto igual que antes.

2. **`detectar_m3u8.py` — que nadie una una pista de audio sola.** En `main()` (`:3131-3148`)
   y en `recuperar_pendientes()` (`:1858`), saltear `s.acompanante` con una línea de
   `[SALTO]` que diga que se une junto a su hermana. Caso borde: una carpeta `_audio`
   **sin** hermana de video (la borró `cosecha_streams`, o nunca se creó) no es
   acompañante y tampoco tiene sentido unirla sola — se le escribe un marcador con
   `"salida": ""` y `"motivo": "pista de audio sin video"` para que `cosecha_streams`
   pueda barrerla, y se loguea.
   **Puerta:** con una sesión de chaturbate a medio unir en disco (las dos carpetas sin
   `unido.json`), `python nucleo\scripts\detectar_m3u8.py --unir` produce **un** `.mp4` y
   cero `*_audio.mp4`. Antes producía dos `.mp4`, uno de ellos mudo.

3. **`detectar_m3u8.py` — reencadenado sin cerrar huecos.** `Reencadenador.__init__` gana
   `cerrar_huecos: bool = True`. Con `False`, la rama `adelanto` de `arreglar()`
   (`:1133-1141`) no reancla nada: el hueco queda como hueco y el reloj absoluto se
   respeta. La rama `retroceso` (reinicio de emisión) sigue igual, porque un reloj que
   vuelve atrás rompe el archivo. Stripchat no pasa nunca `False`, así que su camino no
   cambia.
   **Puerta:** una unión de stripchat sigue logueando los mismos `reinicios`/`huecos` que
   antes sobre la misma sesión (comparar contra el log de una unión previa de la misma
   carpeta con `--forzar`).

4. **`detectar_m3u8.py` — `unir_sesion()` con dos inputs.** Firma nueva:
   `unir_sesion(carpeta_sesion, etiqueta, remux=True, carpeta_audio=None)`, y
   `carpeta_audio` se resuelve solo con `_hermana_audio()` si no se lo pasan — así los tres
   llamadores actuales (el detector al cerrar, `recuperar_pendientes`, el `--unir` de
   `main`) lo ganan sin tocarse. Con audio:
   - un solo `Reencadenador(cerrar_huecos=False)` **compartido** por las dos pasadas (sus
     `desfase`/`fin` están indexados por id de pista, así que no se pisan y el corrimiento
     de un reinicio sale del mismo reloj para las dos);
   - el pegote de audio se materializa con `_escribir_crudo()` dentro del escenario
     (`_preparar_escenario`, SPEC 12) y cuenta en `total_origen` y en `_hay_espacio()`. Es
     barato: AAC de 96-128 kbps son ~115 MB en 2 h contra ~4,5 GB de video, ~2,5 % del
     total;
   - el video sigue yendo por tubería y el audio entra como segundo input:
     `ARGS_REMUX + ["-copyts"]`, `-map 0:v:0 -map 1:a:0`, `-c copy`,
     `-avoid_negative_ts make_zero`, `-muxdelay 0 -muxpreload 0`. El `-copyts` es
     obligatorio: sin él ffmpeg normaliza cada input a su propio `start_time` y pierde
     justo el offset relativo que se quiere conservar. El `make_zero` único de la salida
     corre las dos pistas por igual.
   **Puerta:** `ffprobe` sobre el `.mp4` de una sesión de chaturbate recién unida muestra
   dos streams (`h264` + `aac`) y una sola llamada a ffmpeg en el log de la unión. Antes
   había tres.

5. **`detectar_m3u8.py` — marcar las dos carpetas.** `_marcar_unida()` acepta la pista y,
   con audio acompañante, se llama una vez por carpeta con el mismo `"salida"`. El
   `[LISTO]` sale una sola vez.
   **Puerta:** las dos carpetas de la sesión tienen `unido.json` con el mismo `"salida"`, y
   el botón "Limpiar segmentos" con `dias_segmentos` vencido borra **las dos** carpetas.
   Antes borraba las dos también, pero porque cada una traía su marcador de su propia
   unión.

6. **`grabador_chaturbate.py` — una sola unión.** `_unir_doble()` pasa a ser un
   `_unir_en_fondo()` adentro del mismo `cerrojo_union.turno()`, sin segunda unión y sin
   mezcla. Se borran `_mezclar_audio()` (`:335-390`), `_vigilar_mezcla()` (`:316-333`),
   `ESPERA_MEZCLA` y `FFMPEG` (`:116-117`). La tupla de `cola_union` sigue llevando las dos
   pistas (`:1023`, `:1119`, `:1280`), porque la de audio es la que decide si hay que
   escribirle `info.json` y es la que `--unir` va a encontrar como hermana. El docstring
   del módulo (`:34-45`) se actualiza: ya no describe dos uniones y una mezcla.
   **Puerta:** grabar una modelo de chaturbate 5 minutos y parar. En
   `estado\eventos_chaturbate.jsonl` hay **un** `[UNIR] ... listo` y ningún `[MUX]`. En
   `datos\grabaciones\` hay **un** `.mp4` y ningún `*_audio.mp4`.

7. **`progreso_union.py` — retirar la fase `mezcla`.** Sacarla de la lista de fases y del
   docstring (`:16-21`), junto con la mención de `grabador_chaturbate.py` como escritor
   posible: después de este spec `union.json` tiene **un solo escritor por unión**, el
   subproceso `--unir`, lo que simplifica el argumento de "nunca dos a la vez" que SPEC 19
   y 20 tenían que sostener con dos.
   **Puerta:** durante una unión de chaturbate, `estado\union.json` pasa por `copia` y
   `remux` y nada más; el banner no parpadea entre fases (antes `informe.cerrar()` dejaba
   `activa: null` entre las tres).

8. **`pruebas\sincronia_chaturbate.py` — medir el resultado.** Solo lectura, solo librería
   estándar + `ffprobe`, mismo criterio que el resto de `pruebas\`: sobre un `.mp4` dado,
   reportar el `pts_time` del primer y del último paquete de cada pista, el desfase
   audio-video en los dos extremos y la diferencia de duración entre las dos. Es la
   evidencia de que la sincronía no deriva.
   **Puerta:** `python pruebas\sincronia_chaturbate.py <ruta.mp4>` sobre una grabación del
   camino nuevo da desfase inicial y final dentro de la misma banda (|Δ| < 100 ms en los
   dos y variación entre ellos < 100 ms).

---

## Criterios de aceptación

**La unión**

- [x] Una sesión de chaturbate produce **un** `.mp4` con dos streams (`ffprobe`: `h264` +
      `aac`). Antes producía dos `.mp4` y después reemplazaba uno.
- [x] El log de esa unión tiene **una** invocación de ffmpeg. Antes tenía tres (remux
      video, remux audio, mezcla).
- [x] No queda ningún `*_audio.mp4` en `datos\grabaciones\`, ni cuando la unión sale bien
      ni cuando falla.
- [x] El pico de disco durante la unión es ~2x el tamaño final, no ~3x.
- [x] Las dos carpetas de la sesión terminan con `unido.json` apuntando al mismo `.mp4`.

**La sincronía**

- [~] `pruebas\sincronia_chaturbate.py`: la **deriva** (el desfase al final menos el
      desfase al principio) da −0,001 s sobre una grabación en vivo de 5 min, y la misma
      −0,001 s sobre esa grabación con 9,6 s de video borrados a mano. Eso es lo que este
      criterio quería decir y se cumple. La otra mitad, *"|Δ| < 100 ms al principio y al
      final"*, **está mal planteada y no es alcanzable**: la chunklist de audio arranca y
      termina en instantes distintos de la de video (medido, +0,700 s de cabeza y +0,699 s
      de cola en esa grabación), así que ese Δ no es desincronización sino material que no
      se capturó — lo que importa es que sea **el mismo** en los dos extremos. Pendiente:
      reescribir el criterio, y medirlo sobre una grabación de 30 min o más (solo se probó
      con 5 min).
- [x] Sobre una sesión que perdió segmentos de video (buscar una con
      `[UNIR] ... corte(s) que no llegaron a bajarse` en el log): el desfase sigue dentro
      de esa banda. Antes el audio quedaba adelantado exactamente los segundos del hueco.
- [x] Una sesión con reinicio de emisión (`reinicios > 0`) abre bien y el tiempo corre
      hacia adelante de punta a punta.

**Que nadie una una pista sola**

- [x] Con las dos carpetas de una sesión sin `unido.json`,
      `python nucleo\scripts\detectar_m3u8.py --unir` produce un `.mp4` con audio. Antes
      producía uno mudo más un `*_audio.mp4`, los dos marcados como unidos.
- [x] `recuperar_pendientes()` tras un cierre a lo bruto hace lo mismo.
- [x] `--listar` marca las carpetas `_audio` como acompañantes.
- [x] Una carpeta `_audio` sin hermana de video no genera ningún `.mp4` y queda marcada
      para que `cosecha_streams` la barra.

**Compatibilidad**

- [x] Una unión de stripchat (camino sin audio acompañante) da el **mismo** `.mp4` byte a
      byte que antes del spec sobre la misma carpeta de segmentos, y los mismos
      `reinicios`/`huecos` en el log.
- [~] La previa en vivo de chaturbate: **cero** archivos de `nimbo\` o de
      `nimbo\web\` modificados (confirmado con `git diff --stat`), y `vivo.py` sigue
      encontrando las dos pistas sobre una sesión real (`sesiones_vivas` devuelve
      `sesion_audio`, y `vivo.listar` entrega init + segmentos de las dos). Pendiente:
      abrirla en la ventana con el motor corriendo.
- [x] La grilla de Grabaciones no tiene ningún grupo `sin-convencion` nuevo.
- [x] **Queda anulado** el criterio de SPEC 20 que exige *"una unión de chaturbate pasa por
      `remux` (video), `remux` (audio) y `mezcla`"*
      (`specs\20-progreso_union_y_marca_sitio.md:494-495`): a partir de este spec pasa por
      `copia` y `remux`, una sola vez.

---

## Al implementar

Dos cosas que el spec no tenía bien y se resolvieron contra la medición.

### El `Reencadenador` compartido del paso 4 no era posible como estaba escrito

El paso 4 pedía *"un solo `Reencadenador` **compartido** por las dos pasadas (sus
`desfase`/`fin` están indexados por id de pista, así que no se pisan)"*. **Esa premisa es
falsa**: medidos los dos init de una sesión real, los dos declaran **`track_id = 1`** (el
de video con timescale 90000, el de audio con 48000). Compartir el objeto haría que
`fin[1]`/`desfase[1]` se pisaran y que la pasada de audio viera un retroceso enorme contra
el reloj del video, reanclando la pista entera.

Se implementó con **una cadena por pasada**, las dos con `cerrar_huecos=False`. Es
equivalente a la intención del spec: cada pista se reancla donde acabó *su* fragmento
anterior, y como las dos estaban en sincronía antes del reinicio, lo siguen estando
después. Verificado sintetizando un reinicio de emisión (−150 s de `tfdt` en las dos
pistas desde la mitad de la sesión): 1 reinicio corregido por pista, deriva −0,001 s, y
`ffmpeg -f null` lee el archivo entero sin un solo error.

### Sin arreglar el ORDEN de los segmentos, el paso 3 no funciona

El `epoch` del nombre de un segmento es la hora de descarga **local**
(`int(time.time())` en `grabador_chaturbate._guardar_descarga`), y `clave_segmento` ordena
por él a propósito, porque en stripchat el contador de secuencia se reinicia con la emisión
(medido en su momento: solapes 5292 → 6). En chaturbate es al revés y rompe: las partes de
LL-HLS se piden en paralelo, así que una reintentada se guarda **después** de una más nueva
y queda detrás de ella.

Medido sobre una sesión real de 368 fragmentos: ordenando por epoch, **11 "reinicios" y 21
"huecos" (35 s) que no existen**; ordenando por `(secuencia, parte)`, **0 y 0**, con el
mismo span de 201,667 s. Antes de este spec no se notaba porque el `Reencadenador` cerraba
esos huecos y de rebote deshacía el desorden. Al dejar de cerrarlos —que es justo lo que
pide el paso 3— esos 35 s se quedaban dentro del archivo: el video salía de 236,7 s en vez
de 201,7 s.

Por eso se agregó `_orden_segmentos(..., por_secuencia=True)`, que vale **solo** para
chaturbate y se decide en positivo (`info.json` con `"modo": "chaturbate"`, o la convención
`_audio`): stripchat no puede dar `True` por ninguno de los dos caminos y conserva su orden
por epoch byte a byte.

### Un camino degradado que había que cerrar

Sin ffmpeg (o si ffmpeg no puede con ninguna pasada) la salida es el pegote en crudo de
**solo video**. Marcar igual la carpeta de audio la habría dejado lista para que
`cosecha_streams` barriera un audio que no está en ningún archivo. Con `audio_adentro`, en
ese caso la carpeta de audio **no** recibe marcador y se loguea cómo rehacer la unión:
perder disco es reversible, perder la única copia del sonido no.

---

## Decisiones

- **No:** bajar una variante muxada. Medido el 2026-11-10 contra tres salas: las cinco
  rendiciones declaran grupo de audio y los caminos hermanos dan 404/403. **Esta decisión
  no se reabre** — si alguna vez aparece una variante muxada, es otro spec.
- **Sí:** extender `unir_sesion()` en vez de darle a chaturbate su propio ffmpeg de una
  pasada. `unir_sesion()` es el único punto por el que pasan los tres caminos de unión
  (SPEC 20 ya se apoyó en eso para el progreso), y trae gratis `MAX_REPARACIONES`, el
  escenario en TEMP de SPEC 12, `_Freno` de SPEC 13 y `_hay_espacio`. Duplicar todo eso en
  `grabador_chaturbate.py` es duplicar justo la parte frágil y ya probada.
- **Sí:** emparejar por convención de carpeta (`<sesión>` ↔ `<sesión>_audio`) y resolverlo
  adentro de `unir_sesion()`. **No:** un flag nuevo de CLI tipo `--con-audio`. Con la
  convención, los tres llamadores y el `--unir` a mano quedan cubiertos por un solo
  cambio; con el flag, el que lo olvide reproduce el bug de "unido pero mudo".
- **Sí:** no cerrar huecos cuando hay audio acompañante. Las dos pistas quedan en su reloj
  absoluto y la sincronía sale por construcción, no por un cálculo que haya que mantener.
  **No:** cerrarlos con el mapa de tiempo del video aplicado al audio — el audio que cae
  sobre el hueco del video queda solapado y habría que decidir si se descarta o se dejan
  timestamps no monótonos; es complejidad nueva para ganar un `.mp4` que "corre seguido" a
  costa de la sincronía, que es justo el bug que se vino a arreglar.
- **Sí:** `-copyts` solo en la rama con audio. Sin él ffmpeg normaliza cada input a su
  propio `start_time` y el offset relativo entre las dos pistas se pierde — sería el mismo
  bug de hoy con una pasada menos. **No:** `-itsoffset` calculado a mano desde los `tfdt`:
  es el mismo dato que `-copyts` ya respeta, pero a mano y con una resta más que puede
  salir mal.
- **Sí:** `unido.json` en las dos carpetas. Es la primera compuerta de
  `cosecha_streams._borrables()`, y sin marcador los segmentos de audio no se barren
  nunca. **No:** que `cosecha_streams` aprenda a emparejar carpetas: el marcador ya es el
  contrato y no hace falta un segundo.
- **Sí:** dejar la carpeta `_audio` hermana donde está. **No:** meter los segmentos de
  audio en la misma carpeta que el video. Sería más lindo en disco, pero toca el
  `Escritor`, `vivo.py:142` (que descubre el audio por el sufijo) y el reproductor en
  vivo, que es el código más caro de volver a probar y el que hoy no tiene ningún bug.
- **Sí:** retirar la fase `mezcla` de `progreso_union.py` en vez de dejarla sin
  publicador. Una fase que nadie publica es una mentira en el docstring, y retirarla deja
  `union.json` con un solo escritor por unión.
- **No:** tocar lo ya grabado. Decisión del usuario: el spec cambia lo que se grabe de acá
  en adelante.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Se toca `detectar_m3u8.py`, que graba 24/7, y mitmdump **recarga el script solo cuando cambia en disco** — cada recarga construye un `Detector` nuevo y puede arruinar uniones en curso | Editar con Nimbo parado, nunca grabando. Es lo que ya dice la nota de la máquina de build y lo que causó las 15 sesiones con hasta 2.156 segmentos sueltos del 2026-11-08 |
| El camino de stripchat se rompe en silencio | Todo lo nuevo cuelga de `carpeta_audio is not None`. El criterio de aceptación exige el **mismo `.mp4` byte a byte** sobre la misma carpeta de segmentos |
| `-copyts` cambia cómo ffmpeg trata los timestamps y puede sacar un `.mp4` con tiempos negativos o que no arranque en 0 | `-avoid_negative_ts make_zero` se queda, y el `-muxdelay 0 -muxpreload 0` evita el preroll. La puerta del paso 4 es `ffprobe` sobre el archivo real |
| No cerrar huecos hace que el `.mp4` tenga agujeros y algún reproductor se plante | Un agujero es lo que de verdad pasó; el `.mp4` fragmentado ya los representa. Verificar en el reproductor de la propia app y en VLC sobre una sesión con huecos conocidos |
| El pegote de audio no es tan chico como se midió (una emisión muy larga, o audio de más bitrate) | Entra en `total_origen` y por lo tanto en `_hay_espacio()` y en el margen del escenario, igual que el video. No se agrega ninguna excepción al chequeo de disco |
| Queda una carpeta `_audio` huérfana sin hermana de video y sin marcador, ocupando disco para siempre | Paso 2: se le escribe marcador con `"salida": ""` para que `cosecha_streams` la vea |
| `recuperar_pendientes()` corre al arrancar el detector y ahora saltea carpetas | El salteo es solo para las acompañantes **con hermana presente**, que es exactamente el caso en que unirlas sola era el bug |

---

## Lo que **no** está en este spec

- Juntar los segmentos de audio y video en una sola carpeta de sesión.
- Barrer los `*_audio.mp4` que quedaron de mezclas fallidas anteriores.
- Rehacer las grabaciones de chaturbate ya desincronizadas que todavía tengan segmentos.
- Selección de calidad/codec en chaturbate (`@calidad=`, `@codec=`, `@variantes=`).
- El hueco de `cosecha_streams.py:118`/`:159`, que arma el set de sesiones vivas ignorando
  `sesion_audio` — hoy no muerde porque una carpeta de audio viva no tiene marcador.
- Cerrar `detectar_m3u8.py` con Nuitka (sigue cargándose por ruta desde mitmdump).

Cada una, si entra, va en su propio spec.

---

## Verificación de punta a punta

1. Parar Nimbo (no editar el núcleo con el detector corriendo).
2. Aplicar los pasos 1 a 8.
3. `python nucleo\scripts\detectar_m3u8.py --listar` → las carpetas `_audio` salen
   marcadas como acompañantes.
4. Sobre una sesión de chaturbate que ya esté en disco con las dos carpetas y sin
   `unido.json` (o quitarle los marcadores a una unida):
   `python nucleo\scripts\detectar_m3u8.py --unir --forzar "<modelo>_<uid>/<variante>_<sello>"`
   → un `.mp4`, `ffprobe` muestra `h264` + `aac`, cero `*_audio.mp4`.
5. `python pruebas\sincronia_chaturbate.py <ese .mp4>` → desfase dentro de banda en los dos
   extremos.
6. Regresión de stripchat: `--unir --forzar` sobre una sesión de stripchat ya unida y
   comparar el `.mp4` nuevo con el viejo (`fc /b` o hash) → idénticos.
7. Arrancar Nimbo, grabar una modelo de chaturbate 5 minutos, parar → un `.mp4` con audio,
   un solo `[UNIR] ... listo`, ningún `[MUX]`, `union.json` solo con `copia`/`remux`.
8. Abrir la previa en vivo de una modelo de chaturbate → sigue con audio (el frontend no
   se tocó).
