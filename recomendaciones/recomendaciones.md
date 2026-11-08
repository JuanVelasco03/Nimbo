# Recomendaciones — ideas duras para llevar Nimbo más allá

> **Estado:** Propuesta — nada de esto está implementado
> **Fecha:** 2026-11-07
> **Base:** repo en `90dc996`, instalación real (`config.json` con
> `dir_segmentos` en `C:\Nimbo\streams`, `dir_grabaciones` en
> `F:\Nimbo\datos\grabaciones`, `dias_segmentos: 0.25`, `@directo=1`)
> **Objetivo:** dejar anotado qué falta, verificado contra el código, para
> poder elegir el próximo spec sin volver a investigar.

---

## Cómo leer esto

Cada punto dice **qué pasa hoy** (con archivo y línea, verificado, no de
memoria), **qué agregar**, **dónde toca** y **cuánto cuesta**. El orden es
por valor sobre costo, no por lo vistoso.

Lo que domina la lista: Nimbo ya graba bien, pero **cuando algo deja de
grabar no hay nadie que avise**. Tres de los cuatro primeros puntos son
eso, y los tres reusan código que ya está escrito.

| # | Qué | Valor | Costo |
|---|---|---|---|
| A1 | Guardia de disco y retención de `.mp4` | alto | bajo-medio |
| A2 | Notificaciones del sistema desde la bandeja | alto | bajo |
| A3 | Red de seguridad del camino directo degradado | alto | bajo |
| A4 | Panel de salud: cobertura real + canario MOUFLON | alto | medio |
| B5 | Unir las partes de un mismo show en un `.mp4` | medio-alto | bajo |
| B6 | Recortar un clip y capturar fotograma | medio-alto | bajo-medio |
| B7 | Etiquetas, notas y favoritas por grabación | medio | bajo |
| B8 | Abrir la grabación en el reproductor del sistema | bajo | trivial |
| C9 | Ventanas horarias y modo juego | medio | medio |
| C10 | Acceso desde el celular en la LAN | alto | medio-alto |
| C11 | Línea de tiempo y estadísticas | medio | medio |
| C12 | Versión del paquete y aviso de actualización | medio | medio |
| C13 | Atajos de teclado | bajo | bajo |
| D14 | Multi-sitio (chaturbate, cam4, …) | alto | muy alto |

---

## A. Lo que hoy falla en silencio

### A1 — Guardia de disco y retención de `.mp4`

