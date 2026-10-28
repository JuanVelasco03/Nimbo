# Distribución: instalador único, código cerrado y clave con vencimiento

Especificación de diseño, con lo implementado marcado abajo. Rama de trabajo:
`03-distribucion`.

| § | Qué | Estado |
|---|---|---|
| 4 | Instalador con todo adentro | **hecho y probado** — `empaquetado\construir.py` + `nimbo.iss`; paquete de 206 MB armado y arrancado con su propio Python |
| 6 | Clave con vencimiento | **hecho**; cliente probado de punta a punta sin red (`claves.py --probar`). Falta desplegar la Edge Function en Supabase |
| 5 | Cerrar el código con Nuitka | **parcial**: el `--modo` del binario está hecho y probado; la compilación en sí **no se corrió** (Nuitka no está instalado). Alcance recortado — ver §5.1 |

Lo que sigue de esta línea es la spec tal como se escribió antes de
implementar, con las correcciones que la implementación obligó a hacer
marcadas en su lugar y resumidas en §8.

El objetivo que la motiva: pasarle Nimbo a una segunda persona con **un solo
instalador** (sin que instale Python, mitmproxy, ffmpeg ni Playwright por
separado), **sin darle el código fuente**, y con una **clave que vence** y que
se valida contra un backend.

Son tres problemas con dificultad muy distinta y conviene resolverlos en este
orden:

| # | Objetivo | Dificultad | Toca código |
|---|---|---|---|
| 1 | Que lo instale y ande, todo adentro | baja — es empaquetado | casi nada (§4) |
| 2 | Clave con vencimiento | baja/media | sí, módulo nuevo (§6) |
| 3 | Que no vea el código | **parcial, nunca total** (§5) | según cuánto se quiera cerrar |

---

## 1. Lo que ya juega a favor

Nimbo **ya es portable**, y eso es lo que hace que el punto 1 sea empaquetado y
no reescritura:

