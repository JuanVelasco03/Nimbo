# SPEC 08 — Que el paquete que se manda salga con el código cerrado

> **Estado:** Implementado
> **Depende de:** SPEC 02 (`empaquetado\compilar.py`, `--compilado`, `ajustes.compilado()`)
> **Fecha:** 2026-10-28
> **Objetivo:** Que el comando de un solo paso (`construir.py --todo`) compile `nimbo\` con Nuitka y minifique el frontend, y que el build aborte si algún `.py` de la interfaz se cuela legible en el paquete.

---

## Por qué

SPEC 02 S5 construyó toda la maquinaria para cerrar el código
(`empaquetado\compilar.py`, `--compilado`, `_PY_EN_DISCO_COMPILADO`,
`copiar_compilado`, `ajustes.compilado()`, `modos.py`) y funciona. Nadie la
usa.

`construir.py:618-624`: `--todo` prende `descargar`, `completo`, `licencia` e
`instalador`. **No prende `--compilado`.** Y `CLAUDE.md` documenta
`construir.py --descargar [--completo]` como el comando normal. El resultado
está en el disco ahora mismo:

```
dist/Nimbo/paquete.json  ->  {"armado": "2026-10-27T22:42:00", "licencia": true}
dist/Nimbo/nimbo/        ->  activacion.py ajustes.py app.py cosecha_streams.py
                             estado_captura.py favoritas.py firma.py grabaciones.py
                             inicio_automatico.py lanzador_directo.py
                             lanzador_supervisor.py licencia.py lista.py miniaturas.py
                             modos.py motor.py mp4.py preflight.py puente.py
                             registro.py servidor.py sesion.py sitio.py vivo.py
                             vivo_temporal.py
