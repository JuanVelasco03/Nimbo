# SPEC 23 — Uniones pendientes: verlas, protegerlas y reunirlas a mano

> **Estado:** Implementado
> **Depende de:** SPEC 14 (borrado definitivo de segmentos, la cosecha), SPEC 16 (avisos
> de salud), SPEC 19 (turno único de unión), SPEC 20 (progreso de la unión), SPEC 22
> (unión con audio acompañante en chaturbate)
> **Fecha:** 2026-11-15
> **Objetivo:** Que ninguna sesión con segmentos sin unir pase desapercibida ni se borre:
> Nimbo las detecta, las muestra en Grabaciones con aviso de salud y deja reunirlas con
> un botón.

---

## Por qué

### A — "se quedan segmentos sin unir"

El usuario encuentra carpetas en `streams\` sin su `.mp4` y no sabe qué pasó. Los logs
de la instalación real (`estado\eventos_*.jsonl`, desde el 2026-11-01) muestran:

| Fecha | Casos | Qué fue |
|---|---|---|
| 2026-11-08 | 13 | `NO se unio -- ya tenia un unido.json de antes y --unir la salteo`: `recuperar_pendientes()` unía sesiones **vivas** del directo/chaturbate y les dejaba un marcador prematuro; el `--unir` del cierre las salteaba y todo lo grabado después quedaba suelto |
| 2026-11-08 | 1 | `--unir termino sin error pero no generó unido.json` |
| 2026-10-26 | 1 | ffmpeg parado al 84 % por un segmento faltante (lo resolvió el reintento interno) |

El primero lo cerró SPEC 20 (commit `98bcaef`, 2026-11-09: `_sigue_escribiendose` en
`recuperar_pendientes`). Desde entonces no hay errores de unión en los logs y el disco
está sano. **Pero los huecos que dejaron esos casos invisibles siguen abiertos.**

### B — Lo que el código no cubre hoy

1. **Una unión fallida solo se loguea.** Timeout `ESPERA_UNION`, salida 0 sin
   `unido.json`, `OSError`, excepción en el worker
   (`grabador_directo.py:1241-1253`, `grabador_chaturbate.py:1024-1035`) y el
   `_finalizar` del detector (`detectar_m3u8.py:3343-3387`) ignoran el resultado. Nada
   reintenta mientras el proceso vive, nada queda escrito en disco y la interfaz no
   muestra nada. El único reintento es `recuperar_pendientes()`, que corre **solo** al
   construirse un `Detector`.
2. **Marcador viejo + segmentos posteriores = pérdida silenciosa.** Una sesión con
   `unido.json` y segmentos más nuevos que él es invisible para todo chequeo de
   pendientes (`--unir` la saltea como "ya unida"), y `cosecha_streams.barrer()` la
   borra en cuanto pasa `GRACIA_MTIME_S` (300 s), porque tiene marcador. Esos segmentos
   eran la única copia.
3. **`--unir` sale siempre con 0** (`detectar_m3u8.py:main`), así que el éxito solo se
   puede juzgar por el mtime del `unido.json`, igual que ya hace el directo.
4. **`--forzar` no pisa el `.mp4`:** `unir_sesion` elige `base_2.mp4`, `_3`, …
   (`detectar_m3u8.py:1780-1784`). Una re-unión forzada deja dos archivos: el viejo
   incompleto y el nuevo.

---

## Alcance

**Adentro:**

- Módulo nuevo `nimbo\union_pendiente.py` que detecta las sesiones pendientes y une una
  a pedido, lanzando `detectar_m3u8.py --unir` como **subproceso** (nunca import, ver
  CLAUDE.md "Los dos puentes").
- Dos tipos de pendiente:
  - `sin_unir`: carpeta con segmentos y sin `unido.json`.
  - `marcador_viejo`: el segmento más nuevo tiene un mtime mayor que el de `unido.json`
    + `MARGEN_MARCADOR_S` (60 s).
- La compuerta nueva en `cosecha_streams._borrables()`: nunca borra una sesión
  `marcador_viejo`.
- La re-unión completa de una `marcador_viejo` con `--forzar`. El `.mp4` viejo va a la
  papelera y el nuevo queda con el nombre original.
- `union_fallida.json` en la carpeta de la sesión, con los intentos y el último motivo
  de un intento manual fallido.
- `app.estado["pendientes"]`, recalculado por `servidor.py:_bucle_cosecha`, más
  `POST /api/streams/unir` y la clave `pendientes` en `GET /api/streams`.
- La alerta de salud `uniones_pendientes`.
- La sección "Sin unir" en la pestaña Grabaciones, con un botón "Unir" por fila y
  "Unir todas".
- Actualizar `CLAUDE.md`: tabla "Quién escribe qué" y la compuerta nueva de la cosecha.

**Fuera de alcance (para specs futuros):**

- Reintento automático, backoff o "rendirse tras N intentos". Lo reintenta el usuario.
- Tocar `grabador_directo.py`, `grabador_chaturbate.py`, el detector o `main()` de
  `detectar_m3u8.py`. Eso incluye hacer que `--unir` devuelva un código distinto de 0.
- El fallback `.raw.mp4` (sin ffmpeg o con el remux fallado): tiene marcador y no se
  considera pendiente.
- Una unión degradada o un rescate que saltee segmentos rotos más allá de lo que ya
  hace `MAX_REPARACIONES`.
- Unir solo lo posterior al marcador como un `.mp4` "parte 2".

---

## Modelo de datos

### `union_fallida.json` (por carpeta de sesión)

```json
{"intentos": 2, "ultimo": 1794190000.5, "motivo": "últimas líneas de stderr/stdout del --unir, <= 400 caracteres"}
```

- Lo escribe solo Nimbo (`union_pendiente.py`), atómico (`.part` + `os.replace`), al
  fallar un intento manual. `intentos` suma 1 sobre el valor anterior.
- Se borra cuando un intento sale bien.
- Vive en la misma carpeta que `unido.json` (la de video; con audio acompañante, solo
  en la de video). Cuando la cosecha borra la carpeta, se va con ella.

### Ítem de `app.estado["pendientes"]`

```python
{
    "sesion": "kaithra_225310287/960p_h264_20261108-210332",  # ref exacto stream/sesion
    "tipo": "sin_unir",          # o "marcador_viejo"
    "bytes": 48_000_000,         # suma de segmentos (video + audio acompañante)
    "archivos": 371,
    "ultimo_mtime": 1794189994.0,
    "intentos": 0,               # de union_fallida.json, 0 si no existe
    "motivo": "",                # de union_fallida.json
}
```

Lista ordenada por `ultimo_mtime`, la más vieja primero.

### Constantes nuevas (`union_pendiente.py`)

```python
MARCADOR = "unido.json"           # porta detectar_m3u8.MARCADOR (no importar el núcleo)
CARPETA_SEGS = "segmentos"        # idem
FALLIDA = "union_fallida.json"
GRACIA_PENDIENTE_S = 600.0        # el último segmento tiene que tener >10 min
MARGEN_MARCADOR_S = 60.0          # segmentos más nuevos que el marcador + esto
ESPERA_UNION = 3600               # mismo tope que grabador_directo.ESPERA_UNION
```

`cosecha_streams.py` usa el mismo `MARGEN_MARCADOR_S`, copiado o importado de
`union_pendiente`. Los dos son módulos de `nimbo\`, así que importar está permitido.

---

## Plan de implementación

Cada paso deja el sistema andando.

1. **`union_pendiente.listar(dir_streams, sesiones_vivas)`.** Recorre
   `<stream>\<sesion>\segmentos\` igual que `cosecha_streams.sesiones_unidas`, pero sin
   filtrar por marcador.
   - Excluye las que aparecen en `vivo.sesiones_vivas()` y las que tienen el último
     segmento con menos de `GRACIA_PENDIENTE_S`.
   - Clasifica en `sin_unir` o `marcador_viejo`.
   - Una `_audio` con hermana de video (portar `_hermana_video` de `detectar_m3u8.py`)
     no aparece sola. Si la `_audio` es la que tiene `marcador_viejo`, se reporta la
     hermana de video como `marcador_viejo`. Las `_audio` huérfanas quedan afuera.
   - Lee `intentos` y `motivo` de `union_fallida.json`.
   - Prueba a mano con `NIMBO_BASE` y carpetas armadas.
2. **Compuerta en `cosecha_streams._borrables()`.** Saltea las sesiones cuyo segmento
   más nuevo supera el mtime de `unido.json` + `MARGEN_MARCADOR_S`. Esto solo ya cierra
   la pérdida de datos, aunque todavía no exista la UI.
3. **`motor.entorno_union(a)`.** Factoriza el entorno que hoy arma
   `motor._arrancar_directo` para el `--unir` hijo: `NIMBO_SCRIPTS`, `NIMBO_ESTADO`,
   `M3U8_BASE` con `setdefault`, `M3U8_STREAMS`, `M3U8_GRABACIONES`, `M3U8_ESCENARIO` y,
   compilado, `NIMBO_PYTHON`. `_arrancar_directo` pasa a usarlo, con el mismo resultado
   byte a byte.
4. **`union_pendiente.unir(ajustes, ref, tipo)`.**
   - Corre `[a["python"], detectar_m3u8.py, "--unir", ref]` (más `"--forzar"` si
     `tipo == "marcador_viejo"`), con `cwd=a.scripts`, `env=entorno_union(a)`,
     `stdin=DEVNULL`, sin ventana y `timeout=ESPERA_UNION`.
   - El turno global lo toma el propio subproceso (`cerrojo_union`). Nimbo no lo
     pide.
   - Éxito = el mtime de `unido.json` cambió (antes y después de correr, mismo criterio
     que `grabador_directo._unir_en_fondo`).
   - En `marcador_viejo`, **antes** de correr, se lee `salida` del marcador viejo. Si
     sale bien y la `salida` nueva es distinta, el `.mp4` viejo va a la papelera
     (`grabaciones.py`, el helper de `SHFileOperationW`) y el nuevo se renombra al
     nombre viejo. Se reescribe el `salida` de los marcadores (también el de la
     `_audio`). Si el renombre falla, queda `_N`, se loguea un aviso y el nuevo nunca
     se borra.
   - Éxito: se borra `union_fallida.json`. Fallo (timeout, `OSError`, mtime sin
     cambio): se escribe `union_fallida.json` con el motivo, sacado de las últimas
     líneas de stderr + stdout (logging escribe en stderr).
   - Devuelve `(ok: bool, motivo: str)`.
5. **`servidor.py`.**
   - `_bucle_cosecha`: en cada `INTERVALO_LIMPIEZA` (y al arrancar) recalcula
     `app.estado["pendientes"]` vía `app.a_hilo(union_pendiente.listar, …)`, antes de
     `barrer`. Viaja como sección del delta de `/ws` igual que las demás.
   - `POST /api/streams/unir` con cuerpo `{"sesion": "<ref>"}` o `{"sesion": "todas"}`:
     `_lanzar_tarea(app, "unir_pendientes", fn)` y responde 202, o 409 si ya hay una
     corriendo. `fn` une en serie y recalcula `pendientes` al terminar cada una.
   - Un `ref` que no está en la lista actual se rechaza con 400. Nunca se lanza
     `--unir` con un filtro que no salió de `listar()`.
   - `GET /api/streams` suma `"pendientes"`.
   - `TOPE_TAREA["unir_pendientes"]`: alto, porque "todas" puede encadenar varias
     uniones de hasta `ESPERA_UNION` cada una.
6. **`salud.py`.** `_alerta_pendientes(estado)`, pura, sobre `estado["pendientes"]`:
   - `clave="uniones_pendientes"`, `tipo="union"`, `titulo="N grabaciones sin unir"`.
   - `nivel="aviso"`, o `"error"` si alguna tiene `intentos > 0`.
   - Las notificaciones de Windows salen solas por `notificaciones.py`.
7. **UI.**
   - `index.html`: un contenedor "Sin unir" debajo de `#union-banner`, oculto con
     `.oculta` si la lista viene vacía.
   - `vista_grabaciones.js`: reconciliación por clave (`Map` sesión→fila, sin
     `innerHTML` en el camino del delta). Cada fila muestra modelo, sesión, tipo ("sin
     unir" o "incompleta"), tamaño (`formato.js`), intentos y último motivo si falló, y
     un botón "Unir".
   - Un botón "Unir todas".
   - Mientras la tarea corre, los botones quedan deshabilitados. El progreso real se ve
     en el banner de SPEC 20, que ya existe.
   - Si hacen falta clases nuevas, `npm run build:css` y se versiona `app.css`.
8. **`CLAUDE.md`.**
   - Fila de `union_fallida.json` en "Quién escribe qué".
   - La compuerta nueva de la cosecha (tres compuertas pasan a ser cuatro).
   - Mención de `POST /api/streams/unir` y de que es el único lugar de `nimbo\` que
     lanza `--unir`.

---

## Criterios de aceptación

- [ ] Una sesión sin `unido.json`, que no está viva y con el último segmento de hace más
      de 10 min aparece en "Sin unir" y en `GET /api/streams`, y dispara la alerta
      `uniones_pendientes` en `nivel="aviso"`.
- [ ] Una sesión viva (en `vivo.sesiones_vivas()` o con un segmento de hace menos de
      10 min) nunca aparece.
- [ ] Una carpeta `_audio` con hermana de video no aparece como fila propia.
- [ ] Una sesión con segmentos más de 60 s posteriores a su `unido.json` aparece como
      "incompleta", y `cosecha_streams.barrer()` **no** la borra aunque hayan pasado
      `dias_segmentos`.
- [ ] "Unir" sobre una `sin_unir` deja un `.mp4` en `dir_grabaciones` y el
      `unido.json`, y la fila desaparece sin recargar la pestaña.
- [ ] "Unir" sobre una `marcador_viejo` deja **un solo** `.mp4`, con el nombre
      original y la duración de todos los segmentos. El viejo queda en la papelera de
      Windows.
- [ ] Una unión que falla (por ejemplo, con `ffmpeg.exe` renombrado) deja
      `union_fallida.json` con `intentos: 1` y un `motivo` no vacío. La fila los
      muestra y la alerta pasa a `nivel="error"`. Un segundo fallo deja `intentos: 2`.
- [ ] Un intento exitoso después de uno fallido borra `union_fallida.json`.
- [ ] Chaturbate: "Unir" sobre una sesión con audio acompañante produce un solo `.mp4`
      con dos pistas, y las dos carpetas quedan con `unido.json`.
- [ ] Dos POST seguidos a `/api/streams/unir` no lanzan dos uniones (el segundo da
      409).
- [ ] Un `ref` inventado en el POST da 400 y no lanza ningún subproceso.
- [ ] `git diff` no muestra cambios en `nucleo\scripts\`.
- [ ] `motor._arrancar_directo` le pasa al directo el mismo entorno que antes del
      refactor.

---

## Decisiones

**Tomadas:**

- **Nada automático.** El usuario eligió primero "reintento automático con backoff y
  rendirse tras 3 intentos" y después lo cambió a "que lo reintente el usuario, nada en
  automático". Vale lo último. Una unión es lectura pesada del mismo disco en el que se
  graba (SPEC 13), y relanzarla sola contra una sesión que ya falló una vez es la forma
  de repetir el daño de noche.
- **La lanza Nimbo y no los grabadores.** El núcleo graba 24/7 y no se toca. Desde
  Nimbo se cubren de una los tres productores de uniones (detector, directo,
  chaturbate) y también las sesiones que dejó una corrida anterior.
- **Sidecar en la carpeta (`union_fallida.json`) y no un índice central.** Viaja con la
  sesión y desaparece con ella, sin limpieza aparte ni riesgo de que el índice y el
  disco digan cosas distintas.
- **Re-unión completa + papelera para `marcador_viejo`.** Queda un solo archivo por
  sesión, que es lo que asume la pestaña Grabaciones. Se descartó unir solo lo nuevo
  como "parte 2" porque deja dos archivos por grabación y un corte en el medio.
- **Éxito por el mtime de `unido.json`.** Es el mismo criterio que ya usa el directo, y
  evita tocar `main()` para que devuelva un código distinto de 0.
- **Solo refs que salieron de `listar()`.** `--unir` con un filtro sin `/` es substring
  y se puede llevar sesiones vivas de otras modelos (CLAUDE.md, "Los dos puentes"). El
  endpoint nunca acepta un filtro libre.

**Descartadas:**

- Reintento automático con backoff de 10 min / 1 h / 6 h (ver arriba).
- Corregir el código de salida de `--unir` en `detectar_m3u8.py`: es núcleo y el mtime
  alcanza.
- Que la cosecha deje de borrar cualquier sesión con marcador hasta revisarla a mano:
  es demasiado amplio, y la compuerta por mtime ya separa exactamente el caso dañino.

---

## Riesgos

- **"Unir" sobre una sesión que su grabador todavía tiene en cola** (esperando el
  turno, que puede tardar). La unión manual deja el marcador y, cuando le llega el
  turno al grabador, ve el marcador sin cambio de mtime y loguea un falso `NO se unio`.
  La mitigación es `GRACIA_PENDIENTE_S` (600 s). El caso queda documentado en el
  docstring de `union_pendiente.py`.
- **El renombre del `.mp4` nuevo puede fallar** si el viejo o el nuevo está abierto en
  el reproductor de Grabaciones. Queda `base_N.mp4`, se avisa y no se pierde nada.
- **Una unión manual larga ocupa el turno global.** Mientras tanto, las uniones de los
  grabadores esperan. Es el mismo comportamiento de SPEC 19 y el freno de SPEC 13 sigue
  aplicando dentro del subproceso.
- **`marcador_viejo` por mtime puede dar un falso positivo** si algo toca los segmentos
  después de unir (antivirus, copia manual). El costo es una re-unión innecesaria a
  pedido del usuario, sin pérdida de datos.
