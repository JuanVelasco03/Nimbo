# Grabador directo: capturar sin navegador

Especificación de implementación. Estado: **el camino está probado de punta a
punta contra el sitio vivo** (ver "Evidencia medida"); lo que falta es
integrarlo en Nimbo.

Rama de trabajo: `01-optimizar-navegador`.
Herramienta de diagnóstico ya en el repo: `pruebas\sonda_directo.py`.

---

## 1. Por qué

Hoy cada modelo cuesta una pestaña de Chromium a 1920x1080 **decodificando
1080p60 por software** dentro del supervisor (`nucleo\scripts\supervisor.py`),
más mitmdump copiando cada segmento que pasa. El techo práctico son ~5 modelos
simultáneas, y el cuello es RAM y CPU del navegador, no la red.

La premisa que mantuvo este camino cerrado está escrita en `CLAUDE.md`
("Reproductor en vivo", "Tres procesos") y en el docstring de
`nucleo\scripts\supervisor.py:12-15`:

> *"los segmentos de doppiocdn vienen con las URLs ofuscadas (tags
> `#EXT-X-MOUFLON`) y solo el reproductor del sitio sabe pedirlas"*

**Es falsa.** El navegador no descifra nada localmente: le presenta al CDN una
`pkey` que viaja **en texto plano dentro del propio master playlist**, y el CDN
responde con la media playlist ya resuelta. El descifrado que queda es un XOR
con `sha256` de una clave derivada, reproducible en 20 líneas de stdlib.

Corregir esa afirmación en `CLAUDE.md` y en el docstring de `supervisor.py` es
parte del trabajo (ver §10).

---

## 2. Evidencia medida (21/10/2026, modelo `afrodita__25`, id 105277105)

Todo lo de esta sección está **verificado**, no inferido.

### 2.1 El protocolo

| Qué | Resultado |
|---|---|
| URL del master | `https://edge-hls.doppiocdn.com/hls/<id>/master/<id>_auto.m3u8` — **host fijo**. Los `b-hls-01`/`b-hls-16`/`b-hls-29` aparecen solo *dentro* del master, en las URIs de variante, y **rotan entre corridas** (se vieron los tres para la misma modelo en una tarde). |
| Master sin cookies | 200. Ofrece `1080p60, 720p60, 720p, 480p, 240p` — **idéntico** al master que capturó el navegador logueado y que ya estaba en `datos\streams\afrodita__25_105277105\playlists\`. La calidad **no** depende del perfil autenticado. |
| Variante **sin** `pkey` | 200 pero **302 interno a `/cpa/v2/stream.m3u8`**: playlist de publicidad, con `#EXT-X-MOUFLON-ADVERT` y `#EXT-X-PLAYLIST-TYPE:VOD`. |
| Variante **con** `&psch=v2&pkey=<token>` | 200 con la playlist real: `#EXT-X-MAP:URI=`, `#EXT-X-MOUFLON:URI:`, sin `ENDLIST`. |
| `playlistType=standard` | La media playlist trae **solo segmentos completos de 2s** (`#EXTINF:2.000`), sin `#EXT-X-PART`, sin `PART-INF`, sin `PRELOAD-HINT`. Los parts de 0.5s son un artefacto de que el reproductor pide `lowLatency`. |
| URLs de init y segmento | **Sin query string.** Cero tokens, cero firmas, cero expiración. GET pelado. |
| Cabeceras | Hacen falta las de `supervisor.py:121-125` (`Accept`, `Accept-Language`, `Upgrade-Insecure-Requests`, UA de Chrome). Sin ellas el sitio contesta 406. |
| Por el túnel VPN | Funciona. Probado con `Anavi_` (marcada `@vpn=` en `grabar.txt`) vía `ProxyHandler` de `urllib` a `127.0.0.1:25345`, con el detector real corriendo, sin interferirlo. |

### 2.2 El descifrado MOUFLON v2

