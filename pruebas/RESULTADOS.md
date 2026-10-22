# Resultados de las pruebas del camino directo

Registro forense de lo que se probó el **21/10/2026**, con los datos crudos
embebidos y el **procedimiento de recuperación** por si el protocolo cambia.

Acompaña a `specs\grabador_directo.md` (el qué construir). Este archivo es el
*qué observamos y cómo reproducirlo*.

Herramienta: `pruebas\sonda_directo.py`.

> **Por qué está embebida la evidencia y no solo referenciada:** mientras
> corrían estas pruebas, `cosecha_streams.py` (cada 10 min, `dias_segmentos: 1`)
> barrió a la papelera todas las sesiones ya unidas de `datos\streams\` —
> incluida `_gaby2_171961503`, de la que se derivó el material de clave. Las 35
> grabaciones `.mp4` finales quedaron intactas; los segmentos crudos y sus
> `playlists\*.m3u8`, no. **La evidencia cruda de este hallazgo es perecedera a
> las ~24h.** De ahí que esté copiada acá abajo.

---

## 1. Lo más importante si algo se rompe: cómo recuperar el material de clave

El riesgo número uno del camino directo es que stripchat rote la tabla de claves
(ver `specs\grabador_directo.md` §6). **No hace falta esperar a que alguien
publique la clave nueva: se puede derivar del propio disco.**

### 1.1 El hallazgo que lo hace posible (verificado)

El descifrado es un XOR contra `sha256(pdkey)` ciclado. Pero:

- **Todos los tokens cifrados observados son de exactamente 16 bytes**
  (22 caracteres base64 + 2 de padding = 24 → 16 bytes).
- `sha256()` devuelve 32 bytes, así que **nunca se usa más que los primeros 16**
  y el ciclado jamás entra en juego.
- Entonces `keystream = sha256(pdkey)[:16]` es todo lo que se necesita, y
  `keystream = cifrado XOR claro` se obtiene de **un solo par conocido**.

**El `pdkey` nunca es necesario.** Verificado: los 3 pares de disco dan el mismo
keystream, y ese keystream es idéntico a `sha256("tOcYOap4Ty1l9Jzb")[:16]`.

```
keystream (pkey NTK9aqcLmNFMWrpQ) = 1838c78f543606e6de5fc81167f36e5a
```

### 1.2 El procedimiento

Hace falta **una sesión grabada por el camino del navegador** (el que sigue
funcionando aunque roten las claves, porque ahí descifra el player del sitio).
Nimbo la produce sola: el detector guarda la playlist cifrada **y** los
segmentos con su nombre ya en claro, en la misma carpeta de sesión.

1. Tomar una sesión reciente de `datos\streams\<modelo>_<id>\`
   **antes de que la coseche** (menos de `dias_segmentos`).
2. En `playlists\<id>_<variante>.m3u8`, buscar una línea
   `#EXT-X-MOUFLON:URI:` y extraer `_<msn>_<22 chars base64>_<epoch>`.
3. En `<variante>_<sello>\segmentos\`, buscar el archivo con **el mismo `msn` y
   el mismo `epoch`**: su nombre trae el hash real,
   `_<msn>_<16 alfanuméricos>_<epoch>`.
4. El `pkey` a usar es el de la línea `#EXT-X-MOUFLON:PSCH:v2:<pkey>` de **esa
   misma media playlist**.
5. Correr:

```python
import base64
def keystream(cifrado_b64, claro):
    t = cifrado_b64[::-1]; t += "=" * (-len(t) % 4)
    b = base64.b64decode(t)
    return bytes(x ^ y for x, y in zip(b, claro.encode()))
# -> 16 bytes; guardar junto al pkey del paso 4
```

6. Validar con `pruebas\sonda_directo.py <modelo> --fase 4`: el 100% de los
   tokens tiene que descifrar a `^[A-Za-z0-9]{16}$`.

### 1.3 Consecuencia de diseño para `mouflon.py`

`KEY_MAP` debería guardar **`pkey -> keystream` (16 bytes)**, no
`pkey -> pdkey` (string). Son equivalentes para descifrar, pero el keystream es
lo único recuperable sin ingeniería inversa de terceros. La sonda hoy guarda el
`pdkey` porque lo tenemos; `mouflon.py` no debería depender de tenerlo.

> **Acción operativa recomendada:** conservar **una** carpeta de sesión completa
> fuera del alcance de la cosecha (playlist cifrada + segmentos) como muestra de
> referencia permanente. Hoy no existe tal copia y la original ya se perdió.

---

## 2. Evidencia cruda (copiada, el original ya fue cosechado)

### 2.1 Los 3 pares verificados

