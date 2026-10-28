# SPEC 07 — Licencia que no se saca borrando un archivo

> **Estado:** Implementado
> **Depende de:** SPEC 02 (empaquetado, `--compilado`, `ajustes.compilado()`), SPEC 08 (es la que hace que el paquete salga compilado por defecto)
> **Fecha:** 2026-10-28
> **Objetivo:** Que la licencia deje de poder quitarse borrando un archivo, corriendo otro entrypoint o moviendo el reloj, pasando la verificación a una puerta única que falla cerrada en todo build compilado.

---

## Por qué

Una auditoría del 28/10/2026 sobre el paquete real (`dist\Nimbo`, armado el
27/10) encontró seis caminos para quedarse sin licencia. Cuatro no necesitan
tocar una línea de código:

| # | Bypass | Dónde | ¿Funciona compilado? |
|---|---|---|---|
| A1 | Borrar `nimbo\licencia_publica.json` → `activa()` devuelve False | `licencia.py:73-75` | **sí** — es un `.json` de datos al lado del binario |
| A2 | `Nimbo.exe --modo directo` | `app.py:23-25` corre **antes** del chequeo de `app.py:230`; `grabador_directo.py` no menciona la licencia | **sí** |
| A3 | `runtime\python\python.exe nimbo\servidor.py` → aplicación completa | `servidor.py:__main__` no chequea nada | no (necesita paquete sin compilar) |
| A4 | Reloj del sistema hacia atrás | `_vigente` compara `time.time()` contra `exp`, `licencia.py:187`; no hay marca de agua pese a que ya se guarda `revisado` | **sí** |
| A5 | Editar `activa()` → False, o `id_maquina()` → constante (una clave, N máquinas) | `licencia.py:75`, `:98` | no |
| A6 | Forzar el fallback de `id_maquina()` a `COMPUTERNAME/USERNAME`, que salen del entorno | `licencia.py:106-119` | sí |

La raíz es una sola, y es de diseño: **`activa()` es opt-in por presencia de
un archivo**. Eso se eligió a propósito para que la copia de desarrollo nunca
quedara bloqueada (SPEC 02 S6, y el docstring de `poner_licencia` en
`construir.py:387-399`), pero el mismo mecanismo que protege al desarrollador
es el que le regala la salida al usuario final: el archivo que decide si hay
licencia es un archivo que el usuario puede borrar.

La segunda raíz es que el chequeo vive en **un** entrypoint (`app.py:230`) y
hay tres. `app.py` es el único que pasa por la puerta; `servidor.py:__main__`
y `modos.correr("directo")` entran por la ventana.

Lo que esta spec no pretende: hacerla inviolable de verdad. La verificación
corre en la máquina del usuario y `licencia.py:23-27` ya lo dice. El objetivo
medible es subir el costo de "borrás un `.json`" a "parcheás un binario de
Nuitka".

---

## Scope

**In:**

- Invertir el default de `licencia.activa()`: en build compilado exige
  licencia salvo marca explícita de desarrollo.
- Mover `clave_publica` del `.json` a un módulo que entra al binario.
- Una puerta única en `ajustes.cargar()` por la que pasan los tres
  entrypoints (A2, A3).
- Marca de agua de reloj (`visto_max`) con revalidación online (A4).
- Endurecer la cadena de `id_maquina()` para que el eslabón controlable por
  el entorno sea el último (A6).

**Out of scope (otro spec):**

- Cerrar `mouflon.py` / `detectar_m3u8.py` — SPEC 08 y su continuación.
- Firmar los `.exe` — SPEC 08, Decisiones.
- Cualquier chequeo de integridad del propio binario (hash propio,
  anti-debug, packers). Es la carrera que `licencia.py:23-27` ya declara
  perdida y no cambia el orden de magnitud.
- Atar la licencia a más de una máquina, licencias flotantes, o un panel de
  administración. Hoy se emite con `insert into licencias` a mano
  (`esquema.sql:52-69`) y alcanza.