**Hoy.** `DEFECTOS` (`nimbo\ajustes.py:86`) tiene `dias_segmentos` y nada
equivalente para las grabaciones: `cosecha_streams.py` barre
`datos\streams\`, y **nadie** barre `datos\grabaciones\*.mp4` — crecen para
siempre. El único chequeo de espacio del motor es
`detectar_m3u8.py:_hay_espacio()` (línea 1319, `MARGEN_DISCO = 1.10`), y
corre **solo al unir** (los dos llamadores están en 1531 y 1676, siempre
con `MARGEN_DISCO + 1`): la escritura de segmentos no lo consulta nunca.
`rutas.espacio()` existe y sabe medir, pero sus únicos llamadores son
`servidor.py:152` y `servidor.py:271`, los dos para pintar Ajustes.

O sea: nadie vigila el disco. El modo de falla real no es "se llena y se
pierde todo" (eso lo frena `_hay_espacio` al unir, y lo dice en el log),
es **"deja de unir y nadie se enteró"** — y los segmentos siguen
escribiéndose hasta que el disco diga basta.

**Agregar.** Un bucle de vigilancia al lado de `servidor.py:_bucle_cosecha`
(mismo patrón, cada 10 min), con umbral configurable (`min_libre_gb`) por
cada carpeta de SPEC 12, y política elegible en Ajustes:

1. solo avisar (aviso `nivel="error"`, que ya se ve en ventana y tooltip);
2. parar el motor antes de llenar;
3. papelera de las grabaciones más viejas (retención por días **o** por GB);
4. mover las viejas a un disco de archivo — SPEC 12 ya resolvió el tema
   rutas, así que es destino nuevo, no mecanismo nuevo.

**Dónde toca.** `ajustes.DEFECTOS`, `rutas.espacio()`,
`grabaciones.eliminar()` (ya manda a papelera), un bucle nuevo en
`servidor.py`, `vista_ajustes.js`.

**Costo.** Bajo-medio: todo el código pesado ya está, falta el pegamento y
la decisión de política.

### A2 — Notificaciones del sistema desde la bandeja

**Hoy.** `pystray` está cargado y el icono tiene menú
(`nimbo\app.py:169`), pero `notify` **no aparece en ningún `.py` del
repo**. Con la ventana cerrada — que es el caso normal: graba de noche —
un "rendido" del motor existe solo en el tooltip y en
`estado\eventos_motor.jsonl`.

**Agregar.** `icono.notify()` para los avisos que ya emite `motor.py` con
`nivel="error"`, más cuatro disparadores concretos:

- el motor se rindió (agotó los tres reintentos de `_bucle_vigilancia`);
- el camino directo degradó una modelo (ver A3);
- el disco cruzó el umbral (ver A1);
- la licencia está por vencer (`_bucle_licencia` ya lo sabe).

Opcional y simpático: una favorita entró en vivo.

**Dónde toca.** `app.py` (es quien tiene el icono), `servidor.py` (ya
enruta los avisos del motor al frontend; acá hay que cruzarlos también al
icono). Degradar sin ruido si el backend de notificaciones no está,
mismo criterio que ya tiene la bandeja entera como opcional.

**Costo.** Bajo.

### A3 — Red de seguridad del camino directo degradado

**Hoy.** `grabador_directo.py:648` define `FALLOS_PARA_DEGRADAR = 3`;
`_registrar_fallo()` (1021) pone `degradado = True` y loguea textual:
*"agregala a `@navegador=` en `grabar.txt` para que vuelva a grabarla el
navegador"*. Con `@directo=1` en toda la lista (la configuración de esta
instalación), eso significa **esa modelo no se graba más hasta que una
persona edite el archivo a mano**.

Lo llamativo: **el dato ya llega al frontend**. `directo.json` publica
`degradado` global y por modelo (`grabador_directo.py:1176`, y `estado:
"degradado"` en 1145), y `estado_captura.py:96` lo pasa en el dict
fusionado. Pero `grep degradado nimbo\web\js\*.js` da **cero**: ningún
`render*` lo muestra. El aviso está servido y nadie lo mira.

**Agregar.**

1. Estado rojo "degradado" en la tarjeta de `vista_grabando.js` /
   `vista_modelos.js` — es pintar un dato que ya viaja.
2. Botón de un click "pasar al navegador", que agrega el nombre a
   `@navegador=`. `lista.py` ya edita in-place conservando comentarios, y
   la invariante se respeta: **escribe Nimbo, no el grabador**.
3. Opcional: hacerlo automático, con aviso visible y un "volver a probar
   directo" a las N horas (una rotación de claves se arregla sola cuando
   se actualiza `KEY_MAP`; una modelo con CDN raro, no).

**Costo.** Bajo. Lo caro ya está hecho.

### A4 — Panel de salud: cobertura real y canario MOUFLON

**Hoy.** Hay dos señales de salud excelentes y las dos están fuera de la
interfaz:

- `pruebas\huecos.py` mide la pérdida real del grabador por sesión (es el
  que cuantificó los 430–856 s perdidos de la noche del 2026-10-29, SPEC
  13). Vive como script de medición.
- `mouflon.verificar_canario()` (`nucleo\scripts\mouflon.py:433`) dice,
  sin red y al instante, si la tabla de claves sigue vigente. `grep
  canario nimbo\` da **cero**.

Hoy la única forma de saber si anoche salió bien es leer el registro y
confiar.

**Agregar.** Un panel (o una franja en Grabaciones) con:

- segundos perdidos y % de cobertura por grabación, cacheado por sesión
  igual que `duraciones.json`;
- el resumen de la noche: "7h02 grabadas de 7h12 en vivo";
- semáforo del canario en Ajustes, con el enlace a
  `pruebas\RESULTADOS.md` S1 cuando se apaga.

**Dónde toca.** Portar `huecos.py` a un módulo de solo lectura en
`nimbo\`, cachearlo, `vista_grabaciones.js` / `vista_ajustes.js`.
`mouflon.py` sí se puede importar sin peligro (es el único del núcleo que
puede).

**Costo.** Medio: es el único de la tanda A que tiene código nuevo de
verdad.

---

## B. Grabaciones: hoy se listan, se buscan, se ordenan, se ven y se borran

### B5 — Unir las partes de un mismo show en un `.mp4`

Cuando el encoder reconecta, el detector cierra la sesión y abre otra: un
show queda en N archivos. La interfaz ya los agrupa (los grupos y partes
de `vista_grabaciones.js`, SPEC 06), pero en disco siguen sueltos. Falta
el botón: `ffmpeg` concat demuxer, sin reencodear.

Dos cuidados: no borrar las partes hasta verificar el resultado, y cuando
se borren, a la **papelera** (criterio de `grabaciones.py`, que SPEC 14
dejó explícito: la excepción son los segmentos, no los `.mp4`).

**Costo.** Bajo.

### B6 — Recortar un clip y capturar fotograma

`ffmpeg -ss -to -c copy` desde el modal, marcando entrada y salida en el
reproductor. Y capturar fotograma: el snapshot **ya existe** en la previa
en vivo (`vista_previa.js`, acción `salir-vivo`) y no existe sobre una
grabación. Costo bajo-medio: la parte fina es la interfaz de marcado, no
el ffmpeg.

### B7 — Etiquetas, notas y favoritas por grabación

Hoy hay buscar por texto y ordenar (`grabaciones-buscar`,
`grabaciones-orden`). Falta marcar: un `estado\etiquetas.json` escrito
solo por Nimbo, atómico como todo el resto, más filtro por etiqueta en la
barra. Costo bajo.

### B8 — Abrir la grabación en el reproductor del sistema

Hoy `grabaciones.abrir_carpeta()` abre el explorador. Un `os.startfile` al
archivo es una línea y se usa todo el tiempo. Trivial.

---

## C. Operación

### C9 — Ventanas horarias y modo juego

Dos funciones con el mismo motor. Ventanas horarias: arrancar y parar el
motor en franjas (disco y ancho de banda). Modo juego: el HDD de grabación
es el mismo disco donde viven Valorant y las VMs de MEmu, así que si uno
de esos está corriendo conviene frenar las uniones y bajar prioridad.

Lo importante: **el mecanismo ya existe**. `_Freno` (SPEC 13) ya sabe
frenar la unión según una señal externa; esto es una señal nueva, no un
sistema nuevo. Costo medio, casi todo en decidir la semántica (¿frena o
pospone? ¿qué pasa con una unión en curso?).

### C10 — Acceso desde el celular en la LAN

La que más se nota, y la más delicada. Hoy `servidor.py` escucha solo en
`127.0.0.1` y exige `?t=<token>` nuevo por arranque en `/`, `/api/*` y el
handshake de `/ws`.

Para salir a la LAN hacen falta, no negociables: opt-in explícito (jamás
por defecto), contraseña obligatoria, token persistente en vez de uno por
arranque, y un QR en Ajustes para no tipear. Tener en cuenta que los
estáticos hoy van **sin** token a propósito (los imports del navegador son
URLs relativas), así que el modelo de autenticación hay que pensarlo
entero, no parchear el `bind`.

**Costo.** Medio-alto, casi todo en seguridad.

### C11 — Línea de tiempo y estadísticas

Un heatmap de quién grabó qué día y a qué hora, más GB por modelo, horas
por mes y tasa de éxito del directo contra el navegador. Los datos ya
están en los `.jsonl` (`registro.py` ya sabe leerlos hacia atrás sin
cargarlos enteros) y en los nombres de archivo. Sirve para decidir
horarios (C9) con datos en vez de intuición. Costo medio.

### C12 — Versión del paquete y aviso de actualización

**Hoy** la versión está escrita a mano en un solo lugar y nadie la
consulta: `empaquetado\nimbo.iss:13` tiene `#define Version "1.0.0"`, y
`paquete.json` guarda `armado`, `version_python`, `completo`, `licencia`,
`compilado` y `js_minificado` — **ninguna versión de la app**. No hay
chequeo de actualización en ningún lado: el que recibe el instalador queda
clavado en esa build para siempre.

**Agregar.** Versión única en un solo archivo, de ahí la toman `.iss` y
`paquete.json`, y un manifiesto **firmado** con `firma.py` (Ed25519 en
stdlib pura, ya está escrito y probado para la licencia) con el aviso "hay
una nueva". Descargar y reemplazar puede venir después; enterarse ya vale.

**Costo.** Medio.

### C13 — Atajos de teclado

Hoy hay `keydown` en tres lugares: Grabaciones (línea 432), el buscador de
Modelos (760) y la previa (475). Falta lo global: cambiar de pestaña,
arrancar/parar, abrir la previa de la primera en vivo, y quizá una paleta
de comandos. Costo bajo.

---

## D. La grande y cara

### D14 — Multi-sitio

Soportar chaturbate, cam4 y compañía. Hay que abstraer "sitio" en
`sitio.py` y en la capa de protocolo (`mouflon.py` es específico de
doppiocdn), y después **mantener ingeniería inversa de un protocolo ajeno
por cada sitio nuevo** — con el precedente de que ya hubo que derivar la
tabla de claves MOUFLON una vez. Alto impacto, alto costo permanente. No
antes de que A y B estén cerrados.

---

## Lo que no conviene hacer

Queda escrito para no volver a gastar tiempo ahí:

- **Automatizar el login con Playwright.** Cloudflare detecta CDP (el
  protocolo, no la ventana) y corta con "Actividad automática detectada".
  Probado tres veces. SPEC 15 lo resolvió al revés: ventana de Edge normal
  ruteada por el proxy, y el sniffing del tráfico.
- **Importar `detectar_m3u8.py` desde el proceso de Nimbo.** Termina en
  `addons = [Detector()]`: cualquier import construye un detector vivo
  dentro de `app.py`. Por eso `mp4.py`, `vivo.py` y `cosecha_streams.py`
  portan constantes a mano.
- **Exponer el servidor a la red sin contraseña** (ver C10).
- **Borrar `.mp4` sin papelera.** La excepción de SPEC 14 son los
  segmentos ya unidos, que son regenerables; una grabación son horas.
- **Reconstruir una grilla con `innerHTML` en el camino del delta.** Es lo
  que congelaba la ventana (SPEC 01); cualquier vista nueva se reconcilia
  por clave.

---

## Por dónde arrancar

**A1 + A2 + A3**, en ese orden. Las tres tapan pérdida silenciosa de
grabación — que es el único trabajo de la app — y las tres reusan código
que ya está escrito y probado: medir disco, mandar a papelera, emitir
avisos, editar `grabar.txt` in-place, y un dato (`degradado`) que ya viaja
hasta el frontend sin que nadie lo pinte.

Después **B5 y B6**, que son lo que más se nota al usar la app todos los
días.