- `ajustes.py:28` calcula `RAIZ` desde la ubicación del propio archivo. Nada
  está clavado a una letra de unidad (ver `CLAUDE.md`, "Detalles con
  historia").
- `detectar_m3u8.py`, `grabador_directo.py` y `mouflon.py` importan **solo
  librería estándar** (verificado por grep sobre los tres archivos). El único
  que necesita una dependencia pesada es `supervisor.py`, y la importa
  **perezosamente** dentro de las funciones (`supervisor.py:779`, `:795`,
  `:905`, `:922`), no a nivel de módulo — por eso `sitio.py` lo puede importar
  para reusar su `RE_ESTADO` sin arrastrar Playwright.
- `nimbo\` depende de stdlib + `websockets` (obligatoria) + `pystray`/`Pillow`
  (opcionales, sin ellas cae a modo sin bandeja).
- Edge lo pone el sistema, con fallback al navegador por defecto
  (`app.py:_localizar_edge`).
- Node/Tailwind **no** hacen falta en runtime: `app.css` se versiona compilado.
- `preflight.py` ya enumera lo que falta y qué hacer, e `inicio_automatico.py`
  ya resuelve el autoarranque con un `.bat` en la carpeta de Inicio.

---

## 2. Qué va adentro del instalador

Pesos medidos en esta instalación (27/10/2026), no estimados:

Primero lo estimado, y abajo lo que de verdad pesó al armarlo.

| Pieza | De dónde sale | Peso |
|---|---|---|
| CPython 3.12 *embeddable* + `websockets`, `pystray`, `Pillow` | zip oficial de python.org + `pip install --target` en el build | ~25 MB |
| `mitmdump.exe` + su `_internal\` | zip standalone de mitmproxy (**no** su instalador) | ~55 MB |
| `ffmpeg.exe` | el que ya está en `nucleo\scripts\` | **102,8 MB** (un build *essentials* baja a ~40) |
| `wireproxy.exe` | ídem | **10,7 MB** |
| Código de Nimbo (`nimbo\`, `nucleo\scripts\*.py`, `web\`, `app.css`) | el repo | <2 MB |
| **Opcional**: `playwright` + un Chromium | `pip` + `playwright install chromium` en el build | ~450 MB |

→ estimado: **~195 MB** sin el respaldo del navegador, **~645 MB** con él.

### 2.0 Lo que pesó de verdad

El liviano dio **206 MB**, cerca. El completo dio **1,1 GB**, casi el doble
de lo estimado:

| Carpeta | Medido |
|---|---|
| `runtime\navegadores` | **719 MB** (Chromium completo 437 + headless shell 278) |
| `runtime\python` | **150 MB** |
| `nucleo` (ffmpeg + wireproxy) | 109 MB |
| `runtime\mitmproxy` | 59 MB |
| `nimbo` | 0,6 MB |

Dos errores en la estimación, los dos en el mismo lugar:

1. **La rueda de `playwright` trae el driver de Node adentro** (~130 MB).
   Por eso `runtime\python` pasa de 25 MB a 150 MB en cuanto se arma con
   `--completo`.
2. **`playwright install chromium` baja dos builds, no uno.** Playwright usa
   el *headless shell* cuando el supervisor corre headless (el caso normal) y
   el Chromium completo cuando el usuario prende "ver navegador" en Ajustes.

`construir.py --solo-shell` saltea el completo y deja el paquete en ~660 MB,
a cambio de que "ver navegador" deje de funcionar. No es el defecto porque
ese conmutador está en la interfaz y fallaría en silencio.

`nucleo\` completo mide hoy 819 MB, pero la mayor parte es `perfil\` (el perfil
de Chromium del supervisor) y no se distribuye: se regenera solo.

### 2.1 Por qué el zip standalone de mitmproxy y no pip

El `mitmdump.exe` instalado trae su propio runtime en
`C:\Program Files\mitmproxy\bin\_internal\` — layout de PyInstaller
(`base_library.zip` + `python314.dll`). O sea: **es autocontenido y se puede
copiar a la carpeta de instalación tal cual**, sin pip, sin resolver las
dependencias nativas de mitmproxy (`cryptography`, `aioquic`, `brotli`,
`argon2`, todas con `.pyd`).

Dato importante para §5: ese runtime embebido es **Python 3.14**, distinto del
3.12 que usa el resto de Nimbo.

### 2.2 Qué NO va adentro

| Qué | Por qué |
|---|---|
| `nucleo\scripts\proton-nl.conf`, `tunel_wireproxy.conf` | Son las **claves privadas de WireGuard** de la cuenta de Proton del autor. Quien las tenga sale a internet como él. El destinatario pone las suyas. |
| `sesion.json` | Equivale a la contraseña de la cuenta real mientras la sesión viva (`CLAUDE.md`, "Detalles con historia"). |
| `nucleo\scripts\grabar.txt` | Datos de usuario. El detector lo crea solo en el primer arranque (`Detector.__init__`). |
| `estado\`, `datos\`, `perfil_ventana\`, `nucleo\perfil\` | Se generan solos. |
| `node_modules\`, `pruebas\`, `specs\`, `recomendaciones\` | No son runtime. |

---

## 3. Instalar **per-user**, no en `Program Files`

Decisión, no preferencia. `config.json`, `estado\`, `datos\` y
`nucleo\scripts\grabar.txt` cuelgan de `RAIZ`, **junto al código**
(`ajustes.py:30-48`). En `Program Files` no son escribibles sin elevación, y
partir `RAIZ` en código/datos es un refactor que cruza `ajustes.py`, `motor.py`
(que pasa `M3U8_BASE=<raiz>\datos`), `cosecha_streams.py` y los dos puentes.

Instalando en `%LOCALAPPDATA%\Programs\Nimbo` **no hay que cambiar nada de
eso**. `NIMBO_BASE` no sirve de atajo: redirige la raíz entera, incluido
`nucleo\`, así que no separa código de datos.

---

## 4. Cambios de código del punto 1 (mínimos)

Solo `ajustes.py`:

1. `localizar_mitmdump()` (`ajustes.py:94`) mira hoy el PATH y
   `C:\Program Files\mitmproxy\bin\`. Agregar **primero**
   `<RAIZ>\runtime\mitmproxy\mitmdump.exe`.
2. `localizar_python()` (`ajustes.py:118`) prueba `sys.executable`, el PATH y
   `C:\Python312|311|313\python.exe`. Agregar **primero**
   `<RAIZ>\runtime\python\python.exe`.
   `_sin_consola()` sigue andando sin tocarlo: el zip embeddable trae
   `pythonw.exe` al lado.
3. `preflight.py:10` busca Chromium **solo** en
   `~\AppData\Local\ms-playwright`. Si el instalador trae el navegador y se lo
   pasa por `PLAYWRIGHT_BROWSERS_PATH`, esa comprobación va a decir que falta
   cuando está. Hay que hacerla consultar la misma variable.

Son ~20 líneas en total. Nada de `motor.py`, nada del núcleo.

> Al implementarlo los tres puntos quedaron así, con dos arreglos de más que
> el paquete dejó a la vista: `localizar_python()` tiene que **cortar** en el
> embarcado y no seguir buscando (§8.3), `_sin_consola()` estaba roto con
> rutas en mayúsculas (§8.4) y la fila de Playwright del preflight se partió
> en dos (§8.5). `motor.py` sí se tocó, pero por §5.2, no por esto.

### 4.1 Lo que el instalador hace además de copiar

- Acceso directo que llama al `pythonw.exe` embebido contra `nimbo\app.py`
  (lo mismo que `arrancar.bat`, que también conviene regenerar apuntando al
  runtime embebido — hoy tiene `C:\Python312\pythonw.exe` escrito a mano).
- **El CA de mitmproxy.** Para interceptar el HLS de doppiocdn el certificado
  tiene que estar en el store de Windows. Es lo único genuinamente intrusivo
  del paquete; hoy lo resolvía el instalador de mitmproxy. Dos caminos:
  `certutil -addstore -user ROOT` desde el instalador (sin admin, store del
  usuario), o un botón explícito en el preflight. **Preferir el segundo**: que
  un instalador agregue una CA raíz en silencio es exactamente lo que hace un
  malware, y no hay motivo para que esto no sea una decisión visible.
- Herramienta: Inno Setup. No hace falta nada más: el resto es copiar árbol.

### 4.2 Lo que va a pasar igual

SmartScreen va a marcar un `.exe` sin firma que instala una CA y levanta un
proxy. Firmar cuesta plata y no cambia nada técnico. Y distribuir `ffmpeg.exe`
obliga a incluir su licencia (GPL/LGPL según el build) — igual que
mitmproxy (MIT), wireproxy (MIT) y Chromium (BSD).

---

## 5. Cerrar el código: hasta dónde se puede

**El techo:** el código corre en la PC del destinatario, así que *existe* en su
PC. Toda protección local es un freno, no un candado.

| Opción | Qué frena |
|---|---|
| Borrar los `.py` y dejar `.pyc` | Una mirada casual. `decompyle3` lo revierte en un minuto. |
| PyInstaller | Lo mismo: `pyinstxtractor` saca los `.pyc` del exe. |
| **Nuitka** | Compila a C y de ahí a binario nativo. No hay bytecode que recuperar, solo desensamblado. Para alguien que no es reverser, alcanza de sobra. |

### 5.1 Qué se puede cerrar y qué no

Esto es lo que de verdad define el alcance, y sale de cómo arrancan los hijos
(`CLAUDE.md`, "Cuatro procesos"):

| Archivo | ¿Se puede compilar? | Por qué |
|---|---|---|
| `nimbo\app.py`, `servidor.py`, `motor.py`, `vivo.py`, `lista.py`, `sitio.py`, `mp4.py`, … | **Sí** | Es un proceso propio. Nuitka standalone con `--follow-imports` los toma todos. Ojo con los **imports planos** (`import ajustes as ajustes_mod`, ver `CLAUDE.md` "Imports planos"): hay que compilar con `nimbo\` como raíz, no como paquete. |
| `nucleo\scripts\detectar_m3u8.py`, `nimbo\puente.py` | **No** | mitmdump los carga con `-s <ruta.py>`, como archivos en disco, dentro de su runtime **3.14**. A lo sumo `.pyc` compilado específicamente con 3.14 — y un `.pyc` se decompila. Queda expuesto el detector entero: HLS/CMAF, unión con ffmpeg, el túnel. |
| `nucleo\scripts\supervisor.py` | **No, mientras lo importe `lanzador_supervisor.py` como `.py`** | Y `sitio.py` también lo importa (para `RE_ESTADO`). |
| `nucleo\scripts\grabador_directo.py` | **Sí, con el cambio de §5.2** | Hecho: `--include-module=grabador_directo` en `empaquetado\compilar.py`. |
| `nucleo\scripts\mouflon.py` | **No** (corregido al implementar) | `detectar_m3u8.py:124` hace `import mouflon` al correr, bajo el intérprete de mitmdump. El archivo tiene que seguir en disco; compilarlo adentro del binario no lo saca de ahí. **La tabla de claves MOUFLON queda expuesta.** |

**Corrección a lo que dije antes en la conversación:** compilar `nimbo\` con
Nuitka **no** protege el camino directo por sí solo. `motor.py:477` lanza
`[a["python"], LANZADOR_DIRECTO]`, o sea `python.exe lanzador_directo.py` —
un `.py` suelto que importa `grabador_directo.py`, otro `.py` suelto. Mientras
ese sea el mecanismo de arranque, los dos quedan legibles en disco.

### 5.2 El cambio que cierra el camino directo

Convertir el exe compilado en **multi-entrada**: que `motor.py` lance
`[sys.executable, "--modo", "directo"]` (y `--modo supervisor`) en vez de
`[a["python"], LANZADOR_*.py]`, con un dispatch al principio de `app.py` que
mire `sys.argv` **antes de cualquier otro import**.

Es chico y es seguro de hacer:

- `grabador_directo.py:1461` y `supervisor.py:940` tienen `if __name__ ==
  "__main__"` propio, así que importarlos no arranca nada.
- **No** viola la invariante dura del repo ("nunca importar
  `detectar_m3u8.py` desde el proceso de Nimbo"): el hijo es otro proceso, y
  el que se importaría es `grabador_directo`, no el detector.
- `lanzador_directo.py` (116 líneas) y `lanzador_supervisor.py` (282) pasan a
  ser funciones del binario en vez de scripts.

Lo que **no** cambia y hay que tener presente: `grabador_directo.py` delega la
unión en `python detectar_m3u8.py --unir <sesión>` como subproceso
(`CLAUDE.md`, "Los dos puentes"). Eso sigue necesitando el Python embebido y el
`.py` del detector en disco. Consistente con que el detector no se puede
cerrar de todos modos.

### 5.3 Conclusión del punto 3

Con Nuitka sobre `nimbo\` + el cambio de §5.2 se cierra: la interfaz, el
servidor, el motor de arranque, el reproductor en vivo, la cosecha y **la
lógica del camino directo** (`grabador_directo.py`).

Queda abierto: el detector (`detectar_m3u8.py` + `puente.py`), el supervisor
y **`mouflon.py`** — con su `KEY_MAP`.

Eso último es una corrección a lo que esta spec decía antes de
implementarse, y cambia el balance: el protocolo MOUFLON **no** se puede
cerrar, porque el detector lo importa como `.py` en tiempo de ejecución bajo
el intérprete de mitmdump (3.14), que no ve nada de lo compilado. Cerrarlo
pediría partir `mouflon.py` en dos (un módulo abierto con lo que usa el
detector —`leer_master`/`mejor_rendicion`/`puntaje`— y uno compilado con el
descifrado y la tabla), y eso toca el núcleo copiado y su contrato de
sincronización a mano. No se hizo: es más cirugía que lo que esta spec
quiere, y conviene decidirlo aparte.

Entonces lo que Nuitka compra hoy es que no se lea la interfaz ni el motor
de Nimbo. Si lo que importa es el protocolo, no alcanza — y ahí la respuesta
honesta no es técnica: no compartir el binario.

---

## 6. Clave con vencimiento

### 6.1 Diseño

Backend en Supabase (ya disponible):

```sql
create table licencias (
  clave       text primary key,          -- la que se entrega a mano
  vence       timestamptz not null,
  maquina     text,                       -- se fija en el primer uso
  activa      boolean not null default true,
  nota        text
);
```

Edge Function `validar`:

1. Recibe `{clave, maquina}`.
2. Rechaza si no existe, si `activa = false`, si `vence < now()`, o si
   `maquina` ya está fijada y no coincide.
3. Si `maquina` está en `null`, la fija (atadura al primer equipo).
4. Devuelve un **token firmado con Ed25519** cuyo payload es
   `{clave, maquina, exp}`, donde `exp = min(vence, now + 7 días)`.

Cliente (módulo nuevo `nimbo\licencia.py`):

- `maquina` = `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` (se lee sin
  admin).
- Guarda el token en `estado\licencia.json`, atómico (`.part` + `os.replace`,
  como todo lo que escribe Nimbo).
- Verifica la **firma** con la clave pública embebida en el binario y mira
  `exp`. Sin red, el token vigente alcanza.
- Pide uno nuevo cuando le queda menos de la mitad de vida.

### 6.2 La gracia offline no es opcional

Si el chequeo exige red en cada arranque, un corte de internet deja la noche
entera sin grabar — exactamente lo que `Motor._bucle_vigilancia` existe para
evitar. De ahí el `exp = now + 7 días` del token: una semana de margen.

### 6.3 Dónde engancha

- **Al arrancar**, en `app.py`, antes de levantar el servidor: si no hay
  licencia válida, mostrar una pantalla de "ingresá la clave" en vez de la
  interfaz. Conviene que sea la misma ventana de Edge contra un endpoint
  mínimo, no un diálogo nativo: no hay toolkit gráfico en el proyecto y no
  vale la pena agregar uno.
- **Mientras corre**, una revisión diaria. Si vence con grabaciones en curso,
  **no** matar nada: avisar por el canal `motor` del WebSocket y llamar a
  `Motor.parar()`, que es la parada ordenada por stdin — matar a lo bruto deja
  grabaciones sin unir (`CLAUDE.md`, "Cuatro procesos").

### 6.4 Lo que esto no es

La verificación es local, así que un parche al binario la saltea. Firmar el
token (en vez de devolver un `{"ok": true}` que se puede falsificar con un
`hosts` y un proxy) sube la vara de "interceptar un HTTP" a "encontrar y
neutralizar la verificación dentro de un binario Nuitka". Para el caso de uso
—una persona de confianza, no un atacante— alcanza. Para cualquier otro, no.

---

## 7. Orden de trabajo sugerido

1. **§4**: instalador con todo adentro, sin cerrar código ni clave. Ya es
   entregable y es el 80% del valor.
2. **§6**: la clave. Independiente del empaquetado.
3. **§5**: Nuitka sobre `nimbo\`, y recién después el cambio de §5.2 si el
   camino directo importa cerrarlo.

Cada paso es útil solo; ninguno bloquea al siguiente.

---

## 8. Lo que encontró la implementación

Cinco cosas que no se veían leyendo código y aparecieron al armar y
**correr** el paquete. Las tres primeras habrían hecho que el instalador
llegara roto o mintiendo a la máquina del otro.

### 8.1 Un `._pth` apaga el `sys.path` del script — Nimbo no arrancaba

El zip *embeddable* trae un `python312._pth`, y la sola presencia de ese
archivo hace que Python **no** agregue la carpeta del script a `sys.path`.
Los módulos de `nimbo\` se importan sin paquete (`import ajustes`, ver
`CLAUDE.md` "Imports planos") **justamente** porque `app.py` corre como
script y su carpeta queda en el path. Con el embeddable eso deja de pasar:

```
File "...\nimbo\app.py", line 35, in <module>
    import ajustes as ajustes_mod
ModuleNotFoundError: No module named 'ajustes'
```

El build quedaba perfecto y el `.exe` no abría. Se arregla con una línea
`..\..\nimbo` en el `._pth` (`construir.py:_habilitar_site`; las rutas de
ese archivo son relativas a la carpeta que lo contiene).

### 8.2 La CA de mitmproxy puede estar en el store de la máquina

La comprobación nueva mirando solo `certutil -user -store ROOT` daba "falta
el certificado" **en la instalación de desarrollo, que graba perfecto**: el
instalador de mitmproxy la pone en el store de la máquina (con elevación).
`preflight._ca_en_el_store()` mira los dos; cualquiera alcanza. Nimbo sigue
instalando en el del usuario, que es el que se escribe sin admin.

### 8.3 `localizar_python()` se iba del paquete

`localizar_python()` descarta todo intérprete que no tenga Playwright. En el
paquete liviano el embebido **no** lo tiene, así que la búsqueda lo
descartaba y se iba a buscar un Python del sistema — en una máquina con otro
Python instalado, el paquete terminaba corriendo el supervisor con un
intérprete ajeno. Ahora, si hay Python embarcado, manda y se corta ahí; que
falte el respaldo de navegador lo dice `preflight.py`.

### 8.4 `_sin_consola()` no servía con `shutil.which()`

Hacía `python.replace("python.exe", "pythonw.exe")`, y `which()` devuelve lo
que diga `PATHEXT`: en esta máquina, `python.EXE`. El replace no cambiaba
nada y se seguía usando el exe con consola. Ahora compara el basename sin
mirar mayúsculas. Es un bug que ya existía, no algo que trajo el paquete.

### 8.5 La fila "Python con Playwright" mentía

Solo comprobaba que el archivo existiera. En el paquete liviano daba OK
sobre un intérprete sin Playwright. Se partió en dos filas: que haya
intérprete, y que haya navegador de respaldo.

---
