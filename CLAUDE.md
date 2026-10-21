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
lo hacen dos procesos hijos.

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
```

No hay suite de tests ni linter configurados en el repo.

### Al correr fuera de la instalación real

`NIMBO_BASE=<carpeta>` redirige la raíz entera (config, estado, datos) sin
tocar la instalación de verdad — ver `nimbo\ajustes.py`. `M3U8_BASE` hace
lo mismo para grabaciones/streams del lado del detector; `motor.py` lo fija
con `setdefault`, así que un valor propio del entorno no se pisa.

## Arquitectura

### Tres procesos

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
   una página por modelo para que el reproductor del sitio pida el stream
   (las URLs vienen ofuscadas con `#EXT-X-MOUFLON` y solo ese reproductor
   sabe pedirlas).

`nimbo\motor.py` los arranca y para. **El orden importa**: arranca detector
→ supervisor; para supervisor → detector. La parada va por **stdin**
(`"parar\n"`), nunca por `kill`: los dos hijos se abren con `stdin=PIPE` y
ese extremo se mantiene abierto justo para esto. Matar el detector a lo
bruto deja grabaciones sin unir; matar el supervisor deja Chromium huérfano
y el perfil con cerrojos. La parada del detector puede tardar **minutos**
(`ESPERA_CIERRE_DETECTOR = 960s`) porque espera a que termine de unir.

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
usa **solo librería estándar**.

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
`estado\eventos_*.jsonl`, en append y sin rotación.

**Nunca importar `detectar_m3u8.py` desde el proceso de Nimbo.** Ese módulo
termina en `else: addons = [Detector()]`, así que cualquier import que no
sea `__main__` construye un `Detector` entero vivo — con su `Escritor`, su
`_Tunel` y sus hilos — dentro de `app.py`. Por eso `nimbo\mp4.py` y
`nimbo\vivo.py` **portan deliberadamente** constantes y el recorrido de
cajas MP4 del detector en vez de importarlo; si se cambia algo allá, hay
que sincronizarlo acá a mano (los comentarios de esos módulos citan las
líneas originales).

### El núcleo copiado

