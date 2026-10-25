# La ventana congelada con 6-8+ modelos

Qué pasaba, por qué, qué se cambió y qué midió cada paso. Fecha: **25/10/2026**.

Herramienta: `pruebas\carga_api.py` (solo lee). El lado del cliente se midió con
el navegador headless y `page.route`, ver §5.

---

## 1. El síntoma

Con más de 6-8 modelos en `grabar.txt`, la ventana se queda "pegada": el botón
**Actualizar** no hace nada y la interfaz deja de responder. Pasaba sobre todo
cuando a alguna modelo se le trababa el stream.

## 2. La cadena, verificada en el código

1. `servidor.py:_bucle_estado` publica un delta **cada segundo**: `detector.json`
   + `directo.json` traen contadores de bytes, así que
   `detector != app.estado["detector"]` da `True` en cada tick. Medido contra el
   servidor con un WS suscrito: **12 deltas en 12 s, los 12 con la clave
   `detector`**.
2. `main.js` repintaba **todo** en cada delta, sin mirar qué secciones venían.
3. `vista_grabando.js` y `vista_modelos.js` hacían `cont.innerHTML = ""` y
   reconstruían una tarjeta por modelo, cada una con `construirMiniatura()`.
4. `sitio.js` → `<img src="/api/sitio/imagen?url=...">`. **DOM nuevo = pedido
   nuevo.**
5. `servidor.py:_responder_cabeceras` mandaba `Cache-Control: no-store` en
   *toda* respuesta, incluida la imagen → el navegador no la podía cachear ni en
   memoria. Y `Connection: close` → una conexión TCP nueva por imagen.

**N modelos = N pedidos de imagen por segundo, para siempre.** Cada uno, del
lado del servidor, es un HTTPS saliente al CDN ocupando un hilo del pool de 12
(`EJECUTOR_HILOS`).

### Por qué explota a partir de 6-8 y no en 3

- **Navegador**: Chromium da **6 sockets por host**. Con 8 imágenes/s el pool de
  sockets a `127.0.0.1` queda lleno → el `fetch()` del botón Actualizar nunca
  consigue socket. El botón no está roto: su pedido está encolado.
- **Servidor**: la aritmética está en el medidor `demanda del pool` de
  `carga_api.py`. Con la red sana cada miniatura tarda ~0,1 s y 8/s piden 0,8
  hilos de 12: no satura. Cuando el CDN o el DNS se trancan y cada pedido se
  come su timeout de 10 s, **8/s × 10 s = 80 hilos** contra 12, y la cola de
  `run_in_executor` no tiene tope: no se recupera solo.

Que el DNS acá falla de verdad está documentado: **2.391
`urlopen error [Errno 11001] getaddrinfo failed`** en
`estado\eventos_directo.jsonl`. Y `getaddrinfo()` **no** está cubierto por el
`timeout=` de urllib, así que el timeout por sí solo no acota nada.

### Honestidad sobre la línea de base

Con la red sana **la saturación no se reproduce**. Medido contra la instalación
real antes de tocar nada:

| carga | `/api/lista` p50/p95 | hueco máx. entre deltas |
|---|---|---|
| sin carga | 0 / 15 ms | 1,0 s |
| 8 img/s por 6 sockets | 0 / 16 ms | 1,0 s |
| 24 img/s por 24 sockets | 0 / 15 ms | 1,0 s |

O sea: el brazo de servidor solo muerde con la red fea. El brazo de navegador
(6 sockets) y el volumen de pedidos no dependen de eso. Por eso la puerta
principal de `carga_api.py` **no** es la latencia de la API sino la de la
miniatura: si sale de RAM, el escenario de arriba no puede pasar, ande la red
como ande.

> `/api/estado` **no sirve de sonda**: se responde desde `app.estado`, la caché
> que mantiene `_bucle_estado`, sin tocar el pool (`servidor.py`). Medido con
> él, cualquier saturación queda invisible. La sonda es `/api/lista`, que sí
> pasa por `a_hilo` y es lo que devuelve cada conmutador de las tarjetas.

