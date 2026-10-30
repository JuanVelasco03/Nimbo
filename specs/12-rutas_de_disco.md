# SPEC 12 — Elegir en qué disco van los segmentos, la unión y la grabación

> **Estado:** Implementado
> **Depende de:** SPEC 03 (camino directo), SPEC 02 (empaquetado)
> **Fecha:** 2026-10-28
> **Objetivo:** Poder elegir desde Ajustes tres carpetas independientes — segmentos temporales, escenario de unión y grabaciones finales — y que el motor de captura las respete sin perder el comportamiento de hoy cuando no se configura nada.

---

## Por qué

Grabar y unir son dos cargas de disco distintas que hoy comparten unidad por
accidente de instalación. Los segmentos crudos son escritura constante de
archivos chicos (un `.mp4` de una hora deja miles), la unión es lectura masiva
de esos mismos archivos (hasta 6 veces con `MAX_REPARACIONES`) y el `.mp4`
final es escritura secuencial grande. `detectar_m3u8.py:_preparar_escenario`
ya reconoce que mezclarlas cuesta — por eso copia a `%TEMP%` cuando eso cae en
otro disco — pero esa decisión la toma solo, con la variable `%TEMP%` del
sistema como única entrada.

Con un HDD grande para material y un SSD chico de sistema, lo que conviene es
decidirlo a mano: segmentos en el SSD (escritura chica y constante), unión en
el SSD, `.mp4` final en el HDD. Hoy no hay forma de pedir eso sin mover la
instalación entera.

## Scope

**In:**

- Cuatro claves nuevas en `config.json`: `dir_segmentos`, `dir_grabaciones`,
  `union_modo`, `dir_union`. Vacío / `"auto"` = exactamente el comportamiento
  de hoy.
- Tres variables de entorno nuevas que el núcleo entiende: `M3U8_STREAMS`,
  `M3U8_GRABACIONES`, `M3U8_ESCENARIO`.
- Sección "Carpetas y discos" en la pestaña Ajustes, con campo de texto,
  botón "Examinar" (diálogo nativo de Windows), espacio libre del disco
  elegido y validación con mensaje en línea.
- Los campos de segmentos y grabaciones quedan deshabilitados mientras el
  motor corre, con el motivo escrito al lado.
- Caída al defecto `<raiz>\datos\...` cuando la ruta configurada no existe o
  no se puede escribir, con fila roja en el banner de puesta a punto y aviso
  nivel `error` en el registro. `config.json` no se reescribe.
- El indicador de espacio libre del encabezado pasa a mostrar el disco de
  segmentos y el de grabaciones, deduplicados por letra de unidad.

**Fuera de scope (para otro spec):**

- Mover o copiar material ya grabado al cambiar una ruta. Lo viejo se queda
  donde está.
- Listar grabaciones de carpetas anteriores. Grabaciones mira una sola
  carpeta: la configurada hoy.
- Mover `datos\miniaturas\`, `detector.log`, `directo.log`, `estado\`,
  `config.json` o `nucleo\scripts\grabar.txt`. Son caché regenerable, logs
  chicos y operativos: siguen junto a la instalación.
- Rutas por modelo (una modelo a un disco, otra a otro) ni directivas nuevas
  en `grabar.txt`.
- Cuotas de tamaño por carpeta o borrado automático por disco lleno. La
  cosecha de `datos\streams\` sigue siendo la de hoy (`dias_segmentos`).
- Cambiar `MARGEN_DISCO` / `MARGEN_ESCENARIO` o el `_Freno`.
- Rutas UNC (`\\servidor\share`) y unidades de red: no se prohíben, pero no
  se prueban ni se soportan en este spec.

## Modelo de datos

Cuatro claves nuevas en `DEFECTOS` (`nimbo\ajustes.py`):

```python
DEFECTOS = {
    # ...lo de hoy...
    "dir_segmentos": "",     # "" = <raiz>\datos\streams
    "dir_grabaciones": "",   # "" = <raiz>\datos\grabaciones
    "union_modo": "auto",    # "auto" | "carpeta" | "apagado"
    "dir_union": "",         # solo se usa con union_modo == "carpeta"
}
```

Vacío siempre significa "lo de hoy": una instalación existente que nunca entre
a la sección nueva se comporta igual, byte por byte.

Resolución efectiva, calculada una vez y guardada en el objeto `Ajustes` (no
en una property, que se llama en cada tick de `_bucle_estado`):

```python
# ajustes.Ajustes, tras resolver_rutas()
self._rutas = {"segmentos": r"D:\nimbo\streams",
               "grabaciones": r"F:\videos",
               "union": r"C:\Users\...\Temp"}   # "" si apagado
