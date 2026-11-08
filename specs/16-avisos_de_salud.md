# SPEC 16 — Avisos de salud: disco, notificaciones y canario

> **Estado:** Aprobado
> **Depende de:** SPEC 01 (deltas de estado por WebSocket), SPEC 03 (camino directo: el campo `degradado`), SPEC 07 (licencia: el aviso de vencimiento), SPEC 12 (rutas de disco: `_disco()` por unidad)
> **Fecha:** 2026-11-07
> **Objetivo:** Que Nimbo avise de lo que ya sabe y hoy no muestra — disco por debajo de un umbral, motor rendido, camino directo degradado y licencia por vencer — con una franja persistente en la ventana, notificaciones de Windows configurables una por una, y la parada ordenada del motor antes de llenar el disco.

---

## Por qué

Nimbo graba de noche con la ventana cerrada. Todo lo que falla en ese
rato existe solo en `estado\eventos_*.jsonl` y en el tooltip de la
bandeja, y los tres datos que importan ya están calculados y publicados:
el problema no es medir, es avisar.

**El disco se mide y nadie lo compara contra nada.**
`Aplicacion._disco()` (`servidor.py:262`) devuelve `libres`/`total` por
unidad y `_bucle_estado` lo publica en el delta cada `INTERVALO_DISCO`
(30s); el frontend lo pinta como texto gris en `#info-disco`
(`vista_grabando.js:renderBarraGlobal`). El único chequeo de espacio del
motor es `detectar_m3u8.py:_hay_espacio()` (línea 1319, `MARGEN_DISCO =
1.10`), y sus dos llamadores (1531 y 1676) están **solo en el camino de
la unión**: la escritura de segmentos no lo consulta nunca, y nada purga
`datos\grabaciones\*.mp4` (`DEFECTOS` tiene `dias_segmentos` y no hay
equivalente para las grabaciones). El modo de falla real no es "se llena
y se pierde todo" — es *"dejó de unir y nadie se enteró"*.

**La bandeja puede notificar y nunca notifica.** `pystray` está cargado
(`app.py:ejecutar_con_bandeja`) y `notify` no aparece en ningún `.py` del
repo. Un motor rendido (agotados los tres reintentos de
`Motor._bucle_vigilancia`) queda en `_resumen_estado()` como título del
icono y en `eventos_motor.jsonl`. Si pasó a las 3 de la mañana, no hay
nada que lo cuente a la mañana siguiente.

**El degradado del camino directo ya viaja hasta el frontend.**
`grabador_directo.py:1176` publica `degradado` por modelo y global, y
`estado_captura.py:96` lo pasa en el dict fusionado. `grep degradado
nimbo\web\js\` da cero: ningún `render*` lo mira. Son 3 fallos de
descifrado y esa modelo deja de grabarse, en silencio.

**El canario de la tabla de claves tampoco se ve.**
`mouflon.verificar_canario()` (`nucleo\scripts\mouflon.py:433`) dice sin
red y al instante si `KEY_MAP` sigue vigente. `grep canario nimbo\` da
cero.

Este spec no inventa métricas nuevas: conecta las cuatro señales que ya
existen a una franja, a un globo de Windows y, en el caso del disco, a la
parada ordenada que ya usa la licencia.

---

## Scope

**Entra:**

- Dos umbrales de disco en `config.json` (`min_libre_gb: 20`,
  `min_libre_critico_gb: 8`), evaluados **por unidad** sobre lo que ya
  publica `_disco()`.
- Al cruzar el umbral de aviso: alerta en la franja + notificación. Al
  cruzar el crítico: además `Motor.parar()` por el camino ordenado, una
  sola vez, y solo si el motor está corriendo.
- Módulo nuevo `nimbo\salud.py`: la política, como función pura sobre el
  snapshot de estado que `_bucle_estado` ya tiene en la mano.
- Sección `salud` en el estado, publicada como delta igual que el resto.
- Franja persistente `#aviso-salud` debajo del header, hermana de
  `#aviso-preflight`: queda hasta que el problema se resuelva.
- Módulo nuevo `nimbo\notificaciones.py`: notificaciones de Windows por
  `icono.notify`, solo en la transición bien→mal, con enfriamiento de 30
  minutos si el estado malo persiste, y degradación silenciosa si no hay
  bandeja.
- Panel "Notificaciones" en Ajustes con un conmutador por clave
  (`notif_rendido`, `notif_degradado`, `notif_disco`, `notif_licencia`),
  por el mecanismo de `/api/preferencias` que ya existe.