La línea `#EXT-X-MOUFLON:URI:<url>` va **inmediatamente antes** de la línea HLS
estándar, que siempre apunta a un señuelo fijo (`.../media.mp4`). En la URL real
todo está en claro **menos el campo de hash**, que son 22 caracteres base64 en
vez de los 16 alfanuméricos del nombre de archivo verdadero.

```
token[::-1]  →  padding "=" hasta múltiplo de 4  →  base64.b64decode
             →  XOR contra sha256(pdkey).digest() ciclado
```

Validado **offline, 4/4**, contra pares que ya estaban en disco: los
`#EXT-X-MOUFLON:URI:` de
`datos\streams\_gaby2_171961503\playlists\171961503_1080p.m3u8` (msn 1849-1852)
contra los nombres reales que el detector había grabado en `segmentos\` de esa
misma sesión. Ejemplo:

```
gNmEqXlhpNtSoM7FD7my0L  →  7taceM4bsiPt9RHl
wOWpYF7NKauDddERS/E+UU  →  IwCrprs607kjry8a
AHjkaLXhbG6OoaaFj1qqHc  →  hBmYelledFpFJZMF
```

Los nombres descifrados **matchean `RE_SEGMENTO`** (`detectar_m3u8.py:246`) y dan
`clave_segmento()` válida → el layout de disco actual sirve tal cual, sin tocar
unión, `vivo.py` ni `cosecha_streams.py`.

### 2.3 Sobre la tabla de claves — leer antes de implementar

El master lista 11 tokens `#EXT-X-MOUFLON:PSCH:v2:<pkey>` en texto plano, los
mismos 11 para modelos distintas (es una tabla global rotativa).

**Solo tenemos UN par `pkey:pdkey` verificado:**

```
NTK9aqcLmNFMWrpQ  →  tOcYOap4Ty1l9Jzb
```

Se derivó comparando cifrado-contra-claro de material propio en disco. Los otros
10 `pdkey` **no los tenemos**: el repo de referencia
([aitschti/scp-standalone](https://github.com/aitschti/scp-standalone)) distribuye
un `keys.txt` que es una **plantilla vacía** (`"Put key pair in format
pkey:pdkey in this file"`) que cada usuario llena a mano sacando el par de una
sesión de navegador real. El algoritmo sí es público y coincide byte a byte
(`scp-standalone-v2.py:81-100`).

**Lo que salva esto, y está verificado en vivo:** el CDN acepta cualquiera de los
11 `pkey` que el propio master ofrece y **codifica la respuesta con ESE `pkey`**
— no hace falta que sea "el que le tocaba" a la sesión. Se comprobó pidiendo la
misma variante con un `pkey` arbitrario de la lista y viendo que el
`#EXT-X-MOUFLON:PSCH:v2:` de la respuesta era el que mandamos.

> **Consecuencia de diseño:** el grabador directo debe **forzar siempre el único
> `pkey` del que tiene `pdkey`**, nunca "tomar el primero del master". Esto es
> una decisión explícita, no un atajo.

### 2.4 Sostenido en el tiempo (fase 7 de la sonda)

| Corrida | Segmentos | Huecos reales | Duración remuxeada | Esperada |
|---|---|---|---|---|
| 10 min continuos | 303 | **0** | 606.28s | 606.00s |
| 2 min (smoke) | 63 | **0** | 126.00s | 126.00s |
| 10 min (3er intento) | 294 | 1 hueco real (msn 1467) por timeout bloqueante — ver §2.5 | — | — |
| 10 min (5to intento) | — | la modelo se fue offline a los ~25s (404 sostenido); la sonda lo detectó y siguió sin crashear | — | — |

El hueco de la corrida de 10 min continuos se verificó **contra los archivos
crudos en disco**, no solo contra el log: `msn` estrictamente consecutivo de
1433 a 1728, cero faltantes.

### 2.5 Tres trampas encontradas construyendo la sonda

No son del protocolo: son del *grabador*. Las tres se repiten en producción si no
se tienen en cuenta, así que el grabador directo tiene que nacer con ellas
resueltas.

**a) Las descargas no pueden bloquear el polling de la playlist.**
Un `TimeoutError` de 15s en UN segmento, en un loop secuencial, se tradujo en
**29 segmentos perdidos** (64s de video): mientras el GET estaba colgado no se
refrescó la playlist, y la ventana deslizante del CDN se comió segmentos que
nunca se habían ni intentado pedir. Las descargas van a un pool de hilos; el
loop solo somete trabajo y recolecta lo que ya terminó (`fut.done()`), nunca
espera.

**b) Un `#EXT-X-MAP` nuevo es una sesión nueva, no un hueco.**
La modelo reconectó el encoder a mitad de una corrida y el CDN empezó a servir
un `init_*` distinto. Concatenar segmentos de dos generaciones de encoder bajo el
init viejo produjo un `.mp4` con metadata rota: **294 segmentos, 1 sola falla de
continuidad, y la duración salió 66s en vez de ~588s**. Separando por init, la
sesión sana (261 segmentos) remuxeó a 522.00s exactos contra 522s esperados.
Esto es exactamente lo que `detectar_m3u8.py` ya resuelve con carpetas de sesión
separadas, y el grabador directo tiene que hacer lo mismo.

