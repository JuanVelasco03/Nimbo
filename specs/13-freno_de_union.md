# SPEC 13 — Resucitar el freno de la unión

> **Estado:** Aprobado
> **Depende de:** SPEC 03 (camino directo), SPEC 12 (rutas de disco)
> **Fecha:** 2026-10-29
> **Objetivo:** Que `_Freno` vuelva a actuar cuando la unión corre como subproceso `--unir`, leyendo de `estado\directo.json` + `estado\detector.json` si hay grabaciones en curso, para que unir deje de congelar al grabador.

---

## Por qué

`_Freno` (`nucleo\scripts\detectar_m3u8.py:1178`) existe desde que se midió el
daño: *"unir 2021 MB tardó 40s y en esos mismos 40s las otras cuatro modelos
perdieron entre 12 y 30 segundos cada una"*. Está escrito, probado y conectado
en los dos lugares que importan — `_alimentar()` y la copia al escenario de
`_preparar_escenario()`.

Pero no se activa nunca. `hay_grabaciones` es un global del módulo
(`:1175 = None`) que se rellena en un solo lugar: `Detector.__init__`
(`:1962-1963`), dentro del addon de mitmproxy. El camino directo une como
**subproceso** (`python detectar_m3u8.py --unir <ref>`, ver
`grabador_directo.py:319`), donde ese global queda en `None`, `frenar` queda
en `False` y `paso()` nunca duerme. La unión lee a toda velocidad del mismo
disco en el que ocho modelos están escribiendo segmentos.

El agujero tiene un segundo tramo. Aun dentro del addon, `_grabando_alguna()`
(`:1975`) solo recorre `self.streams`, que son los streams **del detector**.
Con toda la lista en `@directo=1` el detector no graba nada, así que también
ahí el freno ve cero.

Medido en la instalación real el 2026-10-29, con 8 modelos a `@calidad=max`
sobre un HDD SMR:

| Qué | Dato |
|---|---|
| Uniones que agotaron el tope de 1200s | 2 (sesiones de 5,85 GB y 7,13 GB) |
| Huecos de log del camino directo durante esas ventanas | 8, de 112s a 431s |
| Video perdido en el día | 4.889s (81,5 min) en 26 grabaciones |
| Perdido en sesiones que solaparon con una unión grande | 430s, 430s, 454s, 458s, 856s |
| Perdido en sesiones sin unión grande encima | 8s a 57s |

El hueco de 431s a las 17:55 y el `1 corte(s) que no llegaron a bajarse
(430 s)` de dos sesiones distintas son el mismo evento. Mientras el proceso
estaba congelado, `estado\directo.json` quedó viejo, `estado_captura.fusionar()`
marcó todas las filas como `sin_dato` y la grilla se pintó entera de "SIN
DATO" en naranja. La interfaz decía la verdad.

SPEC 12 dejó `_Freno` explícitamente fuera de scope. Este spec es ese pendiente.

## Scope

**In:**

- `DIR_ESTADO` en `detectar_m3u8.py`: `NIMBO_ESTADO` si está, y si no
  `<BASE>\..\estado`.
- Función nueva `_grabaciones_por_estado()` en `detectar_m3u8.py`, que lee
  `directo.json` y `detector.json` y dice si alguien está grabando ahora.
- `main()` conecta esa función a `hay_grabaciones` al entrar en la rama
  `--unir` / `--recuperar`.
- El addon pasa a `self._grabando_alguna() or _grabaciones_por_estado()`, para
  que el freno vea también lo que graba el camino directo.
- Freno pegajoso: una vez enganchado dentro de una unión, no se suelta por un
  JSON vencido — solo por un JSON fresco que diga que nadie graba.
- Una línea de log en cada transición de `frenar`, con la fase (`copia` o
  `remux`).
- `ESPERA_UNION = 3600` como constante nombrada en `grabador_directo.py`, en
  lugar del `timeout=1200` literal de `:333`.
- Párrafo nuevo en `CLAUDE.md`.

**Fuera de scope (para otro spec):**

- Cambiar `RITMO_UNION` (25 MB/s). Es el valor con el que se midió el freno
  originalmente; recalibrarlo antes de verlo funcionar es adivinar.
- Hacer `RITMO_UNION` configurable desde `config.json` o por variable de
  entorno.
- Tocar `MAX_REPARACIONES`, `MARGEN_DISCO` o `MARGEN_ESCENARIO`.
- Subir `ESPERA_CIERRE_DIRECTO` (`motor.py:77`) ni cambiar nada de la parada.
- Qué hacer con una sesión cuyo `--unir` agota `ESPERA_UNION`. Hoy queda sin
  `unido.json` y `recuperar_pendientes()` la puede retomar; eso no cambia.
