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
// Cinco decisiones que no son obvias, documentadas donde importan en el
// codigo de abajo: SourceBuffer.mode = "sequence" (MODO_SB), el append de
// un grupo (epoch, secuencia) entero y no de cada parte por separado
// (_agrupar / _appendearGrupos), la cola unica de operaciones (_bombear),
// el arranque siempre en un limite de segmento (lo resuelve el propio
// servidor via ?atras=, no este archivo), y el reloj del tick en un Web
// Worker (igual que vista_previa.js).
//
// SPEC 18 (hallazgo tardio, posterior a las cinco de arriba): chaturbate
// grava video y audio como DOS streams HLS separados (ver
// grabador_chaturbate.py, docstring), asi que la previa de una sesion de
// chaturbate puede traer una SEGUNDA pista -- su propia carpeta, su propio
// init, sus propios segmentos. Eso se modela como una segunda "pista"
// (_nuevaPista("audio")) con su propio SourceBuffer adentro del MISMO
// MediaSource: dos SourceBuffer independientes, cada uno en mode=
// "sequence", es el patron estandar de MSE para audio+video sin muxar
// (lo mismo que hace hls.js con una pista de audio alternativa). El audio
// es siempre mejor-esfuerzo: cualquier fallo suyo (init que no llega,
// codec no soportado, chunklist que da error) deja la previa en silencio,
// nunca tira el video -- mismo criterio que _mezclar_audio() del lado del
// grabador (mudo es mejor que nada).

import { conToken, bandera } from "./api.js";

const INTERVALO_TICK_MS = 500;
const SEGMENTOS_ATRAS = 3;
// Tiene que cubrir SEGMENTOS_ATRAS grupos enteros, no 3 archivos: un
// segmento CMAF "standard" del camino directo trae varios moof pegados
// que grabador_directo.py parte en <nombre>_partN, asi que un grupo son
// ~4 archivos. Con 8 el servidor nunca llegaba a mandar los 3 grupos del
// arranque y contestaba salto=true en cada tick (ver vivo.py,
// GRUPOS_BACKLOG_SALTO).
const MAX_POR_TICK = 24;
// Cuantos ticks seguidos fallando se toleran antes de avisar el error de
// verdad (que vuelve al snapshot, ver vista_previa.js:_volverASnapshot).
// Antes UN solo 404/hipo de red en /api/vivo/segmentos tiraba toda la
// reproduccion -- con esto un hipo transitorio de 1-1.5s (2-3 ticks de
// 500ms) no se nota.
const TOLERANCIA_FALLOS = 3;
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
// "sequence": el navegador pega cada append inmediatamente detras del
// anterior, ignorando los tfdt del fragmento -- es el mismo trabajo que
// hace el Reencadenador de detectar_m3u8.py (detectar_m3u8.py:1142) del
// lado del grabador.
//
// OJO con el porque: NO es que los timestamps vengan sucios dentro de una
// sesion (eso decia este comentario y es falso, medido el 2026-10-25 con
// pruebas\drift.py: 0 discontinuidades en 240 fragmentos salvo las que
// deja una parte REALMENTE perdida). Un reinicio de emision tampoco los
// ensucia: crea una carpeta nueva con init nuevo, y el cliente la remonta
// desde cero. Lo que si pasa es lo otro: una parte perdida salta hacia
// adelante, y "sequence" cose ese hueco sin que haya que vigilarlo.
//
// El precio de "sequence" es que recalcula el timestampOffset en CADA
// appendBuffer, tomando el final de presentacion mas alto de las pistas
// del append: como una parte de 0,5 s termina en 0,5000 s de video pero
// en 0,4907 o 0,5120 s de audio (el AAC va en bloques de 1024 muestras),
// cada append realinea sobre una pista y le deja a la otra unos ms de
// hueco o solape. Por eso se appendea el GRUPO entero (ver
// _appendearGrupos): dentro de un append Chrome respeta la cadena tfdt
// interna, asi que la compensacion del encoder -- que es periodica y vive
// en la linea de tiempo absoluta -- se cancela dentro del segmento de 2 s
// en vez de quedar colgada en cada parte.
//
// El arreglo de fondo es mode="segments" (specs, reproductor_vivo_
// ticks.md 4.2): necesita sembrar video.currentTime en
// buffered.start(0) -- el error mas comun de MSE -- y un vigilante que
// salte los huecos reales, que "sequence" hoy tapa.
const MODO_SB = "sequence";