De `datos\streams\_gaby2_171961503\`, media playlist `171961503_1080p.m3u8`
(`#EXT-X-MOUFLON:PSCH:v2:NTK9aqcLmNFMWrpQ`) contra `segmentos\`:

| msn | epoch | cifrado (22 b64) | claro real (16 alnum) |
|---|---|---|---|
| 1849 | 1792591360 | `gNmEqXlhpNtSoM7FD7my0L` | `7taceM4bsiPt9RHl` |
| 1850 | 1792591362 | `wOWpYF7NKauDddERS/E+UU` | `IwCrprs607kjry8a` |
| 1851 | 1792591364 | `AHjkaLXhbG6OoaaFj1qqHc` | `hBmYelledFpFJZMF` |

Estos son los `PARES_CANARIO` de `sonda_directo.py`. `--fase 0` los verifica sin
red: **es el canario de rotación de claves.**

### 2.2 Los 11 tokens PSCH del master

Listados en texto plano en `171961503_auto.m3u8`, y **los mismos para modelos
distintas** (tabla global rotativa):

```
1Dzcc6OjP73LKbtI   7uUnbD0jMCB9GH32   Fq6m2TO2ZeBkRPm9   GrRncsoByZmsiT6L
N2oLovTIXb0o28Uj   NTK9aqcLmNFMWrpQ   OLzu7QlySkG2fVRn   Ohi7eTRBpkAuML0l
Ook7quaiNgiyuhai   qjfc0a2Mi4ngsau6   rHQhP6lNYB8v13s1
```

**Solo de `NTK9aqcLmNFMWrpQ` tenemos material de clave.** Los otros 10 figuran
en el master pero su `pdkey`/keystream es desconocido.

### 2.3 Estructura de la media playlist

El `URI` estándar de HLS es **siempre un señuelo fijo** (`.../media.mp4`); la URL
real va en la línea `#EXT-X-MOUFLON:URI:` **inmediatamente anterior**:

```
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MOUFLON:PSCH:v2:NTK9aqcLmNFMWrpQ
#EXT-X-TARGETDURATION:2
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA-SEQUENCE:3239
#EXT-X-MAP:URI="https://media-hls.doppiocdn.com/b-hls-16/105277105/105277105_1080p60_h264_init_gNI5XXpPaXCXJMUK.mp4"
#EXT-X-PROGRAM-DATE-TIME:2026-10-22T00:06:01.395+0000
#EXTINF:2.000
#EXT-X-MOUFLON:URI:https://media-hls.doppiocdn.com/b-hls-16/105277105/105277105_1080p60_h264_3239_AIFobPmx7FLebajlDxr6HI_1792627561.mp4
https://media-hls.doppiocdn.com/b-hls-16/media.mp4
```

Nótese: `id`, `calidad`, `codec`, `msn` y `epoch` están **en claro**; solo el
campo de hash está cifrado. Por eso a primera vista parece texto plano — fue el
primer error de lectura de esta investigación (ver §5).

### 2.4 El señuelo de publicidad

Pedir la variante **sin** `psch`/`pkey` devuelve 200, pero con redirección
interna:

```
https://media-hls.doppiocdn.com/b-hls-01/cpa/v2/stream.m3u8
```

con `#EXT-X-MOUFLON-ADVERT` y `#EXT-X-PLAYLIST-TYPE:VOD`. Son 6 chunks de
publicidad. **Sin la compuerta `es_publicidad()` se graban como si fueran la
emisión.** Reproducido en las 4 corridas (la sonda lo imprime como control).

### 2.5 El `pkey` es intercambiable (clave para que esto funcione)

Verificado en vivo: se pidió la misma variante con un `pkey` arbitrario de los
11 y el CDN respondió con `#EXT-X-MOUFLON:PSCH:v2:` **igual al que mandamos**,
cifrando con ese. O sea: **el servidor usa el `pkey` que le presentás**, no "el
que le tocaba" a la sesión.

Por eso alcanza con forzar siempre el único del que tenemos clave.

| `pkey` enviado | PSCH devuelto | ¿descifra? |
|---|---|---|
| `1Dzcc6OjP73LKbtI` (primero del master) | `1Dzcc6OjP73LKbtI` | no (sin clave) |
| `NTK9aqcLmNFMWrpQ` (forzado) | `NTK9aqcLmNFMWrpQ` | **sí** |

### 2.6 Otros datos del protocolo