## 3. Los cinco cambios

| # | Dónde | Qué |
|---|---|---|
| 1 | `servidor.py` handler de `/api/sitio/imagen` | `Cache-Control: public, max-age=86400, immutable`. La URL es `snapshot/<id>/<snapshotTimestamp>`: contenido inmutable por URL. Es la **única** respuesta cacheable del servidor; el resto sigue con el `no-store` global, que este extra pisa vía `.update()`. |
| 2 | `main.js`, `vista_grabando.js`, `vista_modelos.js`, `sitio.js` | Reconciliación por clave en vez de `innerHTML`, y `sincronizarMiniatura()`, que solo reemplaza el `<img>` si la URL cambió. Más el filtrado por secciones de `pintarEstado(estado, secciones)`. |
| 3 | `sitio.py` | LRU en RAM de las miniaturas (`CACHE_IMAGEN_*`), con el fallo cacheado aparte y más corto. Timeout de 10 s → `TOPE_IMAGEN = 4.0`. |
| 4 | `servidor.py` | `CUPO_IMAGEN = 3`, mismo criterio que `CUPO_SITIO`. Y el `latido` del WS sin `await`, para que un pool ocupado no mate la conexión por "sin pong". |
| 5 | `servidor.py`, `sesion.py`, `main.js` | `_lanzar_tarea` devuelve si lanzó; el 202 lleva `ya_corriendo` y el cliente lo loguea. `sesion.TOPE_TOTAL_S = 45` acota la vuelta entera (antes 10 páginas × 15 s = 150 s de botón mudo). |

### Por qué el 2 no se arregla filtrando por secciones

`detector` cambia en **cada** tick, y de él dependen `renderTarjetas`,
`renderFavoritas` y `renderBarraGlobal`. El filtrado solo saca del camino a
`renderSesion` y `renderDirectivas`. Lo que hace barato el repintado es que las
tarjetas se actualizan in-place: el `<img>` no se recrea, así que no se vuelve a
pedir.

## 4. Lo que midió cada paso (servidor)

`python pruebas\carga_api.py --imagenes 8 --sockets 6 --seg 25`

| | antes | después |
|---|---|---|
| pedidos que llegan al servidor | 480/min | 480/min *(la herramienta pide igual: es el cliente)* |
| **latencia de la miniatura p50** | **78 ms** | **0 ms** |
| demanda del pool | 0,8 hilos de 12 | 0,3 hilos de 12 |
| `/api/lista` p95 | 16 ms | 16 ms |
| hueco máx. entre deltas | 1,0 s | 2,3 s |

El p50 de 0 ms es la caché de RAM: **el pedido ya no sale a la red**. Con eso,
los 80 hilos del peor caso de §2 pasan a ser 0.

Verificado aparte, sobre `sitio.Sitio` directo: 79,7 ms → 0,009 ms en el
segundo pedido; el fallo también se cachea (221,9 ms → 0,011 ms); un host fuera
de `HOSTS_IMAGEN_PERMITIDOS` no se pide ni se cachea; y los invariantes de la
LRU (tope de entradas, tope de bytes, imagen más grande que el tope, reemplazo
de la misma clave, orden por uso y no por inserción, expiración sin dejar el
contador inflado) dan todos bien.

### El cupo, comprobado contra la predicción

24 pedidos fríos lanzados de una contra el servidor:

```
tiempo de servicio de un pedido frio: 91 ms
12 pedidos frios de una:              318 ms
  prediccion con cupo 3:              364 ms
  prediccion sin cupo (12 hilos):      91 ms
  -> cupo efectivo medido:            3.4
```

## 5. Lo que midió el punto 2 (cliente)

Navegador headless contra un servidor de prueba con `NIMBO_BASE` propio y un
detector falso que reescribe `directo.json` a 1 Hz (para que el delta por
segundo —la condición del bug— exista de verdad y la medición no sea vacua).

Pestaña Grabando, ventana de 14 s con ~14 repintados de por medio (probado: el
`.detalle` pasó de `1080p_h264 · 1:00 · 61.0 MB` a `1080p_h264 · 1:15 · 75.0 MB`):

