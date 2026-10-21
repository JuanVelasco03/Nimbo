# Recomendaciones — ideas duras para llevar Nimbo más allá

## 1. Reproductor en vivo propio — IMPLEMENTADO (rama `vivo`)

~~Revisitar lo que `vista_previa.js` descartó...~~ Ese razonamiento era
falso: no hacía falta reverse-engineerear `#EXT-X-MOUFLON`. El Chromium
del supervisor ya resuelve esas URIs y el detector ya escribe los
segmentos fMP4/CMAF resultantes en `datos\streams\`. Se implementó un
reproductor con `MediaSource` nativo que lee esos bytes directamente
(sin hls.js, sin playlist, sin tocar nunca la ofuscación): `nimbo\mp4.py`
(codec string del init), `nimbo\vivo.py` (listar/leer segmentos, dedup,
arranque seguro en keyframe), rutas `/api/vivo/*` en `servidor.py`,
campo `sesiones` agregado por `puente.py`, y `nimbo\web\js\
reproductor_vivo.js` + `vista_previa.js` del lado del navegador. Incluye
"ver en vivo" temporal (`vivo_temporal.py`) para modelos online que
todavía no se están grabando. Ver `CLAUDE.md`, sección "Reproductor en
vivo", para el detalle completo. Verificado con QA de navegador real
(`pruebas\qa_vivo.mjs`): video decodificando de verdad, con rotación de
sesión incluida.

## 2. Watchdog activo del túnel huérfano

Hoy solo se sabe que `wireproxy.exe` puede quedar huérfano si se para
muy rápido después de arrancar. Implementar detección real en
`motor.py` (buscar proceso por nombre+puerto que no sea hijo de nadie
vivo, matarlo y relanzar el túnel) en vez de confiar solo en la
autocuración de `_Tunel`.

## 3. Multi-perfil / multi-cuenta en paralelo

Correr varias instancias de `lanzador_supervisor.py` con perfiles de
Playwright distintos (varias cuentas del sitio a la vez). El problema
duro no es lanzar los procesos sino que `grabar.txt` deja de tener un
solo escritor implícito — hay que meter locking real (hoy `lista.py`
asume que es dueño exclusivo) y decidir cómo se reparten
`estado\supervisor.json` entre instancias.

## 4. Validación de integridad en vivo de los segmentos

Usar `ffprobe` sobre los fMP4 a medida que se escriben para detectar
drops/corrupción de timestamps y disparar una reconexión antes de que
termine el stream, en vez de descubrir un archivo roto recién al
reproducirlo.

## 5. Pasar de `ThreadingHTTPServer` a asyncio con websockets

El SSE actual es unidireccional y por hilo; para soportar push
bidireccional real (ej. cancelar un refresco de imagen en curso,
multiplexar más eventos sin saturar hilos) hay que reescribir
`servidor.py` sobre un loop asyncio, tocando motor/SSE/API a la vez —
es el cambio de mayor superficie de los cinco.