| Qué | Valor observado |
|---|---|
| Master (host **fijo**) | `https://edge-hls.doppiocdn.com/hls/<id>/master/<id>_auto.m3u8` |
| Host de los segmentos | **rota entre corridas**: se vieron `b-hls-01`, `b-hls-16`, `b-hls-29`, `b-hls-31` para las mismas modelos en una tarde. Sale del master, no se adivina. |
| id por nombre | `GET /api/front/users/user-ids/<nombre>?<uniq>` → `{"id": N}` |
| estado | `GET /api/front/v2/models/<id>/cam?timezoneOffset=0&triggerRequest=loadCam&uniq=<rnd>` → `user.user.status`, `cam.isCamAvailable`, `cam.show`, `user.isGeoBanned` |
| Init / segmentos | **sin query string**: cero tokens, cero firmas, cero expiración |
| `playlistType=standard` | solo segmentos completos de 2s; **sin** `#EXT-X-PART` |
| Cabeceras | las de `supervisor.py:121-125`; sin ellas → 406 |
| Bitrate medido (1080p60) | ~8.1 Mbit/s, segmentos de ~2.0 MB |

---

## 3. Corridas de la fase 7 (sostenido en el tiempo)

Modelo `afrodita__25` (id 105277105), 1080p60, en vivo ~3h corridas.

| # | Duración | Segmentos | Huecos reales | Remuxeado | Esperado | Resultado |
|---|---|---|---|---|---|---|
| 1 | ~3 min (abortó) | 84 | 0 hasta el corte | — | — | **crash**: `TimeoutError` sin try/except en la bajada |
| 2 | 10 min | 272 | **1 real** (msn 1467) | 606.01s | 544.00s | falla: timeout bloqueó el polling → 29 segmentos perdidos |
| 3 | 10 min | 295 | 1 real + 2 inits | 66.52s | 588.00s | falla: no partía sesión en reconexión de encoder |
| 4 | 10 min | **303** | **0** | **606.28s** | **606.00s** | **limpia** (el fallo reportado fue falso positivo, §5c) |
| 5 | 2 min (smoke) | 63 | **0** | **126.00s** | **126.00s** | **limpia, exit 0** |
| 6 | 10 min | — | — | — | — | la modelo se fue offline a los ~25s (404 sostenido); la sonda lo detectó y siguió sin crashear |

### 3.1 Verificación de la corrida limpia (#4), contra los archivos crudos

No contra el log: contra los nombres de archivo en disco.

- 303 segmentos, `msn` de 1433 a 1728.
- **Cero saltos de `msn`** (estrictamente consecutivo).
- Distribución de deltas del campo `epoch`:

```
      5 delta=1
    292 delta=2
      5 delta=3
```

Es decir: **el epoch tiene jitter de ±1s alrededor de los 2s nominales, sin
perder un solo segmento.** Ese jitter fue lo que generó el falso positivo (§5c).

### 3.2 Verificación de la corrida con reconexión (#3)

```
salto en msn: 1466 -> 1468                       <- hueco real (1 segmento)
salto en epoch: 1792638197 -> 1792638218         <- 21s: la reconexión
salto en epoch: 1792638218 -> 1792638219
```

Dos `#EXT-X-MAP` distintos en la misma corrida:

```
105277105_1080p60_h264_init_5VaYcjOPslO9aJaC.mp4   22:02:21
105277105_1080p60_h264_init_aVYSSpjOlsvRlFKu.mp4   22:03:43
```

Separando a mano por init y remuxeando cada tramo:

| Tramo | Segmentos | Duración |
|---|---|---|
| sesión 1 (msn ≤1466) | 36 | 68.01s |
| sesión 2 (msn ≥1468) | **261** | **522.00s** (= 261 × 2s exacto) |

**El dato estaba sano; el error era del armado.** Concatenar dos generaciones de
encoder bajo un init produce `Non-monotonic DTS` y una duración absurda.

---

## 4. Otras pruebas puntuales

