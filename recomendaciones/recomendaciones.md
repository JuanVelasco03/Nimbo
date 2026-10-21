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

## 5. Pasar de `ThreadingHTTPServer` a asyncio con websockets

El SSE actual es unidireccional y por hilo; para soportar push
bidireccional real (ej. cancelar un refresco de imagen en curso,
multiplexar más eventos sin saturar hilos) hay que reescribir
`servidor.py` sobre un loop asyncio, tocando motor/SSE/API a la vez —
es el cambio de mayor superficie de los cinco.

## 6. Cerrar el traversal de `_archivo_estatico` en `servidor.py`

`Handler._archivo_estatico` filtra los componentes con
`[p for p in ruta_rel.split("/") if p and p != ".."]` y arma la ruta con
`os.path.join(self.app.dir_web, *partes)` — pero parte **solo por `/`**,
y `self.path` nunca se desescapa ni se normaliza. En Windows eso deja
pasar un componente entero con barras invertidas adentro: un
`GET /..\..\sesion.json` llega como UN componente (que no es `".."`), se
une a `nimbo\web\` y resuelve a `<raiz>\sesion.json`. Probado contra el
servidor real: devuelve `200` con el archivo completo. Y esa ruta **no
pide token** — `necesita_token` solo cubre `/`, `/index.html`,
`/eventos` y `/api/`, justamente porque los estáticos se piden con URL
relativa sin `?t=` — así que cualquier proceso local sin privilegios se
baja `sesion.json`, que según su propio docstring "equivale en la
práctica a la contraseña de la cuenta", más `config.json` e
`instancia.json` (puerto + token, o sea el resto de la API).

El arreglo es barato y ya está escrito dos veces en el repo: aplicar el
mismo patrón de `grabaciones.py:_resolver` y `vivo.py:_resolver`
(`os.path.realpath` + `os.path.commonpath([base, destino]) != base`),
más `unquote()` sobre el path y rechazar cualquier componente que traiga
`\` o `:`. La decisión de fondo que vale discutir aparte es si los
estáticos tienen que seguir sin token: hoy la única barrera real para
`js/`, `app.css` e `index.html` es que escuchás en `127.0.0.1`, y el
Chromium del supervisor corre en la misma máquina.

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
`_emitir_log`, que termina en `_Suscriptores.publicar` — una cola por
pestaña abierta, `maxsize=200`, y nada más. No hay backlog: si nadie
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

## 10. Matar el poll de 2 segundos: el estado ya tiene por dónde empujarse

`main.js` hace `setInterval(refrescar, 2000)` **además** de mantener el
SSE abierto, y el SSE hoy se usa para una sola cosa útil: el
`estado_inicial` del primer mensaje (de ahí en más solo llegan `log` y
`aviso`). Así que cada 2 segundos, por cada pestaña abierta,
`Aplicacion.estado_completo()` hace `shutil.disk_usage`, parsea tres
JSON (`detector.json`, `supervisor.json`, `siguiendo.json`), relee
`grabar.txt` entero con `lista.leer()` (que a propósito no cachea nada)
y recorre `datos\streams\` con `vivo.sesiones_vivas()`. Encima
`ThreadingHTTPServer` levanta un hilo por pedido, y con el modal de
previa abierto a eso se le suman un `/api/sitio/imagen` por segundo
(`ESPERA_ENTRE_IMAGENES_MS`, con un `urllib` saliente real adentro, sin
cache) y hasta nueve pedidos por tick del reproductor en vivo.

Lo bueno es que no hace falta cambiar de transporte para arreglarlo (eso
es la recomendación 5, y es mucho más caro): alcanza con que el hilo que
hoy existe para otra cosa — `_bucle_cosecha` — calcule
`estado_completo()` una sola vez por tick, lo publique por
`_Suscriptores` solo si cambió respecto del anterior, y que `main.js`
borre el `setInterval` y se quede con un `refrescar()` de reconciliación
cuando el `EventSource` dispara `onerror`. El trabajo de verdad está en
abaratar el cálculo: `Lista.firma()` ya existe para esto exacto —
devuelve `(mtime, size)` para no reparsear `grabar.txt` — y hoy **no la
llama nadie**; lo mismo vale para los tres JSON de estado y para un
`ETag`/`304` en `/api/estado` para quien siga pidiéndolo a mano.