```

Veinticuatro `.py` en claro, más los `.js` sin minificar, en el `.exe` que ya
se armó para mandar. Con `licencia: true`, lo que significa que la licencia
que SPEC 07 endurece se puede editar con el Notepad.

El agujero no es la falta de una herramienta: es que el camino cómodo no pasa
por la herramienta que ya existe. Esta spec cierra eso y pone una red para
que no se vuelva a abrir en silencio.

Lo que esta spec **no** arregla, y conviene decirlo acá arriba: Nuitka
standalone no es ofuscación. Compila a código máquina, pero deja nombres de
módulo, docstrings y constantes en el binario. Sirve contra copiar y pegar, no
contra alguien decidido. Y el activo más valioso del repo —la tabla `KEY_MAP`
de `mouflon.py`— sigue en disco como `.py` por un motivo estructural que se
documenta en Decisiones.

---

## Scope

**In:**

- `--todo` compila con Nuitka y arma el paquete con `--compilado`.
- `construir.py` llama a `compilar.py` él mismo: un comando, de punta a punta.
- Minificar los módulos ES de `nimbo\web\js\` al copiarlos al paquete.
- `auditar()` gana una compuerta nueva: en build compilado, abortar si
  aparece cualquier `nimbo\*.py` que no sea de `_PY_EN_DISCO_COMPILADO`.
- Actualizar `CLAUDE.md` y `empaquetado\LEEME.md` para que el comando
  documentado sea el que cierra el código.

**Out of scope (otro spec):**

- **Cerrar `mouflon.py`.** Es el ítem de más valor y el de más riesgo: hoy
  `detectar_m3u8.py` hace `import mouflon` bajo el intérprete de mitmdump, que
  no ve nada de lo compilado. Ver Decisiones para la salida documentada.
- Cerrar `detectar_m3u8.py`, `supervisor.py`, `puente.py` y
  `lanzador_supervisor.py`. Los carga mitmdump/Playwright por ruta; está
  razonado en `compilar.py:11-28` y no cambia.
- Firmar los `.exe`. Ver Decisiones.
- Ofuscadores, packers o anti-debug sobre la salida de Nuitka.
- Minificar o cerrar `app.css` (ya sale minificado de Tailwind) y
  `index.html`.
- Tocar nada de la licencia: eso es SPEC 07.

---

## Modelo de datos

Esta feature no introduce estructuras de datos nuevas. Cambia qué archivos
viajan dentro de `dist\Nimbo\` y agrega un campo informativo a
`paquete.json`, que ya existe (`construir.py:673-680`):

```json
{
  "armado": "2026-10-28T00:00:00",
  "version_python": "3.12.8",
  "completo": true,
  "licencia": true,
  "compilado": true,
  "js_minificado": true
}
```

Sirve para contestar "¿este `.exe` que mandé estaba cerrado?" sin tener que
abrirlo.

---

## Plan de implementación

### 1. `empaquetado\construir.py` — `--todo` compila

- `--todo` pasa a prender también `compilado`.
- Si `args.compilado` es el flag (no una ruta ya armada), correr Nuitka
  primero: `import compilar` y llamar a `compilar.compilar(salida_compilado,
  consola=False)`, con `salida_compilado = dist\compilado` y
  `args.compilado = <esa ruta>\app.dist`.
- `--compilado <ruta>` sigue aceptando una carpeta `app.dist\` ya hecha, para
  no recompilar cinco minutos cuando se está iterando sobre el empaquetado.
- Orden: la clave de licencia (SPEC 07, paso 7) se genera **antes** de Nuitka;
  si no, no entra al binario. Es la única dependencia de orden entre las dos
  specs y va con un comentario que lo dice.
- `--sin-compilar`: escape explícito para armar un paquete abierto a
  propósito (depurar el paquete leyendo los `.py`). Que exista el flag es lo
  que permite que el **default** sea cerrado.

Prueba manual del paso: `python empaquetado\construir.py --todo` deja
`dist\Nimbo\nimbo\Nimbo.exe` y **no** deja `app.py` al lado.

### 2. `empaquetado\construir.py` — minificar los `.js`

- `minificar_js(salida)`: corre
  `npx --no-install esbuild <salida>\nimbo\web\js\*.js --minify
  --outdir=<salida>\nimbo\web\js --allow-overwrite`.
- Sin `--bundle`: los módulos se importan por nombre desde `index.html` y
  entre sí, así que hay que conservar un archivo por archivo con su nombre.
  Bundlear obligaría a tocar los `<script type="module">` y no agrega nada
  acá.
- Si `npx`/`esbuild` no está, **avisar y seguir** (`_log("  OJO: ...")`), no
  abortar. El build no puede depender de Node: `runtime\` no lo lleva y
  `app.css` ya viene compilado en el repo justo por eso (CLAUDE.md,
  "Servidor y frontend").
- `esbuild` va a `devDependencies` de `package.json` y se suma un script
  `"minify:js"` para poder correrlo a mano.

Prueba manual del paso: `dist\Nimbo\nimbo\web\js\servidor*.js` pesan menos y
la ventana sigue abriendo y pintando.

### 3. `empaquetado\construir.py` — la red de seguridad

`auditar(salida, compilado)` gana una compuerta, en el mismo espíritu de
`_PROHIBIDOS` (`:109-112`) y con el mismo razonamiento del docstring de
`copiar_codigo`: una lista negra olvidada es como se filtra una credencial, y
un `--compilado` que no se aplicó es como se filtra el código entero.

- Si `compilado` y aparece un `nimbo\*.py` fuera de
  `_PY_EN_DISCO_COMPILADO` → `SystemExit` con la lista.
- Si `compilado` y **no** existe `nimbo\Nimbo.exe` → `SystemExit`: significa
  que `copiar_compilado` no corrió.
- Si `compilado` y aparece `nucleo\scripts\grabador_directo.py` →
  `SystemExit` (ya debería filtrarlo `_NUCLEO_FUERA_COMPILADO:102`, esto es
  la red).

Prueba manual del paso: copiar a mano un `nimbo\motor.py` dentro de
`dist\Nimbo\nimbo\` y volver a correr la auditoría; tiene que abortar.

### 4. `empaquetado\construir.py` — `paquete.json`

Sumar `"compilado"` y `"js_minificado"` al dict de `:673-680`.

### 5. Documentación

- `CLAUDE.md`, bloque de comandos de "Empaquetado para otra máquina": el
  comando de referencia pasa a ser `python empaquetado\construir.py --todo` y
  se aclara que compila. La línea de `compilar.py --comprobar` se queda (sigue
  siendo la forma de ver qué se cierra y qué no).
- `CLAUDE.md`, sección "Empaquetado para otra máquina": donde hoy dice que
  compilar con Nuitka es **opcional**, decir que es el default de `--todo` y
  que `--sin-compilar` es el escape.
- `empaquetado\LEEME.md`: mismo cambio.

---

## Criterios de aceptación

- [x] `python empaquetado\construir.py --todo` termina dejando
      `dist\NimboSetup.exe` sin pasos manuales intermedios. (354 MB; Nuitka
      + paquete + ISCC 213s.)
- [x] `dir dist\Nimbo\nimbo\*.py` lista **exactamente** `puente.py`,
      `lanzador_supervisor.py` y `__init__.py`. (Y ninguno más en todo el
      árbol: el `app.dist\` de Nuitka no deja un solo `.py`.)
- [x] `dist\Nimbo\nimbo\Nimbo.exe` existe y abre la interfaz. (35 MB; sirve
      `/` con `js/main.js` y responde `/api/lista`, `/api/estado`,
      `/api/grabaciones`.)
- [x] `dist\Nimbo\nucleo\scripts\grabador_directo.py` **no** existe.
- [x] `dist\Nimbo\nucleo\scripts\mouflon.py` **sí** existe (queda abierto a
      propósito; si desapareciera, el detector no graba).
- [x] Los `.js` de `dist\Nimbo\nimbo\web\js\` están minificados (149 KB →
      58 KB, 14 archivos) y la ventana pinta las pestañas sin error en la
      consola: recorridas en navegador, **0 errores de consola y 0 pedidos
      fallidos**. (El header tiene cinco pestañas, no seis: Favoritas y
      Siguiendo son filtros dentro de Modelos.)
- [x] Copiar un `nimbo\*.py` cualquiera dentro de `dist\Nimbo\nimbo\` y
      correr la auditoría: aborta nombrando el archivo. (También sin
      `Nimbo.exe` y con `grabador_directo.py`.)
- [x] Armar con `--sin-compilar` deja los `.py` (26 hoy) y **no** aborta.
- [x] `paquete.json` dice `"compilado": true` y `"js_minificado": true`.
- [x] El paquete instalado graba de punta a punta: detector, supervisor y
      camino directo (`--modo directo` relanzando el binario), y une un
      `.mp4`. Medido sobre el paquete: dos modelos en vivo a la vez, una
      por cada camino, 4 sesiones, 4 `unido.json`, y los `.mp4`
      verificados con `ffmpeg -i` (detector 2:39 @1920x1080 h264+aac;
      directo 1:00 y 0:06 @720x960 h264+aac). Parada ordenada por stdin:
      los seis hijos cerraron solos.
- [x] `python empaquetado\compilar.py --comprobar` sigue informando la lista
      correcta de lo que queda abierto.

---

## Lo que apareció al verificar (y hubo que arreglar)

El camino compilado estaba escrito desde SPEC 02 S5 y **nunca se había
ejecutado**. Hacerlo el default destapó tres fallas, todas del mismo tipo
—código que calcula rutas de sí mismo— y todas **silenciosas**: el paquete
abría igual y no se quejaba de nada.

La raíz de las tres, medida con una sonda compilada aparte (Nuitka 4.3.1):

| | en el binario |
|---|---|
| `sys.executable` | `<dist>\python.exe` — **no existe** |
| `sys.argv[0]` | el `.exe` de verdad |
| `sys.frozen` | no definido (es de PyInstaller) |
| `__compiled__` | `True` — el único marcador bueno, el que ya usaba `ajustes.compilado()` |
| `<módulo>.__file__` | nocional: cae en `<paquete>\nimbo\`, no donde vivía el `.py` |

1. **El camino directo no arrancaba nunca.**
   `motor._cmd_directo` devolvía `[sys.executable, "--modo", "directo"]`, o
   sea un `python.exe` inexistente → `Popen` tiraba `FileNotFoundError`.
   Peor: `_reiniciar` tenía `try/finally` sin `except`, así que la
   excepción se perdía con el hilo, `self.directo` quedaba en `None` (la
   rama "se cerró solo" no se enteraba), nadie llamaba a
   `_agendar_reinicio` y el watchdog relanzaba **cada 2s para siempre, sin
   backoff y sin una línea en el log** — 90 relanzados en tres minutos y
   ni un error. Arreglado con `ajustes.ejecutable()` y con un `except` que
   avisa (`nivel="error"`) y agenda el backoff.
2. **El directo no leía `grabar.txt`.** `grabador_directo.AQUI` sale de su
   `__file__`, que compilado cae en `nimbo\`, así que
   `_al_lado("grabar.txt")` buscaba `<paquete>\nimbo\grabar.txt` y
   `<paquete>\datos\grabar.txt`. No encontraba ninguno y el hilo se
   quedaba sin lista, releyendo un archivo que no existe cada 2s.
   Arreglado pasándole `M3U8_GRABAR` desde `motor.py` — la variable que el
   propio módulo ya ofrece para esto (y de ahí sale también
   `activos.txt`).
3. **La unión habría fallado siempre.** `grabador_directo.PYTHON_EXE` es
   `sys.executable` y `DETECTAR_PY` es `AQUI\detectar_m3u8.py`: compilado,
   los dos apuntan a archivos que no existen, así que cada grabación del
   directo se habría quedado en segmentos crudos sin `.mp4`. Arreglado
   desde `lanzador_directo.py`, que pisa esas tres globales
   (`AQUI`/`DETECTAR_PY`/`PYTHON_EXE`) antes de llamar a `main()` — el
   mismo patrón de "parchear desde afuera" que ya usa
   `lanzador_supervisor.py`, para no tocar el núcleo. El intérprete lo
   pasa `motor.py` por `NIMBO_PYTHON`.

Y una cuarta, cosmética pero fea: el banner de puesta a punto decía
**"falta `grabador_directo.py` — Reinstala Nimbo"** en todo paquete
compilado, porque `ajustes.nucleo_incompleto()` lo pedía en disco cuando
es justo el archivo que `_NUCLEO_FUERA_COMPILADO` saca a propósito.
Arreglado con `ajustes.nucleo_requerido()`, que ahora es también de donde
sale el texto del arreglo (así no vuelven a divergir).

Lo que **sí** estaba bien y se confirmó: `copiar_compilado` poniendo el
`.exe` dentro de `nimbo\` (los cinco `_AQUI` resuelven igual que sin
compilar), `ajustes.compilado()`, `arrancar.bat` detectando
`nimbo\Nimbo.exe` para el inicio automático, `OCULTOS` de `compilar.py`
(el canario de MOUFLON da OK adentro del binario), la parada ordenada por
stdin y las miniaturas (`ffmpeg` del paquete).

---

## Decisiones

- **Sí:** `--todo` compila. El flag ya existía y no se usaba; mientras el
  camino cómodo no pase por él, cada build apurado sale abierto. Este es el
  cambio de una línea que hace que todo el trabajo de SPEC 02 S5 sirva para
  algo.
- **Sí:** `construir.py` llama a `compilar.py` él mismo. Es la continuación
  del "un solo comando de punta a punta" que ya guía `--todo`; dos comandos
  encadenados a mano es exactamente cómo se saltea el segundo.
- **Sí:** `--sin-compilar` como escape explícito. Depurar el paquete leyendo
  los `.py` es un caso real; lo que no puede ser es el default.
- **Sí:** la compuerta en `auditar()`. `auditar()` ya existe para esto y su
  docstring ya dice por qué se hace por lista blanca con red de seguridad.
  Un `--compilado` que silenciosamente no se aplicó es el mismo tipo de fallo
  que un `.conf` que se cuela, y merece el mismo `SystemExit`.
- **Sí:** minificar sin bundlear. Los imports son relativos y por nombre de
  archivo (`main.js` → `vista_*.js`); bundlear obliga a tocar `index.html` y
  no mejora la protección.
- **Sí:** que falte `esbuild` avise y no aborte. Node no viaja en `runtime\`
  y el repo ya decidió que el runtime no lo necesita (`app.css` versionado).
  Un build sin Node tiene que seguir saliendo.
- **No:** cerrar `mouflon.py` ahora. Es el único activo que justifica el
  esfuerzo de verdad y es el único cambio que puede **dejar de grabar** si
  sale mal. La salida está identificada y documentada acá para quien la
  implemente: `detectar_m3u8.py` deja de hacer `import mouflon` y le pide el
  descifrado al proceso de Nimbo por el `stdin`/`stdout` que detector y motor
  **ya comparten** (`motor.py` abre los tres hijos con `stdin=PIPE` y lee su
  `stdout` línea por línea; la parada ya viaja por ahí con `"parar\n"`). Así
  `KEY_MAP` vive solo dentro del binario. Va en su propio spec: toca el
  módulo que no se toca, y el fallback natural si falla es el supervisor, que
  justamente sigue en pie como respaldo (CLAUDE.md, proceso 3).
- **No:** cerrar `detectar_m3u8.py`, `supervisor.py`, `puente.py`,
  `lanzador_supervisor.py`. Los carga mitmdump con `-s` y Playwright con su
  driver de Node; ya está razonado en `compilar.py:11-28` y no cambió nada.
- **No:** firmar los `.exe` en este spec. Requiere un certificado OV/EV
  comprado (del orden de USD 200-500/año, y token hardware para EV) que hoy
  no existe, así que el criterio de aceptación no sería ejecutable. Cuando
  exista, el paso es un `signtool sign /fd SHA256 /tr <timestamp-url> /td
  SHA256 /f <pfx> /p <pass>` sobre `dist\Nimbo\nimbo\Nimbo.exe` **antes** de
  `compilar_instalador()` y sobre `dist\NimboSetup.exe` después. Sin eso,
  SmartScreen va a avisar en la primera instalación y nada impide
  reempaquetar el instalador.
- **No:** un ofuscador o packer sobre la salida de Nuitka. Sube el falso
  positivo de los antivirus —que para un `.exe` sin firma que instala una CA
  raíz y levanta un proxy ya es el problema número uno— y no cambia el orden
  de magnitud del esfuerzo de ingeniería inversa.
- **Asumido:** Nuitka standalone deja nombres de módulo, docstrings y
  constantes legibles en el binario. Se acepta: el objetivo es que el código
  no se lea con un editor de texto, no resistir a un desensamblador.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El binario compilado calcula mal las rutas y Nimbo no abre en la máquina del cliente | `copiar_compilado` ya resuelve esto poniendo el `.exe` dentro de `nimbo\` (su docstring, `construir.py:345-367`, lista los cinco `_AQUI` que dependen de eso). El criterio de aceptación exige grabar de punta a punta sobre el paquete instalado |
| Nuitka no encuentra `modos`, `lanzador_directo`, `grabador_directo` o `mouflon` (viven detrás de un `sys.path.insert` en runtime) | Ya está resuelto con `OCULTOS` y el `PYTHONPATH` que arma `compilar.compilar()` (`compilar.py:40-45`, `:106-112`); el criterio "el camino directo graba" lo verifica |
| `--todo` pasa de minutos a bastante más (Nuitka tarda varios minutos y el paquete completo ya tardaba 3m27s en ISCC) | Es un build de distribución, no del día a día; `--compilado <ruta>` permite reusar un `app.dist\` ya hecho mientras se itera |
| `esbuild` minifica algo que rompe un módulo ES | Los `.js` del repo son ES nativo sin transpilar; el criterio de aceptación exige pintar las seis pestañas sin error de consola. Si rompe, `--sin-minificar` (o no instalar esbuild) deja el paquete andando |
| Alguien agrega un `nimbo\*.py` nuevo que sí necesita estar en disco y la auditoría aborta el build | El mensaje nombra el archivo y la solución es una línea en `_PY_EN_DISCO_COMPILADO`, que ya documenta por qué cada uno está ahí |
| `KEY_MAP` sigue legible en `nucleo\scripts\mouflon.py` | Aceptado y documentado. Es el único archivo del paquete cuyo contenido tiene valor por sí mismo, y cerrarlo va en su propio spec |

---

## Verificación

```bat
rem 1. qué se cierra y qué no (no compila, solo informa)
python empaquetado\compilar.py --comprobar

