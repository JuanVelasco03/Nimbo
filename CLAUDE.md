# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

El código, los comentarios y la documentación de este repo están **en
español** (rioplatense, nombres de módulos/funciones/variables incluidos:
`motor.py`, `arrancar()`, `dir_estado`). Mantener ese idioma al escribir
código nuevo.

## Qué es

Nimbo es una aplicación de escritorio para Windows que graba streams de
stripchat/doppiocdn. No es una app web: `nimbo\app.py` levanta un servidor
HTTP en `127.0.0.1` y abre Edge en modo `--app` contra él, con perfil
propio (`perfil_ventana\`). La ventana es solo la interfaz; el trabajo real
lo hacen tres procesos hijos.

## Comandos

```bat
rem arrancar la app (lo que hace arrancar.bat / Nimbo.lnk)
C:\Python312\pythonw.exe nimbo\app.py

rem servidor solo, con consola y log visible (sin bandeja ni ventana)
python nimbo\servidor.py

rem dependencias de la app (opcionales: sin ellas cae a modo sin bandeja)
pip install -r requirements.txt

rem CSS (solo si se edita nimbo\web\tailwind.css; app.css ya viene compilado)
npm install
npm run build:css        rem o watch:css
```

Operaciones del motor de captura que la interfaz **no** cubre, a mano:

```bat
python nucleo\scripts\detectar_m3u8.py --listar
python nucleo\scripts\detectar_m3u8.py --unir [ID ...] [--forzar]
python nucleo\scripts\supervisor.py --login

rem diagnostico del camino directo (no toca grabar.txt ni estado\)
python nucleo\scripts\grabador_directo.py --una-vez <modelo> --minutos 5
```

No hay suite de tests ni linter configurados en el repo.

### Al correr fuera de la instalación real

`NIMBO_BASE=<carpeta>` redirige la raíz entera (config, estado, datos) sin
tocar la instalación de verdad — ver `nimbo\ajustes.py`. `M3U8_BASE` hace
lo mismo para grabaciones/streams del lado del detector; `motor.py` lo fija
con `setdefault`, así que un valor propio del entorno no se pisa.

## Arquitectura

### Cuatro procesos

1. **`nimbo\app.py`** (el que abre el usuario) — servidor HTTP + ventana de
   Edge + icono de bandeja (`pystray`). Instancia única vía
   `instancia.json`: si Nimbo ya corre, una segunda ejecución solo abre otra
   ventana contra ESE servidor y termina (si no, se pelearían por el puerto
   8080 del proxy y por el perfil de Chromium).
2. **El detector** — `mitmdump -s nucleo\scripts\detectar_m3u8.py -s nimbo\puente.py`.
   Es el que graba: intercepta el HLS/CMAF de doppiocdn, guarda init +
   segmentos y los une a `.mp4` con ffmpeg.
3. **El supervisor** — `python nimbo\lanzador_supervisor.py`, que importa
   `nucleo\scripts\supervisor.py`. Mantiene un Chromium de Playwright con
   una página por modelo para que el reproductor del sitio pida el stream.
   Durante mucho tiempo se creyó que esto hacía falta porque "las URLs
   vienen ofuscadas con `#EXT-X-MOUFLON` y solo ese reproductor sabe
   pedirlas" — **es falso** (verificado en `specs\grabador_directo.md` S1 y
   `pruebas\RESULTADOS.md`): el navegador no descifra nada localmente, solo
   le presenta al CDN una `pkey` que viaja en texto plano dentro del propio
   master playlist. El supervisor sigue en pie como **respaldo**: el camino
   directo (punto 4) depende de ingeniería inversa de un protocolo ajeno, y
   si stripchat rota la tabla de claves el supervisor es el único camino
   que sigue funcionando sin cambiar una línea.
