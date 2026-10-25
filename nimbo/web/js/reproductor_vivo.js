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
//     getItem por segundo, no cuesta nada).
//
// localStorage.nimbo_append_por_grupo = "0"
//     vuelve al append parte por parte de antes del 2026-10-25, para
//     poder comparar las dos variantes sobre la MISMA modelo (plan de
//     medicion, specs\reproductor_vivo_ticks.md 8). No es un fallback:
//     es el "antes" del experimento. Se lee en montar(), asi que cambiarlo
//     toma efecto en el proximo remontaje (cerrar y abrir la previa), no
//     en medio de una sesion -- mezclar las dos variantes en el mismo
//     SourceBuffer no mediria ninguna de las dos.
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

  let mediaSource = null;
  let sourceBuffer = null;
  let sesion = null;
  let cursor = null;
  let saltoPendiente = false;
  let cola = [];               // [{tipo:"append"|"remove", ...}]
  let destruido = false;
  let tickEnVuelo = false;     // ver _tick(): el worker no espera al await
  let ultimaClave = null;      // [epoch, secuencia, parte] ya appendeado
  let diag = _diagNuevo();     // solo se llena si nimbo_diag_vivo (_medir)
  let rangosPrevios = 0;
  let appendPorGrupo = true;   // se fija en montar(), ver _bandera
  let initActual = null;       // el init que ya esta en el SourceBuffer

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
  // eso: el ultimo grupo de la respuesta puede quedar en espera.
  async function _appendearGrupos(mia, segmentos) {
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
      if (claveMin && !_posterior(claveMin, ultimaClave)) {
        cursorNuevo = ultimaParte.cursor;
        continue;
      }

      const trozos = [];
      for (const seg of g.partes) {
        trozos.push(await obtenerBytes(
          "/api/vivo/segmento?sesion=" + encodeURIComponent(mia)
          + "&nombre=" + encodeURIComponent(seg.nombre)));
        if (sesion !== mia) return null;   // se desmonto/roto mientras bajaba
      }
      _encolarAppend(_concatenar(trozos));
      diag.appends += 1;
      diag.partes += trozos.length;
      if (claveMax) ultimaClave = claveMax;
      cursorNuevo = ultimaParte.cursor;
    }
    return cursorNuevo;
  }

  // El "antes" del experimento (localStorage.nimbo_append_por_grupo="0"):
  // un appendBuffer por archivo. Mismo contrato de retorno.
  async function _appendearPartes(mia, segmentos) {
    let cursorNuevo = null;
    for (const seg of segmentos) {
      const clave = _clave(seg.cursor);
      // clave null = cursor con un formato que este cliente no entiende:
      // mejor appendear sin poder deduplicar que quedarse en negro.
      if (clave && !_posterior(clave, ultimaClave)) {
        cursorNuevo = seg.cursor;
        continue;
      }
      const bytes = await obtenerBytes(
        "/api/vivo/segmento?sesion=" + encodeURIComponent(mia)
        + "&nombre=" + encodeURIComponent(seg.nombre));
      if (sesion !== mia) return null;
      _encolarAppend(bytes);
      diag.appends += 1;
      diag.partes += 1;
      if (clave) ultimaClave = clave;
      cursorNuevo = seg.cursor;
    }
    return cursorNuevo;
  }

  async function _appendear(mia, segmentos) {
    return appendPorGrupo
      ? _appendearGrupos(mia, segmentos)
      : _appendearPartes(mia, segmentos);
  }

  // -- diagnostico: los dos numeros de la puerta 1 -------------------------

  function _diagNuevo() {
    return { ticks: 0, appends: 0, partes: 0, rangos: 0, conHuecos: 0,
             huecosNuevos: 0, huecoMax: 0, desde: Date.now(),
             finBase: null, relojBase: 0, partesBase: 0,
             inflacion: 0, avance: 0 };
  }

  function _medir() {
    if (!sourceBuffer || !_bandera("nimbo_diag_vivo", false)) return;
    diag.ticks += 1;
    try {
      const b = sourceBuffer.buffered;
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
      "[vivo diag] " + sesion
      + " modo=" + (appendPorGrupo ? "grupo" : "parte")
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

  async function _tick() {
    if (!sesion || !sourceBuffer || destruido) return;
    // El worker tiquea cada 500 ms sin esperar a que termine el tick
    // anterior, y `cursor` recien se actualiza al final: sin esta guarda
    // un tick lento (8+ segmentos de disco + HTTP) hace que el siguiente
    // arranque con el cursor viejo y vuelva a appendear lo mismo.
    if (tickEnVuelo) return;
    tickEnVuelo = true;
    // la sesion puede rotar (reconexion de encoder, cambio de calidad)
    // mientras este tick espera: compararla contra la de ESTE tick, no
    // contra null, o los bytes de la sesion vieja se appendean en el
    // SourceBuffer de la nueva.
    const mia = sesion;
    try {
      const desde = saltoPendiente ? null : cursor;
      saltoPendiente = false;
      const r = await obtenerJSON(
        "/api/vivo/segmentos?sesion=" + encodeURIComponent(mia)
        + "&desde=" + (desde == null ? "" : encodeURIComponent(desde))
        + "&limite=" + MAX_POR_TICK + "&atras=" + SEGMENTOS_ATRAS);
      if (!r || sesion !== mia) return;     // se desmonto/roto mientras esperaba
      const cursorNuevo = await _appendear(mia, r.segmentos || []);
      if (sesion !== mia) return;
      // El cursor avanza hasta lo ULTIMO consumido, no hasta r.cursor: el
      // ultimo grupo de la respuesta puede haber quedado en espera (ver
      // _appendearGrupos) y pasarlo por arriba lo perderia para siempre.
      if (cursorNuevo) cursor = cursorNuevo;
      if (r.salto) saltoPendiente = true;   // mucho backlog: la proxima
                                             // pide desde el borde vivo,
                                             // no sigue tratando de alcanzar
      _recortar();
      _vigilarDeriva();
      _medir();
      avisarEstado({ vivo: !!r.vivo });
    } catch (e) {
      avisarError(e);
    } finally {
      tickEnVuelo = false;
    }
  }

  function _reiniciar(motivo) {
    const s = sesion;
    desmontar();
    if (s) montar(s);
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
  // -> true si empalmo (el llamador no tiene que hacer nada mas), false
  // si hay que remontar de cero (init distinto: otra calidad u otro
  // codec, y ahi el corte es inevitable).
  async function _empalmar(sesionNueva) {
    if (!mediaSource || !sourceBuffer || !initActual) return false;
    if (mediaSource.readyState !== "open") return false;
    const previa = sesion;
    let bytes;
    try {
      bytes = await obtenerBytes(
        "/api/vivo/init?sesion=" + encodeURIComponent(sesionNueva));
    } catch (e) {
      return false;              // sin init todavia: remontar normal
    }
    if (sesion !== previa) return false;   // roto otra vez mientras esperaba
    if (!_mismosBytes(bytes, initActual)) return false;

    // El SourceBuffer sigue sirviendo tal cual: lo unico que cambia es de
    // donde salen los segmentos. En mode="sequence" lo nuevo se pega
    // atras de lo que ya estaba, asi que no hay hueco ni salto de reloj,
    // y el <video> no se entera. El tick que ya esta corriendo se encarga
    // del resto: con cursor en null pide la ventana de arranque de la
    // sesion nueva (que siempre empieza en un keyframe) en los proximos
    // 500 ms.
    sesion = sesionNueva;
    cursor = null;
    ultimaClave = null;
    saltoPendiente = false;
    avisarEstado({ empalmada: sesionNueva });
    return true;
  }

  async function montar(sesionNueva) {
    if (sesion === sesionNueva) return;
    if (await _empalmar(sesionNueva)) return;
    desmontar();
    sesion = sesionNueva;
    cola = [];
    cursor = null;
    saltoPendiente = false;
    ultimaClave = null;
    diag = _diagNuevo();
    rangosPrevios = 0;
    appendPorGrupo = _bandera("nimbo_append_por_grupo", true);

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
        initActual = initBytes;    // para el empalme de una rotacion
        // el arranque pasa por el mismo camino que el tick (grupos
        // enteros, nunca medio grupo): si el ultimo grupo de la ventana
        // todavia se esta escribiendo, lo trae el primer tick.
        cursor = await _appendear(sesionNueva, perfil.segmentos || []);
        if (sesion !== sesionNueva) return;
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
    initActual = null;
    sesion = null;
    cursor = null;
    ultimaClave = null;
  }

  function destruir() {
    destruido = true;
    desmontar();
    worker.terminate();
  }

  return { montar, desmontar, alVivo, silenciar, volumen,
           sesionActual: () => sesion, diagnostico: () => ({ ...diag }),
           destruir };
}