| Prueba | Resultado |
|---|---|
| Fase 0 (canario, sin red) | OK, 3/3 pares |
| Fase 6 completa, `afrodita__25` | OK: init 1240 B + 3 segmentos (~2 MB c/u) → `salida.mp4` 6.00s, 1920x1080@60, h264 + aac |
| Integridad del `.mp4` | `ffmpeg -v error -f null -` → **sin un solo error de decodificación** |
| Calidad anónima vs logueada | **idéntica**: `['1080p60','240p','480p','720p','720p60']` en los dos casos → la calidad **no** depende de cookies, contra lo que dice `supervisor.py:48-51` para el camino del navegador |
| Por el túnel VPN (`--vpn`) | OK con `Anavi_` (marcada `@vpn=`), vía `ProxyHandler` a `127.0.0.1:25345`, **con el detector real corriendo y sin interferirlo** |
| Detección de offline | `Amy_lively`, `angel09_a_`, `AngelRuby` → `status='off'`, `isCamAvailable=False`; la sonda aborta limpio |
| Aislamiento | `git status` quedó limpio en todo momento; la sonda escribe solo bajo `--salida` (`%TEMP%\nimbo_sonda\`) |

---

## 5. Errores cometidos en la investigación (y por qué importan)

Documentados porque **cada uno es una trampa que se repite en producción**.

**a) Lectura falsa inicial: "las URLs están en texto plano".**
El campo de hash tiene 22 caracteres base64 mientras el nombre real tiene 16
alfanuméricos; como el resto de la URL (id, calidad, codec, msn, epoch) **sí**
está en claro, parece plano a simple vista. Corregido al comparar largos.
*Lección:* comparar contra el nombre de archivo real, no "leer" la URL.

**b) Tabla de claves fabricada.**
El primer diseño incluyó 11 pares `pkey:pdkey` como si fueran una tabla pública.
Al bajar el repo de referencia se vio que su `keys.txt` es una **plantilla
vacía** (`"Put key pair in format pkey:pdkey in this file"`,
[aitschti/scp-standalone](https://github.com/aitschti/scp-standalone)) que cada
usuario llena a mano. **10 de los 11 pares eran inventados**; solo el derivado
de disco era real. Lo detectó la fase 0 al fallar.
*Lección:* por eso existe la fase 0 sin red, y por eso §1 documenta cómo
derivar el material uno mismo.

**c) Falso positivo en la detección de huecos.**
Se medía continuidad por delta del campo `epoch`, que tiene jitter normal
(§3.1). Reportó "5 huecos" en una sesión con **cero** `msn` faltantes.
*Lección:* el hueco se mide por **número de secuencia**, nunca por el reloj del
encoder.

**d) Descargas bloqueantes.**
Un `TimeoutError` de 15s en UN segmento, en un loop secuencial, costó **29
segmentos (64s de video)**: mientras el GET estaba colgado no se refrescaba la
playlist y la ventana deslizante del CDN se comió segmentos nunca pedidos.
*Lección:* el polling de la playlist no puede esperar a una descarga. Pool de
hilos, recolectar con `fut.done()`.

**e) No se partía sesión en reconexión de encoder.** §3.2.
*Lección:* `#EXT-X-MAP` nuevo = sesión nueva, igual que hace el detector.

**f)** (menor) Un segmento que da 404 al cortarse la emisión quedaba contado
como "sin hueco" porque su `msn` seguía en la lista aunque el archivo no
existiera. Corregido filtrando por "se bajó de verdad".

---

## 6. Lo que NO se probó

Honestidad sobre los límites de esta evidencia:

- **Concurrencia real.** Todo se midió con **una** modelo a la vez. La escalera
  de 5→30 simultáneas (`specs\grabador_directo.md` §8) está sin correr. La
  predicción de que el cuello se mueve a ancho de banda (~240 Mbit/s a 30
  modelos) es aritmética, no medición.
- **Comparación directa contra el navegador.** No se grabó la misma modelo por
  los dos caminos en paralelo para comparar segundos perdidos. Es la métrica que
  decide, y falta.
- **Ventanas largas.** Lo más largo fueron 10 minutos. Nada sobre una noche
  entera, ni sobre qué pasa con una modelo que entra y sale de privado.
- **`@vpn=` sostenido.** El túnel se probó solo hasta la fase 2 (master), no
  bajando segmentos 10 minutos.
- **Rotación de claves.** Nunca se observó una. Todo el plan de degradación de
  `specs\grabador_directo.md` §6 es teórico.
- **Modelos con `source` en vez de calidades fijas.** `Anavi_` ofrecía
  `source 1080x1440` (vertical). No se bajó ni armó video de una de esas.

---

## 7. Cómo reproducir

```bat
rem canario de claves: sin red, instantáneo
python pruebas\sonda_directo.py cualquiera --fase 0

rem cadena completa contra una modelo en vivo
python pruebas\sonda_directo.py <modelo_online> --fase 6

rem sostenido (la prueba que decide)
python pruebas\sonda_directo.py <modelo_online> --fase 7 --minutos 10

rem por el túnel (requiere el detector arriba: wireproxy es su hijo)
python pruebas\sonda_directo.py <modelo_con_vpn> --fase 2 --vpn
```

Verificar continuidad real a mano sobre lo que bajó la fase 7:

```bash
ls <salida> | grep -v -E "init|pegado|salida|sesion" \
  | sed -E 's/.*_([0-9]+)_[A-Za-z0-9]+_([0-9]+)\.mp4/\1 \2/' | sort -n \
  | awk '{if (p!="" && $1 != p+1) print "hueco msn:", p, "->", $1; p=$1}'
```

La sonda **nunca** escribe dentro de `F:\Nimbo`: solo bajo `--salida`
(por defecto `%TEMP%\nimbo_sonda\`). Después de correrla, `git status` tiene que
seguir limpio.
