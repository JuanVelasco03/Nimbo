# Reporte de pruebas de punta a punta — camino directo (2026-10-22, ~19:30-19:40)

Contexto: reinicio completo de Nimbo (app.py + motor) para validar en limpio
tres fixes aplicados hoy a `nucleo\scripts\grabador_directo.py`:

1. Cola de unión en serie (`Grabador.cola_union` + `_bucle_union`), en vez de
   un hilo suelto o el pool de descargas por cierre de sesión.
2. `stdin=subprocess.DEVNULL` en el subproceso `--unir`.
3. Un status HTTP ≠ 200 en la media playlist ya no cuenta como fallo hacia
   `degradado` — solo fuerza una reconciliación (master nuevo) en el próximo
   ciclo.

## Confirmado arreglado

Con 6 modelos reales grabando a la vez (`afrodita__25`, `_iink_`,
`__abrill__`, `ivyvalentine`, `kendall_ruiz`, `_marr_`) y sacándolas todas de
`grabar.txt` en el mismo ciclo (prueba de estrés — este es el escenario que
colgó antes del fix):

- Las 5 que tenían segmentos se unieron **en serie**, sin solaparse, en
  menos de 8 segundos totales (ver `estado\eventos_directo.jsonl`,
  cierres a `t=1792715799.x`, últimos `[UNIR]...listo` a `t=1792715807.x`).
- La sexta (`__abrill__`) cerró sola sin segmentos («sin segmentos, no se
  une») — correcto, no hay nada que unir.
- Cero procesos `python.exe`/`ffmpeg.exe` colgados después (verificado con
  `Get-Process`).
- Cero apariciones de `Acceso denegado`/`WinError 5` en `directo.json`
  desde el reinicio (antes del reinicio sí aparecían, repetido).
- Cero `DEGRADADO` desde el reinicio (antes del reinicio aparecían ~10
  veces en la corrida vieja).
- Cero `--unir no termino en 20 min` desde el reinicio (antes del reinicio
  aparecieron 5 veces en la corrida vieja, todas sesiones que quedaron sin
  unir en `datos\grabaciones`).

Una modelo removida sola (`melody_rousse__`) se unió en ~1 segundo sin
afectar a las que seguían grabando (`afrodita__25` siguió sin corte).

## Bug nuevo encontrado (no corregido a propósito — pide el usuario)

**Síntoma:** una modelo en estado `p2p` (`__abrill__` durante esta prueba)
queda pidiendo la media playlist y recibiendo 403 en loop **continuo**, sin
backoff, cada ~1.2-1.6s, sin bajar nunca el ritmo mientras dura el estado.
En esta corrida: 106+ warnings en 45s, sin un solo 200 intermedio, mientras
duró la observación (no vimos si se resuelve sola cuando `p2p` termina,
porque no esperamos tanto).

**Por qué pasa (lectura del código, sin tocar nada):**

- `mouflon.ESTADOS_VIVOS = {"public", "live", "groupShow", "p2p",
  "private", "ticketShow"}` — `p2p` se trata como "en vivo" a propósito
  (es una decisión de diseño existente, portada de `supervisor.py`), así
  que `_en_vivo()` da `True` y el modelo sigue intentando grabar.
- La detección de "está en un show privado" (`_reconciliar_variantes`,
  `privado_ahora`) se basa en la **altura** que el master anuncia por
  variante (`r.alto`, atributo RESOLUTION del `EXT-X-STREAM-INF`). En un
  show `private` normal, el master anuncia una resolución de relleno
  (160p) y esa compuerta corta sola, bien.
- En `p2p` aparentemente el master **no** baja la resolución anunciada (o
  no lo hace de la misma manera) — no lo confirmamos leyendo el contenido
  real del master en ese momento, es una inferencia a partir del
  comportamiento observado. El resultado es que `deseadas` sigue
  incluyendo la variante, `_refrescar_variante` sigue pidiendo la media
  playlist real, y esa sí devuelve 403 (el contenido de un show p2p no es
  accesible por el mecanismo público).
- Con el fix de hoy (punto 3 arriba), cada 403 fuerza
  `self.proximo_chequeo = 0.0` → reconciliación en el próximo ciclo (~1-2s)
  en vez de esperar hasta 60s. Eso es correcto para el caso común (URI
  vieja tras salir de privado), pero en este caso el master nunca cambia
  lo suficiente como para que la media playlist deje de dar 403 — entonces
  el "forzar reconciliación" se convierte en un loop apretado: pide master
  nuevo, sigue sin servir, repite cada 1-2s, sin fin mientras dure el
  estado `p2p`.