- Conmutador `parar_por_disco` (prendido) en la sección "Grabación" de
  Ajustes.
- Semáforo del canario MOUFLON en Ajustes, vía `GET /api/salud`.
- Del degradado del camino directo: **solo** la notificación y el
  contador en la franja.

**Fuera de scope (para specs futuros):**

- Badge de "degradado" en las tarjetas de Grabando/Modelos y el botón
  "pasar al navegador" (`lista.marcar_navegador()`). Decisión explícita:
  el comportamiento del degradado queda exactamente como está hoy.
- Cobertura real por grabación y resumen de la noche (portar
  `pruebas\huecos.py` a un módulo de `nimbo\` y cachearlo por sesión).
- Retención automática de `.mp4` por días o GB, y mover las viejas a un
  disco de archivo.
- Umbral distinto por unidad configurado a mano.
- Notificación de "favorita entró en vivo".
- Cualquier notificación que no sea una de las cuatro claves de arriba.

---

## Modelo de datos

```python
# nimbo\ajustes.py -- claves nuevas en DEFECTOS
"min_libre_gb": 20,            # umbral de aviso, por unidad
"min_libre_critico_gb": 8,     # umbral critico: ademas para el motor
"parar_por_disco": True,       # si el critico para el motor o solo avisa
"notif_rendido": True,
"notif_degradado": True,
"notif_disco": True,
"notif_licencia": True,
```

```python
# estado["salud"] -- seccion nueva del delta de _bucle_estado.
# Lista de alertas vigentes, recalculada en cada tick; se publica solo
# cuando cambia (mismo criterio que "disco").
[
  {"clave": "disco_critico:C:", "tipo": "disco", "nivel": "error",
   "titulo": "Disco C: casi lleno",
   "texto": "quedan 6,2 GB de 142,1 GB -- el motor se paro solo"},
  {"clave": "degradado", "tipo": "degradado", "nivel": "error",
   "titulo": "2 modelos sin grabar",
   "texto": "el camino directo las degrado tras 3 fallos; pasalas a "
            "@navegador= en grabar.txt para que las grabe el navegador"},
  {"clave": "rendido", "tipo": "motor", "nivel": "error",
   "titulo": "detector no arranca",
   "texto": "agoto los reintentos; sigue probando cada 15 minutos"},
]
```

`clave` es el identificador de deduplicación y lleva la unidad adentro
(`disco_aviso:F:`), así que dos discos en problemas son dos alertas y el
enfriamiento de una no tapa a la otra. `nivel` es el mismo vocabulario
que ya usan los avisos del motor (`"aviso"` / `"error"`).

```python
# GET /api/salud -- fuera del delta: KEY_MAP es estatica por build.
{"canario": True}     # None si no se pudo evaluar (falta mouflon.py)
```

Nada de esto se persiste. La lista se recalcula en cada tick y el
enfriamiento vive en RAM, en el `Notificador`: después de un reinicio de
Nimbo, un estado malo que siga vigente vuelve a notificar una vez, que es
lo correcto.

---

## Plan de implementación

1. **`nimbo\ajustes.py`:** agregar las siete claves a `DEFECTOS` con los
   valores de arriba. Nadie las lee todavía. Verificación: Nimbo arranca y
   `config.json` queda con las claves nuevas al guardar.
2. **`nimbo\salud.py`:** `evaluar(estado, ajustes) -> list[dict]`, función
   pura, sin tocar disco ni red: lee `estado["disco"]` (lista por unidad),
   `estado["motor"]["rendido"]` y
   `estado["detector"]["directo"]["degradado"]`, y devuelve la lista de
   alertas. Verificación: llamarla a mano con un dict inventado desde
   `python -i`.
3. **`servidor.py`:** en `_bucle_estado`, después del bloque de disco,
   calcular `salud = salud_mod.evaluar(...)` y meterlo en el delta si
   cambió; sumarlo a `estado_completo()`. Verificación: `/api/lista` sigue
   respondiendo, `/api/estado` trae `"salud": []`, y poniendo
   `min_libre_gb` por encima del libre real aparece la alerta en menos de
   30s.
4. **Franja:** `<div id="aviso-salud" class="aviso-salud oculta">` en
   `index.html`, inmediatamente después de `#aviso-preflight`; módulo
   nuevo `nimbo\web\js\salud.js` que la pinta; registro en la lista de
   `main.js` (`[renderSalud, ["salud"]]`); estilos en `tailwind.css` +
   `npm run build:css`. Verificación: la franja aparece y desaparece sola
   según el umbral.
5. **Parada por disco crítico:** en el mismo bucle, si hay una alerta
   `disco_critico`, `parar_por_disco` está prendido y el motor está
   corriendo, `_lanzar_tarea(app, "motor_parar", app.motor.parar)` —
   exactamente la llamada que ya hace `_bucle_licencia:601`. Latch en
   `app.parada_por_disco` para no repetir; se suelta cuando el espacio
   vuelve por encima del umbral de **aviso** (histéresis, no del crítico).
   Verificación: `eventos_motor.jsonl` tiene una sola parada.
6. **`nimbo\notificaciones.py`:** clase `Notificador` con un `sink`
   opcional, `ENFRIAMIENTO_S = 1800`, método
   `sincronizar(alertas, ajustes)` que notifica solo las claves nuevas
   (transición) o vencidas de enfriamiento, filtrando por la preferencia
   `notif_<tipo>`. El `sink` va envuelto en `try/except`: un backend de
   notificaciones ausente no tira el bucle.
7. **`app.py`:** construir el `Notificador` y asignarle
   `icono.notify` dentro de `ejecutar_con_bandeja`; en
   `ejecutar_sin_bandeja` queda con `sink = None`. Verificación: con el
   umbral forzado, aparece el globo de Windows; con `bandeja: false`,
   Nimbo arranca igual y no tira traceback.
8. **Enganchar las llamadas:** `sincronizar()` desde `_bucle_estado` (las
   alertas de disco, rendido y degradado) y desde `_bucle_licencia`, en la
   rama `r.get("aviso")` que ya existe (`servidor.py:591`), sin tocar la
   lógica de licencia.
9. **Panel "Notificaciones" en Ajustes:** sección nueva en `index.html`
   (`<h3 class="titulo-seccion">Notificaciones</h3>` + `<div
   id="notificaciones">`), ampliar la whitelist del POST
   `/api/preferencias` y el dict del GET con las cinco claves nuevas
   (`notif_*` + `parar_por_disco`), y en `vista_ajustes.js` un array
   `NOTIFICACIONES` con el mismo markup de fila que `PREFERENCIAS`,
   usando `acusarOk`/`acusarMal` de `acuse.js`. Verificación: apagar
   `notif_disco` y comprobar que la franja sigue apareciendo y el globo
   no.
10. **Canario:** `salud.canario()` con import perezoso de `mouflon` desde
    `nucleo\scripts\` (patrón calcado de `sitio.py:_supervisor()`,
    líneas 111-123), cacheado en memoria tras el primer cálculo; `GET
    /api/salud`; semáforo en la sección "Grabación" de Ajustes con el
    enlace a `pruebas\RESULTADOS.md` S1 cuando da `False`. Verificación:
    en esta instalación tiene que dar verde.

---

## Criterios de aceptación

- [ ] Con `min_libre_gb` puesto por encima del espacio libre real, la
      franja `#aviso-salud` aparece en menos de 30 segundos y nombra la
      unidad.
- [ ] Con `min_libre_critico_gb` por encima del libre real y
      `parar_por_disco` prendido, el motor se para y en
      `eventos_motor.jsonl` hay **una** sola parada, no una por tick.
- [ ] Con `parar_por_disco` apagado, la misma situación avisa y el motor
      sigue corriendo.
- [ ] Al volver el espacio por encima del umbral de aviso, la franja
      desaparece sola y la parada vuelve a estar habilitada.
- [ ] Ninguna grabación se borra: el listado de `datos\grabaciones\` es
      idéntico antes y después de cruzar los dos umbrales.
- [ ] Un motor rendido produce exactamente una notificación de Windows, y
      otra si sigue rendido 30 minutos después.
- [ ] Apagar `notif_disco` en el panel deja de mandar el globo del disco y
      no afecta a la franja ni a las otras tres claves.
- [ ] Cada conmutador del panel sobrevive a recargar la ventana (quedó en
      `config.json`) y destella el acuse al guardar.
- [ ] Con `bandeja: false`, Nimbo arranca, la franja funciona y no hay
      traceback en el log.
- [ ] `GET /api/salud` devuelve `{"canario": true}` en esta instalación, y
      Ajustes lo pinta en verde.
- [ ] `estado["salud"]` solo aparece en el delta del WebSocket cuando la
      lista cambió (verificable con `localStorage.nimbo_diag_vivo` apagado
      y mirando los mensajes en la consola).
- [ ] `python nimbo\servidor.py` arranca y `/api/lista` responde con el
      motor parado.

---

## Decisiones

- **Sí:** umbrales en **GB libres por unidad**. Los discos en juego son
  dispares (C: 142 GB total contra F: 562 GB) y un porcentaje razonable
  para uno es absurdo para el otro.
- **No:** porcentaje libre. Por lo mismo.
- **No:** umbral configurado por letra de unidad. Dos números globales
  alcanzan y no hay estado nuevo que mantener.
- **Sí:** dos umbrales, y **nada se borra solo**. Borrar un `.mp4` son
  horas de grabación; que vaya a la papelera no lo hace reversible en la
  práctica si el disco está lleno.
- **No:** retención automática de `.mp4`. Si llega, es su propio spec,
  junto con mover las viejas a un disco de archivo.
- **Sí:** la parada va por `_lanzar_tarea(app, "motor_parar",
  app.motor.parar)`, el mismo camino que la licencia. Reusa el dedupe y el
  tope de 1500s que ya están medidos para una parada que puede tardar 20
  minutos uniendo, y nunca mata un proceso a lo bruto.
- **Sí:** la política vive en `salud.py` como **función pura** sobre el
  snapshot. El dato ya lo junta `_bucle_estado`; así la política se prueba
  con un dict a mano y no hace I/O duplicado.
- **No:** un `_bucle_salud` nuevo. El snapshot fresco ya existe en
  `_bucle_estado`, y dos bucles mirando lo mismo a intervalos distintos se
  desincronizan justo cuando importa.
- **Sí:** notificar solo en la transición bien→mal, con enfriamiento de 30
  minutos. Un disco bajo umbral es un **estado**, no un evento: sin
  enfriamiento serían dos globos por minuto; sin repetición, perderse el
  único globo significa no enterarse nunca.
- **Sí:** un conmutador por clave en un panel propio (lo pidió el
  usuario), con **claves planas** en `DEFECTOS`.
  `Ajustes.__setitem__` rechaza lo que no esté en `DEFECTOS` y
  `/api/preferencias` es clave+valor booleano: un dict anidado obligaría a
  cambiar esa validación y el contrato del endpoint.
- **Sí:** `icono.notify` de pystray, con degradación silenciosa. Mismo
  criterio que la bandeja entera, que ya es opcional.
- **No:** tocar el comportamiento del degradado del camino directo. Ni
  badge en las tarjetas ni pase automático o manual a `@navegador=`:
  decisión del usuario, se avisa y nada más.
- **No:** meter el canario en `/api/preflight`. Ese banner es de puesta a
  punto y sus filas traen un botón que arregla la falla; una tabla de
  claves rotada no se arregla con un click.
- **No:** meter el canario en el delta de 1 Hz. `KEY_MAP` es estática por
  build: se calcula una vez y se cachea.
- **Sí:** la franja se arma con `innerHTML`. La sección `salud` entra al
  delta solo cuando cambió de verdad (no es el camino de 1 Hz que SPEC 01
  prohibe) y no contiene ningún `<img>`, que era el costo real.

---

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| `shutil.disk_usage` falla con la unidad desconectada | `rutas.espacio()` ya devuelve `None` y `_disco()` saltea la fila: sin dato no hay alerta falsa. La unidad caída la avisa SPEC 12 por su cuenta. |
| pystray presente pero sin backend de notificaciones | El `sink` va en `try/except` y la franja sigue siendo la fuente visible. Nunca tira el bucle de estado. |
| La parada por disco deja la noche sin grabar | Es la decisión: sin espacio el detector ya frena la unión solo, y seguir escribiendo segmentos no produce ningún `.mp4`. `parar_por_disco` permite apagarla. |
| Umbral mal puesto (más alto que el libre habitual) | La parada solo actúa si el motor **ya** está corriendo; nunca impide arrancar a mano, y la franja dice por qué se paró. |
| El enfriamiento tapa una alerta nueva del mismo tipo | La `clave` incluye la unidad (`disco_critico:C:`), así que cada unidad tiene su propio enfriamiento. |
| `salud.canario()` importa del núcleo | Import perezoso y solo de `mouflon.py`, el único módulo del núcleo que se puede importar sin construir nada (ver CLAUDE.md, "Los dos puentes"). Nunca `detectar_m3u8.py`. |

---

## Lo que **no** está en este spec

- El badge de "degradado" en las tarjetas y el botón "pasar al
  navegador": el degradado del camino directo se comporta exactamente
  como hoy.
- La cobertura real por grabación y el resumen de la noche (portar
  `pruebas\huecos.py`).
- La retención automática de `.mp4` y el disco de archivo.
- Notificaciones que no sean las cuatro claves (`rendido`, `degradado`,
  `disco`, `licencia`).

Cada una de esas, si llega, va en su propio spec.