- Reducir el techo de pérdida del disco (las de 8s a 57s por sesión). Eso es
  hardware, cantidad de modelos o `@calidad`, no freno.
- Mover `estado\` a otro disco. SPEC 12 ya lo dejó fuera.
- Mejorar `_resumen_union` (`grabador_directo.py:286`), que con `tope=3` se
  come las líneas de escenario cuando la unión loguea más de tres cosas
  interesantes.

## Modelo de datos

Este spec no introduce estructuras nuevas. **Lee** dos archivos que ya
existen, con el schema que ya tienen (`grabador_directo.py:fila_estado` y
`puente.py`, idénticos a propósito — ver `nimbo\estado_captura.py`):

```python
# estado\directo.json  /  estado\detector.json
{
  "generado": 1793316636.07,          # latido: se reescribe cada 2s SIEMPRE
  "streams": [
    {"modelo": "...", "grabando": True, "ultimo_segmento": 1793316634.2, ...},
  ],
}
```

De cada archivo se usan exactamente tres campos: `generado`, y por fila
`grabando` y `ultimo_segmento`.

Constantes nuevas:

```python
# nucleo\scripts\detectar_m3u8.py
DIR_ESTADO = os.environ.get("NIMBO_ESTADO") or os.path.join(BASE, "..", "estado")
FRESCURA_ESTADO = 15.0    # copiado de nimbo\estado_captura.py:FRESCURA_S
EDAD_SEGMENTO = 30.0      # mismo numero que _grabando_alguna() ya usa