**c) El hueco se mide por `msn`, no por `epoch`.**
El campo epoch del nombre del segmento es el reloj del encoder y tiene jitter
normal: en una sesión sana de 303 segmentos, **292 deltas dieron exacto 2, cinco
dieron 1 y cinco dieron 3**, sin perder un solo `msn`. Medir huecos por delta de
epoch da falsos positivos. El número de secuencia (`msn`) sí es estrictamente
consecutivo y es la única señal válida.

**d)** (menor) Un segmento que da 404 justo al cortarse la emisión queda
asignado a la sesión pero sin archivo en disco. Al contar continuidad hay que
filtrar por "se bajó de verdad", no por "estaba en la playlist".

---

## 3. Decisiones tomadas

| Decisión | Valor | Por qué |
|---|---|---|
| El navegador **no se borra** | Queda de respaldo, apagado | El `pdkey` es prestado y no lo controlamos: si stripchat rota la tabla, el directo se cae **entero y de golpe**. El navegador es el único camino que no depende de ingeniería inversa, porque ahí el descifrado lo hace el player del sitio. |
| Modo por modelo | `@directo=1` global + `@navegador=<nombres>` como opt-out | Mismo mecanismo de "marca + lista con coma" que ya tiene `@vpn=`. Permite grabar la misma modelo por los dos caminos y comparar el `.mp4`. |
| Tipo de playlist | `playlistType=standard` | Sin parts: ¼ de requests y de archivos, y desaparece el riesgo de solape parts-vs-segmento entero. La latencia extra (~2s) es irrelevante para grabar. |
| `pkey` | Forzar `PKEY_VERIFICADO` siempre | §2.3. |
| Salida a red | Conexión propia (`urllib`), con `ProxyHandler` al túnel para `@vpn=` | Es lo que da el ahorro real: mitmdump no copia 2MB por segmento ni re-cifra TLS. El túnel por modelo queda más limpio que la cabecera `x-grab-tunel` actual. |
| Unión a `.mp4` | Reusar `detectar_m3u8.py --unir`, no reimplementar | Corre como `__main__`, así que **no** construye un `Detector` (no viola la prohibición de `CLAUDE.md`), y hereda `Reencadenador`, los 6 intentos de reparación, `_hay_espacio` y `_marcar_unida` sin duplicar nada. |
| `activos.txt` | **No se toca** | Invariante de `CLAUDE.md`: lo escribe solo el detector. Dos escritores sería una regresión. |

---

## 4. Piezas nuevas

