// Motor de reproduccion en vivo: lee los segmentos que detectar_m3u8.py
// ya escribio en datos\streams\<sesion>\ (ver nimbo/vivo.py) y los va
// appendeando a un <video> real via MediaSource -- sin playlist, sin
// hls.js, sin tocar nunca la ofuscacion #EXT-X-MOUFLON del sitio (el
// Chromium del supervisor ya la resolvio; este modulo solo consume los
// bytes que eso dejo en disco). Ver CLAUDE.md, seccion "Reproductor en
// vivo", para el porque completo.
//
// No sabe nada de modales, de grabar.txt ni de favoritas: recibe un
// <video> y una "sesion" (la ruta relativa que devuelve /api/vivo/*) y
// solo hace MSE. El dueno del ciclo de vida (que sesion mostrar, cuando
// cerrar) es vista_previa.js.
//
// Cuatro decisiones que no son obvias, documentadas donde importan en el
// codigo de abajo: SourceBuffer.mode = "sequence" (_abrirSesion), la cola
// unica de operaciones (_bombear), el arranque siempre en un limite de
// segmento (lo resuelve el propio servidor via ?atras=, no este archivo),
// y el reloj del tick en un Web Worker (igual que vista_previa.js).

import { conToken } from "./api.js";

const INTERVALO_TICK_MS = 500;
const SEGMENTOS_ATRAS = 3;
const MAX_POR_TICK = 8;
const VENTANA_MAX_S = 90;
const VENTANA_OBJETIVO_S = 45;
const DERIVA_MAX_S = 6.0;
// Cuanto se deja el playhead atras del borde del buffer al saltar al vivo
// (alVivo() y la correccion de deriva). Con 1.0 el margen era mas chico
// que el tiempo que tarda un ciclo de tick+fetch+append en traer el
// proximo segmento (poll de 500ms + red + disco): el playhead alcanzaba
// el borde del SourceBuffer antes de que llegara mas data y el video se
// quedaba pegado por una fraccion de segundo, aunque la grabacion en
// disco siguiera perfecta (son pipelines independientes). 3.0 da colchon
// de sobra sin que se note como "delay" en una previa.
const MARGEN_VIVO_S = 3.0;
const INTENTOS_CUOTA = 3;
// "sequence": el navegador pega cada grupo de frames inmediatamente detras
// del anterior, ignorando los tfdt del fragmento -- es el mismo trabajo que
// hace el Reencadenador de detectar_m3u8.py (detectar_m3u8.py:1142) del lado
// del grabador, porque esos timestamps vienen sucios a proposito (un
// reinicio de emision los vuelve a cero, una parte perdida salta hacia
// adelante). Con "segments" habria que sembrar video.currentTime en
// buffered.start(0) o no se ve nada -- el error mas comun de MSE.
const MODO_SB = "sequence";

async function obtenerBytes(ruta) {
  const r = await fetch(conToken(ruta));
  if (!r.ok) throw new Error(ruta + " -> " + r.status);
  return r.arrayBuffer();
}

async function obtenerJSON(ruta) {
  const r = await fetch(conToken(ruta));
  if (!r.ok) throw new Error(ruta + " -> " + r.status);
  return r.json();
}