```
pedidos de miniatura nuevos ... 0
el <img> marcado sobrevivio ... si
```

Pestaña Modelos, 28 tarjetas con imagen, misma ventana: **0 pedidos nuevos**.

A/B sobre la misma instalación con `localStorage.nimbo_tarjetas_in_place`,
contando pedidos interceptados con `page.route` (que además desactiva la caché
HTTP: es el caso "Disable cache" / ventana recién abierta, que el punto 2 tiene
que cubrir solo):

| variante | tarjetas | pedidos en 20 s | el `<img>` sobrevivió |
|---|---|---|---|
| `innerHTML` (antes) | 26 | 26 | **no** |
| in-place (nuevo) | 26 | **0** | **sí** |

> **Cuidado al medir esto**: `performance.getEntriesByType("resource")` **no**
> sirve con el punto 1 puesto. Un `<img>` recreado que pega en la caché de
> *memoria* de Chromium no genera entrada de performance ninguna, así que las
> dos variantes miden 0 y el punto 2 queda invisible. Hay que contar pedidos
> interceptados, o mirar si el nodo `<img>` sobrevive al repintado.

## 6. Lo que NO se tocó

- **`EJECUTOR_HILOS`** sigue en 12. Subirlo es un parche: con los puntos 1-4 la
  demanda se desploma (0,8 → 0,3 hilos) en vez de repartirse mejor.
- **El bucle 403 del camino directo**: 14.904 líneas
  `media playlist dio 403 otra vez tras pedir un master nuevo` en
  `eventos_directo.jsonl`, ~3/s sostenidas por horas. Es ruido real y merece su
  propio arreglo, pero no es lo que congelaba la ventana.
- **`vivo._candidatos`**: `listdir` + regex del directorio de segmentos entero
  cada 500 ms con la previa abierta. Hoy son 21.309 archivos en una sesión y
  44 ms medidos; crece lineal con las horas de grabación. **Deuda anotada.**
- **Cachear los estáticos** (`app.css`, `js\*.js`): se re-descargan en cada
  apertura de ventana, pero no están en el camino de 1 Hz.

## 7. El `WinError 5` sobre `directo.json`

Aparece 23 veces en `eventos_directo.jsonl` y se **reprodujo** acá: el detector
falso, escrito sin reintento, murió con
`PermissionError: [WinError 5] Acceso denegado: directo.json.part -> directo.json`
a los pocos segundos de tener a Nimbo leyendo el archivo.

Es la carrera que `grabador_directo.py:_escribir_atomico` ya contempla
(reintenta 5 veces en 250 ms). Que igual se escape 23 veces significa que el
lector tuvo el handle más de 250 ms, o sea que el pool estaba atascado. **Es
síntoma, no causa**: tiene que desaparecer solo con este arreglo. Si vuelve a
aparecer, el pool se está saturando por otra cosa.

## 8. Cómo reproducir la verificación

```bat
rem servidor: antes y despues, contra la instalacion real
python pruebas\carga_api.py --solo-estado --seg 20
python pruebas\carga_api.py --imagenes 8 --sockets 6 --seg 25

rem si no hay instancia.json (p.ej. arrancado con `python nimbo\servidor.py`)
python pruebas\carga_api.py --url "http://127.0.0.1:PUERTO/?t=TOKEN"
```

En la consola de la ventana de Nimbo, con 6-8 modelos y la pestaña Grabando
abierta un par de minutos:

```js
// cuantos pedidos de miniatura lleva hechos la ventana
performance.getEntriesByType("resource")
  .filter(r => r.name.includes("sitio/imagen")).length
// antes: crecia ~N por segundo. despues: queda clavado.

// el "antes" sobre la misma instalacion, para comparar
localStorage.nimbo_tarjetas_in_place = "0";   // F5 para que tome efecto
```

Y a mano: apretar **Actualizar** dos veces seguidas tiene que loguear
"ya hay una actualizacion en curso" en vez de quedarse mudo.