// -- diagnostico y variantes, desde la consola de la ventana de Nimbo ----
//
// localStorage.nimbo_diag_vivo = "1"
//     loguea cada DIAG_CADA_TICKS ticks los numeros que pide la puerta 1
//     del spec: cuantos rangos tiene sourceBuffer.buffered, el tamano de
//     los huecos entre rangos y cuanto se infla la linea de tiempo contra
//     el reloj (ver _medir: eso ultimo es LA metrica de la realineacion,
//     porque Chrome fusiona los huecos de pocos ms y buffered.length se
//     queda en 1). Se lee en cada tick, no al cargar el modulo: asi se
//     prende y se apaga desde la consola sin recargar la ventana (son dos
//     getItem por segundo, no cuesta nada). Mide solo la pista de VIDEO --
//     es la que importa para el calibre de "sequence" (ver MODO_SB); la
//     de audio, cuando existe, no tiene diagnostico propio.
//
// localStorage.nimbo_append_por_grupo = "0"
//     vuelve al append parte por parte de antes del 2026-10-25, para
//     poder comparar las dos variantes sobre la MISMA modelo (plan de
//     medicion, specs\reproductor_vivo_ticks.md 8). No es un fallback:
//     es el "antes" del experimento. Se lee en montar(), asi que cambiarlo
//     toma efecto en el proximo remontaje (cerrar y abrir la previa), no
//     en medio de una sesion -- mezclar las dos variantes en el mismo
//     SourceBuffer no mediria ninguna de las dos. Se aplica por igual a
//     las dos pistas cuando hay audio.
// (la implementacion vive en api.js:bandera -- misma lectura, un solo
// lugar, porque vista_grabando/vista_modelos tambien la necesitan)
const _bandera = bandera;
const DIAG_CADA_TICKS = 20;    // 20 x 500 ms = una linea cada 10 s

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

  // Una "pista" agrupa todo el estado mutable de UN SourceBuffer: cual
  // sesion (rel de disco) le da datos, el SourceBuffer en si, su cola de
  // appendBuffer/remove (comparten el flag "updating", nunca se llama
  // directo), el cursor de paginacion, la ultima clave appendeada (para
  // deduplicar, ver _clave/_posterior) y el init ya puesto (para empalmar
  // una rotacion sin remontar, ver _empalmar). pv (video) existe siempre
  // que hay algo montado; pa (audio) es null salvo que la sesion actual
  // de chaturbate declare una pista de audio hermana.
  function _nuevaPista(tipo) {
    return {
      tipo,              // "video" | "audio" -- solo para logs/diag
      sesion: null,      // rel de ESTA pista (video y audio tienen cada una la suya)
      sourceBuffer: null,
      cola: [],
      cursor: null,
      saltoPendiente: false,
      ultimaClave: null,
      initActual: null,
    };
  }

  let mediaSource = null;
  let pv = _nuevaPista("video");
  let pa = null;                // _nuevaPista("audio") si esta sesion tiene audio
  let destruido = false;
  let tickEnVuelo = false;     // ver _tick(): el worker no espera al await
  let fallosSeguidos = 0;      // ver TOLERANCIA_FALLOS en _tick()
  let diag = _diagNuevo();     // solo se llena si nimbo_diag_vivo (_medir) -- solo video
  let rangosPrevios = 0;
  let appendPorGrupo = true;   // se fija en montar(), ver _bandera

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

  // -- la cola unica de CADA pista: appendBuffer y remove comparten el
  // flag "updating" del SourceBuffer -- dos operaciones solapadas tiran
  // InvalidStateError, asi que nada se llama directo, todo pasa por aca.
  // video y audio tienen cada una su propia cola: son dos SourceBuffer
  // independientes, con su propio "updating". --------------------------

  function _bombear(p) {
    if (!p.sourceBuffer || p.sourceBuffer.updating || p.cola.length === 0) return;
    const op = p.cola.shift();
    try {
      if (op.tipo === "append") p.sourceBuffer.appendBuffer(op.datos);
      else p.sourceBuffer.remove(op.inicio, op.fin);
    } catch (e) {
      if (e.name === "QuotaExceededError" && op.tipo === "append") {
        _manejarCuota(p, op);
      } else {
        avisarError(e);
      }
    }
  }

  function _encolarAppend(p, datos) {
    p.cola.push({ tipo: "append", datos });
    _bombear(p);
  }

  function _encolarRemove(p, inicio, fin) {
    if (fin <= inicio) return;
    p.cola.push({ tipo: "remove", inicio, fin });
    _bombear(p);
  }

  function _manejarCuota(p, op) {
    const intentos = (op.intentos || 0) + 1;
    if (intentos > INTENTOS_CUOTA) {
      _reiniciar("cuota");
      return;
    }
    if (p.sourceBuffer.buffered.length === 0) { _reiniciar("cuota"); return; }
    const ini = p.sourceBuffer.buffered.start(0);
    const limite = Math.max(ini + 1, video.currentTime - 10);
    p.cola.unshift({ ...op, intentos });
    p.cola.unshift({ tipo: "remove", inicio: ini, fin: limite });
    _bombear(p);
  }

  // ventana de memoria: recortar lo viejo en cada updateend, nunca mas
  // alla de 10s detras de donde esta mirando el usuario. Se aplica a cada
  // pista por separado (cada SourceBuffer acumula a su propio ritmo).
  function _recortar(p) {
    if (!p.sourceBuffer || p.sourceBuffer.buffered.length === 0) return;
    const ini = p.sourceBuffer.buffered.start(0);
    const fin = p.sourceBuffer.buffered.end(0);
    if (fin - ini <= VENTANA_MAX_S) return;
    const nuevoInicio = Math.min(fin - VENTANA_OBJETIVO_S, video.currentTime - 10);
    if (nuevoInicio > ini) _encolarRemove(p, ini, nuevoInicio);
  }

  // la deriva y "al vivo" se miden siempre contra la pista de VIDEO: es
  // la que de verdad gobierna donde esta parado el <video> (el audio, si
  // se queda corto o se adelanta unos ms, lo tapa el propio navegador
  // mientras las dos pistas tengan algo bufferizado en ese instante).
  function _vigilarDeriva() {
    if (!pv.sourceBuffer || pv.sourceBuffer.buffered.length === 0) return;
    const fin = pv.sourceBuffer.buffered.end(0);
    if (fin - video.currentTime > DERIVA_MAX_S) {
      video.currentTime = Math.max(0, fin - MARGEN_VIVO_S);
    }
  }

  function alVivo() {
    if (!pv.sourceBuffer || pv.sourceBuffer.buffered.length === 0) return;
    video.currentTime = Math.max(0, pv.sourceBuffer.buffered.end(0) - MARGEN_VIVO_S);
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

  // "<epoch>:<secuencia>:<parte>" -> [n,n,n], el mismo orden que usa
  // vivo.py:clave_segmento. Sirve para no appendear dos veces lo mismo:
  // en mode="sequence" un duplicado NO se detecta por timestamp, se pega
  // atras del anterior como si fuera contenido nuevo.
  function _clave(cursor) {
    if (!cursor) return null;
    const p = String(cursor).split(":");
    if (p.length !== 3) return null;
    const n = p.map(Number);
    return n.some(Number.isNaN) ? null : n;
  }

  function _posterior(a, b) {      // a > b, lexicografico
    if (!b) return true;
    if (!a) return false;
    for (let i = 0; i < 3; i++) {
      if (a[i] !== b[i]) return a[i] > b[i];
    }
    return false;
  }

  // -- append por grupo ----------------------------------------------------
  //
  // Un "segmento" del camino directo son ~4 archivos: grabador_directo.py
  // parte los CMAF multi-moof en <nombre>_partN (ver
  // mouflon.partir_fragmentos), asi que la unidad real es el grupo
  // (epoch, secuencia), no el archivo. Appendear parte por parte le daba
  // a mode="sequence" ~2 realineaciones por segundo, cada una sobre un
  // par de pistas que no termina en el mismo instante (ver MODO_SB);
  // appendear el grupo entero las baja a una por borde de segmento y deja
  // que la compensacion periodica del encoder se cancele adentro del
  // append.

  function _agrupar(segmentos) {
    const grupos = [];
    for (const seg of segmentos) {
      const clave = _clave(seg.cursor);
      const epoch = clave ? clave[0] : seg.epoch;
      const secuencia = clave ? clave[1] : seg.secuencia;
      const ultimo = grupos[grupos.length - 1];
      if (ultimo && ultimo.epoch === epoch && ultimo.secuencia === secuencia) {
        ultimo.partes.push(seg);
      } else {
        grupos.push({ epoch, secuencia, partes: [seg] });
      }
    }
    return grupos;
  }

  function _concatenar(trozos) {
    if (trozos.length === 1) return trozos[0];
    let total = 0;
    for (const t of trozos) total += t.byteLength;
    const salida = new Uint8Array(total);
    let fin = 0;
    for (const t of trozos) {
      salida.set(new Uint8Array(t), fin);
      fin += t.byteLength;
    }
    return salida;
  }

  // -> cursor de lo ultimo que se dio por consumido, o null si no se
  // consumio nada. El llamador NUNCA debe avanzar el cursor mas alla de
  // eso: el ultimo grupo de la respuesta puede quedar en espera. `p` es
  // la pista (video o audio) a la que van estos segmentos -- el diag de
  // la puerta 1 solo cuenta lo que entra a la pista de video (ver su
  // docstring arriba: el calibre de "sequence" asume 0,5s de VIDEO por
  // parte, mezclar los dos contaria doble).
  async function _appendearGrupos(p, mia, segmentos) {
    const grupos = _agrupar(segmentos);
    let cursorNuevo = null;
    for (let i = 0; i < grupos.length; i++) {
      const g = grupos[i];
      // Un grupo se da por completo si ya llego el siguiente, o si es un
      // segmento entero sin partes (camino del supervisor: un archivo).
      // El ultimo grupo con partes NO se appendea: las 4 partes se
      // escriben en rafaga pero el tick puede caer en el medio, y medio
      // grupo appendeado es exactamente la realineacion que se quiere
      // evitar. Cuesta como maximo un grupo (~2 s) de latencia, holgado
      // contra MARGEN_VIVO_S.
      const entero = g.partes.every((seg) => seg.parte == null);
      if (i === grupos.length - 1 && !entero) break;

      const ultimaParte = g.partes[g.partes.length - 1];
      const claveMin = _clave(g.partes[0].cursor);
      const claveMax = _clave(ultimaParte.cursor);
      // Ya appendeado: la ventana que devuelve un "salto" se solapa con lo
      // viejo. El cursor avanza, los bytes no se repiten -- en
      // mode="sequence" un duplicado no se detecta por timestamp, se pega
      // atras del anterior como si fuera contenido nuevo.
      if (claveMin && !_posterior(claveMin, p.ultimaClave)) {
        cursorNuevo = ultimaParte.cursor;
        continue;
      }

      const trozos = [];
      let faltante = false;
      for (const seg of g.partes) {
        try {
          trozos.push(await obtenerBytes(
            "/api/vivo/segmento?sesion=" + encodeURIComponent(mia)
            + "&nombre=" + encodeURIComponent(seg.nombre)));
        } catch (e) {
          // Un 404 de una sola parte (el detector roto la sesion, o
          // cosecha_streams.py barrio la carpeta) ya no va a aparecer
          // nunca: insistir no sirve, y antes esto se propagaba hasta
          // _tick() y volaba TODA la reproduccion de vuelta al snapshot
          // por un hipo transitorio. Se abandona este grupo y se avanza
          // el cursor -- no se pierde nada mas que el (como mucho) medio
          // segundo que cubria.
          faltante = true;
          break;
        }
        if (p.sesion !== mia) return null;   // se desmonto/roto mientras bajaba
      }
      if (faltante) {
        cursorNuevo = ultimaParte.cursor;
        if (claveMax) p.ultimaClave = claveMax;
        continue;
      }
      _encolarAppend(p, _concatenar(trozos));
      if (p === pv) { diag.appends += 1; diag.partes += trozos.length; }
      if (claveMax) p.ultimaClave = claveMax;
      cursorNuevo = ultimaParte.cursor;
    }
    return cursorNuevo;
  }

  // El "antes" del experimento (localStorage.nimbo_append_por_grupo="0"):
  // un appendBuffer por archivo. Mismo contrato de retorno.
  async function _appendearPartes(p, mia, segmentos) {
    let cursorNuevo = null;
    for (const seg of segmentos) {
      const clave = _clave(seg.cursor);
      // clave null = cursor con un formato que este cliente no entiende:
      // mejor appendear sin poder deduplicar que quedarse en negro.
      if (clave && !_posterior(clave, p.ultimaClave)) {
        cursorNuevo = seg.cursor;
        continue;
      }
      let bytes;
      try {
        bytes = await obtenerBytes(
          "/api/vivo/segmento?sesion=" + encodeURIComponent(mia)
          + "&nombre=" + encodeURIComponent(seg.nombre));
      } catch (e) {
        // mismo criterio que _appendearGrupos: un 404 no vuelve, se
        // salta este archivo en vez de tirar toda la reproduccion.
        if (clave) p.ultimaClave = clave;
        cursorNuevo = seg.cursor;
        continue;
      }
      if (p.sesion !== mia) return null;
      _encolarAppend(p, bytes);
      if (p === pv) { diag.appends += 1; diag.partes += 1; }
      if (clave) p.ultimaClave = clave;
      cursorNuevo = seg.cursor;
    }
    return cursorNuevo;
  }

  async function _appendear(p, mia, segmentos) {
    return appendPorGrupo
      ? _appendearGrupos(p, mia, segmentos)
      : _appendearPartes(p, mia, segmentos);
  }

  // -- diagnostico: los dos numeros de la puerta 1 -------------------------
  // (solo la pista de video -- ver su comentario arriba, cerca de
  // _nuevaPista)

  function _diagNuevo() {
    return { ticks: 0, appends: 0, partes: 0, rangos: 0, conHuecos: 0,
             huecosNuevos: 0, huecoMax: 0, desde: Date.now(),
             finBase: null, relojBase: 0, partesBase: 0,
             inflacion: 0, avance: 0 };
  }

  function _medir() {
    if (!pv.sourceBuffer || !_bandera("nimbo_diag_vivo", false)) return;
    diag.ticks += 1;
    try {
      const b = pv.sourceBuffer.buffered;
      const rangos = b.length;
      diag.rangos += rangos;
      if (rangos > 1) {
        diag.conHuecos += 1;
        for (let i = 1; i < rangos; i++) {
          const hueco = b.start(i) - b.end(i - 1);
          if (hueco > diag.huecoMax) diag.huecoMax = hueco;
        }
      }
      // los huecos NUEVOS, no los que siguen abiertos de antes: un hueco
      // ya contado se ve igual en cada tick hasta que _recortar lo tira.
      if (rangos > rangosPrevios) diag.huecosNuevos += rangos - rangosPrevios;
      rangosPrevios = rangos;

      // Como se ve la realineacion de mode="sequence" desde el cliente.
      // NO se ve en buffered.length: Chrome fusiona los rangos separados
      // por pocos ms (su "fudge factor"), asi que el buffer sigue siendo
      // un solo rango aunque cada append deje ~9 ms de hueco en una
      // pista. Dos numeros, entonces:
      //
      // avance = cuanto avanza el borde del buffer por cada PARTE
      //   appendeada. Lo nominal son 0,5000 s exactos de video (medido
      //   con pruebas\drift.py: la pista de video siempre trae 45000/
      //   90000). Si "sequence" realineara sobre el final de presentacion
      //   mas alto del append, las partes con audio largo (0,5120 s)
      //   empujarian la linea de tiempo y esto daria ~0,505. Es la
      //   metrica buena: no la afecta la perdida de segmentos.
      //
      // inflacion = lo mismo contra el reloj, que si la afecta -- un
      //   stream en vivo avanza 1 s de media por 1 s de reloj, asi que
      //   un valor negativo es perdida real del grabador (los huecos de
      //   secuencia que mide pruebas\huecos.py), no realineacion.
      // el calibre arranca despues del primer tramo de ticks: el arranque
      // trae SEGMENTOS_ATRAS grupos de una sola vez (el buffer crece de
      // golpe) y medir desde ahi contaminaria el porcentaje
      if (rangos && diag.ticks >= DIAG_CADA_TICKS) {
        const fin = b.end(rangos - 1);
        if (diag.finBase === null) {
          diag.finBase = fin;
          diag.relojBase = Date.now();
          diag.partesBase = diag.partes;
        } else {
          const reloj = (Date.now() - diag.relojBase) / 1000;
          const partes = diag.partes - diag.partesBase;
          diag.inflacion = (fin - diag.finBase) - reloj;
          diag.avance = partes ? (fin - diag.finBase) / partes : 0;
        }
      }
    } catch (e) {
      return;                  // SourceBuffer ya removido, no es un error
    }
    if (diag.ticks % DIAG_CADA_TICKS !== 0) return;
    const minutos = Math.max((Date.now() - diag.desde) / 60000, 1 / 60);
    console.log(
      "[vivo diag] " + pv.sesion
      + " modo=" + (appendPorGrupo ? "grupo" : "parte")
      + (pa ? " +audio" : "")
      + " ticks=" + diag.ticks
      + " appends=" + diag.appends + " partes=" + diag.partes
      + " rangos~" + (diag.rangos / diag.ticks).toFixed(2)
      + " ticks-con-hueco=" + Math.round(100 * diag.conHuecos / diag.ticks) + "%"
      + " huecos-nuevos/min=" + (diag.huecosNuevos / minutos).toFixed(1)
      + " hueco-max=" + diag.huecoMax.toFixed(3) + "s"
      + " avance/parte=" + diag.avance.toFixed(5) + "s"
      + " inflacion=" + diag.inflacion.toFixed(3) + "s"
      + " (" + (100 * diag.inflacion
                / Math.max((Date.now() - diag.relojBase) / 1000, 1)).toFixed(2)
      + "% del reloj)");
  }

  // pide/appendea lo nuevo de UNA pista. -> la respuesta de /api/vivo/
  // segmentos (para leer "vivo"/"salto" del lado que llama), o null si la
  // pista roto/desmonto mientras se esperaba la red.
  async function _tickPista(p, atras) {
    const mia = p.sesion;
    const desde = p.saltoPendiente ? null : p.cursor;
    p.saltoPendiente = false;
    const r = await obtenerJSON(
      "/api/vivo/segmentos?sesion=" + encodeURIComponent(mia)
      + "&desde=" + (desde == null ? "" : encodeURIComponent(desde))
      + "&limite=" + MAX_POR_TICK + "&atras=" + atras);
    if (!r || p.sesion !== mia) return null;
    const cursorNuevo = await _appendear(p, mia, r.segmentos || []);
    if (p.sesion !== mia) return null;
    if (cursorNuevo) p.cursor = cursorNuevo;
    if (r.salto) p.saltoPendiente = true;   // mucho backlog: la proxima
                                             // pide desde el borde vivo,
                                             // no sigue tratando de alcanzar
    return r;
  }

  async function _tick() {
    if (!pv.sesion || !pv.sourceBuffer || destruido) return;
    // El worker tiquea cada 500 ms sin esperar a que termine el tick
    // anterior, y `cursor` recien se actualiza al final: sin esta guarda
    // un tick lento (8+ segmentos de disco + HTTP) hace que el siguiente
    // arranque con el cursor viejo y vuelva a appendear lo mismo.
    if (tickEnVuelo) return;
    tickEnVuelo = true;
    // la sesion puede rotar (reconexion de encoder, cambio de calidad)
    // mientras este tick espera: comparar contra la de ESTE tick, no
    // contra null, o los bytes de la sesion vieja se appendean en el
    // SourceBuffer de la nueva.
    const mia = pv.sesion;
    try {
      const r = await _tickPista(pv, SEGMENTOS_ATRAS);
      if (!r || pv.sesion !== mia) return;

      // El audio es mejor-esfuerzo: un hipo en SU chunklist (token
      // vencido, 404 transitorio) no tiene que tirar la reproduccion de
      // video, que es la que de verdad importa -- se reintenta solo en
      // el proximo tick, 500 ms despues.
      if (pa && pa.sourceBuffer) {
        try {
          await _tickPista(pa, SEGMENTOS_ATRAS);
        } catch (e) { /* se reintenta en el proximo tick */ }
        if (pv.sesion !== mia) return;
      }

      _recortar(pv);
      if (pa) _recortar(pa);
      _vigilarDeriva();
      _medir();
      avisarEstado({ vivo: !!r.vivo });
      fallosSeguidos = 0;
    } catch (e) {
      fallosSeguidos += 1;
      if (fallosSeguidos >= TOLERANCIA_FALLOS) avisarError(e);
    } finally {
      tickEnVuelo = false;
    }
  }

  function _reiniciar(motivo) {
    const s = pv.sesion;
    const sa = pa ? pa.sesion : null;
    desmontar();
    if (s) montar(s, sa);
    avisarEstado({ reiniciado: motivo });
  }

  function _mismosBytes(a, b) {
    if (!a || !b || a.byteLength !== b.byteLength) return false;
    const x = new Uint8Array(a);
    const y = new Uint8Array(b);
    for (let i = 0; i < x.length; i++) {
      if (x[i] !== y[i]) return false;
    }
    return true;
  }

  // Rotacion de sesion: el encoder reconecta (o el detector cierra la
  // carpeta y abre otra) y aparece una sesion nueva de la MISMA variante.
  // Remontar eso cuesta un corte de varios segundos -- MediaSource nuevo,
  // init nuevo, arranque SEGMENTOS_ATRAS grupos atras -- y era el
  // artefacto mas visible de la previa (specs\reproductor_vivo_ticks.md
  // 5: lolykush roto 4 veces en 15 min).
  //
  // No hace falta: si el init de la sesion nueva es byte a byte igual al
  // que ya esta adentro del SourceBuffer, describe las mismas pistas con
  // los mismos timescales, asi que se puede seguir appendeando ahi mismo.
  // Medido con pruebas\inits.py sobre la instalacion real: 26 de 26
  // rotaciones de la misma variante tenian el init IDENTICO (los nombres
  // difieren siempre -- traen un id aleatorio del CDN -- por eso se
  // compara el contenido y no el nombre).
  //
  // Con audio (SPEC 18) las dos pistas rotan SIEMPRE juntas (grabador_
  // chaturbate.py cierra y abre video+audio en la misma sesion, ver
  // _cerrar_sesion/_abrir_sesion): si una pista empalma y la otra no (o
  // una tenia audio y la sesion nueva no, o viceversa) es mas seguro
  // remontar TODO de cero que dejar las dos pistas desincronizadas entre
  // si -- por eso las dos tienen que empalmar, o ninguna.
  //
  // -> true si empalmo (el llamador no tiene que hacer nada mas), false
  // si hay que remontar de cero (init distinto: otra calidad u otro
  // codec, o la presencia de audio cambio entre una rotacion y la
  // siguiente -- y ahi el corte es inevitable).
  async function _empalmar(sesionVideoNueva, sesionAudioNueva) {
    if (!mediaSource || !pv.sourceBuffer || !pv.initActual) return false;
    if (mediaSource.readyState !== "open") return false;
    const previaVideo = pv.sesion;
    let bytesVideo;
    try {
      bytesVideo = await obtenerBytes(
        "/api/vivo/init?sesion=" + encodeURIComponent(sesionVideoNueva));
    } catch (e) {
      return false;              // sin init todavia: remontar normal
    }
    if (pv.sesion !== previaVideo) return false;   // roto otra vez mientras esperaba
    if (!_mismosBytes(bytesVideo, pv.initActual)) return false;

    const teniaAudio = !!pa;
    const tendraAudio = !!sesionAudioNueva;
    if (teniaAudio !== tendraAudio) return false;   // aparicion/desaparicion del audio

    if (teniaAudio) {
      const previaAudio = pa.sesion;
      let bytesAudio;
      try {
        bytesAudio = await obtenerBytes(
          "/api/vivo/init?sesion=" + encodeURIComponent(sesionAudioNueva));
      } catch (e) {
        return false;
      }
      if (pa.sesion !== previaAudio) return false;
      if (!_mismosBytes(bytesAudio, pa.initActual)) return false;
    }

    // Las dos pistas (o la unica, si no hay audio) empalman: el
    // SourceBuffer sigue sirviendo tal cual, lo unico que cambia es de
    // donde salen los segmentos. En mode="sequence" lo nuevo se pega
    // atras de lo que ya estaba, asi que no hay hueco ni salto de reloj,
    // y el <video> no se entera. El tick que ya esta corriendo se encarga
    // del resto: con cursor en null pide la ventana de arranque de la
    // sesion nueva (que siempre empieza en un keyframe) en los proximos
    // 500 ms.
    pv.sesion = sesionVideoNueva;
    pv.cursor = null;
    pv.ultimaClave = null;
    pv.saltoPendiente = false;
    if (pa) {
      pa.sesion = sesionAudioNueva;
      pa.cursor = null;
      pa.ultimaClave = null;
      pa.saltoPendiente = false;
    }
    fallosSeguidos = 0;
    avisarEstado({ empalmada: sesionVideoNueva });
    return true;
  }

  // sesionAudioNueva es opcional: null/undefined si esta sesion no tiene
  // (o no se sabe si tiene) pista de audio -- la previa sigue andando
  // solo con video, exactamente como antes de SPEC 18.
  async function montar(sesionVideoNueva, sesionAudioNueva = null) {
    if (pv.sesion === sesionVideoNueva
        && (pa ? pa.sesion : null) === (sesionAudioNueva || null)) return;
    if (await _empalmar(sesionVideoNueva, sesionAudioNueva)) return;
    desmontar();
    pv.sesion = sesionVideoNueva;
    diag = _diagNuevo();
    rangosPrevios = 0;
    appendPorGrupo = _bandera("nimbo_append_por_grupo", true);

    let perfilVideo;
    try {
      perfilVideo = await obtenerJSON(
        "/api/vivo/segmentos?sesion=" + encodeURIComponent(pv.sesion)
        + "&limite=" + MAX_POR_TICK + "&atras=" + SEGMENTOS_ATRAS);
    } catch (e) {
      avisarError(e);
      pv.sesion = null;
      return;
    }
    if (pv.sesion === null) return;   // se desmonto mientras esperaba

    if (!perfilVideo.mime || !perfilVideo.codecs) {
      avisarError(new Error("sin perfil de codec (el init todavia no llego)"));
      pv.sesion = null;
      return;
    }
    if (!window.MediaSource || !MediaSource.isTypeSupported(perfilVideo.mime)) {
      avisarError(new Error("codec no soportado por este navegador: " + perfilVideo.mime));
      pv.sesion = null;
      return;
    }

    // El audio es mejor-esfuerzo (ver docstring del modulo): si su perfil
    // no esta disponible o el navegador no soporta su codec, la previa
    // sigue el camino de siempre -- solo video, en silencio -- en vez de
    // no reproducir nada.
    let perfilAudio = null;
    if (sesionAudioNueva) {
      try {
        const candidato = await obtenerJSON(
          "/api/vivo/segmentos?sesion=" + encodeURIComponent(sesionAudioNueva)
          + "&limite=" + MAX_POR_TICK + "&atras=" + SEGMENTOS_ATRAS);
        if (pv.sesion === null) return;   // se desmonto mientras esperaba
        if (candidato.mime && candidato.codecs && window.MediaSource
            && MediaSource.isTypeSupported(candidato.mime)) {
          perfilAudio = candidato;
        }
      } catch (e) { /* sin audio, la previa sigue silenciosa */ }
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

    const sesionVideoDeEsteMontaje = pv.sesion;

    mediaSource.addEventListener("sourceopen", async () => {
      if (pv.sesion !== sesionVideoDeEsteMontaje || !mediaSource) return;
      try {
        pv.sourceBuffer = mediaSource.addSourceBuffer(perfilVideo.mime);
        pv.sourceBuffer.mode = MODO_SB;
        pv.sourceBuffer.addEventListener("updateend", () => { _recortar(pv); _bombear(pv); });
        pv.sourceBuffer.addEventListener("error", (ev) => avisarError(ev));

        // Las dos pistas tienen que existir ANTES de appendear la primera
        // muestra. Chaturbate entrega CMAF de video y AAC por separado; si
        // se creaba el SourceBuffer de audio despues de poner a actualizar
        // el de video, Edge podia rechazarlo y el catch de mejor-esfuerzo
        // dejaba una previa silenciosa. Preparar ambos buffers primero evita
        // esa carrera y mantiene el fallback a solo video si el codec no
        // pudiera abrirse.
        let nueva = null;
        if (perfilAudio) {
          try {
            nueva = _nuevaPista("audio");
            nueva.sesion = sesionAudioNueva;
            nueva.sourceBuffer = mediaSource.addSourceBuffer(perfilAudio.mime);
            nueva.sourceBuffer.mode = MODO_SB;
            nueva.sourceBuffer.addEventListener(
              "updateend", () => { _recortar(nueva); _bombear(nueva); });
            nueva.sourceBuffer.addEventListener("error", (ev) => avisarError(ev));
          } catch (e) {
            nueva = null;
          }
        }

        const initBytesVideo = await obtenerBytes(
          "/api/vivo/init?sesion=" + encodeURIComponent(pv.sesion));
        if (pv.sesion !== sesionVideoDeEsteMontaje) return;
        _encolarAppend(pv, initBytesVideo);
        pv.initActual = initBytesVideo;    // para el empalme de una rotacion
        // el arranque pasa por el mismo camino que el tick (grupos
        // enteros, nunca medio grupo): si el ultimo grupo de la ventana
        // todavia se esta escribiendo, lo trae el primer tick.
        pv.cursor = await _appendear(pv, pv.sesion, perfilVideo.segmentos || []);
        if (pv.sesion !== sesionVideoDeEsteMontaje) return;

        if (nueva) {
          try {
            const initBytesAudio = await obtenerBytes(
              "/api/vivo/init?sesion=" + encodeURIComponent(nueva.sesion));
            if (pv.sesion !== sesionVideoDeEsteMontaje) return;
            _encolarAppend(nueva, initBytesAudio);
            nueva.initActual = initBytesAudio;
            nueva.cursor = await _appendear(nueva, nueva.sesion, perfilAudio.segmentos || []);
            if (pv.sesion !== sesionVideoDeEsteMontaje) return;
            pa = nueva;
          } catch (e) {
            // sin audio, la previa sigue silenciosa -- no tira el video
            pa = null;
          }
        } else {
          pa = null;
        }

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
    try {
      if (pv.sourceBuffer && pv.sourceBuffer.updating) pv.sourceBuffer.abort();
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (pa && pa.sourceBuffer && pa.sourceBuffer.updating) pa.sourceBuffer.abort();
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (mediaSource && pv.sourceBuffer && mediaSource.readyState === "open") {
        mediaSource.removeSourceBuffer(pv.sourceBuffer);
      }
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (mediaSource && pa && pa.sourceBuffer && mediaSource.readyState === "open") {
        mediaSource.removeSourceBuffer(pa.sourceBuffer);
      }
    } catch (e) { /* ya cerrado, no importa */ }
    try {
      if (mediaSource && mediaSource.readyState === "open") mediaSource.endOfStream();
    } catch (e) { /* ya cerrado, no importa */ }
    // _tick()/_appendear siguen una referencia a ESTE objeto pv/pa, no a la
    // variable del modulo: reasignarla mas abajo no las avisa. Un fetch que
    // ya estaba en vuelo (otro tick, u otra parte del mismo _appendearGrupos)
    // retoma despues del remove de arriba y, sin esto, llama appendBuffer
    // sobre un SourceBuffer ya removido del MediaSource -- "This SourceBuffer
    // has been removed from the parent media source", reportado como error
    // en vez de ser la carrera benigna que es. Con sourceBuffer=null, la
    // guarda que ya tiene _bombear() (`if (!p.sourceBuffer ...) return;`)
    // lo descarta en silencio, igual que el SourceBuffer-ya-removido de
    // _medir() un poco mas arriba.
    pv.sourceBuffer = null;
    if (pa) pa.sourceBuffer = null;
    video.pause();
    if (video.src) { URL.revokeObjectURL(video.src); }
    video.removeAttribute("src");
    video.load();
    mediaSource = null;
    pv = _nuevaPista("video");
    pa = null;
    fallosSeguidos = 0;
  }

  function destruir() {
    destruido = true;
    desmontar();
    worker.terminate();
  }

  return { montar, desmontar, alVivo, silenciar, volumen,
           sesionActual: () => pv.sesion, diagnostico: () => ({ ...diag }),
           destruir };
}