| Pieza | Ruta | Responsabilidad |
|---|---|---|
| Sonda (ya existe) | `pruebas\sonda_directo.py` | Diagnóstico por fases. `--fase 0` es el **canario permanente** de rotación de claves: sin red, instantáneo. |
| Biblioteca de protocolo | `nucleo\scripts\mouflon.py` | Stdlib pura, **sin estado y sin efectos al importar**. Es la diferencia clave con `detectar_m3u8.py`: este módulo sí se puede importar desde cualquier parte. |
| Grabador | `nucleo\scripts\grabador_directo.py` | El proceso. Un hilo por modelo, escribe el layout de disco, publica `estado\directo.json`, para por stdin. |
| Lanzador | `nimbo\lanzador_directo.py` | Lo arranca `motor.py`; reenvía el logger `"directo"` a `estado\eventos_directo.jsonl` con rotación a 8 MB (`MAX_EVENTOS`). |
| Fusión de estado | `nimbo\estado_captura.py` | `fusionar(detector, directo) -> dict` con `streams` concatenado, para que `vivo.py`, `cosecha_streams.py` y el frontend no se enteren de dónde salió cada sesión. |

### 4.1 `nucleo\scripts\mouflon.py`

```python
PKEY_VERIFICADO = "NTK9aqcLmNFMWrpQ"
KEY_MAP = {PKEY_VERIFICADO: "tOcYOap4Ty1l9Jzb"}

RE_TOKEN_SEGMENTO = re.compile(r"_(\d+)_([A-Za-z0-9+/=]{22})_(\d{10})")

def descifrar_token(token_b64, pdkey) -> str
def es_token_valido(claro) -> bool          # ^[A-Za-z0-9]{16}$
def descifrar_playlist(texto) -> (texto_descifrado, fallos)
def url_master(id_modelo) -> str
def pedir_media(uri_variante, pkey, por_tunel) -> str
def es_publicidad(texto, url_final) -> bool
def resolver_id(nombre, por_tunel) -> int
def consultar_cam(id_modelo, por_tunel) -> dict
```

El regex de token va **anclado**: `_(\d+)_([A-Za-z0-9+/=]{22})_(\d{10})`,
reemplazando solo el grupo 2. El del repo de referencia
(`_([A-Za-z0-9+/=]{10,})`) es frágil — el epoch tiene 10 dígitos y puede
matchear primero.

**Movimiento recomendado:** llevar `leer_master()`, `Rendicion`,
`mejor_rendicion()` y `puntaje()` **desde** `detectar_m3u8.py:847-911` **hacia**
`mouflon.py`, y que el detector los importe de ahí. Como `mouflon.py` es
importable sin efectos, esto **elimina** duplicación en vez de crearla. Es el
único cambio estructural que se le hace al detector, y el más delicado del plan:
hay que verificar que sigue grabando igual antes de seguir.

### 4.2 `nucleo\scripts\grabador_directo.py`

Un hilo por modelo. Cada vuelta del hilo:

1. Si venció el chequeo de estado (60s): `consultar_cam()`. Si el estado no está
   en `ESTADOS_VIVOS` (portado de `supervisor.py:134`), cerrar sesión.
   Reusar el criterio de `estado_incierto()` (`supervisor.py:137-148`): un 5xx
   o un 429 **no** significa "dejó de emitir".
2. Si no hay sesión abierta: `url_master()` → `leer_master()` →
   `mejor_rendicion()` según `@calidad`/`@codec`/`@fallback` → abrir sesión.
3. Refrescar la media playlist cada `TARGETDURATION/2` (1s).
4. **Compuerta dura antes de escribir un solo byte:** `es_publicidad()`. Sin
   esto se graban 6 chunks de publicidad creyendo que es la emisión.
5. Descifrar los `#EXT-X-MOUFLON:URI:`, validar que el 100% da 16 alfanuméricos
   y que `clave_segmento()` no es `None`.
6. Someter las descargas nuevas al pool de hilos (§2.5a). Dedupe por nombre de
   archivo, igual que `var.vistos` del detector.
7. Si el `#EXT-X-MAP` cambió → **cerrar la sesión actual y abrir una nueva**
   (§2.5b), y lanzar la unión de la que se cerró.
8. Si el descifrado falla 3 veces seguidas → marcar `degradado`, avisar
   `nivel="error"` y **caer al navegador para esa modelo** (§6).