# nucleo\scripts\grabador_directo.py
ESPERA_UNION = 3600       # era el timeout=1200 literal de _unir_en_fondo
```

`FRESCURA_ESTADO` es una **duplicación deliberada** de
`estado_captura.FRESCURA_S`, por el mismo motivo que `nimbo\mp4.py` y
`nimbo\vivo.py` portan constantes del detector en vez de importarlo: el núcleo
no importa nada de `nimbo\`. Si se cambia una, hay que cambiar la otra a mano.

Sobre `<BASE>\..\estado`: `BASE` es `<raíz>\nucleo` cuando `--unir` se corre a
mano (`_raiz_proyecto()`, con `scripts\` en `SUBCARPETAS_SCRIPT`) y
`<raíz>\datos` cuando lo lanza Nimbo (`M3U8_BASE`, que `motor.py` fija). Los
dos tienen el mismo padre, así que el fallback cae en `<raíz>\estado` en ambos
casos, sin clavar ninguna letra de unidad. Si igual no encuentra los JSON, no
frena: exactamente el comportamiento de hoy.

## Plan de implementación

1. **`nucleo\scripts\detectar_m3u8.py`, junto a `_Freno` (~`:1171`)** —
   agregar `DIR_ESTADO`, `FRESCURA_ESTADO`, `EDAD_SEGMENTO`, un lector de JSON
   que se trague `OSError`/`ValueError` y devuelva `None` (mismo patrón que
   `_leer_json` de Nimbo), y `_grabaciones_por_estado()`: recorre
   `directo.json` y `detector.json`, saltea el que tenga `generado` más viejo
   que `FRESCURA_ESTADO`, y devuelve `True` al primer stream con `grabando` y
   `ultimo_segmento` a menos de `EDAD_SEGMENTO`. Todavía no se conecta a nada.
   Probar a mano, con Nimbo grabando:
   `python -c "import detectar_m3u8 as d; print(d._grabaciones_por_estado())"`
   desde `nucleo\scripts\` — tiene que dar `True`. **Ojo:** eso construye un
   `Detector` (es el `else: addons = [Detector()]` del final del módulo), así
   que se corre con el motor **parado** para el caso `False`, y para el caso
   `True` se verifica con el paso 3 en vez de con este import.
2. **`detectar_m3u8.py:_Freno`** — `__init__` recibe una `etiqueta` opcional
   (`"copia"` / `"remux"`) y guarda `self.frenado_alguna_vez`. En `paso()`,
   dentro del bloque que ya corre cada 2s: si la consulta da `True`, frenar; si
   da `False`, soltar; y si el JSON no dice nada (vencido o ausente)
   **mantener** el estado anterior. Loguear una línea `info` solo cuando
   `frenar` cambia de valor. Los dos usos existentes pasan su etiqueta:
   `_preparar_escenario():1329` → `"copia"`, `_alimentar()` → `"remux"`.
3. **`detectar_m3u8.py:main():2917`** — al entrar en la rama
   `if "--unir" in argv or "--recuperar" in argv`, `global hay_grabaciones` y
   `hay_grabaciones = _grabaciones_por_estado`. Un comentario al lado explica
   que el camino CLI es el que usa el directo y que antes quedaba sin freno.
   Probar: con el motor grabando,
   `python nucleo\scripts\detectar_m3u8.py --unir --forzar "<modelo>_<id>/<variante>_<ts>"`
   y ver la línea de freno en la consola.
4. **`detectar_m3u8.py:1962-1963`** — el addon pasa a
   `hay_grabaciones = lambda: self._grabando_alguna() or _grabaciones_por_estado()`.
   El chequeo en memoria queda primero porque es gratis y es la verdad para
   los streams propios; el lector de estado solo se consulta si ese dio
   `False`.
5. **`nucleo\scripts\grabador_directo.py`** — `ESPERA_UNION = 3600` junto a las
   otras constantes de módulo, usarla en el `timeout=` de `:333`, y el mensaje
   de `:364` deja de decir "20 min" fijo y nombra la constante. Comentario con
   la cuenta: 7 GB a `RITMO_UNION` por `1 + MAX_REPARACIONES` pasadas ≈ 39 min.
6. **`CLAUDE.md`** — en "Los dos puentes", donde ya se explica que el directo
   invoca `--unir` como subproceso, sumar que ese subproceso lee
   `estado\directo.json` + `estado\detector.json` para frenar, por qué
   `hay_grabaciones` no alcanzaba, y que durante la parada el freno se suelta
   solo porque el directo cierra las sesiones antes de drenar `cola_union`.

## Criterios de aceptación

- [ ] Con el motor grabando al menos una modelo,
      `python nucleo\scripts\detectar_m3u8.py --unir --forzar "<ref exacta>"`
      escribe una línea de freno con fase `copia` y otra con fase `remux`, y
      el `.mp4` final queda igual que antes.
- [ ] Con el motor parado, esa misma unión no escribe ninguna línea de freno y
      tarda lo mismo que antes del spec.
- [ ] Con el motor arrancado y todas las modelos offline, la unión no frena.
- [ ] Una unión lanzada por el camino directo mientras otras modelos graban
      deja al menos una línea de freno en `estado\eventos_directo.jsonl`.
- [ ] Durante esa unión, `estado\eventos_directo.jsonl` no tiene ningún hueco
      mayor a 60s entre líneas consecutivas.
- [ ] Durante esa unión, `estado\directo.json` nunca queda con `generado` a
      más de 15s de `time.time()`.
- [ ] Durante esa unión, ninguna tarjeta de la grilla "Grabando" pasa a "SIN
      DATO".
- [ ] Una sesión de ~6 GB o más, unida con el motor grabando, termina sin el
      error de tope agotado.
- [ ] Con `@directo=1` en toda la lista (el detector sin streams propios),
      `hay_grabaciones()` dentro del detector devuelve `True` mientras el
      camino directo graba.
- [ ] `--unir` corrido a mano desde una consola sin `NIMBO_ESTADO` en el
      entorno, con Nimbo grabando, frena igual.
- [ ] `--unir` corrido a mano con `NIMBO_ESTADO` apuntando a una carpeta sin
      JSON no frena y no tira ningún error.
- [ ] Tras una noche completa, `python pruebas\huecos.py` no muestra ninguna
      sesión con más de 60s de corte atribuible a una ventana de unión
      (hoy: 430s, 430s, 454s, 458s, 856s).
- [ ] Una unión que de verdad se cuelgue sigue cortándose, ahora a los 3600s,
      con su línea de error nombrando la constante.

## Decisiones

- **Sí:** el freno consulta el estado en disco, no una variable de entorno. Una
  unión dura minutos y en ese rato las modelos arrancan y paran; una variable
  fijada al lanzar el hijo es una foto que miente a los dos minutos. `_Freno`
  ya repregunta cada 2s (`:1198`) justo para esto.
- **Sí:** el criterio es `grabando` **y** `ultimo_segmento` a menos de 30s,
  sobre un JSON de menos de 15s. Replica `_grabando_alguna()` palabra por
  palabra, así el freno se comporta igual en los dos caminos. `grabando` solo
  significa "hay una sesión abierta": una modelo con 404 en bucle mantendría
  el freno puesto hasta que salte `@inactividad=120`.
- **Sí:** freno pegajoso ante un JSON vencido. Si el grabador se congela, su
  latido envejece — y con el criterio literal el freno se soltaría justo en el
  momento en que más falta hace, acelerando la unión que causó el congelamiento.
  Mantener el último estado conocido corta ese lazo. Para soltarse hace falta
  un JSON **fresco** que diga que nadie graba.
- **Sí:** el addon suma el lector de estado a su chequeo en memoria, en vez de
  reemplazarlo. El chequeo en memoria es gratis y es la verdad para los
  streams del detector; hacerlo depender de `detector.json` (que escribe
  `puente.py`, un addon aparte) sería pedirle al detector que lea en disco algo
  que ya tiene en RAM.
- **No:** tocar `RITMO_UNION`. 25 MB/s es el número con el que se midió el
  freno y el que sostiene el comentario de su docstring. Si con el freno
  andando sigue habiendo pérdida, `pruebas\huecos.py` da el dato para
  recalibrarlo en su propio spec.
- **Sí:** `ESPERA_UNION = 3600` fijo, no escalonado por tamaño. Un número con
  un comentario que explica de dónde sale se audita de un vistazo; una fórmula
  con dos coeficientes es un número más que puede quedar mal calibrado y que
  nadie recuerda por qué es así.
- **Sí:** fallback a `<BASE>\..\estado` cuando no hay `NIMBO_ESTADO`. Un
  `--unir --forzar` a mano para rehacer una grabación es exactamente el caso
  en que más daño hace ir a fondo, y la ruta sale de `BASE`, que ya se calcula
  sin clavar unidades.
- **Sí:** sin los JSON, no frena. Es el contrato que ya documenta
  `:1172-1175` ("por la CLI se queda en None y la unión va a toda velocidad,
  que es lo que se quiere cuando no hay nada grabando") y sigue siendo cierto:
  sin Nimbo corriendo no hay nada que proteger.
- **Sí:** una línea de log por transición, con la fase. El freno anterior
  fracasó en silencio durante meses; sin una señal en el log no hay forma de
  saber si esta vez engancha. Dos fases (`copia` y `remux`) crean dos `_Freno`
  por unión, y sin la etiqueta las líneas serían indistinguibles.
- **No:** medidor nuevo en `pruebas\`. `pruebas\huecos.py` ya cuenta números de
  secuencia faltantes por sesión y los agrupa por concurrencia, que es
  exactamente la medición que decide si este spec funcionó.
- **No:** tocar la parada. Durante una parada el directo cierra las sesiones
  **antes** de drenar `cola_union`, así que `directo.json` queda con
  `streams: []` y el freno se suelta solo: las uniones de la cola corren a
  fondo, como hoy. Verificado en el JSON de una parada real
  (`{"parando": true, "uniendo": 4, "streams": []}`).

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El grabador se congela, su latido envejece y el freno se suelta justo ahí, empeorando el cuadro | Freno pegajoso: con JSON vencido se mantiene el último estado; solo un JSON fresco que diga "nadie graba" lo suelta |
| `ESPERA_UNION = 3600` es mayor que `ESPERA_CIERRE_DIRECTO = 1260` (`motor.py:77`), así que una unión larga podría exceder lo que el motor espera en la parada | Durante la parada no hay nada grabando y el freno se suelta solo, con lo que las uniones corren al caudal de hoy; subir `ESPERA_CIERRE_DIRECTO` queda fuera de scope y se evalúa con datos |
| 25 MB/s frenado sigue siendo demasiado para este HDD con 8 modelos escribiendo | `pruebas\huecos.py` lo mide; recalibrar `RITMO_UNION` es otro spec, y la constante ya está aislada en un solo lugar |
| `FRESCURA_ESTADO` duplica `estado_captura.FRESCURA_S` y las dos se desincronizan | Comentario en los dos archivos citando al otro, igual que ya hacen `nimbo\mp4.py` y `nimbo\vivo.py` con las constantes del detector |
| El `--unir` que importa el módulo construye un `Detector` entero y el paso 1 de prueba a mano confunde eso con un fallo del freno | El paso 1 lo dice explícito y la verificación real del caso `True` se hace en el paso 3, por CLI, no por import |
| Las líneas de freno no aparecen en `estado\eventos_directo.jsonl` porque `_resumen_union` corta a 3 líneas (`grabador_directo.py:286`) | Las líneas de freno van al stderr del hijo y quedan en el log de todas formas; mejorar `_resumen_union` está fuera de scope y anotado como tal |
| Una unión frenada tarda más y la cola `cola_union` se acumula en una noche con muchos cierres | La cola ya es serial por diseño y el freno se suelta en cuanto nadie graba; si la cola crece igual, queda a la vista en `uniendo` de `directo.json` |

## Lo que **no** está en este spec

- Cambiar `RITMO_UNION`, hacerlo configurable, o tocar `MAX_REPARACIONES` /
  `MARGEN_DISCO` / `MARGEN_ESCENARIO`.
- Subir `ESPERA_CIERRE_DIRECTO` o cambiar algo de la secuencia de parada.
- Rescatar automáticamente una sesión cuyo `--unir` agote `ESPERA_UNION`.
- Bajar el techo de pérdida del disco (los cortes de 8s a 57s por sesión).
- Mover `estado\` a otro disco.
- Arreglar `_resumen_union` para que no se coma las líneas de escenario.
- Medidores nuevos en `pruebas\`.

Cada uno de estos, si se hace, va en su propio spec.
