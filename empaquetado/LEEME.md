# Empaquetado: armar el instalador

Implementa `specs\distribucion_instalador.md`. Nada de esto corre en la
instalación de todos los días — es solo para producir el `.exe` que se le
pasa a otra persona.

## Lo que hace falta en la máquina que construye

| Qué | Para qué |
|---|---|
| Python 3.12 con `pip` | corre `construir.py` y baja las ruedas del runtime embebido |
| El zip *embeddable* de python.org | es el intérprete que va adentro del paquete |
| mitmproxy instalado (o su zip standalone) | se copia su `bin\` tal cual |
| `nucleo\scripts\ffmpeg.exe` y `wireproxy.exe` | ya están en la copia de desarrollo |
| [Inno Setup 6](https://jrsoftware.org/isdl.php) | compila el `.iss` final |

## El comando

```bat
python empaquetado\construir.py --todo
```

Eso es todo: arma el paquete completo con licencia y compila el instalador.
Deja **`dist\NimboSetup.exe`** listo para mandar. Equivale a
`--descargar --completo --licencia --instalador`.

Tarda. En la instalación real: unos minutos de descarga del navegador, y
3m27s de compresión para un paquete de 1,1 GB que terminó en un `.exe` de
317 MB.

## Los pasos por separado

```bat
rem 1) armar dist\Nimbo\ (liviano, 206 MB medidos: sin respaldo de navegador)
python empaquetado\construir.py --descargar

rem 1b) o completo (1,1 GB medidos: incluye Playwright + los dos Chromium)
python empaquetado\construir.py --descargar --completo

rem 1c) completo pero sin el Chromium headed (660 MB; rompe "ver navegador")
python empaquetado\construir.py --descargar --completo --solo-shell

rem 2) compilar el instalador -> dist\NimboSetup.exe
rem ojo con la ruta: el instalador de Inno es per-user por defecto y
rem deja ISCC.exe en %LOCALAPPDATA%\Programs\Inno Setup 6\, no en
rem Program Files
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" empaquetado\nimbo.iss
```

`--descargar` es lo único que sale a internet (baja el embeddable de
python.org). Si preferís bajarlo a mano, pasá `--python-zip <ruta>` y no
toca la red.

### Liviano o completo

El liviano **no trae el supervisor de navegador**. Las modelos marcadas
`@directo=1` en `grabar.txt` graban igual (el camino directo no usa
navegador); el resto no graba. Si el destinatario va a usar el respaldo,
armá el completo.

Lo que cuesta el completo, medido: pasa de 206 MB a **1,1 GB**. 719 MB son
los navegadores (Chromium completo 437 + headless shell 278 — Playwright
baja los dos: el shell para headless, el completo para "ver navegador") y
otros 130 MB son el driver de Node que viene adentro de la rueda de
`playwright`. `--solo-shell` lo deja en ~660 MB sacando el Chromium
completo, pero entonces el conmutador "ver navegador" de Ajustes falla.

## Qué garantiza el build

`construir.py` copia por **lista blanca** (`_ARBOL`), no por "todo el repo
menos unas exclusiones". Y antes de terminar corre `auditar()`, que aborta
si en el paquete apareció alguno de estos:

- cualquier `.conf` — son las claves privadas de WireGuard de la cuenta de
  Proton de quien construye
- `sesion.json` — equivale a la contraseña de la cuenta real
- `config.json`, `instancia.json`, `grabar.txt`, `activos.txt`

Si el build aborta por esto, **no** lo saltees: revisá qué filtro se
aflojó.

## El certificado de mitmproxy

El instalador **no** lo instala. Sin la CA de mitmproxy en el store de
Windows no se puede grabar, así que al primer arranque el banner de puesta
a punto muestra la falla con un botón "Instalar el certificado" que corre
`certutil -user -addstore ROOT` (sin permisos de administrador).

Es a propósito que sea un clic visible y no un paso silencioso: un
instalador que agrega una CA raíz sin decirlo hace exactamente lo que hace
un malware. La CA la genera mitmdump en la primera corrida y es distinta
en cada instalación — no va ninguna compartida en el paquete.

## Dónde instala

`%LOCALAPPDATA%\Programs\Nimbo`, sin elevación. No es cosmético: `config.json`,
`estado\`, `datos\` y `nucleo\scripts\grabar.txt` cuelgan de la misma raíz
que el código (`ajustes.py`), así que en `Program Files` no serían
escribibles. Ver `specs\distribucion_instalador.md` §3.

Al desinstalar quedan `datos\` (las grabaciones), `config.json` y
`grabar.txt`. Es deliberado: son horas de video y la lista de a quién se
graba.

## Licencia (opcional)

Sin `--clave-publica`, el paquete **no pide ninguna clave**: `licencia.py`
se apaga sola cuando no hay clave pública embebida. Para activarla:

```bat
rem una sola vez: genera el par de claves
python empaquetado\licencia\claves.py --generar

rem en cada build
python empaquetado\construir.py --descargar --clave-publica <la publica>
```

La privada **no va a git ni al paquete** — solo al backend. Ver
`empaquetado\licencia\LEEME.md`.

## Lo que va a pasar igual

SmartScreen va a marcar un `.exe` sin firma que levanta un proxy. Firmar
cuesta plata y no cambia nada técnico. Y distribuir `ffmpeg.exe` obliga a
incluir su licencia (GPL/LGPL según el build), igual que mitmproxy (MIT),
wireproxy (MIT) y Chromium (BSD).