Parada por **stdin** (`"parar\n"`), nunca por `kill` — mismo contrato que los
otros dos hijos (`CLAUDE.md`, "Tres procesos").

### 4.3 Layout en disco — idéntico al actual

```
datos\streams\<modelo>_<id>\<variante>_<AAAAMMDD-HHMMSS>\
    init_<nombre>.mp4          <- prefijo "init_" si no lo trae, como _guardar
    segmentos\<nombre>.mp4
    info.json                  <- mismo dict que _finalizar
    unido.json                 <- lo escribe la unión, no el grabador
datos\streams\<modelo>_<id>\playlists\<id>_auto.m3u8, <id>_<variante>.m3u8
```

Las playlists se guardan con el **texto original cifrado**, para no perder la
fuente de verdad del diagnóstico.

El patrón del `Escritor` (hilo + `queue`, `.part` + `os.replace`) se **porta**,
no se importa.

### 4.4 `estado\directo.json`

Schema **idéntico** al de `puente.py:200-264` (`generado`, `streams[]` con `id`,
`modelo`, `mejor`, `elegida`, `variantes`, `estado`, `grabando`, `privado`,
`inicio`, `ultimo_visto`, `segmentos`, `bytes`, `sesiones[]`), más
`{modo: "directo", degradado: bool, ids: {nombre: id}}`.

Atómico (`.part` + `os.replace`) y **se reescribe siempre aunque no cambie
nada**, cada 2s: su `mtime` es el latido, mismo truco que `puente.py`.

Si el schema es idéntico, `vivo.py` y el frontend funcionan sin saber de dónde
salió la sesión.

---

## 5. Qué se toca de lo que ya existe

| Archivo | Cambio |
|---|---|
| `nucleo\scripts\grabar.template.txt` | Documentar `@directo=1` y `@navegador=`. |
| `detectar_m3u8.py` — `Config`/`Seleccion._parsear` | Parsear las dos directivas nuevas para que no loguee "directiva desconocida" (`:501`). **Nada más**: el grabador directo no pasa por el proxy, así que el detector nunca ve ese tráfico y no hay conflicto. |
| `detectar_m3u8.py` — master | Importar `leer_master`/`mejor_rendicion` desde `mouflon.py` (§4.1). |
| `supervisor.py` — `Lista.actual()` | Una modelo en modo directo **no** entra a `emisiones` → no se abre pestaña. Tres líneas en el bucle de `nombres` (`:796-817`). Único cambio funcional del supervisor. |
| `nimbo\motor.py` | Cuarto hijo. `_arrancar_directo()` calcado de `_arrancar_supervisor()` (`:433-452`), entrada en `_reintentos`/`_proximo`/`_vivo_desde`. **Orden:** arranca detector → supervisor → directo; para directo → supervisor → detector. En `_reiniciar("detector")` **no** hay que relanzar el directo (no depende del proxy), a diferencia del supervisor. |
| `nimbo\servidor.py` | Los call sites de `:203`, `:209`, `:365`, `:396`, `:431` pasan por `estado_captura.fusionar()`. `_bucle_estado` compara la firma del dict fusionado. |
| `nimbo\registro.py:26` | Agregar `"directo": "eventos_directo.jsonl"`. |
| `nimbo\ajustes.py` | Property `estado_directo`; `_NUCLEO_REQUERIDO += ("mouflon.py", "grabador_directo.py")`. |
| `CLAUDE.md` | §10. |

### 5.1 Directivas de `grabar.txt` en modo directo

| Directiva | En directo |
|---|---|
| `@calidad=` / `@codec=` / `@fallback=` | Funcionan igual, y **mejor**: se eligen del master antes de bajar un byte, sin ABR que las pelee. |
| `@variantes=todas` | Soportado, y barato de verdad: N playlists y N descargas, sin N renderers. |
| `@forzar=` | **Se ignora.** No hay player al que recortarle el master. Loguearlo una vez por arranque para que no parezca un bug. |
| `@cambio=` | **Se ignora.** La variante se elige una vez. Si cambia el master (la modelo reconfigura OBS), se detecta en el refresco y se corta sesión, como `_cambiar()`. |
| `@espera=` | **Se ignora.** El master lista todas las variantes de una. |
| `@privado=160p` | Mejor que hoy: `cam.show` y `user.user.status` lo dicen explícitamente **antes** de bajar. Se mantiene el chequeo por altura como red de seguridad. |
| `@vpn=` | El grabador manda **sus propios** pedidos por el túnel (`ProxyHandler`). Verificado. |
| `@inactividad=` | Igual, mismo temporizador. |