export function crearReproductor(video, { alErrar, alCambiarEstado } = {}) {
  const avisarError = alErrar || (() => {});
  const avisarEstado = alCambiarEstado || (() => {});

  let mediaSource = null;
  let sourceBuffer = null;
  let sesion = null;
  let cursor = null;
  let saltoPendiente = false;
  let cola = [];               // [{tipo:"append"|"remove", ...}]
  let destruido = false;

  const worker = new Worker(new URL("./temporizador_previa.worker.js", import.meta.url));
  worker.onmessage = (ev) => {
    if (ev.data === "tick") _tick();
  };

  function _pararTick() {
    worker.postMessage({ accion: "parar" });
  }

  function _arrancarTick() {
    worker.postMessage({ accion: "arrancar", ms: INTERVALO_TICK_MS });
  }

  // -- la cola unica: appendBuffer y remove comparten el flag "updating"
  // del SourceBuffer -- dos operaciones solapadas tiran InvalidStateError,
  // asi que nada se llama directo, todo pasa por aca. --------------------

  function _bombear() {
    if (!sourceBuffer || sourceBuffer.updating || cola.length === 0) return;
    const op = cola.shift();
    try {
      if (op.tipo === "append") sourceBuffer.appendBuffer(op.datos);
      else sourceBuffer.remove(op.inicio, op.fin);
    } catch (e) {
      if (e.name === "QuotaExceededError" && op.tipo === "append") {
        _manejarCuota(op);
      } else {
        avisarError(e);
      }
    }
  }

  function _encolarAppend(datos) {
    cola.push({ tipo: "append", datos });
    _bombear();
  }

  function _encolarRemove(inicio, fin) {
    if (fin <= inicio) return;
    cola.push({ tipo: "remove", inicio, fin });
    _bombear();
  }

  function _manejarCuota(op) {
    const intentos = (op.intentos || 0) + 1;
    if (intentos > INTENTOS_CUOTA) {
      _reiniciar("cuota");
      return;
    }
    if (sourceBuffer.buffered.length === 0) { _reiniciar("cuota"); return; }
    const ini = sourceBuffer.buffered.start(0);
    const limite = Math.max(ini + 1, video.currentTime - 10);
    cola.unshift({ ...op, intentos });
    cola.unshift({ tipo: "remove", inicio: ini, fin: limite });
    _bombear();
  }

  // ventana de memoria: recortar lo viejo en cada updateend, nunca mas
  // alla de 10s detras de donde esta mirando el usuario.
  function _recortar() {
    if (!sourceBuffer || sourceBuffer.buffered.length === 0) return;
    const ini = sourceBuffer.buffered.start(0);
    const fin = sourceBuffer.buffered.end(0);
    if (fin - ini <= VENTANA_MAX_S) return;
    const nuevoInicio = Math.min(fin - VENTANA_OBJETIVO_S, video.currentTime - 10);
    if (nuevoInicio > ini) _encolarRemove(ini, nuevoInicio);
  }

  function _vigilarDeriva() {
    if (!sourceBuffer || sourceBuffer.buffered.length === 0) return;
    const fin = sourceBuffer.buffered.end(0);
    if (fin - video.currentTime > DERIVA_MAX_S) {
      video.currentTime = Math.max(0, fin - MARGEN_VIVO_S);
    }
  }

  function alVivo() {
    if (!sourceBuffer || sourceBuffer.buffered.length === 0) return;
    video.currentTime = Math.max(0, sourceBuffer.buffered.end(0) - MARGEN_VIVO_S);
    video.play().catch(() => {});
  }

  function silenciar(mudo) {
    video.muted = mudo;
  }

  // mover el slider siempre saca de mudo (si estaba) -- es el gesto
  // esperado: "subir volumen" tiene que sonar, sin un segundo click en
  // el boton de mute.
  function volumen(v) {
    const clamp = Math.max(0, Math.min(1, v));
    video.volume = clamp;
    video.muted = clamp <= 0;
  }

  // -- ciclo de vida -------------------------------------------------------

  async function _tick() {
    if (!sesion || !sourceBuffer || destruido) return;
    try {
      const desde = saltoPendiente ? null : cursor;
      saltoPendiente = false;
      const r = await obtenerJSON(
        "/api/vivo/segmentos?sesion=" + encodeURIComponent(sesion)
        + "&desde=" + (desde == null ? "" : encodeURIComponent(desde))
        + "&limite=" + MAX_POR_TICK + "&atras=" + SEGMENTOS_ATRAS);
      if (!r || sesion === null) return;    // se desmonto mientras esperaba
      for (const seg of r.segmentos || []) {
        const bytes = await obtenerBytes(
          "/api/vivo/segmento?sesion=" + encodeURIComponent(sesion)
          + "&nombre=" + encodeURIComponent(seg.nombre));
        if (sesion === null) return;
        _encolarAppend(bytes);
      }
      cursor = r.cursor;
      if (r.salto) saltoPendiente = true;   // mucho backlog: la proxima
                                             // pide desde el borde vivo,
                                             // no sigue tratando de alcanzar
      _recortar();
      _vigilarDeriva();
      avisarEstado({ vivo: !!r.vivo });
    } catch (e) {
      avisarError(e);
    }
  }

  function _reiniciar(motivo) {
    const s = sesion;
    desmontar();
    if (s) montar(s);
    avisarEstado({ reiniciado: motivo });
  }

  async function montar(sesionNueva) {
    if (sesion === sesionNueva) return;
    desmontar();
    sesion = sesionNueva;
    cola = [];
    cursor = null;
    saltoPendiente = false;

    let perfil;
    try {
      perfil = await obtenerJSON(
        "/api/vivo/segmentos?sesion=" + encodeURIComponent(sesion)
        + "&limite=" + MAX_POR_TICK + "&atras=" + SEGMENTOS_ATRAS);
    } catch (e) {
      avisarError(e);
      sesion = null;
      return;
    }
    if (sesion === null) return;   // se desmonto mientras esperaba

    if (!perfil.mime || !perfil.codecs) {
      avisarError(new Error("sin perfil de codec (el init todavia no llego)"));
      sesion = null;
      return;
    }
    if (!window.MediaSource || !MediaSource.isTypeSupported(perfil.mime)) {
      avisarError(new Error("codec no soportado por este navegador: " + perfil.mime));
      sesion = null;
      return;
    }

    // NO se fuerza video.muted=true aca: el <video> es un elemento
    // persistente (vive siempre en el DOM, nunca se recrea), asi que su
    // .muted/.volume ya sobreviven solos a traves de remontajes --
    // forzarlo en cada montar() pisaba la eleccion del usuario en cada
    // rotacion de calidad, silenciosamente. El atributo "muted" del HTML
    // ya cubre el autoplay inicial (primera vez que se abre el modal,
    // sin gesto del usuario todavia); de ahi en mas, .play() se llama
    // siempre dentro de la cadena de un click (abrir, cambiar variante)
    // o de un remontaje automatico que Chrome permite sin mute una vez
    // que la pagina ya tuvo sonido con gesto real -- y si llegara a
    // fallar, el .catch(()=>{}) de mas abajo no rompe nada, el usuario
    // solo tiene que tocar "sonido" una vez mas.
    mediaSource = new MediaSource();
    video.src = URL.createObjectURL(mediaSource);

    mediaSource.addEventListener("sourceopen", async () => {
      if (sesion !== sesionNueva || !mediaSource) return;
      try {
        sourceBuffer = mediaSource.addSourceBuffer(perfil.mime);
        sourceBuffer.mode = MODO_SB;
        sourceBuffer.addEventListener("updateend", () => { _recortar(); _bombear(); });
        sourceBuffer.addEventListener("error", (ev) => avisarError(ev));

        const initBytes = await obtenerBytes(
          "/api/vivo/init?sesion=" + encodeURIComponent(sesion));
        if (sesion !== sesionNueva) return;
        _encolarAppend(initBytes);
        for (const seg of perfil.segmentos || []) {
          const bytes = await obtenerBytes(
            "/api/vivo/segmento?sesion=" + encodeURIComponent(sesion)
            + "&nombre=" + encodeURIComponent(seg.nombre));
          if (sesion !== sesionNueva) return;
          _encolarAppend(bytes);
        }
        cursor = perfil.cursor;
        _arrancarTick();
        avisarEstado({ cargando: false });
        video.play().catch(() => {});
      } catch (e) {
        avisarError(e);
      }
    }, { once: true });

    avisarEstado({ cargando: true });
  }

  function desmontar() {
    _pararTick();
    cola = [];
    try {
      if (sourceBuffer && sourceBuffer.updating) sourceBuffer.abort();
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (mediaSource && sourceBuffer && mediaSource.readyState === "open") {
        mediaSource.removeSourceBuffer(sourceBuffer);
      }
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (mediaSource && mediaSource.readyState === "open") mediaSource.endOfStream();
    } catch (e) { /* ya cerrado, no importa */ }
    video.pause();
    if (video.src) { URL.revokeObjectURL(video.src); }
    video.removeAttribute("src");
    video.load();
    mediaSource = null;
    sourceBuffer = null;
    sesion = null;
    cursor = null;
  }

  function destruir() {
    destruido = true;
    desmontar();
    worker.terminate();
  }

  return { montar, desmontar, alVivo, silenciar, volumen, sesionActual: () => sesion, destruir };
}
