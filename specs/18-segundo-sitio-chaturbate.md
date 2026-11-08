# SPEC 18 — Segundo sitio: chaturbate con alcance reducido

> **Estado:** Implementado
> **Depende de:** SPEC 01 (deltas de estado por WebSocket), SPEC 03 (camino
> directo: patrón de proceso propio y método de sonda), SPEC 11 (interfaz de
> modelos y previa)
> **Fecha:** 2026-11-08
> **Objetivo:** Agregar chaturbate como segundo sitio de grabación con
> alcance reducido — sin login ni cuenta, solo favoritas, grabar, previa y
> vista en vivo — como un cuarto proceso de captura aislado, sin cambiar el
> comportamiento de stripchat.

---

## Por qué

`recomendaciones\recomendaciones.md:77` ya tiene esto anotado como **D14 —
Multi-sitio**, marcada "impacto alto, costo muy alto", con la condición
explícita "no antes de que A y B estén cerrados". SPEC 17 la nombra solo
para dejarla afuera (`specs\17-lan_actualizaciones_atajos.md:130`). Este
spec la abre, pero acotada: no "soportar chaturbate, cam4 y compañía" como
dice la recomendación original, sino un segundo sitio concreto con la menor
superficie posible — sin tocar stripchat, sin inventar una API de plugins
para sitios que todavía no existen.

Nimbo no tiene hoy ningún concepto de "sitio" en su modelo de datos:
`grabar.txt` es una lista plana de nombres, `favoritas.json` es un array de
strings, y la identidad de una modelo en toda la interfaz es
`nombre.lower()` sin ningún campo adicional. Agregar un segundo sitio sin
ese campo haría que dos modelos homónimas de sitios distintos colisionen en
al menos cuatro lugares: la grilla de "Modelos" (`tarjetasModelos`, un Map
por nombre), la fusión de estado (`estado_captura.fusionar()`, dedupe por
`modelo.lower()`), el agrupado de grabaciones (`grabaciones.py`, clave
`"<modelo>|<id>|<fecha>"`) y la limpieza de segmentos (`cosecha_streams.py`,
que decide qué carpeta no borrar mirando el JSON fusionado). Este spec
agrega el campo de sitio en los cuatro.

La arquitectura ya tiene el molde para esto: el camino directo (SPEC 03) es
un cuarto proceso aislado que no depende del proxy ni abre páginas, y su
caída no se lleva puesta a los otros tres
(`motor.py:_reiniciar("directo")`). Chaturbate entra con el mismo patrón:
un quinto proceso, no una reforma del motor.

Hay una compuerta real antes de prometer nada de "vista en vivo": el
reproductor (`reproductor_vivo.js` + `MediaSource`) es fMP4/CMAF de punta a
punta, y `MediaSource` no acepta MPEG-TS en ningún navegador. Chaturbate
históricamente sirve `.ts`. Por eso el primer paso de este spec no es
código de producción: es una sonda que mide el formato real contra el sitio
vivo, igual que hizo SPEC 03 antes de prometer el camino directo de
stripchat.

---

## Scope

**Entra:**

- **Fase 0 — `pruebas\sonda_chaturbate.py`**, calco de
  `pruebas\sonda_directo.py`: stdlib puro, fase por fase, aborta en la
  primera que falla, `--fase 0` sin red como canario, escribe solo bajo
  `--salida`. Mide contra el sitio vivo: resolución nombre→id (tiene que
  ser numérico y estable — lo exige `grabaciones.RE_NOMBRE`), URL del
  "master"/manifiesto del stream, cabeceras mínimas necesarias, si hace
  falta cookie o token para ver el stream público, **formato real de los
  segmentos (fMP4/CMAF vs MPEG-TS)**, duración de segmento, y si hay una
  URL de snapshot pública y estable para la miniatura. Deja la evidencia
  impresa, con el mismo nivel de detalle que `specs\03-grabador_directo.md`
  §2.
