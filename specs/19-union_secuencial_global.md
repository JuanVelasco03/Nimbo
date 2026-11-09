# SPEC 19 — Unión secuencial entre todos los sitios

> **Estado:** Aprobado
> **Depende de:** SPEC 03 (camino directo), SPEC 12 (rutas de disco), SPEC 13 (freno de la unión), SPEC 18 (segundo sitio chaturbate)
> **Fecha:** 2026-11-08
> **Objetivo:** Que no haya nunca más de una unión a la vez en toda la instalación — un turno único que compartan el detector, el camino directo, chaturbate y el `--unir` a mano — para que unir una grabación no le robe disco a las que siguen grabando.

---

## Por qué

SPEC 13 arregló *cuánto* lee una unión (`_Freno`, `RITMO_UNION = 25 MB/s`,
`detectar_m3u8.py:1178`). Lo que nunca se arregló es *cuántas uniones hay*.

Hoy hay cuatro productores de uniones, cada uno serial por su cuenta y ninguno
enterado de los otros tres:

| Productor | Dónde | Cómo une |
|---|---|---|
| Detector, cierre de sesión | `detectar_m3u8.py:2830 _finalizar` → `:2856`, hilo `finalizador` (`:2065`) | `unir_sesion()` en proceso |
| Detector, arranque | `detectar_m3u8.py:1779 recuperar_pendientes` → `:1789`, hilo `recuperador` (`:2102`) | `unir_sesion()` en proceso |
| Camino directo | `grabador_directo.py:309 _unir_en_fondo`, hilo de `_bucle_union` (`:1230`) | subproceso `--unir` |
| Chaturbate | `grabador_chaturbate.py:346 _unir_doble`, hilo `chaturbate-union` (`:1038`) | dos subprocesos `--unir` + `_mezclar_audio` |

Son **cuatro uniones posibles al mismo tiempo**, y dos de ellas dentro del mismo
proceso: el detector arranca `recuperador` y `finalizador` como hilos separados
(`:2065` y `:2102`), así que una recuperación de pendientes al arrancar corre en
paralelo con el primer cierre de sesión de la noche. El comentario de `CLAUDE.md`
que dice *"una por vez: la unión ya es serial por diseño"* describe el mundo de
antes de SPEC 03 y dejó de ser verdad con SPEC 18.

El daño es el que SPEC 13 ya midió, multiplicado: el freno le pone un techo de
25 MB/s **a cada unión**, no al total. Con dos uniones en curso el disco entrega
50 MB/s a lecturas de archivos chicos mientras ocho modelos escriben segmentos; y
si las dos están en la fase de copia al escenario (`_preparar_escenario:1369`) son
además 50 MB/s de escritura. La noche del 2026-10-29, con **una sola** unión sin
frenar, cinco sesiones perdieron entre 430 y 856 s de video.

No se pisa nada literalmente: `_preparar_escenario:1416` usa `tempfile.mkdtemp`,
así que dos uniones simultáneas tienen escenarios distintos. El problema es de
contención, no de colisión de rutas.

Aparte, y por el mismo motivo: `_grabaciones_por_estado():1235` recorre
`("directo.json", "detector.json")` y nunca mira `chaturbate.json`. SPEC 18 agregó
un grabador que publica su estado con exactamente la misma forma
(`grabador_chaturbate.py:1139 _publicar_estado`: `generado`, `streams[].grabando`,
`streams[].ultimo_segmento`) y el freno no lo lee. Hoy una unión de stripchat va a
fondo aunque chaturbate esté grabando: el freno nuevo de SPEC 13 tiene un agujero
del tamaño del sitio nuevo.

---

## Scope

### Entra

- Un **turno único por instalación** para unir, con granularidad de *una sesión*
  (una modelo, una variante): se toma antes de empezar y se suelta al terminar,
  sin importar de qué sitio venga.
- Un módulo nuevo, `nucleo\scripts\cerrojo_union.py`, protocolo puro y **seguro de
  importar** con el mismo criterio que hace seguros a `mouflon.py` y
  `chaturbate.py` (sin `addons = [...]`, sin estado de módulo mutable que importe,
  solo librería estándar): un mutex nombrado de Windows por `ctypes`.