- Tocar `DIAS_TOKEN = 7` del backend. La gracia offline es deliberada
  (SPEC 02 S6.2) y bajarla deja noches sin grabar.

---

## Modelo de datos

Tres cambios, dos archivos nuevos.

### 1. `nimbo\licencia_clave.py` — **nuevo**, generado, no versionado

Lo escribe `construir.py` **antes** de llamar a Nuitka, para que entre al
binario como código compilado y no como dato en disco. Va a `.gitignore` y a
`_PROHIBIDOS` de `construir.py` (si aparece como `.py` en el paquete, el
build aborta: significaría que se compiló mal y la clave viaja legible).

```python
# generado por empaquetado\construir.py -- no editar, no versionar
CLAVE_PUBLICA = "1E8vchY5D8rr4m2H1JLqQg_7miIu8xPQX3YmOsI7zE0"
DESARROLLO = False
```

En el repo no existe. `licencia.py` lo importa dentro de un `try/except
ImportError` y sin él se comporta como la copia de desarrollo de hoy.

### 2. `nimbo\licencia_publica.json` — deja de decidir nada

Pasa a tener solo `url` y `anon`, que no son secretos y ya estaban
razonados como públicos (`construir.py:393-398`). `clave_publica` sale de
ahí. Borrarlo ya no apaga la licencia: deja a Nimbo **sin a dónde validar**,
que con la marca de agua y un token vencido es exactamente igual a no tener
licencia.

```json
{
  "url": "https://<proyecto>.supabase.co/functions/v1/validar",
  "anon": "<anon key, publica por diseno>"
}
```

### 3. `estado\licencia.json` — gana `visto_max`

```json
{
  "clave": "NIMBO-....",
  "token": "<payload>.<firma>",
  "revisado": 1761700000.0,
  "visto_max": 1761700000.0
}
```

`visto_max` es el mayor `time.time()` que Nimbo vio alguna vez. Solo crece.
Borrar el archivo lo resetea, pero también borra `clave` y `token`: el
resultado es "hay que activar de nuevo contra el backend", no "licencia
gratis".

`config.json` no cambia, y no podría sin tocar `ajustes.DEFECTOS:72`, que
rechaza claves desconocidas.

---

## Plan de implementación

Cada paso deja el sistema funcionando. Los pasos 1 a 4 no cambian nada en la
copia de desarrollo (sigue sin licencia porque no está compilada).

### 1. `nimbo\licencia.py` — el default se invierte

- `import ajustes as ajustes_mod` **no**: sería un ciclo
  (`ajustes.py` va a importar `licencia.py` en el paso 3). La detección de
  compilado se duplica a propósito, tres líneas, con el comentario que lo
  dice — mismo criterio que la duplicación de `MAX_EVENTOS` y de las
  constantes de `mp4.py`/`vivo.py` (CLAUDE.md, "Los dos puentes").
- Import tolerante de la clave:

  ```python
  try:
      import licencia_clave
      _CLAVE_COMPILADA = licencia_clave.CLAVE_PUBLICA
      _DESARROLLO = bool(getattr(licencia_clave, "DESARROLLO", False))
  except ImportError:          # repo: no existe, lo genera el build
      _CLAVE_COMPILADA, _DESARROLLO = "", True
  ```

- `activa()` nueva: `True` si el proceso está compilado y no hay marca de
  desarrollo. Ya no mira ningún archivo.
- `_clave_publica()` devuelve la compilada; el `.json` ya no se consulta para
  esto.
- Si `activa()` y `_CLAVE_COMPILADA` está vacía: **falla cerrado**. Es un
  build roto (se compiló sin generar la clave) y abrir sin licencia es el
  peor de los dos errores.

### 2. `nimbo\licencia.py` — `exigir()` y `SinLicencia`

