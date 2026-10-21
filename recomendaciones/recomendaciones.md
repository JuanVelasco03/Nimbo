# Recomendaciones — ideas duras para llevar Nimbo más allá

## 1. Reproductor en vivo propio — IMPLEMENTADO (rama `vivo`)

~~Revisitar lo que `vista_previa.js` descartó...~~ Ese razonamiento era
falso: no hacía falta reverse-engineerear `#EXT-X-MOUFLON`. El Chromium
del supervisor ya resuelve esas URIs y el detector ya escribe los
segmentos fMP4/CMAF resultantes en `datos\streams\`. Se implementó un
reproductor con `MediaSource` nativo que lee esos bytes directamente
(sin hls.js, sin playlist, sin tocar nunca la ofuscación): `nimbo\mp4.py`
(codec string del init), `nimbo\vivo.py` (listar/leer segmentos, dedup,
arranque seguro en keyframe), rutas `/api/vivo/*` en `servidor.py`,
campo `sesiones` agregado por `puente.py`, y `nimbo\web\js\
reproductor_vivo.js` + `vista_previa.js` del lado del navegador. Incluye
"ver en vivo" temporal (`vivo_temporal.py`) para modelos online que
todavía no se están grabando. Ver `CLAUDE.md`, sección "Reproductor en
vivo", para el detalle completo. Verificado con QA de navegador real
(`pruebas\qa_vivo.mjs`): video decodificando de verdad, con rotación de
sesión incluida.

## 2. Watchdog activo del túnel huérfano — IMPLEMENTADO

~~Hoy solo se sabe que `wireproxy.exe` puede quedar huérfano...~~ Hecho
en `nimbo\motor.py`, con detección real y sin psutil (el Python de la
ventana solo tiene stdlib): `_procesos()` lee pid/padre/nombre con
`CreateToolhelp32Snapshot` y `_duenos_puerto()` pregunta quién escucha
el puerto del túnel con `GetExtendedTcpTable`, las dos por `ctypes`. Un
`wireproxy.exe` que tenga el puerto y cuyo padre ya no exista es
huérfano y se cierra con `TerminateProcess`; si el padre sigue vivo y no
es el detector (alguien lo corrió a mano) no se toca y se avisa una sola
vez.

Se revisa en tres momentos: antes de arrancar el detector
(`_limpiar_tunel_previo()` — el caso que importa, porque si el puerto
está ocupado `_Tunel.run()` ADOPTA ese túnel en vez de abrir el suyo y
queda uno que nadie vigila ni cierra), cada 30 s desde
`_bucle_vigilancia` (`INTERVALO_TUNEL`), y en el momento mismo en que se
detecta que el detector se cayó solo. Si el huérfano estaba adoptado por
un detector vivo — su `_Tunel` ya terminó y no lo va a reabrir — Nimbo
lo mata y levanta el túnel él mismo con `tunel_wireproxy.conf`
(`_relanzar_tunel()`), y lo cierra en la parada o si el detector muere.

## 3. Multi-perfil / multi-cuenta en paralelo

Correr varias instancias de `lanzador_supervisor.py` con perfiles de
Playwright distintos (varias cuentas del sitio a la vez). El problema
duro no es lanzar los procesos sino que `grabar.txt` deja de tener un
solo escritor implícito — hay que meter locking real (hoy `lista.py`
asume que es dueño exclusivo) y decidir cómo se reparten
`estado\supervisor.json` entre instancias.

## 4. Validación de integridad en vivo de los segmentos

Usar `ffprobe` sobre los fMP4 a medida que se escriben para detectar
drops/corrupción de timestamps y disparar una reconexión antes de que
termine el stream, en vez de descubrir un archivo roto recién al
reproducirlo.

## 5. Pasar de `ThreadingHTTPServer` a asyncio con websockets — IMPLEMENTADO

~~El SSE actual es unidireccional y por hilo...~~ Hecho:
`nimbo\servidor.py` es ahora un solo `asyncio.start_server`, con `/ws`
demultiplexado hacia `websockets.server.ServerProtocol` (la capa
sans-I/O de la librería, re-alimentada con los mismos bytes crudos del
handshake) y el HTTP de siempre escrito a mano porque esa librería no
sabe transmitir un cuerpo en bloques (`_servir_video` sigue mandando
`.mp4` de varios GB sin cargarlos en memoria). Todo lo síncrono —los
`threading.Lock` que ya tenían `lista.py`/`sesion.py`/`sitio.py`/etc,
subprocess, red saliente— pasa por un `ThreadPoolExecutor` propio
(`Aplicacion.a_hilo`); `Motor` sigue avisando desde sus hilos de
siempre, cruzando al loop con `call_soon_threadsafe`.

El beneficio real terminó siendo más la recomendación 10 (de abajo) que
la bidireccionalidad en sí: `/ws` empuja el estado por **secciones**,
solo lo que cambió (`servidor.py:_bucle_estado`, firmas baratas en vez
de reparsear todo cada vez), y el refresco de "Modelos" contra el sitio
real se movió del cliente (un `setInterval` por pestaña, sin forma de
cancelarlo) a una tarea del servidor (`_bucle_sitio`) que se apaga sola
si nadie mira esa pestaña — eso sí es la cancelación de verdad que
pedía este punto, no un `AbortController` del lado del navegador. Se
aprovechó la reescritura de `_archivo_estatico` para cerrar también el
traversal de la recomendación 6 (ver abajo). Verificado con QA de
navegador real: WebSocket conectando, `estado_inicial` + deltas
llegando, cero polling de `/api/estado`, y la suscripción/cancelación
del canal `sitio` disparándose sola al entrar/salir de "Modelos". Ver
`CLAUDE.md`, sección "Servidor y frontend", para el detalle completo.

## 6. Cerrar el traversal de `_archivo_estatico` en `servidor.py` — IMPLEMENTADO

~~`Handler._archivo_estatico` filtra los componentes...~~ Se arregló de
paso al reescribir la función para la recomendación 5
(`_resolver_estatico`, con el mismo `realpath`+`commonpath` que ya usaban
`grabaciones.py`/`vivo.py`, más el rechazo explícito de `\` y `:` en
cualquier componente). Probado contra el servidor real:
`GET /..\..\sesion.json` y su forma percent-encoded dan `404`, no `200`
con el archivo. Los estáticos **siguen sin token** a propósito — esa
discusión de fondo (la decisión que vale discutir aparte, de abajo)
sigue abierta, no era parte de este arreglo.

La falla original, para que quede el porqué escrito: la version anterior
de `_archivo_estatico` filtraba los componentes con
`[p for p in ruta_rel.split("/") if p and p != ".."]`, pero partía
**solo por `/`** y `self.path` nunca se desescapaba — en Windows eso
dejaba pasar un componente entero con barras invertidas adentro (un
`GET /..\..\sesion.json` llegaba como UN componente que no era `".."`,
se unía a `nimbo\web\` y resolvía a `<raiz>\sesion.json`), y esa ruta no
pedía token. `sesion.json` "equivale en la práctica a la contraseña de
la cuenta" según su propio docstring; también quedaban expuestos
`config.json` e `instancia.json` (puerto + token, el resto de la API).
La decisión de fondo que sigue sin resolverse es si los estáticos tienen
que seguir sin token: hoy la única barrera real para `js/`, `app.css` e
`index.html` es que escuchás en `127.0.0.1`, y el Chromium del
supervisor corre en la misma máquina.

## 7. Recolector de basura para `datos\streams\`

Nada borra nunca los segmentos crudos. El detector marca una sesión ya
unida escribiendo `unido.json` (`_marcar_unida`, `MARCADOR`) y
`sesiones_en_disco()`/`recuperar_pendientes()` usan esa marca para saber
qué le falta remuxear, pero ninguno de los dos limpia la carpeta; del
lado de Nimbo, `grabaciones.py` solo mira `datos\grabaciones\` y
`vivo.py` solo lee. Resultado: por cada `.mp4` terminado queda en disco
una copia entera del mismo material en fMP4 dentro de
`datos\streams\<modelo>_<id>\<variante>_<sesion>\segmentos\`, para
siempre. `preflight.comprobar` avisa cuando bajás de 5 GB y
`Aplicacion._disco` pinta los libres, pero nadie actúa: la app te mira
llenar el disco y te lo cuenta.

El trade-off real está en qué NO se puede borrar, y es lo que hace que
esto tenga que vivir en Nimbo y no en el detector: una carpeta sin
`unido.json` es una grabación pendiente que `recuperar_pendientes()`
todavía puede rehacer, y una carpeta que aparece en
`vivo.sesiones_vivas()` (o cuyo último segmento tiene menos de
`UMBRAL_VIVO_S`) la está leyendo el reproductor en vivo en este momento.
Con esas dos exclusiones, un barrido en el `_bucle_cosecha` que ya
existe (hoy solo llama a `vivo_temporal.cosechar` cada 10 s) alcanza:
política por edad o por GB libres objetivo, borrado a la papelera
reusando `grabaciones._a_la_papelera`, y el conteo expuesto en la
pestaña Grabaciones al lado del `resumen` que ya devuelve
`grabaciones.listar`.

## 8. Reinicio supervisado en `motor.py`, no solo el aviso

`Motor._bucle_vigilancia` detecta que un hijo murió, emite `"el detector
se cerro solo (codigo %s)"` y acto seguido hace `self.detector = None`:
tira la referencia y se queda esperando que alguien mire la ventana. Si
el detector se cae a las 3 de la mañana con cinco modelos en
`grabar.txt`, el supervisor sigue abriendo pestañas feliz (nadie le
avisa) y no se graba un byte hasta que vuelvas a tocar "Arrancar". El
caso inverso es igual de malo: muerto el supervisor, el detector queda
escuchando un proxy por el que ya no pasa nadie y `detector.json`
muestra todo en `inactivo`, que en la UI se ve exactamente igual que
"las modelos están offline".

Lo que falta es un relanzado con backoff exponencial y tope de intentos
(ej. 5 s, 15 s, 60 s, y después rendirse con un aviso `nivel="error"`),
distinguiendo "se murió" de "lo paramos nosotros" — el flag `_parando` y
el `self.supervisor = None` de `parar()` ya hacen esa distinción, por
algo están ahí. Dos detalles duros: el reinicio del detector tiene que
ir después de la ventana en la que mitmdump libera el puerto
(`_esperar_puerto` sirve para esperar que vuelva a escuchar, pero no hay
nada que espere a que lo suelte), y una caída a lo bruto deja sesiones a
medias en `datos\streams\` — eso es benigno porque el propio detector
las levanta solo con `recuperar_pendientes()` al arrancar, y conviene
decirlo en el aviso para que no parezca que se perdió la grabación.

## 9. Los logs del motor no quedan en ninguna parte

`Motor._leer_salida` lee el stdout de los dos hijos y lo manda a
`_emitir_log`, que termina en `Aplicacion._publicar` (antes
`_Suscriptores.publicar`, renombrada al pasar `servidor.py` a asyncio —
ver recomendación 5) — una cola por pestaña abierta, `maxsize=200`, y
nada más. No hay backlog: si nadie
está suscrito (el caso normal, porque `arrancar_al_abrir` arranca el
motor antes de que la ventana cargue, y además la ventana se cierra
cuando querés y el servidor sigue), esas líneas **se tiran a la
basura**. Justo las que más importan son las que se pierden: un
`mitmdump` que no levanta porque el 8080 está ocupado, un `-s` mal
resuelto, el `ImportError` de Playwright del lanzador. Encima
`registro.py` solo lee los `.jsonl` que escriben `puente.py` y
`lanzador_supervisor.py`, y esos se empiezan a escribir recién en
`running()`/`main()` — o sea, después del arranque: si el proceso muere
antes, no hay ni un `.jsonl` que mirar. Y `DIR_REGISTRO` (`registro\`)
está declarado en `ajustes.py`, se crea en `cargar()`, y nadie escribe
ahí jamás ("reservado, hoy sin uso activo", dice el README).

La otra mitad del problema es que los `.jsonl` que sí existen crecen sin
techo: los dos `_Eventos` (`puente.py` y `lanzador_supervisor.py`) hacen
`open(..., "a")` y listo, mientras el propio detector sí rota su
`detector.log` con `MAX_REGISTRO = 20 MB`. `registro.leer()` compensa
leyendo hacia atrás, pero se rinde a los `TOPE_BYTES_BARRIDO = 8 MB` y
devuelve `filas: []` con `hay_mas: True` — para el usuario, filtrar por
`nivel=ERROR` en un archivo viejo se ve igual que "no hubo errores". Dos
cosas chicas y concretas: un `registro\motor.jsonl` con las líneas de
ambos hijos (y el aviso de muerte con su `returncode`), y rotación por
tamaño con sufijo `.1` en los dos `_Eventos`, con `registro.leer()`
sabiendo saltar al archivo anterior en vez de cortar en seco.

## 10. Matar el poll de 2 segundos: el estado ya tiene por dónde empujarse — IMPLEMENTADO

~~`main.js` hace `setInterval(refrescar, 2000)`...~~ Se resolvió de
arriba, como efecto directo de la recomendación 5: `servidor.py` ganó un
`_bucle_estado` que calcula `estado_completo()` una vez (no por
pestaña) y publica por el canal `estado` del WebSocket solo las
secciones que cambiaron desde el tick anterior — usando exactamente
`Lista.firma()` para `grabar.txt` (la pieza que este punto señalaba como
"ya existe y no la llama nadie") y el contenido ya parseado de
`detector.json`/`supervisor.json`/`siguiendo.json` para el resto.
`main.js` borró el `setInterval` entero; `refrescar()` quedó solo como
reconciliación puntual (primera pintada antes de que conecte el
WebSocket, y después de cada acción que muta algo). Verificado con QA de
navegador real: un solo `GET /api/estado` en toda la carga de la pestaña,
ninguno más en los 8 segundos siguientes, y el delta llegando solo
cuando `grabar.txt` cambió de verdad.

El `/api/sitio/imagen` por segundo del modal de previa y los pedidos del
reproductor en vivo (`reproductor_vivo.js`) **no** se tocaron — son pull
deliberado contra contenido que cambia en vivo (snapshot, segmentos
CMAF), no el polling de estado que este punto señalaba.