---

## 6. Riesgos y degradación

| Riesgo | Señal | Respuesta |
|---|---|---|
| **Rotación del `KEY_MAP`** (alta × alto) | `--fase 0` falla, o el token descifrado deja de matchear `^[A-Za-z0-9]{16}$` | 3 fallos seguidos → `degradado`, aviso `nivel="error"`, **fallback al navegador por esa modelo**. Es el riesgo que justifica no borrar el supervisor. |
| 302 a `/cpa/` | `es_publicidad()` | Compuerta dura antes del primer byte. **No negociable.** |
| Cloudflare en la API del sitio | `user-ids`/`cam` dan 403 | Se pierde descubrimiento de id/estado, **no** la bajada: con el id conocido (está en el nombre de la carpeta) se va directo al master. Cachear id↔nombre en `estado\ids.json`. Dominios espejo: los mismos de `TUNEL_HOSTS` (`detectar_m3u8.py:180`). |
| El CDN empieza a firmar segmentos | 403 en el GET del segmento | Hoy no hay ninguna firma. Sería el fin del camino directo; cubre el fallback. |
| Geobloqueo | `isGeoBanned`, o 403 del master | Pedidos por el túnel. Ya verificado. |
| Timeout bloqueante | huecos de `msn` | Pool de hilos (§2.5a). |
| Reconexión de encoder | `#EXT-X-MAP` nuevo | Sesión nueva (§2.5b). |

---

## 7. Orden de trabajo

1. **`nucleo\scripts\mouflon.py`** — sacar la biblioteca de la sonda. Mover
   `leer_master`/`Rendicion`/`mejor_rendicion`/`puntaje` desde
   `detectar_m3u8.py` y hacer que el detector importe de ahí.
   **Puerta:** el detector sigue grabando igual que antes. Es el cambio que toca
   el camino de producción, así que se valida solo, antes de todo lo demás.
2. **`grabador_directo.py` en seco** — `--una-vez <nombre> --minutos 5`,
   escribiendo el layout real en un `M3U8_BASE` de prueba.
   **Puerta:** `python detectar_m3u8.py --unir <sesion>` produce un `.mp4`
   indistinguible del que sale hoy.
3. **`estado\directo.json`** + `nimbo\estado_captura.py`.
   **Puerta:** el reproductor en vivo y la cosecha funcionan contra una sesión
   directa **sin tocar `vivo.py` ni `cosecha_streams.py`**.
4. **Directivas `@directo`/`@navegador`** en `Config`/`Seleccion` y en
   `supervisor.Lista`.
   **Puerta:** una modelo marcada no abre pestaña.
5. **`motor.py`** — cuarto hijo, orden de arranque/parada, vigilancia.
   **Puerta:** parada ordenada por stdin; una caída del directo **no** relanza
   el detector.
6. **`servidor.py` + `registro.py` + `ajustes.py`** — fusión de estado y
   pestaña de registro.
7. **Mediciones** (§8).
8. **`CLAUDE.md`** (§10), commit aparte, solo documentación.

---

## 8. Plan de medición

**Línea base (hoy, navegador), 5 modelos, 30 min:**
- RAM: `Get-Process chrome,mitmdump | Measure-Object PrivateMemorySize64 -Sum`,
  muestreado cada 10s.
- CPU: `Get-Counter "\Process(chrome*)\% Processor Time"`, normalizado por
  núcleos.