- `class SinLicencia(Exception)` con `.motivo`.
- `exigir(dir_estado) -> dict`: si no `activa()`, devuelve
  `{"ok": True, "activa": False}`. Si no, llama a `revisar()` y **levanta
  `SinLicencia(motivo)`** cuando no da ok.

  Que sea una excepción y no un booleano es la decisión que cierra A2/A3:
  el default de no atraparla es morir. Solo el único lugar que sabe arreglar
  el problema (la pantalla de activación) la atrapa. Un entrypoint nuevo que
  se olvide de todo esto hereda el comportamiento seguro, que es justo lo
  contrario de lo que pasó con `--modo directo`.

### 3. `nimbo\ajustes.py` — la puerta

- `import licencia as licencia_mod` (plano, como todo `nimbo\`).
- En `cargar()` (`:348-375`), **después** del bucle de `os.makedirs`
  (`:370-374`, `DIR_ESTADO` tiene que existir) y **antes** del `return a`:
  `licencia_mod.exigir(DIR_ESTADO)`.
- Sin parámetro para saltearlo. El que necesita el camino con pantalla
  atrapa la excepción; no hay un `exigir=False` que un parche de una línea
  pueda forzar.

### 4. Los tres entrypoints

- **`nimbo\app.py`** — envolver el `cargar()` de `:216`:

  ```
  intentar cargar()
    SinLicencia -> activacion.pantalla(DIR_ESTADO, abrir_ventana)
                   si el usuario desistio -> return 1
                   si activo -> cargar() otra vez (ahora pasa)
  ```

  Se borra el bloque de licencia de `app.py:225-239`: su trabajo lo hace
  ahora la puerta. `abrir_ventana` y `activacion.pantalla` quedan igual.
- **`nimbo\servidor.py:__main__`** — atrapar `SinLicencia`, imprimir
  `[NIMBO] licencia: <motivo>` y `sys.exit(3)`. Sin pantalla: este
  entrypoint es para depurar con la consola a la vista, no para el usuario.
  **A3 cerrado.**
- **`nimbo\modos.py:correr()`** — ídem alrededor del
  `import lanzador_directo` / `lanzador_directo.main()`: mensaje a `stderr`
  y `return 3`. El motor ya trata una muerte del directo como "se cayó" y
  reintenta con backoff (`Motor._reiniciar("directo")`), así que un hijo que
  se niega a arrancar no se lleva puesto nada más. **A2 cerrado.**

Después de este paso: `Nimbo.exe --modo directo` y `python nimbo\servidor.py`
no graban nada sin licencia.

### 5. `nimbo\licencia.py` — marca de agua de reloj

- `HOLGURA_RELOJ = 3600.0`. Una corrección de NTP son segundos y el horario
  de verano no mueve el epoch; una hora cubre cualquier deriva legítima y no
  le sirve a nadie para estirar un token de 7 días.
- En `revisar()`, antes de decidir si el token guardado vale:

  ```
  visto_max = guardado.get("visto_max") or 0
  if time.time() < visto_max - HOLGURA_RELOJ:
      renovar = True        # el token guardado no se le cree
      vigente = False       # y tampoco sirve de red si falla la red
  ```

  Con red se renueva solo y el usuario no nota nada. Sin red queda sin
  licencia — que es exactamente la situación del bypass, porque A4 consiste
  en quedarse offline con el reloj congelado.
- Al final de `revisar()` y de `activar()`, subir la marca:
  `visto_max = max(visto_max, time.time())`, y guardar solo si subió más de
  60 s (evita una escritura por cada llamada).
- `estado()` no cambia de forma: sigue sin devolver la clave.

### 6. `nimbo\licencia.py` — `id_maquina()` en cadena

Hoy: MachineGuid, y si falla, `COMPUTERNAME/USERNAME` (entorno, o sea
controlable por quien corre el proceso). Pasa a tres eslabones:

1. `MachineGuid` de `HKLM\SOFTWARE\Microsoft\Cryptography` — igual que hoy.
2. **Nuevo:** número de serie del volumen de `%SystemDrive%`, con
   `GetVolumeInformationW` por `ctypes` (sin dependencias, mismo patrón que
   el watchdog del túnel en `motor.py` ya usa para `CreateToolhelp32Snapshot`).
3. `COMPUTERNAME/USERNAME`, prefijado `env:` para que se vea en el backend
   que es el eslabón débil.

El tercero se queda: sacarlo dejaría afuera a una instalación con una
política raria de registro, y eso es peor que A6, que necesita además
romper los dos primeros a propósito.

### 7. `empaquetado\construir.py` — generar la clave y auditarla

- `poner_licencia()` deja de escribir `clave_publica` en el `.json` y, en su
  lugar, escribe `nimbo\licencia_clave.py` **en el repo** antes de compilar,
  con `DESARROLLO = False`.
- `_PROHIBIDOS` suma `licencia_clave.py`: si aparece en el paquete, abortar.
- Borrar `nimbo\licencia_clave.py` del repo al terminar el build
  (`try/finally`), para que la copia de desarrollo vuelva a quedar sin
  licencia sola. Si queda colgado por un build interrumpido, el desarrollador
  ve "Nimbo pide clave" y lo borra — de ahí la línea en `.gitignore`.
- Abortar si hay `--clave-publica` sin `--compilado`: sin binario, la clave
  compilada es un `.py` legible al lado y A5 es una línea. Ese `SystemExit`
  es lo que hace que SPEC 07 y SPEC 08 no se puedan desincronizar.

### 8. `.gitignore` y `CLAUDE.md`

- `.gitignore`: sumar `nimbo/licencia_clave.py` al lado de
  `nimbo/licencia_publica.json`, con el mismo comentario de por qué.
- `CLAUDE.md`: en "Quién escribe qué", `estado\licencia.json` con su
  `visto_max`. En "Empaquetado para otra máquina", corregir el párrafo que
  hoy dice que `licencia.activa()` devuelve `False` si no existe
  `nimbo\licencia_publica.json` — pasa a ser "si el proceso no está
  compilado".

---

## Criterios de aceptación

Sobre un paquete armado con `--compilado` (SPEC 08):

- [ ] Borrar `nimbo\licencia_publica.json` y abrir Nimbo: sigue pidiendo
      licencia (no abre la interfaz sin activar). **A1**
- [ ] `Nimbo.exe --modo directo` sin licencia válida: sale con código 3, deja
      el motivo en `estado\eventos_directo.jsonl` y no escribe nada en
      `datos\streams\`. **A2**
- [ ] `runtime\python\python.exe nimbo\servidor.py` sin licencia válida: sale
      con código 3 y no abre el puerto. **A3**
- [ ] Con licencia activa, atrasar el reloj del sistema un día **sin red**:
      Nimbo no arranca. Con red: arranca sin que el usuario haga nada. **A4**
- [ ] `estado\licencia.json` contiene `visto_max` y su valor nunca baja entre
      arranques.
- [ ] `id_maquina()` devuelve el mismo valor tras cambiar `COMPUTERNAME` y
      `USERNAME` en el entorno del proceso. **A6**
- [ ] Un token emitido para otra máquina, copiado a `estado\licencia.json`,
      no activa nada.
- [ ] `grep -r licencia_clave dist\Nimbo` no encuentra ningún `.py`; el build
      aborta si se lo fuerza a copiarlo.
- [ ] Con licencia válida y sin red, Nimbo arranca y graba (la gracia offline
      sigue viva mientras el token no venza).
- [ ] La licencia vencida con grabaciones en curso sigue avisando y llamando
      a `Motor.parar()`, sin matar nada a lo bruto (`_bucle_licencia`,
      `servidor.py:537`).

Sobre la copia de desarrollo (sin compilar, sin `licencia_clave.py`):

- [ ] `python nimbo\app.py`, `python nimbo\servidor.py` y
      `python nimbo\lanzador_directo.py` arrancan sin pedir ninguna clave.
- [ ] `python empaquetado\construir.py --descargar` (sin licencia) sigue
      armando un paquete que arranca sin pedir clave.

---

## Decisiones

- **Sí:** `activa()` pasa a ser `compilado() and not DESARROLLO`. Invierte el
  default de "abre salvo que haya un archivo" a "exige salvo que sea una
  copia de desarrollo", y de paso borra A1 sin criptografía nueva.
- **No:** firmar `licencia_publica.json` con la clave compilada. Mismo
  resultado que compilar la clave, un archivo más de criptografía y un
  formato más que mantener.
- **No:** dejar `clave_publica` en el `.json` y solo verificar que esté. Sin
  el archivo no hay `url`, y sin `url` tampoco se puede renovar: el usuario
  quedaría con "no se puede validar" en vez de un bloqueo claro.
- **Sí:** la puerta en `ajustes.cargar()`. Es lo único que los tres
  entrypoints llaman antes de hacer cualquier cosa, así que cubre también al
  cuarto que alguien agregue mañana. Mete política en un módulo que hoy solo
  resuelve rutas, y se asume: el costo de olvidarse de llamar a un
  `guardia.py` ya lo pagamos una vez y se llama A2.
- **Sí:** `SinLicencia` como excepción en vez de un booleano. El default de
  ignorar un valor de retorno es seguir; el default de ignorar una excepción
  es morir. Para una puerta de licencia el segundo default es el correcto.
- **No:** un parámetro `cargar(exigir_licencia=False)`. Sería la ruta de
  escape documentada, y el primer lugar donde miraría cualquiera.
- **Sí:** revalidar online al detectar reloj atrás, en vez de bloquear duro.
  Un reloj de BIOS descargado es un caso real y bloquear ahí deja la noche
  sin grabar aunque haya internet — el mismo razonamiento que hizo existir la
  gracia offline (SPEC 02 S6.2).
- **Sí:** `HOLGURA_RELOJ = 3600`. Cubre cualquier deriva legítima y no
  alcanza para estirar un token de 7 días.
- **Sí:** `visto_max` en `estado\licencia.json` y no en el registro ni en
  otro archivo. Ya existe, ya se escribe atómico, y vive junto a lo que
  protege: borrarlo para resetear la marca borra también la clave.
- **No:** guardar la marca en el registro de Windows o en un segundo archivo
  escondido. Esconderla no la hace más fuerte contra quien ya está
  parcheando un binario, y agrega un lugar más donde el estado se puede
  desincronizar.
- **Sí:** mantener el tercer eslabón de `id_maquina()` basado en entorno.
  Dejar a alguien afuera por una política de registro es peor que A6, que
  además exige romper los dos eslabones anteriores a propósito.
- **Asumido:** con la puerta en `cargar()`, una segunda ejecución de Nimbo
  mientras la primera corre pasa por la licencia antes del chequeo de
  instancia única (`app.py:218`). Con licencia válida no cambia nada; con
  licencia vencida, abrir una segunda ventana muestra la pantalla de
  activación en vez de la ventana. Es el comportamiento correcto y no vale
  reordenar `cargar()` para evitarlo.

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Build compilado sin `licencia_clave.py` → Nimbo no abre en la máquina del cliente | `construir.py` genera el archivo él mismo y aborta si hay `--clave-publica` sin `--compilado`; el criterio de aceptación lo cubre antes de mandar nada |
| `licencia_clave.py` queda colgado en el repo por un build interrumpido → la copia de desarrollo empieza a pedir clave | Borrado en `try/finally`, en `.gitignore`, y el síntoma es inconfundible |
| Un reloj accidentalmente adelantado (años) escribe un `visto_max` enorme y después la hora correcta parece un retroceso | Con red se revalida y se arregla solo; es exactamente la rama que elegimos. Sin red, borrar `estado\licencia.json` y reactivar |
| `GetVolumeInformationW` devuelve otro serial después de formatear o clonar el disco | Es el segundo eslabón: solo se usa si MachineGuid falló. Y si el id cambia, el backend contesta "esta clave ya se activó en otro equipo", que se resuelve con `update licencias set maquina = null` (`esquema.sql:66-69`) |
| La puerta en `cargar()` rompe `pruebas\*.py`, que también llaman a `ajustes` | Las pruebas corren sobre la copia de desarrollo (sin compilar), donde `activa()` es False y `exigir()` no hace nada |
| Alguien parchea el binario de Nuitka y salta todo | Aceptado y declarado en `licencia.py:23-27`. Esta spec sube el costo un orden de magnitud, no lo hace imposible |

---

## Verificación

```bat
rem 1. la copia de desarrollo no cambia
python nimbo\app.py
python nimbo\servidor.py
python nimbo\lanzador_directo.py

rem 2. el par de claves y el camino completo sin red
python empaquetado\licencia\claves.py --probar

rem 3. armar el paquete cerrado (SPEC 08)
python empaquetado\construir.py --todo

rem 4. que la clave no viaje legible
findstr /s /i "CLAVE_PUBLICA" dist\Nimbo\*.py
rem  -> no debe encontrar nada
```

A mano, sobre `dist\Nimbo` (o la instalación que deja `NimboSetup.exe`):

1. **A1.** Renombrar `nimbo\licencia_publica.json` y abrir Nimbo: tiene que
   pedir activación, no abrir la interfaz.
2. **A2.** `nimbo\Nimbo.exe --modo directo` desde una consola, con
   `estado\licencia.json` borrado: código de salida 3, nada nuevo en
   `datos\streams\`.
3. **A3.** `runtime\python\python.exe nimbo\servidor.py` en las mismas
   condiciones: código 3, y `netstat` no muestra el puerto abierto.
4. **A4.** Activar con red, cortar la red, atrasar el reloj un día, abrir:
   tiene que pedir activación. Volver a poner la hora y la red: abre solo.
5. **A6.** `set COMPUTERNAME=otro` y `set USERNAME=otro` en la consola, y
   confirmar que `id_maquina()` no cambió (imprimirlo con el Python embebido).
6. **Gracia offline.** Activar con red, cortar la red, abrir: tiene que
   arrancar y grabar. Es el criterio que no se puede romper por endurecer el
   resto.
7. **Vencimiento en caliente.** Con `update licencias set vence = now() +
   interval '2 minutes'` y Nimbo grabando, esperar: tiene que avisar y parar
   ordenado, sin dejar grabaciones sin unir.

No hay suite de tests en el repo; esto es verificación manual, como el resto
de los specs.

---

## Archivos tocados

| Archivo | Qué |
|---|---|
| `specs\07-licencia_inviolable.md` | **nuevo** — este documento |
| `nimbo\licencia_clave.py` | **nuevo**, generado por el build, no versionado — `CLAVE_PUBLICA` + `DESARROLLO` |
| `nimbo\licencia.py` | `activa()` invertida, `exigir()` + `SinLicencia`, `visto_max`, `id_maquina()` en cadena |
| `nimbo\ajustes.py` | la puerta: `licencia.exigir(DIR_ESTADO)` al final de `cargar()` |
| `nimbo\app.py` | atrapar `SinLicencia` → pantalla de activación; se borra el bloque de `:225-239` |
| `nimbo\servidor.py` | `__main__` atrapa `SinLicencia` → exit 3 |
| `nimbo\modos.py` | `correr()` atrapa `SinLicencia` → exit 3 |
| `empaquetado\construir.py` | generar `licencia_clave.py`, `_PROHIBIDOS`, abortar sin `--compilado` |
| `.gitignore` | `nimbo/licencia_clave.py` |
| `CLAUDE.md` | `visto_max` en "Quién escribe qué"; corregir el párrafo de `licencia.activa()` |

---

## Lo que **no** está en este spec

- Cerrar `mouflon.py` y `detectar_m3u8.py` (SPEC 08 y su continuación).
- Firmar los `.exe` (SPEC 08, Decisiones).
- Chequeos de integridad del binario, anti-debug, packers.
- Licencias flotantes, multi-máquina, o panel de administración.
- Cambiar `DIAS_TOKEN` ni la gracia offline.

Cada uno, si entra, va en su propio spec.