- El turno se toma en los **llamadores**, no dentro de `unir_sesion()`, para que
  cubra todo el trabajo de disco de una sesión: la copia al escenario, el remux de
  ffmpeg y el `_mezclar_audio` de chaturbate.
- Reentrancia a través del límite de proceso: el subproceso `--unir` que lanzan el
  directo y chaturbate hereda por entorno el hecho de que su padre ya tiene el
  turno, y no vuelve a pedirlo (si lo pidiera, se trabaría con su propio padre).
- Durante la parada (`_parar` puesto) **no se espera turno**: se une ya mismo.
- `chaturbate.json` sumado a `_grabaciones_por_estado()`, para que el freno de
  SPEC 13 vea también las grabaciones del sitio nuevo.
- Una línea de log por transición de turno (`esperando` / `tomado` / `suelto`), con
  la espera en segundos.
- El lugar de `cerrojo_union.py` en el empaquetado: abierto en disco como
  `mouflon.py` (lo importa `detectar_m3u8.py`, que no se compila) y en `OCULTOS`
  de `compilar.py` porque lo importan los dos grabadores, que sí van al binario.

### No entra

- Tocar `RITMO_UNION`, `MARGEN_DISCO`, `MARGEN_ESCENARIO`, `MAX_REPARACIONES` ni el
  propio `_Freno` más allá de sumar un archivo a la tupla que ya lee. Son los
  números con los que se midió el freno y recalibrarlos es otro spec.
- Cualquier cambio en `nimbo\web\` o en la forma de los JSON de estado. El turno se
  ve en el registro, no en la interfaz.
- Hacer el turno configurable desde `config.json`. Mismo criterio con el que
  SPEC 13 descartó hacer `RITMO_UNION` configurable.
- Garantizar orden FIFO estricto de llegada. El turno garantiza *una por vez*, no
  *en el orden en que cerraron*.
- Unificar las tres colas de unión en una sola cola compartida (un servicio de
  unión aparte, o una cola en disco). Es la solución "grande" y no hace falta:
  cuatro productores serializados por un mutex alcanzan.
- Refactorizar `grabador_directo.py` y `grabador_chaturbate.py` en un módulo común.
  Siguen siendo un puerto deliberado, no una generalización (SPEC 18).
- Subir `ESPERA_CIERRE_DETECTOR` / `ESPERA_CIERRE_DIRECTO` de `motor.py`. El punto 3
  del plan está diseñado justo para no necesitarlo.

---

## Modelo de datos

No hay archivos nuevos ni claves nuevas en `config.json`. Lo único que aparece es
un objeto del kernel de Windows y una variable de entorno:

```
# Mutex nombrado, dueño del turno de unión.
#   Local\NimboUnion.<hash>
# <hash> = primeros 16 hex de sha256(abspath de STREAMS_DIR), para que dos
# instalaciones (o un NIMBO_BASE/M3U8_BASE de prueba) no se bloqueen entre si.
# "Local\" = sesion del usuario: los cinco procesos de Nimbo corren en la misma.