**No es el mismo bug que el de hoy (el de `degradado` prematuro):**
confirmado que esta modelo **nunca se marcó `degradado`** durante el loop
(el fix de hoy funciona como se esperaba ahí) — el problema ahora es sólo
que no hay ningún backoff/límite para este caso puntual, y le pega al
`/api/front/.../cam` y al master del sitio sin pausa mientras dure.

**Impacto:** no bloquea nada (no cuelga el proceso, no afecta a otras
modelos, no impide sacarla de `grabar.txt` — al remover `__abrill__` cerró
normal, sin segmentos). Es ruido en el log + pedidos innecesarios al sitio
mientras la modelo está en p2p. Riesgo a mediano plazo: muchos pedidos
seguidos a la misma IP contra el sitio podrían llamar la atención de algún
rate-limit del lado del CDN.

**Dónde mirar para la solución (sin implementarla):**
`nucleo\scripts\grabador_directo.py`, `_refrescar_variante` (el bloque
`if status != 200:` agregado hoy) y `_reconciliar_variantes` /
`privado_ahora`. Alternativas a evaluar:

- Tratar `p2p` con el mismo criterio de "privado" que `private`/
  `ticketShow` en vez de como "en vivo grabable" (cambiar `ESTADOS_VIVOS`
  rompe el comportamiento ya portado de `supervisor.py` — pensarlo dos
  veces).
- Backoff incremental específico para el caso "status≠200 pero el master
  sigue igual tras reconciliar" (ej.: solo forzar reconciliación inmediata
  la primera vez; si el reintento también falla, volver al `CHEQUEO_ESTADO`
  normal de 60s en vez de seguir forzando cada ciclo).
- Revisar si el master en p2p trae algún atributo distinto (no solo
  altura) que permita detectarlo sin esperar al 403 de la media playlist.

## Corrección aplicada (2026-10-22, pasada siguiente)

Se implementó la segunda alternativa: backoff de "forzar una sola vez por
racha de fallos". Cambios en `grabador_directo.py`:

- `_ModeloDirecto.__init__`: nuevo dict `self._forzado_por_variante`
  (variante -> bool).
- `_refrescar_variante`, bloque `if status != 200:`: solo pone
  `self.proximo_chequeo = 0.0` (fuerza reconciliación ya) la **primera**
  vez de la racha; si la racha sigue, deja el ritmo normal de
  `CHEQUEO_ESTADO` (60s) y loguea un mensaje distinto que lo dice
  explícito, en vez de repetir el mismo warning sin fin.
- En el `200` real se limpia el flag (`self._forzado_por_variante[variante]
  = False`), y `_cerrar_variante` lo saca del dict al cerrar la sesión
  (para que una sesión nueva de la misma variante no herede el estado de
  la anterior).

**Verificación:** no se pudo reproducir el caso `p2p` en esta vuelta
(`__abrill__` ya no estaba en `p2p` al reiniciar — pasó a `idle`), así que
se armó una prueba dirigida (`test_backoff_403.py`, mismo patrón que
`--una-vez`: importa el módulo real, construye un `_ModeloDirecto` con un
`_GrabadorFalso`, parchea `mouflon.pedir` para devolver 403 siempre) que
confirma:

- 1er 403 de la racha → fuerza reconciliación (`proximo_chequeo = 0.0`),
  marca `_forzado_por_variante[variante] = True`.
- 2do, 3er 403 de la **misma** racha → NO vuelve a forzar (se queda en el
  valor que tenía `proximo_chequeo`), loguea el mensaje de "dejo el ritmo
  normal" en vez de repetir el de "pido un master nuevo".

Además, en la corrida real de esta misma prueba, `melody_rousse__` pasó
sola por la rama real (un 404 real → forzó reconciliación → el chequeo de
estado detectó `idle` → cerró la sesión → unió en 1.2s) — confirma que el
camino normal (un solo fallo, se resuelve) sigue intacto. No se consiguió
ver en esta corrida el caso de racha repetida con tráfico real (ninguna
modelo volvió a entrar en `p2p` ni tuvo una racha de 403 sostenida), así
que la cobertura en vivo del caso "no backear" queda pendiente para la
próxima vez que una modelo entre en `p2p`.
