# Nimbo — panel de grabación

Una aplicación con ventana propia, totalmente independiente, para grabar
streams sin tocar consolas ni editar `grabar.txt` a mano. Arrancar, parar,
ver qué se está grabando, agregar modelos buscándolas por nombre, y
cambiar los ajustes de calidad con casillas.

El motor de captura (`detectar_m3u8.py` + `supervisor.py`) vive copiado
dentro de `F:\Nimbo\nucleo\` — no hace falta ninguna otra carpeta ni
proyecto en la máquina para que Nimbo funcione.

## Para empezar

1. Doble clic en el icono **Nimbo** del escritorio (o en
   `F:\Nimbo\arrancar.bat`).
2. Se abre una ventana. Si falta algo (mitmproxy, ffmpeg, el navegador de
   Playwright...), aparece un aviso amarillo arriba con qué hacer.
3. Pestaña **Grabando**: ahí se ve quién está grabando ahora, con cuánto
   lleva y cuánto pesa. El botón grande de arriba a la derecha arranca o
   para todo.
4. Pestaña **Modelos**: escribí un nombre para buscarla (aparecen
   sugerencias con miniatura mientras escribís) y agregala a favoritas o
   grabala directo. Desde cada favorita se puede prender/apagar la
   grabación, marcarla para el túnel (geobloqueadas) o sacarla de
   favoritas.
5. Pestaña **Ajustes**: las mismas opciones que antes iban en
   `grabar.txt` (calidad, códec, qué hacer con los shows privados...) más
   las propias de Nimbo (arrancar solo, mostrar el navegador, bandeja,
   arrancar con Windows).

Cerrar la ventana **no para la grabación**: queda un icono junto al reloj
("Nimbo") con el estado, y desde ahí se reabre la ventana o se para todo
de verdad. "Parar todo" puede tardar varios minutos si hay una grabación
larga uniéndose — no apagues el PC mientras tanto, el aviso lo va a decir.

## Si algo no arranca

- El aviso amarillo de arriba de la ventana dice qué falta y qué hacer.
- Si la ventana no abre: fijate que exista `C:\Python312\pythonw.exe` (o
  edita `F:\Nimbo\arrancar.bat` con la ruta de tu Python). Este no tiene
  que ser el mismo que corre `supervisor.py` ni necesita Playwright — ese
  Python, el que sí hace falta con Playwright instalado, Nimbo lo busca
  solo entre los que haya en la máquina (ver README.md).
- Si Nimbo ya estaba abierto y lo abrís de nuevo, no arranca un segundo
  motor: solo abre otra ventana contra el que ya está corriendo.
- El log del detector queda en `F:\Nimbo\datos\detector.log` (Nimbo lo
  redirige ahí junto con las grabaciones, ver abajo); el del supervisor
  queda en `F:\Nimbo\nucleo\supervisor.log`. Para el día a día no hace
  falta abrir ninguno: todo lo que importa se ve en el panel de
  "registro" abajo de la ventana.

## Qué NO hace (todavía)

- No reemplaza `--unir --forzar` para rehacer una grabación rota: eso se
  sigue haciendo desde una consola, apuntando a
  `nucleo\scripts\detectar_m3u8.py`.
- La búsqueda y las miniaturas usan un endpoint del sitio que no es
  oficial (se descubrió mirando su propio tráfico). Si el sitio lo
  cambia, la búsqueda deja de traer resultados pero todo lo demás sigue
  andando igual: escribí el nombre exacto y agregalo igual.

## Estructura

```
F:\Nimbo\
├─ Nimbo.lnk          (en el escritorio) -> arrancar.bat
├─ arrancar.bat
├─ config.json           el proxy, el sitio, las preferencias
├─ favoritas.json
├─ estado\                lo escriben los procesos hijos, Nimbo solo lee
├─ datos\                 las grabaciones y los streams a medio unir -- ver README.md
├─ nucleo\                el motor de captura copiado -- ver CLAUDE.md
├─ pruebas\                scripts de prueba (no hacen falta para usarlo)
└─ nimbo\                el programa
```

Para la guía de instalación (qué hay que tener puesto y dónde se guarda
cada cosa), ver `README.md`. Para los detalles de cómo está armado por
dentro, ver `CLAUDE.md`.