rem 2. el build completo, un solo comando
python empaquetado\construir.py --todo

rem 3. qué quedó legible
dir /b dist\Nimbo\nimbo\*.py
rem  -> solo __init__.py, lanzador_supervisor.py, puente.py

dir /b dist\Nimbo\nucleo\scripts\*.py
rem  -> detectar_m3u8.py, mouflon.py, supervisor.py (y NO grabador_directo.py)

rem 4. la red de seguridad
copy nimbo\motor.py dist\Nimbo\nimbo\
python -c "import sys; sys.path.insert(0,'empaquetado'); import construir; construir.auditar('dist\\Nimbo', True)"
rem  -> tiene que abortar nombrando motor.py
del dist\Nimbo\nimbo\motor.py

rem 5. el escape
python empaquetado\construir.py --descargar --sin-compilar
rem  -> 24 .py y sin abortar
```

A mano, sobre la instalación que deja `dist\NimboSetup.exe`:

1. **Abre.** Doble clic en el acceso directo del escritorio: la ventana de
   Edge tiene que abrir con la interfaz pintada.
2. **Graba por el detector.** Agregar una modelo en línea, Arrancar todo, y
   confirmar `datos\streams\<modelo>_<id>\` creciendo.
3. **Graba por el camino directo.** Marcar una modelo `@directo=1` y
   confirmar en el Administrador de tareas que hay un segundo `Nimbo.exe`
   (es el `--modo directo` relanzándose) y que su sesión crece.
4. **Une.** Parar todo y confirmar que aparece el `.mp4` en
   `datos\grabaciones\` y el `unido.json` en la sesión.
5. **Frontend minificado.** F12 en la ventana, recorrer las seis pestañas,
   abrir el modal de previa y el de una grabación: cero errores en consola.
6. **Registro.** Pestaña Registro: tiene que leer
   `estado\eventos_motor.jsonl` y `eventos_directo.jsonl` igual que sin
   compilar.

No hay suite de tests en el repo; esto es verificación manual, como el resto
de los specs.

---

## Archivos tocados

| Archivo | Qué |
|---|---|
| `specs\08-cobertura_codigo.md` | **nuevo** — este documento |
| `empaquetado\construir.py` | `--todo` compila, llama a `compilar.py`, `--sin-compilar`, `--sin-minificar`, `minificar_js()`, compuerta en `auditar()`, `paquete.json` |
| `package.json` | `esbuild` en `devDependencies` + script `minify:js` |
| `CLAUDE.md` | comando de referencia `--todo`; compilar deja de ser "opcional" |
| `empaquetado\LEEME.md` | mismo cambio + sección "El código cerrado" |
| `empaquetado\compilar.py` | `__init__.py` a `ABIERTOS` (lo deja en disco `construir.py`) y el docstring deja de decir "opcional" |

No estaban en el plan, pero sin ellos el paquete compilado no graba — ver
"Lo que apareció al verificar":

| Archivo | Qué |
|---|---|
| `nimbo\ajustes.py` | `ejecutable()` (el `sys.executable` de Nuitka no existe) y `nucleo_requerido()` |
| `nimbo\motor.py` | `_cmd_directo` usa `ajustes.ejecutable()`; `M3U8_GRABAR` y `NIMBO_PYTHON` al hijo directo; `_reiniciar` ya no se come la excepción |
| `nimbo\lanzador_directo.py` | `_rutas_compilado()`: pisa `AQUI`/`DETECTAR_PY`/`PYTHON_EXE` de `grabador_directo` para que la unión encuentre con qué unir |
| `nimbo\preflight.py` | el texto del arreglo sale de `ajustes.nucleo_requerido()` |

---

## Lo que **no** está en este spec

- Cerrar `mouflon.py` (la tabla `KEY_MAP`): su propio spec, con la salida ya
  documentada arriba.
- Cerrar `detectar_m3u8.py`, `supervisor.py`, `puente.py`,
  `lanzador_supervisor.py`.
- Firmar `Nimbo.exe` y `NimboSetup.exe`: el comando queda escrito en
  Decisiones para cuando haya certificado.
- Ofuscadores, packers, anti-debug.
- Nada de la licencia: eso es SPEC 07.

Cada uno, si entra, va en su propio spec.