- **Segundos perdidos** — la métrica que importa: por cada sesión, contar `msn`
  faltantes en `segmentos\` y comparar `ffprobe -show_entries format=duration`
  contra el tiempo de pared.

**Hipótesis a falsificar (no a confirmar):**
1. RAM: el directo usa <40 MB por modelo vs ~250-400 MB/renderer. Si no baja
   al menos 5×, está mal implementado.
2. CPU: <2% de un núcleo por modelo (es base64+XOR de 3 KB y un write de 2 MB
   por segundo). Si no baja al menos 10×, idem.
3. **Segundos perdidos: el directo no puede perder más que el navegador.** Es lo
   único que puede invalidar todo el trabajo. Medir con **la misma modelo por
   los dos caminos** (una en `@navegador=`, otra en directo, después invertir)
   para controlar por la emisión.

**Escalera:** 5, 10, 15, 20, 25, 30 modelos, 20 min por escalón. Techo en el
primer escalón donde se rompa *cualquiera* de: segundos perdidos > 1% en alguna
sesión; `Escritor` descartando por cola llena; o la unión de una sesión al
cerrar bloqueando a las demás.

**Predicción honesta:** el cuello se va a mover de RAM/CPU a **ancho de banda y
disco**. 1080p60 son ~8.1 Mbit/s medidos por modelo → 30 modelos ≈ 240 Mbit/s
sostenidos y ~1 GB/minuto a disco. Conviene medir `dias_segmentos` y la cosecha
en ese régimen antes de prometer un número de modelos.

---

## 9. Lo que NO se hace

- **No** se importa `detectar_m3u8.py` desde el proceso de Nimbo
  (`CLAUDE.md`): su `else: addons = [Detector()]` construye un detector vivo.
  La unión se invoca como **subproceso** `--unir`.
- **No** se renombra `nucleo\scripts\`: tanto el detector como el supervisor
  calculan su base con `SUBCARPETAS_SCRIPT`.
- **No** se escribe `activos.txt` desde el grabador directo.
- **No** se toca la ofuscación del lado del reproductor ni el perfil
  `nucleo\perfil\` (exclusivo del supervisor).
- **No** se borra el supervisor ni mitmdump en esta rama. El `_Tunel` vive dentro
  del detector y `motor.py` tiene un watchdog entero construido alrededor;
  `sitio.py` sale por ese proxy a propósito. Reemplazo total = otra rama, y solo
  después de un mes sin caer al fallback.

---

## 10. Documentación a corregir

La premisa falsa (§1) aparece en:
- `CLAUDE.md`, sección **"Tres procesos"** (descripción del supervisor).
- `CLAUDE.md`, sección **"Reproductor en vivo"** ("sin tocar nunca la
  ofuscación `#EXT-X-MOUFLON`").
- `nucleo\scripts\supervisor.py:12-15` (docstring).

Y hay que agregar:
- `CLAUDE.md`: "Tres procesos" → cuatro; tabla **"Quién escribe qué"** gana
  `estado\directo.json` (lo escribe `grabador_directo.py`, lo lee Nimbo) y
  `estado\eventos_directo.jsonl`.
- Nota de que `nucleo\scripts\mouflon.py` **sí** es importable sin peligro, y
  por qué (contraste explícito con `detectar_m3u8.py`).

---

## 11. Verificación de la sonda (ya disponible)

```bat
rem canario de claves: sin red, instantáneo
python pruebas\sonda_directo.py cualquiera --fase 0

rem cadena completa contra una modelo en vivo
python pruebas\sonda_directo.py <modelo_online> --fase 6

rem sostenido, la puerta de decisión
python pruebas\sonda_directo.py <modelo_online> --fase 7 --minutos 10

rem por el túnel (requiere el detector arriba: wireproxy es su hijo)
python pruebas\sonda_directo.py <modelo_con_vpn> --fase 2 --vpn
```

La sonda escribe **solo** bajo `--salida` (por defecto
`%TEMP%\nimbo_sonda\`): nunca en `datos\streams\`, `estado\` ni `nucleo\`.
Después de correrla, `git status` tiene que seguir limpio.
