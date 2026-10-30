# Prueba pendiente de SPEC 13 — unión frenada de una sesión grande

> **Qué queda por verificar:** el criterio 8 de `specs\13-freno_de_union.md`
> ("una sesión de ~6 GB o más, unida con el motor grabando, termina sin el
> error de tope agotado") y, de yapa, el criterio 13 sobre una noche entera.
>
> **Por qué no se verificó el 2026-10-29:** la prueba de punta a punta duró
> 40 min y la sesión frenada más grande fue de 392 MB. Las dos grandes de esa
> tanda (2,40 GB y 1,97 GB) se unieron durante la **parada**, donde el freno
> está suelto a propósito y no prueban nada.
>
> **Todo lo demás del spec ya está verificado** (11 de 13 criterios): ver
> "Lo que ya se midió" al final.

## Lo que esta prueba tiene que demostrar

Que una unión de 6 GB o más, **frenada** a `RITMO_UNION` (25 MB/s) mientras
otras modelos graban:

1. termina sola, sin agotar `ESPERA_UNION = 3600 s`;
2. no le roba disco a las que graban (cero huecos de log, cero `SIN DATO`);
3. produce el mismo `.mp4` que produciría sin freno.

La cuenta del spec: 6-7 GB frenados ≈ 39 min en el peor caso (la copia al
escenario más `1 + MAX_REPARACIONES` pasadas de remux). Eso tiene que entrar
en los 3600 s con margen.

## Lo que hace falta antes de empezar

- **Una sesión de ≥6 GB en una sola carpeta de `datos\streams\`.** Medido el
  2026-10-29 en la instalación real: una modelo en 1440p da ~3 GB/h y una en
  1080p ~3,8 GB/h. O sea **1,5 a 2 horas** de una modelo de bitrate alto, sin
  que rote de calidad (una rotación parte la sesión en dos carpetas y la
  cuenta arranca de cero).
- **Al menos 4 modelos más grabando** al mismo tiempo, que son las que el
  freno tiene que proteger.
- `%TEMP%` en un disco distinto al de `streams\` (hoy: `streams\` en `F:`,
  `%TEMP%` en `C:`) para que se ejercite también la fase `copia`. Si están en
  el mismo disco, la unión salta esa fase y solo se ve la línea `remux`.

## Pasos

### 1. Dejar grabando

Abrir Nimbo, Arrancar, y dejar ≥5 modelos grabando 2 horas. Conviene elegir
una de 1080p o más como "la grande".

Para seguir cómo crece cada sesión, sin tocar nada:

```bat
python nucleo\scripts\detectar_m3u8.py --listar
```

Y el tamaño en disco de la que va a ser la grande:

```powershell
# ajustar la carpeta a la sesión real
$s = "F:\Nimbo\datos\streams\<modelo>_<id>\<variante>_<ts>"
"{0:N2} GB" -f ((Get-ChildItem $s -Recurse -File | Measure-Object Length -Sum).Sum / 1GB)
```

**No seguir hasta que esa cuenta pase de 6 GB.**

### 2. Dejar un muestreador corriendo (opcional pero recomendado)

Es lo que después contesta los criterios de frescura y `SIN DATO` sin tener
que mirar la ventana. Guardar como `pruebas\muestrear_13.py` y dejarlo correr
durante toda la unión:

```python
# -*- coding: utf-8 -*-
"""Cada 5s: edad del latido de los JSON de estado y filas en SIN DATO."""
import json, os, time
EST = r"F:\Nimbo\estado"
with open(r"F:\Nimbo\pruebas\muestras_13.csv", "w", buffering=1) as f:
    f.write("t,edad_directo,edad_detector,grabando,sin_dato\n")
    while True:
        ahora, e = time.time(), {}
        for n in ("directo.json", "detector.json"):
            try:
                with open(os.path.join(EST, n), encoding="utf-8") as g:
                    d = json.load(g)
                e[n] = (ahora - (d.get("generado") or 0), d.get("streams") or [])
            except Exception:
                e[n] = (-1.0, [])
        filas = e["directo.json"][1] + e["detector.json"][1]
        f.write("%.1f,%.1f,%.1f,%d,%d\n" % (
            ahora, e["directo.json"][0], e["detector.json"][0],
            sum(1 for x in filas if x.get("grabando")),
            sum(1 for x in filas if x.get("estado") == "sin_dato")))
        time.sleep(5)
```

Anotar la hora exacta a la que arranca la unión (paso 3) y a la que termina.

### 3. Disparar la unión sin cortar a las demás

En la pestaña **Modelos**, quitar de la lista **solo** la modelo grande. El
camino directo cierra esa sesión y encola la unión; las otras cuatro siguen
grabando. (Equivalente por API: `POST /api/lista/quitar` con
`{"nombre":"<modelo>"}`.)

Alternativa si se prefiere a mano, con la sesión ya cerrada — ese `--unir` es
exactamente lo que corre el hijo del directo:

```powershell
$env:M3U8_BASE        = "F:\Nimbo\datos"
$env:M3U8_STREAMS     = "F:\Nimbo\datos\streams"
$env:M3U8_GRABACIONES = "F:\Nimbo\datos\grabaciones"
Remove-Item Env:\NIMBO_ESTADO -ErrorAction SilentlyContinue   # probar el fallback
python nucleo\scripts\detectar_m3u8.py --unir "<modelo>_<id>/<variante>_<ts>"
```

El filtro va **completo, con la barra** (`Sesion.ref`): sin la barra es un
substring y se lleva puestas las sesiones vivas de las otras modelos.

### 4. Mirar el log mientras une

```powershell
Get-Content F:\Nimbo\estado\eventos_directo.jsonl -Wait -Tail 0 |
    Select-String "freno|UNIR|LISTO|ERROR"
```

Lo que tiene que aparecer:

- `[UNIR] freno copia: puesto (hay emisiones grabandose)` — si `%TEMP%` está
  en otro disco;
- `[UNIR] freno remux: puesto (hay emisiones grabandose)`;
- `[LISTO] ...<modelo>...mp4 (N MB)`.

Lo que **no** tiene que aparecer:

- `--unir no termino en ESPERA_UNION (3600 s)`;
- ningún `[DISCO]` ni `[ERROR]`.

> Ojo: si la unión la lanza el camino directo, en `eventos_directo.jsonl`
> queda **solo** la línea `remux`: `_resumen_union` recorta a las últimas 3
> líneas del hijo y se come la de `copia`. Es conocido y está fuera de scope
> del spec. Corriendo el `--unir` a mano (paso 3, alternativa) se ven las dos.

### 5. Medir cuando termine

```bat
rem perdida real por sesion, agrupada por concurrencia
python pruebas\huecos.py
```

Y sobre el CSV del paso 2:

```powershell
$m = Import-Csv F:\Nimbo\pruebas\muestras_13.csv
"edad maxima directo.json : {0} s" -f ($m.edad_directo  | Measure-Object -Maximum).Maximum
"edad maxima detector.json: {0} s" -f ($m.edad_detector | Measure-Object -Maximum).Maximum
"muestras con SIN DATO    : {0}"   -f ($m | Where-Object { [int]$_.sin_dato -gt 0 }).Count
```

Huecos del log durante la ventana de la unión (reemplazar las dos horas por
las del paso 2):

```powershell
$desde = [datetimeoffset]::Parse("2026-10-30T02:00:00").ToUnixTimeSeconds()
$hasta = [datetimeoffset]::Parse("2026-10-30T03:00:00").ToUnixTimeSeconds()
$t = Get-Content F:\Nimbo\estado\eventos_directo.jsonl |
     ForEach-Object { ($_ | ConvertFrom-Json).t } |
     Where-Object { $_ -ge $desde -and $_ -le $hasta } | Sort-Object
$max = 0; for ($i = 1; $i -lt $t.Count; $i++) {
    $d = $t[$i] - $t[$i-1]; if ($d -gt $max) { $max = $d } }
"lineas: {0} | hueco maximo: {1:N1} s" -f $t.Count, $max
```

## Criterios de aceptación de esta prueba

- [ ] La sesión unida pesaba **≥ 6 GB** (anotar el tamaño real).
- [ ] La unión terminó con `[LISTO]` y **sin** la línea de `ESPERA_UNION`.
- [ ] Apareció `freno remux: puesto` (y `freno copia: puesto`, si hubo copia
      al escenario).
- [ ] La unión tardó, grosso modo, `tamaño / 25 MB/s` por pasada — 6 GB de
      una sola pasada rondan los 4-5 min. Mucho menos que eso significa que
      el freno no engancha; mucho más, que ffmpeg reparó varias veces (sale
      en el log).
- [ ] Hueco máximo de `eventos_directo.jsonl` en esa ventana **< 60 s**.
- [ ] Edad máxima de `directo.json` **< 15 s**.
- [ ] Cero muestras con `SIN DATO`, y ninguna tarjeta naranja en la grilla
      "Grabando" mientras duró la unión.
- [ ] `huecos.py` no muestra ninguna sesión con pérdida atribuible a esa
      ventana (el 2026-10-29 dio **0,00 %** sobre 6710 secuencias con 5-6
      modelos y 3 uniones frenadas).

Si todo eso da bien, el spec queda completo: pasar
`specs\13-freno_de_union.md` a **Implementado** y commitear.

## Si falla

| Síntoma | Qué significa | Qué hacer |
|---|---|---|
| No aparece ninguna línea de freno | `hay_grabaciones` no quedó enganchado, o no se encontraron los JSON de estado | Revisar que `estado\directo.json` tenga el latido fresco; sin `NIMBO_ESTADO` la ruta sale de `<BASE>\..\estado`, que tiene que dar `F:\Nimbo\estado` |
| `--unir no termino en ESPERA_UNION (3600 s)` | 3600 s no alcanzan para este tamaño y este disco | La sesión queda sin `unido.json` y `recuperar_pendientes()` la retoma al arrancar de nuevo: no se pierde. Subir el tope o bajar `RITMO_UNION` es **otro spec** |
| Hay huecos > 60 s igual | 25 MB/s sigue siendo demasiado para este HDD con tantas modelos | Recalibrar `RITMO_UNION` con el dato de `huecos.py`, en su propio spec (SPEC 13 lo dejó explícitamente afuera) |
| Alguna tarjeta en `SIN DATO` | el grabador se congeló y su latido envejeció | Guardar `estado\eventos_directo.jsonl` y el CSV antes de reintentar: es el cuadro exacto que este spec vino a arreglar |

## Lo que ya se midió (2026-10-29, 19:50-20:40)

40 min con 5-7 modelos por el camino directo, 6,49 GB grabados en 8 `.mp4`,
tres uniones frenadas lanzadas por el directo (392, 217 y 315 MB):

| Criterio del spec | Resultado |
|---|---|
| Líneas `copia` + `remux` con el motor grabando | ✅ `20:01:57` y `20:02:05` |
| Con el motor parado no frena | ✅ 9 s contra 18 s frenada, mismo `.mp4` |
| JSON fresco sin nadie grabando no frena | ✅ en la parada, 705 MB a fondo |
| Unión del directo deja línea de freno | ✅ 3 de 3 |
| Huecos > 60 s en el log | ✅ 0 (máximo 7,2 s en 1361 líneas) |
| Edad de `directo.json` | ✅ máximo 2,9 s en 478 muestras |
| Tarjetas en `SIN DATO` | ✅ 0 |
| `hay_grabaciones()` con `@directo=1` en toda la lista | ✅ `True` con `self.streams` vacío |
| `--unir` a mano sin `NIMBO_ESTADO` | ✅ frena (fallback a `F:\Nimbo\estado`) |
| `NIMBO_ESTADO` a carpeta vacía | ✅ no frena y no falla |
| Tope `ESPERA_UNION` | ✅ `timeout=3600` y el error nombra la constante |
| **Sesión ≥ 6 GB frenada** | ❌ **esta prueba** |

Caudal frenado medido: 21-25 MB/s, o sea `RITMO_UNION`. Las cuatro uniones de
la misma sesión (frenada, sin freno, y con el motor parado) dieron el **mismo
SHA256**: el freno no cambia un byte del resultado.