self.rutas_caidas = [
    {"clave": "dir_grabaciones", "pedida": r"G:\videos",
     "usada": r"F:\Nimbo\datos\grabaciones",
     "motivo": "la unidad G: no responde"},
]
```

`Ajustes.streams` y `Ajustes.grabaciones` pasan a devolver `self._rutas[...]`
en vez de derivar de `DIR_DATOS`. `dir_datos`, `dir_miniaturas`,
`detector_log` y `supervisor_log` no cambian.

Variables de entorno que `motor.py` le pasa a sus hijos:

| Variable | Valor | Quién la lee |
|---|---|---|
| `M3U8_STREAMS` | ruta absoluta de segmentos | `detectar_m3u8.py`, `grabador_directo.py` |
| `M3U8_GRABACIONES` | ruta absoluta del `.mp4` final | `detectar_m3u8.py` |
| `M3U8_ESCENARIO` | `""`/`"auto"`, `"apagado"`, o ruta absoluta | `detectar_m3u8.py:_preparar_escenario` |

`M3U8_BASE` sigue existiendo y sigue valiendo `<raiz>\datos`: es de donde
salen `detector.log` y `directo.log`, y es el defecto de las dos primeras
cuando no vienen. Las nuevas son más específicas y ganan.

## Plan de implementación

1. **`nucleo\scripts\detectar_m3u8.py:165-166`** — `STREAMS_DIR` y
   `GRABACIONES_DIR` leen primero su variable propia:
   `os.environ.get("M3U8_STREAMS") or os.path.join(BASE, "streams")`, ídem
   grabaciones. Un comentario al lado explica que Nimbo las pasa desde
   `config.json` y que `M3U8_BASE` sigue siendo el defecto. Probar a mano:
   `set M3U8_STREAMS=D:\x` + `python detectar_m3u8.py --listar` y ver que
   busca ahí.
2. **`nucleo\scripts\detectar_m3u8.py:1274-1327`** — `_preparar_escenario`
   arranca mirando `M3U8_ESCENARIO`. `"apagado"` (o `"0"`) devuelve
   `(inits, segmentos, None)` sin tocar disco. Una ruta absoluta reemplaza a
   `tempfile.gettempdir()` como `tmp_base`, creándola si falta. Vacío o
   `"auto"` es lo de hoy. **La comprobación de "mismo disco" se conserva en
   los tres casos**: copiar dentro del mismo disco que la sesión es E/S pura
   al vacío, y cuando se saltea por eso se loguea a nivel `info` con la ruta
   pedida. Probar a mano: `--unir --forzar` de una sesión chica con
   `M3U8_ESCENARIO` apuntando a otro disco y ver la línea
   `"N archivos copiados a X"`.
3. **`nucleo\scripts\grabador_directo.py:88`** — mismo cambio que el paso 1
   para `STREAMS_DIR`. En `--una-vez --base` (`:1390-1401`) el `--base`
   explícito sigue mandando: además de fijar `BASE`, borra `M3U8_STREAMS` del
   entorno que hereda el `--unir` hijo, para que diagnóstico y producción no
   se pisen.
4. **`nimbo\rutas.py`** (nuevo, ~90 líneas) — tres funciones, todas síncronas
   (van al pool por `a_hilo`):
   - `validar(ruta) -> dict`: `{"ok":bool, "motivo":str, "ruta":str}`. Exige
     ruta absoluta con letra de unidad, intenta `os.makedirs(exist_ok=True)` y
     escribe/borra un `.nimbo_prueba` para confirmar permiso real.
   - `espacio(ruta) -> dict|None`: `{"unidad","libres","total"}` con
     `shutil.disk_usage`.
   - `elegir_carpeta(titulo, inicial) -> str`: `SHBrowseForFolderW` de
     `shell32` por `ctypes` (`BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE`),
     con `CoInitialize` en el hilo y `GetForegroundWindow()` como
     `hwndOwner` para que no aparezca detrás de la ventana de Edge. Devuelve
     `""` si el usuario cancela o si no es Windows.
5. **`nimbo\ajustes.py`** — las cuatro claves en `DEFECTOS`;
   `resolver_rutas()` que llena `self._rutas` y `self.rutas_caidas` usando
   `rutas.validar`; `streams`/`grabaciones` devuelven lo resuelto;
   `dir_union` nueva property (`""` = sin escenario). `cargar()` llama a
   `resolver_rutas()` después de `completar()`.
6. **`nimbo\motor.py`** — `arrancar()` llama `a.resolver_rutas()` antes de
   lanzar el primer hijo (un disco puede haberse conectado desde que abrió
   Nimbo) y emite un `_aviso(nivel="error")` por cada entrada de
   `rutas_caidas`. `_arrancar_detector` (`:452`) y `_arrancar_directo`
   (`:508`) agregan `env["M3U8_STREAMS"]`, `env["M3U8_GRABACIONES"]` y
   `env["M3U8_ESCENARIO"]` — asignación directa, no `setdefault`: a
   diferencia de `M3U8_BASE`, estas las decide la configuración de Nimbo y no
   hay ninguna prueba que las fije por afuera.
7. **`nimbo\preflight.py:104`** — una fila por cada entrada de
   `ajustes.rutas_caidas`, con la ruta pedida, la que se usó y el motivo. Sin
   `arreglo` (no hay un POST que reconecte un disco). Si la lista está vacía,
   no agrega ninguna fila: la sección nueva no ensucia el banner de una
   instalación por defecto.
8. **`nimbo\servidor.py`** — `GET /api/rutas` devuelve las cuatro claves, lo
   resuelto, el espacio libre de cada una y `caidas`. `POST /api/rutas`
   (`{clave, valor}`) valida con `rutas.validar`, rechaza con 400 y motivo si
   no pasa, rechaza con 409 si la clave es `dir_segmentos` o `dir_grabaciones`
   y el motor está vivo, guarda y vuelve a resolver. `POST /api/rutas/examinar`
   (`{clave}`) corre `rutas.elegir_carpeta` en el pool y devuelve la elegida
   sin guardarla. `_disco()` (`:238`) pasa a devolver una lista de
   `{etiqueta, unidad, libres, total}` — segmentos y grabaciones, deduplicadas
   por unidad.
9. **`nimbo\web\index.html:96-103`** — tercer bloque en `#vista-ajustes`:
   `<h3>Carpetas y discos</h3>` + `<div id="rutas">`, misma grilla que
   `#preferencias` y `#directivas`.