`nucleo\` es el motor de captura copiado dentro de Nimbo: el proyecto no
lee nada de `F:\grabador` en runtime. El nombre **`nucleo\scripts\`** no es
cosmético: tanto `detectar_m3u8.py` como `supervisor.py` calculan su propia
base con `SUBCARPETAS_SCRIPT = {"scripts","script","bin","src"}` — si el
basename de su carpeta está en ese set, la base es el padre. Renombrar
`scripts\` rompe dónde buscan el perfil y las grabaciones.

En git van los `.py` y `grabar.txt`; **no** van `ffmpeg.exe`,
`wireproxy.exe`, los `.conf` del túnel, `activos.txt` ni `nucleo\perfil\`.

### Quién escribe qué (invariantes de estado)

| Archivo | Lo escribe | Lo lee |
|---|---|---|
| `nucleo\scripts\grabar.txt` | solo Nimbo (`lista.py`) | detector (cada 2s), supervisor (cada 5s) |
| `nucleo\scripts\activos.txt` | solo el detector | nadie en Nimbo |
| `estado\detector.json` | `puente.py` | Nimbo |
| `estado\supervisor.json` | `lanzador_supervisor.py` | Nimbo |
| `estado\siguiendo.json` | Nimbo (`sesion.py`) | Nimbo |
| `config.json`, `favoritas.json`, `sesion.json`, `estado\vivo_temporal.json` | solo Nimbo | Nimbo |
| `datos\grabaciones\*.mp4`, `datos\streams\` | solo el detector | Nimbo (solo lee / borra a papelera) |

`grabar.txt` es la **única fuente de verdad** de a quién se graba, y Nimbo
es su único escritor — de ahí que no haga falta locking. El usuario lo
puede editar a mano, así que `lista.py` **edita in-place línea por línea** y
conserva byte a byte comentarios, orden y notas al final; nunca regenera el
archivo desde una plantilla, y ninguna clase cachea su contenido entre
llamadas.

Todo lo que Nimbo escribe va **atómico**: `.part` + `os.replace`. Los
lectores (`_leer_json`) tragan `OSError`/`ValueError` y devuelven `None`.

### Servidor y frontend

`nimbo\servidor.py` es un `ThreadingHTTPServer` con todas las rutas en un
único `Handler.do_GET`/`do_POST` (sin framework). Solo escucha en
`127.0.0.1` y exige `?t=<token>` — un `secrets.token_urlsafe(24)` nuevo por
arranque — en `/`, `/eventos` y todo `/api/*`. Los estáticos (`app.css`,
`js\*.js`) **no** llevan token a propósito: los imports del navegador usan
URLs relativas sin query y quedarían en 403.

- `/eventos` es SSE: una cola por pestaña (`_Suscriptores`), con descarte
  del mensaje más viejo si un cliente se cuelga, y latido cada 15s. El
  primer mensaje es el estado completo para que la pestaña pinte de una.
- Lo que tarda (`/api/motor/arrancar`, `/api/motor/parar`,
  `/api/sesion/actualizar`) responde **202** y lanza un hilo; el progreso
  real llega por SSE. Nunca dejar colgada una respuesta HTTP esperando al
  motor.
- Tres formas distintas de servir bytes, no intercambiables:
  `_archivo_estatico`, `_servir_video` (con `Range`/206, para saltar dentro
  de un `.mp4` de varios GB) y `_servir_bytes` (`Accept-Ranges: none` — un
  init o segmento CMAF se appendea entero a un `SourceBuffer` y una 206
  parcial rompe `appendBuffer`).

El frontend son módulos ES nativos sin build (`nimbo\web\js\`): `main.js`
orquesta y cada `vista_*.js` se pinta sola. El CSS sí pasa por Tailwind v4
configurado en el propio `tailwind.css` (`@theme`, `@source`), compilado a
`app.css`, que **se versiona** — el runtime no necesita Node.

### Reproductor en vivo

El modal de previa muestra video real, no snapshots, sin hls.js y sin tocar
nunca la ofuscación `#EXT-X-MOUFLON`: el Chromium del supervisor ya resolvió
esas URIs y el detector ya escribió los fMP4/CMAF en
`datos\streams\<modelo>_<id>\<variante>_<sesion>\`. La cadena es
`puente.py` (campo `sesiones` en `detector.json`) → `nimbo\vivo.py`
(listdir/open, dedup, arranque en keyframe) + `nimbo\mp4.py` (codec string
del init) → `/api/vivo/*` → `reproductor_vivo.js` (MediaSource) manejado
por `vista_previa.js`.

"Ver en vivo" de una modelo que no se está grabando agrega su nombre a
`grabar.txt` mientras el modal está abierto. `nimbo\vivo_temporal.py` es el
dueño de eso y su invariante es dura: **nunca quita de `grabar.txt` un
nombre que no haya agregado él mismo**, o una previa podría cortar una
grabación que el usuario sí quería. El modal manda latido cada 5s;
`cosechar()` (cada 10s) cierra las que quedaron sin latido y `purgar()`
limpia al arrancar lo que dejó una corrida anterior muerta.

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
- **`registro.py`** nunca lee los `.jsonl` enteros: va al final del archivo
  y retrocede en bloques de 64 KB; el `cursor` que devuelve es un offset en
  bytes.
- Nada está clavado a una letra de unidad: la raíz se calcula desde la
  ubicación de `ajustes.py`.

### Imports planos

Los módulos de `nimbo\` se importan **sin paquete** (`import ajustes as
ajustes_mod`), porque `app.py` se ejecuta como script y su carpeta queda en
`sys.path`. No convertirlos en imports relativos sin cambiar también
`arrancar.bat`.