4. **El camino directo** — `python nimbo\lanzador_directo.py`, que importa
   `nucleo\scripts\grabador_directo.py`. Graba sin navegador ni proxy:
   descifra el protocolo MOUFLON v2 con `nucleo\scripts\mouflon.py` (único
   módulo del núcleo que **sí** se puede importar sin peligro, ver "Los dos
   puentes") y baja init + segmentos con pedidos HTTP pelados, un hilo por
   modelo. Solo corre para las marcadas `@directo=1` en `grabar.txt` (menos
   las de `@navegador=`, opt-out); por eso el supervisor nunca les abre
   pestaña (`supervisor.Lista.directo`, filtrado en `supervisar()`). Si el
   descifrado falla 3 veces seguidas para una modelo (tabla de claves
   rotada, CDN cambiado) esa modelo queda **sin grabar** hasta que el
   usuario la pase a mano a `@navegador=` — no hay fallback automático
   editando `grabar.txt`, porque ese archivo lo escribe solo Nimbo
   (`lista.py`, ver "Quién escribe qué").

`nimbo\motor.py` los arranca y para. **El orden importa**: arranca detector
→ supervisor → directo; para directo → supervisor → detector. La parada va
por **stdin** (`"parar\n"`), nunca por `kill`: los tres hijos se abren con
`stdin=PIPE` y ese extremo se mantiene abierto justo para esto. Matar el
detector a lo bruto deja grabaciones sin unir; matar el supervisor deja
Chromium huérfano y el perfil con cerrojos; matar el directo puede dejar
una unión a medias (delega en `detectar_m3u8.py --unir` como subproceso).
La parada del detector puede tardar **minutos**
(`ESPERA_CIERRE_DETECTOR = 960s`) porque espera a que termine de unir; la
del directo también (`ESPERA_CIERRE_DIRECTO = 1260s`), por el mismo motivo.

Si alguno se cae solo, `Motor._bucle_vigilancia` lo relanza sola: backoff
5s/15s/60s y, agotados esos tres intentos, "rendido" (aviso
`nivel="error"`, visible en la ventana y en el tooltip de la bandeja) pero
sin dejar de probar cada 15 minutos — una caída a las 3 de la mañana no
tiene que dejar la noche entera sin grabar. Si el que muere es el
detector se relanzan **detector y supervisor**, en el mismo orden de
siempre (cerrar páginas por stdin → detector → supervisor): las pestañas
viejas del supervisor no vuelven a pedir el stream solas, así que dejarlas
vivas deja dos procesos en pie y cero grabación. Si el que muere es el
supervisor alcanza con relanzar solo eso. **Si el que muere es el
directo, también alcanza con relanzar solo eso**: no depende del proxy ni
abre páginas, así que una caída suya no se lleva puesto a los otros dos
(`Motor._reiniciar("directo")`). El relanzado nunca actúa si el usuario no
pidió arrancar (`Motor._deseado`) ni mientras hay una parada en curso
(`_parando` / `_parada_pedida`) — eso es lo que distingue "se murió solo"
de "lo paramos nosotros", igual que ya hacía `parar()` antes de esto. Una
caída a lo bruto puede dejar sesiones a medias en `datos\streams\`; es
benigno, porque el detector las retoma solo con `recuperar_pendientes()`
al arrancar de nuevo (cubre tanto las suyas como las que dejó el directo a
medio unir, porque las dos comparten el mismo `datos\streams\`).

Hay un cuarto proceso que nadie lanza a mano: `wireproxy.exe`, el túnel
de la VPN, hijo del detector (clase `_Tunel`). Sobrevive a su padre, y
entonces el detector siguiente lo **adopta** (`_Tunel.run()` ve el puerto
ocupado, se da por listo y termina) — un túnel con el `.conf` viejo que
ya nadie vigila ni cierra. Por eso `motor.py` trae un watchdog propio:
busca el proceso por nombre + dueño del puerto del túnel
(`CreateToolhelp32Snapshot` + `GetExtendedTcpTable` por `ctypes`, sin
psutil) y mata solo al que tiene el padre muerto — antes de arrancar el
detector, cada `INTERVALO_TUNEL`, y al detectar que el detector se cayó.
Si el adoptado estaba en uso, Nimbo relanza el túnel él mismo con
`tunel_wireproxy.conf` y lo cierra en la parada. Lo que el usuario corrió
a mano (padre vivo que no es el detector) no se toca: solo se avisa.

Dos interpretes de Python distintos, a propósito: el de la ventana solo
necesita stdlib + `pystray`/`Pillow`; el del supervisor necesita Playwright
y `ajustes.localizar_python()` lo busca entre varios instalados. El
detector corre con el Python empaquetado de mitmdump — por eso `puente.py`
usa **solo librería estándar**. El camino directo también usa solo
librería estándar (no le hace falta Playwright), pero `motor.py` lo
arranca con el mismo intérprete que el supervisor (`a["python"]`) en vez
de inventar una tercera forma de encontrar un Python: no cuesta nada y
simplifica `ajustes.py`.

### Los dos puentes (observar sin modificar)

El motor de captura no se toca: se lo mira desde fuera. Esto es la decisión
de diseño central del repo.

- **`nimbo\puente.py`** — addon de mitmproxy que se carga *junto* al
  detector (`-s detector -s puente`), nunca en su lugar. Alcanza la
  instancia viva con `ctx.master.addons.get("detector")` y publica
  `estado\detector.json` cada 2s. Escribe **siempre** aunque no cambie
  nada: su `mtime` es el latido que le dice a Nimbo que el puente vive.
- **`nimbo\lanzador_supervisor.py`** — importa `supervisor.py` y hace
  `supervisor.Emision = _EmisionVigilada` (subclase que se auto-registra)
  *antes* de llamar a `supervisor.supervisar()`; el dict de `Emision` es
  local a esa función y no se puede leer de otra forma. Publica
  `estado\supervisor.json` cada 5s.

Los dos reenvían además sus loggers (`"detector"` / `"supervisor"`) a
`estado\eventos_*.jsonl`. `motor.py` hace lo mismo con el stdout de sus
hijos y sus propios avisos, en `estado\eventos_motor.jsonl`. Todos rotan
por tamaño a los 8 MB (`MAX_EVENTOS`, duplicado a propósito en varios
módulos — mismo motivo que la duplicación de "Los dos puentes" más abajo)
con un único archivo viejo de sufijo `.1`; `registro.py` sabe cruzar del
actual al `.1` sin perder ni repetir filas.

**Nunca importar `detectar_m3u8.py` desde el proceso de Nimbo.** Ese módulo
termina en `else: addons = [Detector()]`, así que cualquier import que no
sea `__main__` construye un `Detector` entero vivo — con su `Escritor`, su
`_Tunel` y sus hilos — dentro de `app.py`. Por eso `nimbo\mp4.py` y
`nimbo\vivo.py` **portan deliberadamente** constantes y el recorrido de
cajas MP4 del detector en vez de importarlo; si se cambia algo allá, hay
que sincronizarlo acá a mano (los comentarios de esos módulos citan las
líneas originales). Por el mismo motivo, `nimbo\lanzador_directo.py` y
`nucleo\scripts\grabador_directo.py` invocan la unión como **subproceso**
(`python detectar_m3u8.py --unir <sesión>`), nunca como import. El filtro
que se le pasa va **completo**, `<modelo>_<id>/<variante>_<timestamp>`
(`Sesion.ref`, no `Sesion.etiqueta`): `--unir` trata un filtro con `/`
como coincidencia exacta y sin `/` como substring, y el nombre de sesión
suelto es el mismo para todas las modelos que arrancaron el mismo segundo
con la misma variante — `motor.py` las arranca todas juntas, así que el
substring se llevaba puestas las sesiones **vivas** de las otras y les
dejaba un `unido.json` con dos minutos encima de una grabación en curso.
Como red de seguridad, `--unir` con filtro laxo (o sin filtro) saltea
cualquier sesión que **siga creciendo** (dos huellas de la carpeta de
segmentos separadas por 3s, solo si el archivo más nuevo tiene menos de
30s); un filtro exacto no pasa por ahí, porque pedir esa sesión y nada
más ya significa que se acaba de cerrar. Y el `[UNIR] ... listo` del
directo se gana comparando el **mtime** del `unido.json` antes y después
de correr el hijo, no con un `os.path.isfile` pelado: `--unir` saltea sin
ruido una sesión que ya tenga marcador (`s.unida and not forzar`), así
que con uno viejo encima el `isfile` daba `True` y se logueaba "listo"
sin haber unido nada — y el resto de esa grabación no se une nunca más.
El resumen de lo que hizo el hijo sale de su **stderr** y su stdout
(`logging` escribe en stderr, leer solo stdout dejaba el resumen
siempre vacío).

`nucleo\scripts\mouflon.py`, en cambio, **sí es importable sin peligro**
desde cualquier lado: es protocolo puro (descifrado MOUFLON v2, lectura
del master `.m3u8`, elección de la mejor rendición, HTTP mínimo), sin
ningún `addons = [...]` ni estado de módulo mutable — importarlo no
construye ni arranca nada. Por eso lo importan por igual `detectar_m3u8.py`
(que antes tenía `leer_master`/`Rendicion`/`mejor_rendicion`/`puntaje`
duplicados ahí mismo, y ahora los reexporta desde `mouflon.py`) y
`grabador_directo.py` (que además usa `descifrar_playlist`/
`extraer_segmentos`/`pedir`/`resolver_id`/`consultar_cam`). El canario
sin red (`mouflon.verificar_canario()`) es la señal de que la tabla de
claves (`KEY_MAP`) sigue vigente; si deja de dar `True`, seguir
`pruebas\RESULTADOS.md` sección 1 para derivar una nueva sin esperar a
que alguien la publique.

### El núcleo copiado

`nucleo\` es el motor de captura copiado dentro de Nimbo: el proyecto no
lee nada de `F:\grabador` en runtime. El nombre **`nucleo\scripts\`** no es
cosmético: tanto `detectar_m3u8.py` como `supervisor.py` calculan su propia
base con `SUBCARPETAS_SCRIPT = {"scripts","script","bin","src"}` — si el
basename de su carpeta está en ese set, la base es el padre. Renombrar
`scripts\` rompe dónde buscan el perfil y las grabaciones.

En git van los `.py` y `grabar.template.txt`; **no** van `ffmpeg.exe`,
`wireproxy.exe`, los `.conf` del túnel, `activos.txt` ni `nucleo\perfil\`.
`grabar.txt` tampoco: tiene a quién se graba (datos de usuario), así que
está en `.gitignore` y lo crea solo el detector en el primer arranque si
no existe (`detectar_m3u8.py`, bloque de `Detector.__init__` que escribe
la plantilla mínima). `grabar.template.txt` es la versión documentada
completa, para copiar a mano si se quiere algo más rico que esa
plantilla mínima; nadie la lee en runtime.

### Quién escribe qué (invariantes de estado)

| Archivo | Lo escribe | Lo lee |
|---|---|---|
| `nucleo\scripts\grabar.txt` | solo Nimbo (`lista.py`) | detector (cada 2s), supervisor (cada 1s, acortado por `lanzador_supervisor.py` desde los 5s originales), camino directo (cada 2s, `LectorGrabar`) |
| `nucleo\scripts\activos.txt` | solo el detector | supervisor y camino directo (traducen ID→nombre); nadie en Nimbo |
| `estado\detector.json` | `puente.py` | Nimbo |
| `estado\supervisor.json` | `lanzador_supervisor.py` | Nimbo |
| `estado\directo.json` | `grabador_directo.py` | Nimbo (`estado_captura.fusionar()` lo junta con `detector.json`) |
| `estado\ids.json` | `grabador_directo.py` | `grabador_directo.py` (cache propia id↔nombre) |
| `estado\eventos_motor.jsonl` | `motor.py` | Nimbo (`registro.py`) |
| `estado\eventos_directo.jsonl` | `lanzador_directo.py` | Nimbo (`registro.py`) |
| `estado\siguiendo.json` | Nimbo (`sesion.py`) | Nimbo |
| `config.json`, `favoritas.json`, `sesion.json`, `estado\vivo_temporal.json` | solo Nimbo | Nimbo |
| `datos\grabaciones\*.mp4` | solo el detector | Nimbo (lee / borra a papelera) |
| `datos\streams\` | el detector escribe y marca con `unido.json`; Nimbo (`cosecha_streams.py`) borra a papelera lo ya unido | Nimbo |

`grabar.txt` es la **única fuente de verdad** de a quién se graba, y Nimbo
es su único escritor — de ahí que no haga falta locking. El usuario lo
puede editar a mano, así que `lista.py` **edita in-place línea por línea** y
conserva byte a byte comentarios, orden y notas al final; nunca regenera el
archivo desde una plantilla, y ninguna clase cachea su contenido entre
llamadas.

Todo lo que Nimbo escribe va **atómico**: `.part` + `os.replace`. Los
lectores (`_leer_json`) tragan `OSError`/`ValueError` y devuelven `None`.

### Servidor y frontend

`nimbo\servidor.py` es un solo `asyncio.start_server` en `127.0.0.1`,
sin framework: una corutina por conexión (`_atender`) que lee el
preámbulo HTTP y demultiplexa. Si trae `Upgrade: websocket` y el destino
es `/ws`, esos mismos bytes crudos se re-alimentan a
`websockets.server.ServerProtocol` (la capa *sans-I/O* de la librería:
solo entiende bytes y eventos, no abre sockets) para el framing; si no,
se atiende como HTTP común (`_manejar_get`/`_manejar_post`). El HTTP es
propio a propósito: `websockets.asyncio.server.serve()` no sabe
transmitir un cuerpo en bloques, y `_servir_video` tiene que poder mandar
un `.mp4` de varios GB sin cargarlo entero en memoria. Exige `?t=<token>`
— un `secrets.token_urlsafe(24)` nuevo por arranque — en `/`, todo
`/api/*` y en la query del handshake de `/ws`. Los estáticos (`app.css`,
`js\*.js`) **no** llevan token a propósito: los imports del navegador
usan URLs relativas sin query y quedarían en 403.

No hay ningún hilo bloqueado por conexión (`ThreadingHTTPServer` lo
tenía: un hilo por SSE abierto, para siempre). Todo lo síncrono —
disco, subprocess, red saliente, los `threading.Lock` que ya tienen
`lista.py`/`sesion.py`/`sitio.py`/etc — pasa por
`Aplicacion.a_hilo()`, que lo corre en un `ThreadPoolExecutor` propio
(tope `EJECUTOR_HILOS`); nunca se llama un módulo de `nimbo\` directo
desde una corutina. `Motor` sigue avisando desde sus propios hilos (el
de vigilancia, los lectores de stdout) — `Aplicacion._en_evento` cruza
al loop con `loop.call_soon_threadsafe` antes de publicar nada, mismo
patrón que ya usa `lanzador_supervisor.py` con su hilo de stdin.

- `/ws` reemplaza al `/eventos` de SSE: un canal bidireccional por
  pestaña, con canales suscribibles (`estado`, `motor`, `sitio`) y una
  cola por conexión (`_Conexion.cola`) que descarta el mensaje más viejo
  si el cliente se cuelga — nunca bloquea a quien publica, nunca una
  pestaña colgada afecta a otra. Al conectar manda `estado_inicial` (la
  foto completa); de ahí en más solo manda **deltas** (`tipo: "estado"`,
  solo las secciones que cambiaron) — `servidor.py:_bucle_estado` es
  quien decide qué cambió, comparando firmas baratas (`Lista.firma()`
  para `grabar.txt`; el contenido ya parseado para la clave `"detector"`,
  que en realidad es `estado\detector.json` + `estado\directo.json`
  fusionados por `estado_captura.fusionar()` — los dos reescriben su JSON
  siempre, aunque no cambie nada, como latido). El
  ping/pong del WebSocket es manual (`_ws_escritor`): `ServerProtocol` es
  sans-I/O puro, no lo hace solo.
- El refresco de "Modelos" contra el sitio real (`sitio.buscar`,
  `sitio.estado_cacheado`) es una tarea del servidor
  (`servidor.py:_bucle_sitio`), no de cada pestaña: corre cada 15s
  mientras alguien esté suscrito al canal `sitio` y se apaga sola si no
  hay nadie. La pestaña se suscribe/desuscribe al entrar/salir de
  "Modelos" (`main.js`, mensajes `{tipo:"suscribir"}` /
  `{tipo:"cancelar"}`) — eso es lo que de verdad cancela el lote, no un
  `AbortController` del lado del cliente (no hay ninguno en el repo).
- Lo que tarda (`/api/motor/arrancar`, `/api/motor/parar`,
  `/api/sesion/actualizar`) sigue respondiendo **202** y lanzando una
  tarea (`_lanzar_tarea`, con dedupe: dos POST seguidos no duplican el
  trabajo); el progreso real llega por el canal `motor` del WebSocket.
  Nunca dejar colgada una respuesta HTTP esperando al motor.
- Tres formas distintas de servir bytes, no intercambiables:
  `_servir_estatico`, `_servir_video` (con `Range`/206, para saltar
  dentro de un `.mp4` de varios GB, en bloques de 1 MiB) y
  `_servir_bytes` (`Accept-Ranges: none` — un init o segmento CMAF se
  appendea entero a un `SourceBuffer` y una 206 parcial rompe
  `appendBuffer`). `_resolver_estatico` cierra el traversal por
  Windows (`\` o `:` en un componente se rechaza antes del `join`) con
  el mismo patrón que `grabaciones.py:_resolver`/`vivo.py:_resolver`
  (`realpath` + `commonpath`).
- `websockets` (sin dependencias propias) es la única dependencia
  **obligatoria** de `requirements.txt` — a diferencia de
  `pystray`/`Pillow`, que son opcionales (sin ellas Nimbo cae a modo sin
  bandeja). `app.py` envuelve su propio import en un `try/except
  ImportError` para fallar con un mensaje claro en vez de un traceback.

El frontend son módulos ES nativos sin build (`nimbo\web\js\`): `main.js`
orquesta y cada `vista_*.js` se pinta sola. `api.js:conectarWS` reemplaza
al `EventSource` de antes — WebSocket no reconecta solo, así que el
backoff (0.5s → 5s tope) es a mano. `main.js` ya no tiene ningún
`setInterval` pidiendo `/api/estado`: `conMensaje()` mezcla cada delta
sobre `ultimoEstado` (`{...ultimoEstado, ...msg.datos}`) antes de
repintar, así los seis `render*` de `pintarEstado()` no sabían ni saben
que la mitad de las veces el objeto les llega incompleto a propósito.
`api.js:enviarWS` es el único canal de salida que un módulo lejano
(`vista_previa.js`, el latido de "ver en vivo" temporal) necesita sin
que `main.js` le pase el cliente WebSocket a mano por cada import. El
CSS sí pasa por Tailwind v4 configurado en el propio `tailwind.css`
(`@theme`, `@source`), compilado a `app.css`, que **se versiona** — el
runtime no necesita Node.

### Reproductor en vivo

El modal de previa muestra video real, no snapshots, sin hls.js y sin que
`vivo.py` tenga que tocar la ofuscación `#EXT-X-MOUFLON`: venga la sesión
del supervisor (el Chromium ya resolvió esas URIs) o del camino directo
(`mouflon.py` ya las descifró), los fMP4/CMAF quedan escritos igual en
`datos\streams\<modelo>_<id>\<variante>_<sesion>\` — mismo layout, mismo
`clave_segmento()`. La cadena es `puente.py`/`grabador_directo.py` (campo
`sesiones`/`streams` de `detector.json`/`directo.json`, unificados por
`nimbo\estado_captura.fusionar()`) → `nimbo\vivo.py` (listdir/open, dedup,
arranque en keyframe) + `nimbo\mp4.py` (codec string del init) →
`/api/vivo/*` → `reproductor_vivo.js` (MediaSource) manejado por
`vista_previa.js`.

Un segmento CMAF del camino directo trae varios `moof` pegados que
`grabador_directo.py` parte en `<nombre>_partN` (ver
`mouflon.partir_fragmentos`), así que **un "segmento" para el reproductor
son ~4 archivos**, y todo lo que cuenta de a cuántos tiene que contar por
grupo `(epoch, secuencia)`, no por archivo: `vivo.py:listar` nunca corta
un grupo a la mitad (solo la parte 0 arranca en IDR) y mide el backlog
del campo `salto` en grupos (`GRUPOS_BACKLOG_SALTO`), y
`reproductor_vivo.js:MAX_POR_TICK` tiene que cubrir `SEGMENTOS_ATRAS`
grupos **enteros**. Con los valores viejos (8 archivos por tick,
`pendientes > 0`) `salto` daba `True` en cada tick, el cliente pedía
`desde=None` siempre, `_arranque()` le devolvía la misma ventana y el
reproductor reappendeaba el mismo tramo cada 500 ms — en
`mode="sequence"` un duplicado no se detecta por timestamp, se pega atrás
del anterior, así que se veía como saltos y video repetido. El cliente
además deduplica por cursor (`ultimaClave`: nunca appendea hacia atrás,
sí salta hacia adelante), tiene guarda de reentrada en `_tick()` (el
worker tiquea cada 500 ms sin esperar al `await`) y ata cada tick a **su**
sesión, no a "cualquiera menos `null`" — si rota la calidad o reconecta
el encoder mientras un pedido está en vuelo, esos bytes no van al
`SourceBuffer` de la sesión nueva.

"Ver en vivo" de una modelo que no se está grabando agrega su nombre a
`grabar.txt` mientras el modal está abierto. `nimbo\vivo_temporal.py` es el
dueño de eso y su invariante es dura: **nunca quita de `grabar.txt` un
nombre que no haya agregado él mismo**, o una previa podría cortar una
grabación que el usuario sí quería. El modal manda latido cada 5s;
`cosechar()` (cada 10s) cierra las que quedaron sin latido y `purgar()`
limpia al arrancar lo que dejó una corrida anterior muerta.

Nada borraba nunca los segmentos crudos de `datos\streams\`: por cada
`.mp4` terminado quedaba para siempre una copia entera del mismo material
en fMP4. `nimbo\cosecha_streams.py` es quien lo barre, y vive en Nimbo, no
en el detector, porque hace falta cruzar dos señales que viven en lugares
distintos: el detector solo sabe "¿esta sesión ya está unida?"
(`unido.json`), y solo Nimbo sabe "¿alguien la está mirando en vivo ahora
mismo?" (`vivo.sesiones_vivas()`, que a su vez lee el dict fusionado de
`estado\detector.json` + `estado\directo.json`, no el disco). Una sesión
se borra a la papelera solo si pasa las tres
compuertas: tiene `unido.json` (si no, es una grabación pendiente que
`recuperar_pendientes()` todavía puede rehacer, y es la única copia que
existe); no aparece en `vivo.sesiones_vivas()`; y pasaron tanto
`dias_segmentos` (config, defecto 1 día) desde que se unió como
`GRACIA_MTIME_S` (5 min) desde el último archivo escrito en la carpeta —
ese margen por encima del `OLVIDAR_STREAM` del detector es la defensa
extra contra un escritor todavía activo. Corre sola cada 10 minutos desde
`servidor.py:_bucle_cosecha` y también a pedido (`POST
/api/streams/limpiar`, botón "Limpiar segmentos" en la pestaña
Grabaciones). No importa `detectar_m3u8.py` por el mismo motivo que
`vivo.py` no lo hace (ver "Los dos puentes" más abajo): porta sus propias
copias de `MARCADOR`/`CARPETA_SEGS`/`CARPETA_PLAYLISTS`.

### Detalles con historia

- **`sesion.py`** (cuenta real, pestaña "Siguiendo") no abre ningún
  navegador: el usuario pega un "Copiar como cURL". Automatizar el login
  con Playwright **no funciona** — Cloudflare detecta CDP y corta. No
  reintentar ese camino. `sesion.json` equivale a la contraseña mientras la
  sesión viva: nunca loguear su contenido.
- **`sitio.py`** sale por el proxy de grabación (para que las `@vpn=` usen
  el túnel) y **nunca** toca `nucleo\perfil\`, que es exclusivo del
  supervisor. `estado()` reusa el `RE_ESTADO` del propio `supervisor.py` en
  vez de copiarlo. `buscar()` usa un endpoint no oficial descubierto
  mirando el tráfico del sitio: si cambia, devuelve `[]` y todo lo demás
  sigue andando.
- **`grabaciones.py`** borra siempre a la **Papelera** (`SHFileOperationW`
  vía ctypes), nunca `os.remove`: son horas de grabación. Las duraciones
  salen del nombre del archivo cuando se puede, y si no de un `ffmpeg -i`
  en segundo plano (máx. 2 a la vez) cacheado en `estado\duraciones.json`.
- **`temporizador_previa.worker.js`** es un Web Worker a propósito:
  Chromium frena los timers del hilo principal cuando la ventana pierde el
  foco, y la previa tiene que seguir refrescando en segundo plano.
  `vista_registro.js` usa `setInterval` normal justo por lo contrario.
- **`registro.py`** nunca lee los `.jsonl` enteros: va al final del
  archivo actual y retrocede en bloques de 64 KB, cruzando al `.1` si
  hace falta; el `cursor` que devuelve es `"<idx>:<offset>"` (0 = actual,
  1 = el `.1`), opaco para quien lo pide — `servidor.py` y
  `vista_registro.js` solo lo mandan de vuelta tal cual.
- Nada está clavado a una letra de unidad: la raíz se calcula desde la
  ubicación de `ajustes.py`.

### Imports planos

Los módulos de `nimbo\` se importan **sin paquete** (`import ajustes as
ajustes_mod`), porque `app.py` se ejecuta como script y su carpeta queda en
`sys.path`. No convertirlos en imports relativos sin cambiar también
`arrancar.bat`.