- **`nucleo\scripts\chaturbate.py`** — protocolo puro, importable sin
  peligro, con el mismo criterio que hace seguro a `mouflon.py`: sin
  `addons = [...]`, sin estado de módulo mutable, solo librería estándar.
  Expone las cuatro primitivas que `grabador_chaturbate.py` necesita:
  resolver id numérico por nombre, consultar estado (en vivo / offline),
  pedir el manifiesto y listar rendiciones disponibles, y pedir la
  variante elegida para extraer `(init, target_duration, segmentos)`.
  Canario offline (`verificar_canario()`) si la fase 0 encuentra algo
  versionable que pueda rotar (una clave, un esquema de firma); si no hace
  falta descifrar nada, no hace falta canario.
- **`nucleo\scripts\grabador_chaturbate.py` + `nimbo\lanzador_chaturbate.py`**
  — cuarto proceso de captura (quinto hijo de Nimbo), con un hilo por
  modelo. Es un **puerto deliberado** de la parte agnóstica del sitio de
  `nucleo\scripts\grabador_directo.py` — `Escritor`, `LectorGrabar` +
  `_ConfigGeneral`, el pool de descargas, la cola de unión con su hilo
  consumidor único, `fila_estado()`/`_publicar_estado()`, y la clase
  `Grabador` completa con sus dos eventos separados `_parar`/`_fin` (sin
  esa separación el latido de estado muere justo cuando empieza lo que más
  tarda — unir — y el JSON queda congelado en `grabando: true`, el mismo
  bug que `grabador_directo.py` ya tuvo y resolvió) — **citando las líneas
  de origen en cada bloque portado**, igual que ya hacen `mp4.py`,
  `vivo.py` y `cosecha_streams.py` con `detectar_m3u8.py`. Las únicas
  piezas específicas del sitio son las ~20 llamadas que en el original van
  a `mouflon.*`; acá van a `chaturbate.*`.
  - Escribe `estado\chaturbate.json` con el mismo schema de fila que
    `_ModeloDirecto.fila_estado()` (18 claves, incluyendo `estado_cam`,
    `esperando` y `degradado` — `detector.json` no las tiene, y
    `salud.py` depende de `degradado`).
  - Escribe los segmentos al mismo layout de disco que el camino directo
    (`<modelo>_<id>\<variante>_<timestamp>\`, init en la raíz de la
    sesión, segmentos en `segmentos\`), con nombres que **matchean
    `vivo.RE_SEGMENTO`** (`_<secuencia>_<token alfanumérico>_<epoch>[_partN].<ext>`)
    — sin esto el reproductor en vivo no lista nada, sin ningún error
    visible.
  - Delega la unión a `detectar_m3u8.py --unir <sesión.ref exacta>` como
    **subproceso** (nunca import, por la invariante de CLAUDE.md), con
    `stdin=subprocess.DEVNULL` y las tres verificaciones de éxito que ya
    usa `grabador_directo.py`: código de retorno, `unido.json` presente, y
    su `mtime` distinto al de antes de correr el hijo (un `--unir` que
    saltea en silencio una sesión con marcador viejo, de otra forma, se
    loguea como "listo" sin haber unido nada).
  - Parada por **stdin** (`"parar\n"`), nunca `kill`.
  - Reenvía su logger a `estado\eventos_chaturbate.jsonl`, con la misma
    rotación de 8 MB + un único `.1` que ya usan los demás.
- **`motor.py` — quinto hijo.** Orden de arranque: detector → supervisor →
  directo → chaturbate. Orden de parada: chaturbate → directo → supervisor
  → detector. `_reiniciar(cual)` deja de tener un `else` catch-all que
  asume "directo" y pasa a un despacho explícito por nombre, con un `else`
  final que avisa en vez de relanzar el proceso equivocado en silencio.
  `chaturbate` entra en los dos diccionarios cableados
  (`_reintentos`/`_proximo`, que hoy se inicializan dos veces con el mismo
  literal de tres claves: en `__init__` y en `arrancar()`), participa del
  mismo backoff compartido (5s/15s/60s, reintento cada 15 min agotados los
  tres), y se agrega como `chaturbate_vivo` en `estado()`. Su caída se
  resuelve relanzando **solo chaturbate** — no depende del proxy ni abre
  páginas, mismo razonamiento que ya vale para `directo`.
- **Los ocho consumidores externos de `Motor.estado()`** se revisan uno
  por uno para decidir si cuentan `chaturbate_vivo`: `salud.py`,
  `servidor.py` (tres sitios), `app.py` (semáforo de bandeja),
  `main.js`/`vista_ajustes.js` (semáforo de botón y bloqueo de ajustes),
  `registro.py:FUENTES`, `modos.py:MODOS`. Por defecto, igual que hoy
  ignoran `directo_vivo` para "motor detenido", también ignoran
  `chaturbate_vivo`: el motor sigue "corriendo" si cualquiera de los
  procesos activos lo está.
- **`estado_captura.fusionar()`** — tercer origen (`estado\chaturbate.json`),
  y la clave de dedupe pasa de `modelo.lower()` a
  `(sitio, modelo.lower())`. Cada fila lleva un campo `sitio`, con
  `"stripchat"` como valor supuesto si la fila no lo trae (para no romper
  lectores de un `detector.json`/`directo.json` de antes de este spec).
- **`lista.py`** — `marcar_sitio(nombre, sitio)`, calco de `marcar_vpn()`:
  suma/resta del nombre en el set de la directiva `@chaturbate=`,
  reescritura de esa línea en su lugar, inserción antes de la primera `@`
  si la directiva todavía no existía. `leer()` agrega la clave `sitios:
  {nombre: sitio}` al dict que devuelve. Una línea de `grabar.txt` sin
  marca sigue siendo de stripchat, exactamente como hoy.
- **Filtro en `lanzador_supervisor.py`** para que el supervisor no abra
  pestaña para ningún nombre marcado `@chaturbate=` — parcheado desde ahí,
  sin tocar una línea de `nucleo\scripts\supervisor.py`, con el mismo
  patrón que ya usa ese lanzador para `supervisor.Emision`. El detector no
  necesita filtro equivalente: solo graba lo que pasa por el proxy de
  mitmdump, y el tráfico de chaturbate no pasa por ahí.
- **`favoritas.py`** — el array pasa de `["nombre", ...]` a
  `[{"nombre": "...", "sitio": "..."}, ...]`. `leer()` migra en silencio
  cualquier elemento que siga siendo un string suelto a
  `{"nombre": elemento, "sitio": "stripchat"}`, mismo criterio que
  `sesion.leer()` ya usa para migrar un archivo en claro de antes de
  DPAPI. La escritura sigue siendo atómica (`.part` + `os.replace`).
- **`nimbo\sitio_chaturbate.py`** — `buscar()`/`estado()`/`imagen()` para
  el sitio nuevo, como subclase de una base común extraída de `sitio.py`
  para reusar transporte (`_abridor`/`_pedir`, el contexto SSL sin
  verificar porque el certificado es el de mitmproxy), las caches con TTL
  y la LRU de imágenes con su centinela `_AUSENTE`. Lo específico de
  stripchat que no se reusa: el path de búsqueda
  `/api/front/v4/models/search/suggestion`, `HOSTS_IMAGEN_PERMITIDOS`, y
  la dependencia de `supervisor.RE_ESTADO`. Chaturbate expone su catálogo
  sin cuenta (si no lo expone, "agregar modelo" es un campo de texto
  simple, sin autocompletado — ver Decisiones).
- **Rutas HTTP existentes con parámetro nuevo**: `/api/sitio/buscar`,
  `/api/sitio/estado` y `/api/sitio/imagen` aceptan `sitio=`, con default
  `"stripchat"` para que el cliente actual (que nunca manda ese parámetro)
  siga funcionando idéntico. `CUPO_SITIO` y `CUPO_IMAGEN` se **comparten**
  entre los dos sitios — el tope existe porque `getaddrinfo()` no respeta
  el `timeout=` de `urllib`, y duplicarlo duplicaría el riesgo de agotar
  el pool de hilos.
- **Interfaz — "Modelos" seccionada por sitio.** Un `<details>` colapsable
  por sitio (estado abierto/cerrado guardado en `localStorage`), cada uno
  con su propia grilla y su propia barra de filtros. La clave de
  reconciliación de `vista_modelos.js` pasa de `nombre` a
  `sitio + "/" + nombre` en `tarjetasModelos` y en `cacheSitio`, para que
  dos homónimas de sitios distintos no compartan tarjeta ni miniatura.
  "Grabando" **no** se secciona: sigue siendo una sola lista en el orden
  de `grabar.txt`, con un badge de sitio agregado a cada tarjeta — es la
  vista de vigilancia activa, y partirla escondería la mitad de lo que se
  está grabando en cualquier momento. La barra de sesión y el panel de
  pegado de cURL (`#barra-sesion`, `#panel-sesion`) quedan exactamente
  donde están, dentro de la sección de stripchat.
- **Empaquetado** — sumar los archivos nuevos a `_ARBOL` de
  `empaquetado\construir.py` (la copia es por lista blanca: lo que no se
  agrega a mano, no entra, sin ningún error visible) y a
  `empaquetado\compilar.py`. `chaturbate.py` **no** se compila, por el
  mismo motivo que `mouflon.py` no se compila: lo importa el grabador al
  correr, no Nimbo.

**Fuera de scope (para specs futuros):**

- Login, cuenta, "favoritos de la cuenta" o cualquier equivalente de
  "Siguiendo" en el sitio nuevo. Ninguna ruta `/api/sesion/*` nueva.
- VPN/túnel para el sitio nuevo: no pasa por el proxy de grabación
  (`GRAB_PROXY`), así que la marca `@vpn=` no le aplica.
- cam4 o cualquier tercer sitio. La abstracción de este spec cubre
  exactamente lo que chaturbate obliga a generalizar; un tercer sitio se
  suma contra una base ya probada por dos casos reales, no contra una
  API de plugins diseñada a priori.
- **Vista en vivo, si la fase 0 mide segmentos MPEG-TS.** En ese caso
  queda documentada con la medición concreta (duración de segmento,
  estructura de paquete) y remuxear o demuxear en el cliente va en spec
  propio. Grabar y unir a `.mp4` siguen adentro de este spec sin importar
  el resultado de la fase 0, porque `ffmpeg` sí sabe remuxear TS→MP4 al
  cerrar la sesión.
- Extraer un `nucleo\scripts\grabador_base.py` común entre el camino
  directo y el grabador de chaturbate. Decisión explícita: se porta
  código, no se refactoriza el que ya corre 24/7 en producción.
- Equivalentes de `@privado=`/`@borrosas=`/`@calidad=`/`@codec=` para el
  sitio nuevo — chaturbate graba lo que haya, sin selección de calidad.
- Unificar los cuatro parsers independientes de `grabar.txt`
  (`lista.py`, `detectar_m3u8.Seleccion`, `supervisor.Lista`,
  `LectorGrabar`) en uno solo.
- Mostrar en la pestaña "Grabaciones" un filtro por sitio — las dos
  conviven en el mismo listado, diferenciadas solo por el `id` que ya
  trae el nombre del `.mp4`.

---

## Modelo de datos

**`grabar.txt` — directiva nueva, calco de `@vpn=`:**

```
# grabar.txt, archivo mixto de ejemplo
lady_ni_
Miaa_Saenz2
alguna_modelo_de_chaturbate

@directo=1
@navegador=
@chaturbate=alguna_modelo_de_chaturbate
@vpn=
```

Una línea sin que su nombre aparezca en `@chaturbate=` sigue siendo de
stripchat — el comportamiento de hoy, intacto.

**`favoritas.json` — formato nuevo, con el ejemplo del formato viejo que
migra:**

```jsonc
// Formato nuevo
[
  {"nombre": "lady_ni_", "sitio": "stripchat"},
  {"nombre": "alguna_modelo", "sitio": "chaturbate"}
]

// Formato viejo (se sigue leyendo, se migra al escribir de nuevo)
["lady_ni_", "Miaa_Saenz2"]
```

**`estado\chaturbate.json` — mismo nivel raíz que `directo.json`, mismo
schema de fila:**

```jsonc
{
  "generado": 1794158227.53,
  "modo": "chaturbate",
  "degradado": false,
  "ids": {"alguna_modelo": 123456},
  "streams": [
    {
      "id": "123456", "modelo": "alguna_modelo", "sitio": "chaturbate",
      "mejor": "", "elegida": "720p", "variantes": ["720p"],
      "estado": "grabando", "grabando": true, "privado": false,
      "privado_desde": null, "inicio": 1794158000.0,
      "ultimo_visto": 1794158227.0, "ultimo_segmento": 1794158226.5,
      "segmentos": 340, "bytes": 182334221,
      "estado_cam": "public", "esperando": false, "degradado": false,
      "sesiones": [
        {"variante": "720p", "calidad": "720p", "codec": "h264",
         "sesion": "20261108-114734", "carpeta": "...", "init": true,
         "segmentos": 340, "bytes": 182334221, "inicio": 1794158000.0}
      ]
    }
  ],
  "parando": false,
  "uniendo": 0
}
```

**Fila fusionada** (`estado["detector"]["streams"][n]`): la misma fila de
arriba, con el campo `sitio` ya presente — es lo que `fusionar()` usa para
la clave de dedupe `(sitio, modelo.lower())` en vez de `modelo.lower()`.

**`ajustes.DEFECTOS` — claves nuevas:**

```python
"sitio_chaturbate": "https://chaturbate.com",  # URL base, análoga a "sitio"
"chaturbate_activo": False,   # opt-in explícito, nunca por defecto —
                               # el quinto proceso no arranca solo
```

---

## Plan de implementación

1. **`pruebas\sonda_chaturbate.py`** (calco de `sonda_directo.py`) y
   correrla contra el sitio vivo. Verificación: queda impreso, fase por
   fase, el formato real de los segmentos, un id numérico resoluble por
   nombre, y si hace falta cookie/token para el stream público. **Este
   paso es un punto de decisión**: su resultado confirma o recorta el
   resto del plan (vista en vivo adentro o afuera).
2. **`nucleo\scripts\chaturbate.py`** — las cuatro primitivas de
   protocolo, más canario si corresponde. Verificación: un script suelto
   que resuelve id, consulta estado y baja un segmento de prueba, sin
   tocar `grabar.txt` ni `estado\`.
3. **`nimbo\ajustes.py`** — las dos claves nuevas en `DEFECTOS`.
   Verificación: Nimbo arranca y `config.json` las tiene con los valores
   por defecto.
4. **`nimbo\lista.py`** — `marcar_sitio()` + `sitios` en `leer()`.
   Verificación: agregar y quitar una marca a mano conserva el resto del
   archivo byte a byte, igual que `marcar_vpn()`.
5. **`nimbo\favoritas.py`** — formato nuevo + migración silenciosa.
   Verificación: un `favoritas.json` del formato viejo se lee sin perder
   ninguna entrada y, tras un `agregar()`/`quitar()`, se reescribe en el
   formato nuevo.
6. **`nimbo\estado_captura.py`** — tercer parámetro `chaturbate` en
   `fusionar()`, dedupe por `(sitio, modelo.lower())`. Verificación: dos
   filas con el mismo `modelo` y distinto `sitio` aparecen las dos en el
   resultado.
7. **`nucleo\scripts\grabador_chaturbate.py` + `nimbo\lanzador_chaturbate.py`**
   — correrlo suelto (`python lanzador_chaturbate.py`, con
   `grabar.txt`/`estado\` de prueba vía `NIMBO_BASE`) antes de enchufarlo
   al motor. Verificación: graba una sesión de una modelo real, la cierra
   con `"parar\n"` por stdin, y queda un `.mp4` unido con el nombre y el
   layout esperados.
8. **`nimbo\motor.py`** — quinto hijo, arranque/parada en el orden
   definido, `_reiniciar` con despacho explícito, entrada en los
   diccionarios de reintentos y en `estado()`. Verificación: con
   `chaturbate_activo` apagado, `motor.arrancar()` sigue lanzando
   exactamente tres procesos; con él prendido, lanza cuatro y matar el
   proceso de chaturbate a mano lo relanza solo sin tocar los otros tres.
9. **`nimbo\lanzador_supervisor.py`** — filtro de los nombres marcados
   `@chaturbate=`. Verificación: con una modelo marcada, el supervisor no
   le abre pestaña (se puede ver en `estado\supervisor.json`).
10. **`nimbo\sitio_chaturbate.py`** + base compartida extraída de
    `sitio.py`. Verificación: `buscar()`/`estado()` contra el sitio real
    devuelven el mismo shape que la versión de stripchat.
11. **`nimbo\servidor.py`** — `sitio=` en las tres rutas de catálogo,
    sección de estado para chaturbate, `_bucle_sitio` consultando los dos
    sitios dentro del mismo cupo. Verificación: `GET
    /api/sitio/buscar?q=x` sin `sitio=` sigue devolviendo exactamente lo
    mismo que antes de este spec.
12. **`nimbo\web\index.html` + `vista_modelos.js`** — secciones
    `<details>` por sitio, clave de reconciliación con sitio. Verificación:
    dos modelos homónimas de sitios distintos se ven como dos tarjetas en
    dos secciones, cada una con su propia miniatura.
13. **`nimbo\web\js\vista_grabando.js`** — badge de sitio por tarjeta.
    Verificación visual: una modelo de cada sitio grabando a la vez se
    distingue a simple vista en "Grabando".
14. **`nimbo\registro.py`, `nimbo\modos.py`,
    `empaquetado\construir.py` (`_ARBOL`), `empaquetado\compilar.py`**.
    Verificación: `construir.py --descargar` deja el paquete con los
    archivos nuevos adentro y `auditar()` no aborta.
15. **`CLAUDE.md`** — "Cuatro procesos" pasa a cinco, actualizar la tabla
    de "Quién escribe qué" con `estado\chaturbate.json` y
    `eventos_chaturbate.jsonl`, y una nota en "El núcleo copiado" sobre el
    nuevo módulo de protocolo. Verificación: lectura humana, sin
    comandos.

---

## Criterios de aceptación

- [ ] Con `chaturbate_activo` apagado (default), Nimbo arranca y se
      comporta exactamente igual que hoy: tres hijos, `grabar.txt`
      intacto, `/api/lista` y `/api/estado` con la misma forma que antes
      de este spec.
- [ ] Un `favoritas.json` del formato viejo (array de strings) se lee sin
      perder ninguna favorita y se reescribe en el formato nuevo tras la
      primera escritura.
- [ ] Dos modelos con el **mismo nombre**, una en cada sitio, aparecen
      como dos tarjetas distintas en "Modelos" (cada una en su sección),
      graban a carpetas de disco distintas, y sus `.mp4` terminan en
      **grupos distintos** en la pestaña "Grabaciones".
- [ ] Matar el proceso de chaturbate a mano lo relanza solo (backoff
      5s/15s/60s) y **no** reinicia detector ni supervisor.
- [ ] El supervisor no abre ninguna pestaña para un nombre marcado
      `@chaturbate=` (verificable en `estado\supervisor.json`).
- [ ] Una línea de `grabar.txt` sin ninguna marca de sitio sigue
      grabándose por stripchat, igual que antes de este spec.
- [ ] Los `.mp4` producidos por el sitio nuevo matchean
      `grabaciones.RE_NOMBRE` (no caen en el grupo
      `"sin-convencion|..."`), y sus segmentos en disco matchean
      `vivo.RE_SEGMENTO`.
- [ ] `cosecha_streams.barrer()` borra una sesión ya unida del sitio
      nuevo tras el plazo configurado, y no borra una que sigue viva.
- [ ] `python nimbo\servidor.py` arranca sin traceback tanto con
      `chaturbate_activo` prendido como apagado.
- [ ] (Solo si la fase 0 midió fMP4/CMAF) La previa de una modelo de
      chaturbate muestra video real, no el snapshot estático, y una
      rotación de sesión no fuerza un remontaje completo del
      `MediaSource`.
- [ ] (Solo si la fase 0 midió MPEG-TS) El spec deja escrita la medición
      exacta y la previa de chaturbate cae a snapshot sin intentar abrir
      `MediaSource`, sin tirar ningún error en consola.

---

## Decisiones

- **Sí:** chaturbate concreto, no una capa de abstracción de "sitio"
  diseñada por adelantado. Evita inventar una interfaz de plugin para
  sitios que todavía no existen; si llega un tercero, se diseña contra
  dos casos reales en vez de contra una suposición.
- **Sí:** un quinto proceso propio (`grabador_chaturbate.py` +
  `lanzador_chaturbate.py`), aislado de los otros cuatro. Un bug en la
  ingeniería inversa del protocolo nuevo no puede tumbar la grabación de
  stripchat, que corre 24/7 sin supervisión.
- **No:** meter chaturbate dentro de `grabador_directo.py` con un
  parámetro de sitio. Menos código, pero un bug ahí compromete las dos
  grabaciones a la vez.
- **Sí:** portar (copiar) las ~1.000 líneas agnósticas de
  `grabador_directo.py` en vez de extraer un módulo base común ahora.
  `grabador_directo.py` está en producción grabando 24/7; refactorizarlo
  para generalizarlo exige volver a verificar que stripchat sigue
  grabando igual, y el repo ya tiene el precedente de portar en vez de
  importar/refactorizar cuando el original no se puede tocar con riesgo
  (`mp4.py`, `vivo.py`, `cosecha_streams.py` frente a
  `detectar_m3u8.py`). La extracción a un módulo común queda para cuando
  llegue un tercer sitio y la duplicación duela con dos casos reales
  mirando.
- **Sí:** `@chaturbate=n1,n2` en `grabar.txt`, calco exacto de `@vpn=`. Es
  el único patrón de "marca que señala un subconjunto de líneas" que ya
  existe, ya está probado, y los cuatro parsers de `grabar.txt` ya saben
  ignorar una `@directiva` que no reconocen.
- **No:** un prefijo por línea (`chaturbate:nombre`). `RE_NOMBRE` rechaza
  los dos puntos, y habría que tocar los cuatro parsers de `grabar.txt`
  más `RE_NOMBRE_VALIDO`/`RE_NOMBRE` del núcleo.
- **No:** un `grabar_chaturbate.txt` separado. Duplicaría `lista.py`,
  `firma()` y el lector de cada uno de los tres procesos existentes que
  ya leen `grabar.txt`.
- **Sí:** `favoritas.json` pasa a array de objetos con migración
  silenciosa del formato viejo al leer, mismo criterio que
  `sesion.leer()` ya usa para el archivo en claro de antes de DPAPI. Un
  solo archivo, ninguna favorita existente se pierde.
- **Sí:** la fusión de estado dedupea por `(sitio, modelo.lower())` en
  vez de `modelo.lower()`. Sin esto, dos homónimas de sitios distintos se
  pisan en `estado_captura.fusionar()` y una desaparece de la grilla sin
  ningún error.
- **Sí:** "Modelos" se secciona por sitio con `<details>`; "Grabando"
  queda mezclada con un badge. "Grabando" es la vista de vigilancia
  activa — partirla escondería la mitad de lo que se está grabando en
  cualquier momento y rompería el orden que hoy define `grabar.txt`.
- **Sí:** el filtro de "no abrir pestaña para chaturbate" se parchea
  desde `lanzador_supervisor.py`, nunca editando
  `nucleo\scripts\supervisor.py`. Es el patrón que el repo ya usa para
  `supervisor.Emision`, y respeta la decisión de diseño central del repo:
  el motor de captura no se toca, se lo mira desde afuera.
- **Sí:** `chaturbate_activo` apagado por defecto. El quinto proceso no
  tiene por qué arrancar en una instalación que no pidió el sitio nuevo.
- **Sí:** `CUPO_SITIO`/`CUPO_IMAGEN` compartidos entre los dos sitios, no
  duplicados. El tope existe porque `getaddrinfo()` no respeta el
  `timeout=` de `urllib`; duplicar el cupo duplicaría el riesgo de agotar
  el pool de hilos del servidor.
- **Sí:** la fase 0 (sonda) es un paso del plan, no un supuesto. El
  reproductor en vivo depende por completo de que los segmentos sean
  fMP4/CMAF; prometerlo sin medir repetiría el error que SPEC 03
  justamente corrigió (una premisa escrita en el código que nunca se
  había verificado contra el sitio real).

---

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Chaturbate sirve segmentos MPEG-TS, incompatibles con `MediaSource` | La fase 0 lo mide antes de escribir una línea de `grabador_chaturbate.py`; si da TS, la vista en vivo sale del scope con el dato documentado y graba/une igual funcionan |
| El protocolo de chaturbate cambia (rota un token, cambia el host del CDN) | Costo permanente ya advertido en D14 de `recomendaciones.md`; `chaturbate.py` queda aislado para que la ingeniería inversa futura no toque nada más |
| Las ~1.000 líneas portadas divergen con el tiempo de `grabador_directo.py` | Cada bloque portado cita la línea de origen, mismo criterio que ya sostiene `mp4.py`/`vivo.py`/`cosecha_streams.py` contra `detectar_m3u8.py` |
| Dos modelos homónimas de sitios distintos colisionan en algún lugar no cubierto por este spec | La clave compuesta `(sitio, modelo)` se aplica en los cuatro puntos identificados (fusión de estado, grilla de Modelos, `grabar.txt` vía `@chaturbate=`, agrupado de grabaciones vía `id`); cualquier punto nuevo que use solo `nombre.lower()` es un bug a reportar |
| Un archivo nuevo se olvida en `_ARBOL` de `empaquetado\construir.py` | `auditar()` ya aborta el build ante archivos que no deberían estar, pero no ante los que faltan — revisión manual del checklist de empaquetado en el paso 14 |
| Los cuatro parsers de `grabar.txt` divergen más todavía al sumarles una directiva más | Se usa el patrón ya probado de `@vpn=`, no uno nuevo; el riesgo es el mismo que ya existe hoy con las 14 directivas actuales |

---

## Lo que **no** está en este spec

- Login, cuenta o "Siguiendo" para chaturbate.
- VPN/túnel para el sitio nuevo.
- cam4 o cualquier tercer sitio.
- Vista en vivo de chaturbate, si la fase 0 mide MPEG-TS.
- Un módulo `grabador_base.py` común entre los dos grabadores.
- Selección de calidad/codec o shows privados para el sitio nuevo.
- Unificación de los cuatro parsers de `grabar.txt`.
- Filtro por sitio en la pestaña "Grabaciones".

Cada una de esas, si llega, va en su propio spec.