# Variable de entorno, heredada por los subprocesos --unir.
NIMBO_UNION_TURNO=<nombre completo del mutex>
#   Puesta por quien TOMA el turno, antes de lanzar al hijo. El hijo la ve al
#   importar cerrojo_union y no vuelve a pedir el turno. Se lee UNA vez, al
#   importar el modulo (_HEREDADO), para que las mutaciones que hace este mismo
#   proceso despues no lo confundan.
```

Constantes nuevas, en `cerrojo_union.py`:

```python
# Tope absoluto de espera, en segundos. NO es el caso normal: en operacion
# normal el turno se espera lo que haga falta (modelo 1, despues 2, despues 3).
# Existe solo como red contra un turno filtrado -- algo que Windows ya hace casi
# imposible, porque suelta el mutex cuando el proceso dueño muere (y el que
# espera recibe WAIT_ABANDONED, que igual le da la propiedad). 3 h es mas del
# doble del peor caso medido: 7 GB frenados a RITMO_UNION por 1 + MAX_REPARACIONES
# pasadas = ~39 min.
ESPERA_TURNO = 10800.0
# Cada cuanto se despierta WaitForSingleObject para mirar el evento de parada.
# Un segundo: no se nota en una espera de minutos y hace que la parada no tenga
# que esperar a que termine la union ajena.
TAJADA_ESPERA = 1.0
```

---

## Plan de implementación

Cada paso deja el sistema funcionando. Los pasos 1 y 2 no cambian ningún
comportamiento por sí solos.

1. **`nucleo\scripts\detectar_m3u8.py:1235`** — agregar `"chaturbate.json"` a la
   tupla de `_grabaciones_por_estado()`. Una línea, más el comentario que diga que
   los tres archivos tienen la misma forma y quién escribe cada uno. Arregla el
   agujero del freno de SPEC 13 sin depender del resto del spec.

   **Puerta:** con solo chaturbate grabando, un `--unir --forzar` de una sesión
   vieja de stripchat loguea `[UNIR] freno remux: puesto (hay emisiones
   grabandose)`. Antes de este paso no loguea nada.

2. **`nucleo\scripts\cerrojo_union.py`** (nuevo) — el módulo del turno. Solo
   stdlib (`os`, `sys`, `hashlib`, `time`, `threading`, `contextlib`, `ctypes`),
   nada de `nimbo\`, nada que se construya al importar.

   ```python
   NOMBRE_ENTORNO = "NIMBO_UNION_TURNO"

   def nombre_turno(raiz: str) -> str:
       """'Local\\NimboUnion.<hash de raiz>'. raiz = STREAMS_DIR."""

   _HEREDADO: bool            # al IMPORTAR: os.environ.get(...) == nombre_turno(...)
   _local = threading.Lock()  # los hilos de ESTE proceso (finalizador + recuperador)

   @contextlib.contextmanager
   def turno(etiqueta: str, parar=None, raiz: str = ""):
       """Un turno de union. Nadie mas une mientras este abierto.

       - Si lo heredo de un padre (_HEREDADO), no hace nada: el padre lo tiene.
       - Si ctypes/CreateMutexW falla (o no es Windows), avisa UNA vez y sigue
         sin turno: degrada exactamente al comportamiento de antes de este spec,
         nunca deja una sesion sin unir.
       - `parar`: threading.Event opcional. Si se pone mientras se espera, se
         deja de esperar y se une igual (ver paso 5).
       """
   ```

   Lo que hace `turno()`, en orden: `_HEREDADO` → ceder y salir; tomar `_local`;
   `CreateMutexW(None, False, nombre)`; `WaitForSingleObject` en tajadas de
   `TAJADA_ESPERA` hasta `ESPERA_TURNO`, mirando `parar` entre tajadas; poner
   `os.environ[NOMBRE_ENTORNO]`; `yield`; y en un `finally` que corre siempre:
   sacar la variable de entorno, `ReleaseMutex`, `CloseHandle`, soltar `_local`.

   Log, una línea por transición y solo si de verdad hubo que esperar:
   `[UNIR] turno <etiqueta>: esperando (otra union en curso)` →
   `[UNIR] turno <etiqueta>: tomado tras %.0f s` → `[UNIR] turno <etiqueta>:
   suelto tras %.0f s`. Si no hubo espera, una sola línea al soltar. SPEC 13 dejó
   la lección escrita: *"el freno anterior fracasó en silencio durante meses: sin
   una línea por transición no hay forma de saber si enganchó"*.

   **Puerta:** `python -c "import cerrojo_union, time; ..."` desde dos consolas a
   la vez — la segunda imprime "esperando" y entra recién cuando la primera sale.
   Nada de Nimbo cambió todavía.

3. **`nucleo\scripts\detectar_m3u8.py`, los tres llamadores de `unir_sesion()`** —
   envolver cada llamada en `cerrojo_union.turno(...)`, nunca el cuerpo de
   `unir_sesion()` (dejarlo libre es lo que permite que el hijo `--unir` no se
   trabe con su padre y que el turno cubra también el mux de chaturbate):

   - `:2856`, dentro de `_finalizar` — `parar=self._parar`, etiqueta `etiqueta`.
   - `:1789`, dentro de `recuperar_pendientes()` — sin evento de parada.
   - `:3045`, dentro de `main()` para `--unir` / `--recuperar` — sin evento. Este
     es el que cubre el `--unir --forzar` a mano, y también el que el hijo del
     directo/chaturbate saltea por `_HEREDADO`.

   `raiz=STREAMS_DIR` en los tres.

   **Puerta:** el detector solo, con `recuperar_pendientes()` encontrando
   pendientes y una sesión cerrando al mismo tiempo: el log muestra las dos
   uniones una después de la otra, nunca entrelazadas.

4. **`nucleo\scripts\grabador_directo.py:309 _unir_en_fondo`** — tomar el turno
   **después** del `escritor.barrera().wait(180)` (no hay por qué ocupar el turno
   mientras se esperan escrituras pendientes) y envolver el `subprocess.run`.
   Firma nueva: `_unir_en_fondo(escritor, sesion, parar=None)`; `_bucle_union`
   (`:1230`) le pasa `self._parar`.

5. **`nucleo\scripts\grabador_chaturbate.py:346 _unir_doble`** — tomar el turno
   una vez, alrededor de **las dos** uniones (video y audio) y del
   `_mezclar_audio`. Firma nueva `_unir_doble(escritor, pista_video, pista_audio,
   parar=None)`; `_bucle_union` (`:1038`) pasa `self._parar`, y
   `_correr_una_vez` (`:1203`) pasa `None`. `_unir_en_fondo` de este archivo **no**
   toca el cerrojo: lo tiene el `_unir_doble` que lo llama, y el subproceso lo
   saltea por `_HEREDADO`.

   Sobre el `parar=`: durante la parada el turno se deja de esperar a propósito.
   Nada está grabando (el directo y chaturbate cierran sus sesiones antes de drenar
   `cola_union`, así que su JSON queda con `streams: []` y el freno se suelta solo
   — SPEC 13 ya cuenta con eso), así que no hay nada que proteger, y esperar turno
   haría que una parada excediera `ESPERA_CIERRE_DIRECTO = 1260s` (`motor.py:78`) y
   el motor se rindiera justo sobre una unión a medias.

6. **Empaquetado** — `cerrojo_union.py` es el caso de `mouflon.py`, no el de
   `chaturbate.py`: lo importa `detectar_m3u8.py`, que **no** se compila, así que
   tiene que quedar como `.py` en disco (fuera de
   `construir.py:_NUCLEO_FUERA_COMPILADO`, dentro de `compilar.py:_ABIERTOS`) y
   además sumarse a `compilar.py:OCULTOS`, porque lo importan `grabador_directo` y
   `grabador_chaturbate`, que sí entran al binario y Nuitka no los descubre solo.

   **Puerta:** `python empaquetado\compilar.py --comprobar` nombra
   `cerrojo_union.py` entre los que quedan abiertos y no se queja.

7. **`CLAUDE.md`** — corregir el paréntesis *"una por vez: la unión ya es serial
   por diseño"* de la sección del escenario de unión, que es exactamente la
   suposición que este spec rompe, y documentar el turno en "Los dos puentes", la
   fila de `_grabaciones_por_estado()` y la lista de módulos seguros de importar.

---

## Criterios de aceptación

- [ ] `nucleo\scripts\cerrojo_union.py` existe, no importa nada de `nimbo\`, y
      `python -c "import cerrojo_union"` no arranca ni construye nada.
- [ ] Dos `--unir --forzar` a mano, en dos consolas, sobre dos sesiones distintas:
      el segundo loguea `turno ...: esperando` y su primera línea de
      `[UNIR] ... remuxeando` aparece **después** del `turno ...: suelto` del
      primero.
- [ ] Con stripchat y chaturbate grabando a la vez y dos sesiones cerrando casi
      juntas, `eventos_directo.jsonl` y `eventos_chaturbate.jsonl` no tienen nunca
      dos uniones solapadas: cada `turno ...: tomado` tiene su `suelto` antes del
      `tomado` siguiente.
- [ ] Durante esas dos uniones, `%TEMP%` nunca tiene más de una carpeta
      `nimbo_union_*` al mismo tiempo.
- [ ] Con solo chaturbate grabando, un `--unir --forzar` de una sesión de
      stripchat loguea `[UNIR] freno remux: puesto`. Antes de este spec no lo
      logueaba.
- [ ] El subproceso `--unir` que lanza el directo **no** espera turno (lo hereda):
      su log no tiene ninguna línea de `turno` propia, y la unión arranca sin
      segundos de espera agregados.
- [ ] Una parada del motor con uniones pendientes en las dos colas termina dentro
      de `ESPERA_CIERRE_DIRECTO` y deja los dos `.mp4` completos, con su
      `unido.json`.
- [ ] Matar el proceso dueño del turno a lo bruto (Administrador de tareas) no deja
      el turno trabado: la unión siguiente lo toma y loguea la espera real.
- [ ] `--unir --forzar` a mano desde una consola **sin Nimbo corriendo** sigue
      funcionando igual que hoy: toma el turno al instante, sin freno, sin espera.
- [ ] `pruebas\huecos.py` sobre una noche con los dos sitios grabando y varias
      uniones: la pérdida por sesión no es peor que la de una noche de un solo
      sitio.
- [ ] `python empaquetado\compilar.py --comprobar` nombra `cerrojo_union.py` entre
      los que quedan abiertos en disco, y un paquete compilado lo tiene como `.py`
      en `nucleo\scripts\` sin que `auditar()` aborte.

---

## Decisiones

- **Sí:** un mutex nombrado de Windows, no un archivo de cerrojo. Windows lo suelta
  cuando el dueño muere, así que no hay cerrojo rancio que limpiar a mano; el que
  esperaba recibe `WAIT_ABANDONED` y se queda con la propiedad igual. Un `.lock`
  en `estado\` habría necesitado heartbeat, detección de PID muerto y limpieza al
  arrancar — tres cosas que fallan en silencio. `msvcrt.locking` sobre un archivo
  también lo libera el SO, pero obliga a una espera por sondeo; `WaitForSingleObject`
  espera sin quemar CPU.
- **Sí:** el nombre del mutex depende de `STREAMS_DIR`. Es lo que permite correr un
  `M3U8_BASE`/`NIMBO_BASE` de prueba (ver "Al correr fuera de la instalación real"
  en `CLAUDE.md`) sin que le pida turno a la instalación de verdad, ni al revés.
- **Sí:** el cerrojo en los **llamadores**, no dentro de `unir_sesion()`. Dentro
  sería un solo lugar y cubriría los cuatro caminos de una, pero dejaría afuera el
  `_mezclar_audio` de chaturbate (`grabador_chaturbate.py:_unir_doble`), que es otro
  ffmpeg leyendo dos `.mp4` enteros del mismo disco, y partiría el par video+audio
  de una misma grabación en dos turnos separados.
- **Sí:** reentrancia por variable de entorno, leída **una sola vez al importar**.
  El padre (directo/chaturbate) tiene el turno y el hijo (`--unir`) lo heredaría sin
  saberlo: sin esto, el hijo esperaría un mutex que solo su propio padre puede
  soltar. Leerla al importar y no en cada llamada es lo que evita que un proceso se
  confunda con su propia escritura de la variable.
- **Sí:** en operación normal se espera el turno lo que haga falta — modelo 1,
  después 2, después 3, sin importar el sitio. `ESPERA_TURNO = 10800` no es "el
  tiempo que se espera", es la red contra un turno filtrado; el caso real es
  `WAIT_OBJECT_0` en minutos.
- **Sí:** durante la parada no se espera turno. No hay grabaciones que proteger y
  es lo que mantiene la parada dentro de `ESPERA_CIERRE_DIRECTO = 1260s` sin tocar
  ese número. Es el mismo razonamiento con el que SPEC 13 dejó que el freno se
  suelte solo en la parada.
- **No:** FIFO estricto por orden de llegada. `WaitForSingleObject` no garantiza
  orden, y no hace falta: lo que importa es que haya una sola unión a la vez, no
  cuál de las dos pendientes va primero. Un sistema de tickets (archivo de turnos
  en `estado\`) sería mucho más código para ordenar dos o tres sesiones.
- **No:** una cola de unión compartida en disco, o un sexto proceso "unidor". Es la
  solución general y hace falta un día si los sitios se multiplican, pero hoy
  significa reescribir las tres colas que ya funcionan y mover la unión fuera del
  proceso que sabe cuándo cerró la sesión.
- **No:** unificar `grabador_directo.py` y `grabador_chaturbate.py`. SPEC 18 decidió
  portar en vez de generalizar porque el directo graba 24/7 en producción, y ese
  motivo no cambió.
- **No:** hacer el turno (o su espera) configurable. Mismo criterio con el que
  SPEC 13 descartó hacer `RITMO_UNION` configurable: una constante con la cuenta
  escrita al lado se defiende mejor que un valor que se puede torcer.
- **No:** tocar `ESPERA_UNION = 3600` de los dos grabadores. Con el turno en el
  padre, el hijo `--unir` sigue teniendo su hora entera para trabajar: la espera de
  turno pasa afuera de ese `timeout=`, no se le come tiempo. Esto es justamente lo
  que descartó poner el cerrojo dentro de `unir_sesion()`.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El hijo `--unir` no hereda la variable de entorno (alguien pasa un `env=` limpio) y espera un mutex que solo su padre puede soltar: la unión se cuelga hasta `ESPERA_TURNO` | Los dos grabadores ya lanzan al hijo con `env=dict(os.environ)` (`grabador_directo.py:339`, `grabador_chaturbate.py:264`), y el criterio de aceptación "el subproceso no espera turno" prueba justo eso. Aun en el peor caso, `ESPERA_TURNO` suelta y la unión sale: no se pierde el `.mp4` |
| La unión total de la noche tarda más y la cola de alguno de los tres se acumula | Las colas ya son seriales por diseño y el `qsize()` ya se publica en `uniendo` de cada JSON de estado; una cola que crece queda a la vista sin agregar nada |
| `ctypes`/`CreateMutexW` falla en el Python empaquetado de mitmdump (el detector) y nadie se entera | El módulo avisa una vez y degrada a "sin turno", que es exactamente el comportamiento de hoy. El aviso va al log del detector, que `puente.py` reenvía a `eventos_detector.jsonl` |
| El turno queda tomado por un proceso vivo pero colgado (no muerto): Windows no lo suelta y todo lo demás espera | `ESPERA_TURNO` lo destraba a las 3 h y la unión sale igual, con un `error` en el log que nombra la etiqueta que ocupaba el turno. Es el mismo criterio pegajoso del freno de SPEC 13: ante la duda, no acelerar |
| Durante la parada se vuelven a solapar uniones (el turno no se espera) y el disco se satura | Es deliberado: nada graba durante la parada. El detector y los dos grabadores paran en secuencia (`motor.py`), así que el solapamiento real es chico |
| El turno serializa también las uniones de la **recuperación** al arrancar, y arrancar se vuelve más lento | Es lo que se quiere: `recuperar_pendientes()` corre justo cuando el detector empieza a grabar de nuevo, el peor momento para leer a fondo. Hoy ese hilo compite con `finalizador` dentro del mismo proceso |

---

## Lo que **no** está en este spec

- Recalibrar `RITMO_UNION` con los dos sitios grabando. La constante ya está
  aislada en un solo lugar y `pruebas\huecos.py` mide el daño; se evalúa con datos.
- Mostrar el turno o la cola de unión en la interfaz.
- Mover la unión a un proceso propio, o a una cola de unión compartida en disco.
- Unificar `grabador_directo.py` y `grabador_chaturbate.py`.
- Subir `ESPERA_CIERRE_DETECTOR` / `ESPERA_CIERRE_DIRECTO`.
