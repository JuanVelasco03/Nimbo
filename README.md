# Nimbo

Aplicación gráfica, totalmente independiente, para grabar streams de
stripchat/doppiocdn sin tocar consolas ni editar `grabar.txt` a mano.
Arrancar, parar, ver qué se está grabando, agregar modelos buscándolas
por nombre, y cambiar los ajustes de calidad con casillas.

El motor de captura (`detectar_m3u8.py` + `supervisor.py`) vive copiado
dentro de `nucleo\` — Nimbo no depende de ningún otro proyecto ni de
ninguna otra carpeta de la máquina para funcionar. Para el uso diario
una vez instalado, ver `LEEME.md`; para cómo está armado por dentro,
`CLAUDE.md` (sección "El núcleo copiado" explica el porqué de ese
layout).

Este documento es la guía de instalación: qué hace falta tener puesto
en la máquina y dónde termina guardándose cada cosa.

## 1. Antes de instalar Nimbo

El motor de captura (`nucleo\scripts\detectar_m3u8.py` y
`supervisor.py`) ya viene incluido. Lo que hace falta tener puesto en la
máquina es lo que esos scripts necesitan para correr:

| Programa | Para qué |
|---|---|
| **Python 3.12+** | corre `detectar_m3u8.py` y `supervisor.py` |
| **mitmproxy** (`mitmdump.exe`) | el proxy que graba — instalador binario de mitmproxy.org, **no** de pip |
| **Playwright** + Chromium | el navegador invisible que mantiene viva la emisión (`pip install playwright && python -m playwright install chromium`) |
| **ffmpeg** | tiene que estar en `nucleo\scripts\ffmpeg.exe` — es un binario, no viaja por git; si falta, copiarlo ahí a mano |
| **wireproxy** | solo si grabás modelos geobloqueadas con `@vpn=`; también va en `nucleo\scripts\`, tampoco viaja por git |

Además, Nimbo en sí necesita:

- **Windows 10/11** — usa `pythonw.exe`, `msedge.exe` y mecanismos de
  proceso propios de Windows; no es multiplataforma.
- **Microsoft Edge** para la ventana de la app (`--app`, sin barra de
  direcciones). Si no está, Nimbo abre el navegador que esté puesto por
  defecto — funciona igual, solo que con pestaña y barra visibles.

## 2. Instalar Nimbo

1. Descargar o clonar este repositorio a cualquier carpeta (no hace
   falta que sea `F:\Nimbo` — la ubicación se calcula sola desde dónde
   quedó el archivo; ver `NIMBO_BASE` en `nimbo\ajustes.py` si hiciera
   falta forzarla).
2. Instalar las dependencias propias de Nimbo (icono de bandeja):

   ```bat
   pip install -r requirements.txt
   ```

   Son **opcionales en la práctica**: si faltan, Nimbo abre igual, solo
   que sin icono junto al reloj (cae sola a un modo sin bandeja).
3. Doble clic en `arrancar.bat` (o el acceso directo `Nimbo.lnk`, si ya
   existe uno en el escritorio). Por dentro llama a:

   ```bat
   C:\Python312\pythonw.exe nimbo\app.py
   ```

   Si tu Python no está en esa ruta, editar la ruta directamente en
   `arrancar.bat`. **No tiene que ser el mismo Python que corre
   `supervisor.py`** — este solo necesita la librería estándar más
   `pystray`/`Pillow`; Playwright no hace falta aquí.
4. Al abrir por primera vez, Nimbo busca el `mitmdump.exe` instalado en
   el sistema, y entre varios Python del equipo, uno que tenga
   Playwright — ese es el que usará para el supervisor (puede no ser el
   mismo que abrió la ventana de Nimbo).
5. Si algo de lo anterior no se encontró (incluido `ffmpeg.exe`/
   `wireproxy.exe` faltantes en `nucleo\scripts\`), arriba de la ventana
   aparece un aviso amarillo diciendo qué falta y qué hacer.

Nada de esto pide permisos de administrador ni toca el sistema más allá
de las carpetas propias.

### Opcional: tocar los estilos de la interfaz

Solo hace falta si se va a **editar** el CSS (`nimbo\web\tailwind.css`).
Para simplemente correr Nimbo no hace falta Node ni `npm install`: el
`app.css` que sirve el servidor ya viene compilado.

```bat
npm install
npm run build:css
```

## 3. Dónde se guarda todo

```
Nimbo\                     (esta carpeta, donde se descargó/clonó)
├─ config.json             proxy, sitio, preferencias de Nimbo
├─ favoritas.json           lista de favoritas (viene vacio "[]" en el repo)
├─ sesion.json               cookies/JWT de tu cuenta real, si se pegó una (ver LEEME.md)
├─ instancia.json            puerto+token de la ventana que ya esta corriendo
├─ estado\                   detector.json/supervisor.json/siguiendo.json + los .jsonl
│                            de eventos -- los escriben los procesos hijos, Nimbo solo lee
├─ datos\                    LAS GRABACIONES (ver mas abajo)
│  ├─ streams\                 segmentos crudos mientras se graba
│  ├─ grabaciones\              los .mp4 terminados
│  └─ detector.log              historial del proxy
├─ nucleo\                   EL MOTOR DE CAPTURA (ver CLAUDE.md, "El nucleo copiado")
│  ├─ scripts\                  detectar_m3u8.py, supervisor.py, grabar.txt (SI van en git);
│  │                            ffmpeg.exe, wireproxy.exe, *.conf, activos.txt (NO van en git)
│  └─ perfil\                   perfil de Chromium del supervisor -- la sesion del sitio
├─ perfil_ventana\           perfil del Edge que abre la ventana (no es el de nucleo\perfil)
├─ registro\                  reservado, hoy sin uso activo
└─ nimbo\, web\, pruebas\...  el programa en si (esto SI va en git)
```

**Lo importante: las grabaciones (`datos\`) viven separadas del motor de
captura (`nucleo\`).** Por dentro, Nimbo arranca el detector con la
variable de entorno `M3U8_BASE` apuntando a `datos\` — un mecanismo que
`detectar_m3u8.py` ya trae de fábrica para redirigir `streams\` y
`grabaciones\` enteros sin que haga falta tocarlo.

`grabar.txt` y `activos.txt` **no** se mueven a `datos\`: siguen junto a
`detectar_m3u8.py`, en `nucleo\scripts\` — son archivos operativos
del detector (a quién grabar, qué está activo ahora), no datos de
usuario.

**Nada de lo de arriba se sube a git** salvo la carpeta `nimbo\`/`web\`
(el código), `favoritas.json` vacío (así un clon nuevo arranca con la
lista en blanco, lista para usarse), y los `.py`/`grabar.txt` de
`nucleo\scripts\`. Todo lo demás está en `.gitignore` porque son datos
de la instalación de cada uno: la cuenta real, la configuración de la
máquina, los binarios y secretos del túnel VPN, o directamente los
videos grabados.

## 4. Si algo no arranca

El aviso amarillo de arriba de la ventana dice qué falta y qué hacer —
es la pestaña de comprobaciones (`nimbo\preflight.py`). Para los casos
más comunes y el detalle de cada pestaña, ver `LEEME.md`.