10. **`nimbo\web\js\vista_ajustes.js`** — `renderRutas(estado)` exportada,
    con el mismo patrón in-place que `renderDirectivas`: construye una vez,
    después solo escribe valores y saltea el control que tiene el foco. Tres
    filas (segmentos, grabaciones, escenario). La de escenario trae el
    desplegable de modo y muestra u oculta su campo de ruta con `.oculta`
    según el modo. Cada fila: texto + "Examinar" + espacio libre + mensaje de
    validación. `main.js:85` agrega `[renderRutas, ["motor", "disco"]]` para
    que el deshabilitado siga al estado del motor
    (`m.detector_vivo || m.supervisor_vivo || m.directo_vivo || m.parando`).
11. **`nimbo\web\js\vista_grabando.js:243-245`** — `renderBarraGlobal` recorre
    la lista de discos y arma `"espacio libre: F: 1,2 TB de 3,6 TB · C: 40 GB
    de 475 GB"`; con una sola entrada el texto queda igual que hoy.
12. **`CLAUDE.md`** — fila nueva en "Quién escribe qué" para las tres
    variables, y un párrafo en "Cuatro procesos" explicando que las rutas
    salen de `config.json` y que `M3U8_BASE` quedó como defecto.

## Criterios de aceptación

- [ ] Con `config.json` sin las claves nuevas, los segmentos siguen en
      `<raiz>\datos\streams`, el `.mp4` en `<raiz>\datos\grabaciones` y la
      unión usa `%TEMP%` exactamente como antes del spec.
- [ ] Configurado `dir_segmentos` en otro disco y arrancado el motor, la
      carpeta de sesión nueva aparece en ese disco y `<raiz>\datos\streams` no
      recibe nada nuevo.
- [ ] Configurado `dir_grabaciones` en otro disco, el `.mp4` unido aparece ahí
      y la pestaña Grabaciones lo lista, con su miniatura, desde
      `datos\miniaturas\`.
- [ ] Con `union_modo = "carpeta"` apuntando a un disco distinto del de
      segmentos, el log de unión dice `"N archivos copiados a X"` con esa
      unidad, y el `.mp4` final igual queda en `dir_grabaciones`.
- [ ] Con `union_modo = "apagado"`, ninguna unión crea carpeta
      `nimbo_union_*` en ningún disco.
- [ ] Con `union_modo = "carpeta"` apuntando al mismo disco que los
      segmentos, no se copia nada y queda una línea `info` explicándolo.
- [ ] Con el motor corriendo, los campos de segmentos y grabaciones están
      deshabilitados y un `POST /api/rutas` sobre esas claves responde 409.
- [ ] Con el motor parado, cambiar una ruta a una carpeta válida responde 200
      y el valor queda en `config.json`.
- [ ] Una ruta inválida (relativa, o en un disco sin permiso de escritura)
      responde 400 con motivo y no cambia `config.json`.
- [ ] "Examinar" abre el diálogo de carpetas de Windows por delante de la
      ventana de Nimbo; cancelarlo deja el campo como estaba.
- [ ] Con `dir_grabaciones` en una unidad desconectada, el motor arranca y
      graba en `<raiz>\datos\grabaciones`, el banner muestra una fila roja con
      las dos rutas y `config.json` conserva la ruta pedida.
- [ ] Reconectada la unidad y reiniciado el motor, vuelve a grabar en la ruta
      configurada sin que el usuario toque nada.
- [ ] El encabezado muestra dos espacios libres cuando segmentos y
      grabaciones están en discos distintos, y uno solo cuando coinciden.
- [ ] `python nucleo\scripts\detectar_m3u8.py --listar` con `M3U8_STREAMS`
      fijado a mano lista las sesiones de esa carpeta.
- [ ] "Ver en vivo" funciona con los segmentos en el disco nuevo (`vivo.py`
      recibe la ruta por parámetro, no la recalcula).
- [ ] "Limpiar segmentos" barre la carpeta configurada, no
      `<raiz>\datos\streams`.

## Decisiones

- **Sí:** variables de entorno nuevas en el núcleo. Es el mismo mecanismo que
  `M3U8_BASE`/`M3U8_GRABAR`/`M3U8_ACTIVOS` ya usan, queda en el log y se puede
  reproducir a mano desde una consola.
- **No:** junctions o symlinks de NTFS sobre `datos\streams\`. No agregaría
  una línea al núcleo, pero esconde el disco real detrás de un enlace y deja
  el borrado a papelera de `grabaciones.py` / `cosecha_streams.py` en terreno
  no probado.
- **No:** una sola ruta para `datos\` entero. Es justo lo que impide separar
  escritura de segmentos de escritura del `.mp4` final, que es el pedido.
- **Sí:** vacío significa "lo de hoy". Una instalación existente no cambia de
  comportamiento al actualizar y no hay migración que escribir.
- **Sí:** exigir motor parado para segmentos y grabaciones. Cambiar la carpeta
  con una sesión viva la partiría entre dos discos y `--unir` no la volvería a
  encontrar entera.
- **Sí:** el escenario de unión se cambia en caliente. `_preparar_escenario`
  lee la variable en cada unión, así que no hay estado que quede viejo.
- **Sí:** caer al defecto cuando el disco no está, en vez de negarse a
  arrancar. Mismo criterio que la licencia sin red: un disco externo
  desconectado no tiene que dejar la noche sin grabar.
- **No:** reescribir `config.json` con el defecto cuando se cae. El usuario
  pidió ese disco; cuando vuelva, tiene que volver sola.
- **Sí:** conservar la comprobación de "mismo disco" incluso con carpeta
  elegida a mano. Copiar dentro del mismo disco solo agrega E/S; si alguien
  quiere forzar la copia igual, el escape es elegir otra carpeta.
- **Sí:** diálogo nativo por `ctypes` (`SHBrowseForFolderW`). `tkinter` no
  está garantizado en el Python embebido del paquete y la API del navegador
  (`showDirectoryPicker`) no devuelve la ruta real por diseño.
- **No:** mover material al cambiar de ruta. Mover cientos de GB entre discos
  con un corte posible a mitad de camino es su propio spec, si alguna vez se
  hace.
- **No:** mover `datos\miniaturas\` ni los logs. Caché regenerable y archivos
  chicos; sumarlos serían dos validaciones más por cero ganancia de disco.
- **Sí:** `resolver_rutas()` una vez (al cargar y al arrancar el motor) en vez
  de validar en una property. `Ajustes.streams` se llama en cada tick de
  `_bucle_estado`; una escritura de prueba por tick sería absurda.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El diálogo nativo aparece detrás de la ventana de Edge y parece que Nimbo se colgó | `hwndOwner = GetForegroundWindow()` y `CoInitialize` en el hilo del pool; si igual no aparece, el campo de texto sigue siendo el camino completo |
| El usuario cambia `dir_grabaciones` y cree que perdió sus grabaciones viejas | Al guardar, la interfaz muestra la ruta anterior escrita y aclara que lo viejo sigue ahí; la fila guarda ese texto hasta el próximo cambio |
| Sesiones a medio unir quedan en la carpeta vieja de segmentos y `recuperar_pendientes()` no las ve | Criterio de aceptación explícito de cambiar solo con el motor parado; el mensaje del paso 8 nombra la carpeta anterior para rehacerlas con `--unir --forzar` |
| Una ruta en unidad de red (UNC) hace que `shutil.disk_usage` o la escritura de prueba cuelguen | `rutas.validar` exige letra de unidad; UNC se rechaza con motivo y queda fuera de scope |
| El escenario elegido se llena y la unión falla a mitad | `MARGEN_ESCENARIO = 1.15` ya corta antes de copiar y cae a leer in situ, sin cambio en este spec |
| `M3U8_ESCENARIO` con una ruta inexistente en el núcleo (alguien la fija a mano) | `_preparar_escenario` intenta crearla y, si falla, loguea `warning` y lee in situ — mismo camino que ya tiene para el `OSError` de la copia |

## Lo que **no** está en este spec

- Mover o copiar material ya grabado al cambiar de carpeta.
- Listar grabaciones de carpetas anteriores.
- Mover `datos\miniaturas\`, los logs, `estado\` o `grabar.txt`.
- Rutas por modelo o directivas nuevas en `grabar.txt`.
- Cuotas por disco, borrado automático por disco lleno o cambios a
  `dias_segmentos`.
- Soporte probado de rutas UNC y unidades de red.

Cada uno de estos, si se hace, va en su propio spec.
