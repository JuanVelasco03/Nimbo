// Modal de vista previa: click en una tarjeta de "Modelos" -> ventana con
// el snapshot en vivo (se refresca solo) y los mismos botones de accion
// que ya tiene la tarjeta. Si la modelo tiene una sesion viva en
// datos\streams\ (se esta grabando, o se abrio "ver en vivo" temporal),
// el snapshot se reemplaza por video real -- ver reproductor_vivo.js y
// CLAUDE.md, seccion "Reproductor en vivo", para el porque esto SI es
// viable (no hace falta reverse-engineerear #EXT-X-MOUFLON: el Chromium
// del supervisor ya resolvio esas URIs, el detector ya escribio los
// segmentos en disco, este modulo solo los consume).
//
// El snapshot (img.doppiocdn.media/snapshot/<id>/<epoch>) no necesita
// sesion ni cookies -- confirmado en vivo con un timestamp cualquiera,
// no hace falta el exacto que trae la API autenticada -- asi que
// refrescarlo seguido mientras el modal esta abierto no toca para nada
// la decision de "sin refresco automatico" que aplica al endpoint de
// favoritas/siguiendo (ese es otro pedido, con sesion).
//
// El reloj del refresco de IMAGEN vive en un Web Worker
// (temporizador_previa.worker.js) a proposito: setTimeout/setInterval del
// hilo principal se frenan solos cuando la ventana pierde el foco
// (confirmado: sin esto, el refresco se trababa en una sola imagen apenas
// Nimbo dejaba de tener el foco) -- un worker no se frena por eso. El
// reloj del LATIDO (vivo temporal) usa una segunda instancia del mismo
// worker, por la misma razon: si el usuario deja el modal abierto en una
// pestana sin foco, el latido tiene que seguir llegando o el servidor
// cierra la vista sola a los 30s (ver vivo_temporal.cosechar).

import { enviar, obtener, enviarWS } from "./api.js";
import { crearReproductor } from "./reproductor_vivo.js";
import { urlImagenProxy } from "./sitio.js";
import { fusionarFavoritas, fusionarChaturbate, estadoDeFila, grabacionDeFila } from "./vista_modelos.js";

const ESPERA_ENTRE_IMAGENES_MS = 1000;
const ESPERA_LATIDO_MS = 5000;

const relojImagen = new Worker(new URL("./temporizador_previa.worker.js", import.meta.url));
relojImagen.onmessage = (ev) => {
  if (ev.data === "tick") intentarPintarImagen();
};

const relojLatido = new Worker(new URL("./temporizador_previa.worker.js", import.meta.url));
relojLatido.onmessage = (ev) => {
  if (ev.data === "tick" && nombreAbierto && esTemporal) {
    // Por el WebSocket, no un POST: el cierre de ESTA conexion ya es
    // senal de que el navegador se fue, mas precisa que esperar los 30s
    // de vivo_temporal.cosechar() (ver CLAUDE.md, seccion "Servidor y
    // frontend").
    enviarWS({ tipo: "latido", nombre: nombreAbierto });
  }
};

const reproductor = crearReproductor(
  document.getElementById("modal-previa-video"),
  {
    alErrar: (e) => { _mostrarErrorVivo(e); _volverASnapshot(); },
    alCambiarEstado: () => {},
  },
);

// Antes esto volvia al snapshot en silencio, sin decir por que -- en un
// celular (sobre todo iOS: WebKit tiene soporte parcial de MediaSource
// Extensions para el fragmented MP4 que graba Nimbo) eso se ve como "el
// boton no hace nada". El mensaje se queda puesto hasta la proxima
// apertura o el proximo intento de montar -- ver abrirPreviaNombre() y
// montarVivo().
function _mostrarErrorVivo(e) {
  const el = document.getElementById("modal-previa-error-vivo");
  el.textContent = "no se pudo reproducir en vivo: " + ((e && e.message) || "error desconocido");
  el.classList.remove("oculta");
}

function _ocultarErrorVivo() {
  document.getElementById("modal-previa-error-vivo").classList.add("oculta");
}

let ultimoEstado = null;
let nombreAbierto = null;
let idAbierto = null;
// SPEC 18: que sitio es la modelo abierta -- decide que URL de snapshot
// usar y que parametro mandar a /api/sitio/* (ver pintarImagen/
// resolverIdYArrancar mas abajo).
let sitioAbierto = "stripchat";
let cargandoImagen = false;
let esTemporal = false;       // esta vista agrego la modelo a grabar.txt
let sesionVivaMontada = null; // rel de la sesion que el reproductor tiene puesta
// La pista de audio puede aparecer despues que la de video. Se guarda por
// separado para volver a montar exactamente una vez al llegar, no en cada
// delta de estado.
let sesionAudioVivaMontada = null;
let variantesDisponibles = []; // sesiones vivas de esta modelo ahora mismo

function filaAbierta() {
  if (!ultimoEstado || !nombreAbierto) return null;
  const clave = nombreAbierto.toLowerCase();
  // SPEC 18: fusionarFavoritas() es solo de stripchat desde que "Modelos"
  // se seccionó por sitio -- probar tambien fusionarChaturbate() antes de
  // caer al fallback generico, o una tarjeta de chaturbate abria el modal
  // en blanco.
  const filas = sitioAbierto === "chaturbate"
    ? fusionarChaturbate(ultimoEstado)
    : fusionarFavoritas(ultimoEstado);
  const fila = filas.find(f => f.nombre.toLowerCase() === clave);
  if (fila) return fila;
  // Ninguna de las dos conoce una modelo agregada con "grabar directo"
  // (solo en grabar.txt, nunca favoriteada) -- el boton "ver en vivo" de
  // la pestana Grabando (vista_grabando.js) abre el modal igual para
  // esas, asi que si tiene sesion viva se arma una fila minima en vez de
  // dejar el modal en blanco -- "local:false" es la mejor suposicion
  // posible (no se sabe si es favorita; el boton +favoritas de todos
  // modos es correcto). "sitio" se adivina de estado.lista.sitios: no es
  // perfecto si dos homonimas de sitios distintos tienen sesion viva a la
  // vez (caso raro, no cubierto), pero es la unica pista disponible aca.
  const sesiones = _sesionesDe(nombreAbierto);
  if (!sesiones.length) return null;
  const sitios = (ultimoEstado.lista && ultimoEstado.lista.sitios) || {};
  return {
    nombre: nombreAbierto, sitio: sitios[clave] || "stripchat",
    local: false, siguiendo: false,
    enLista: true, enVpn: false, estadoSitio: "", online: true,
    grabando: true, privadoDetector: false, vivo: true,
  };
}

function pintarCabecera(fila) {
  const info = fila ? estadoDeFila(fila) : { clase: "offline", texto: "" };
  const rec = fila ? grabacionDeFila(fila) : null;
  document.getElementById("modal-previa-panel").dataset.estado = rec && rec.clase === "grabando" ? "grabando" : info.clase;
  document.getElementById("modal-previa-nombre").textContent = nombreAbierto || "";
  document.getElementById("modal-previa-badge").className = "estado-badge " + info.clase;
  document.getElementById("modal-previa-badge-texto").textContent = info.texto;
  const badgeRec = document.getElementById("modal-previa-badge-grabacion");
  badgeRec.className = "estado-badge" + (rec ? " " + rec.clase : " oculta");
  document.getElementById("modal-previa-badge-grabacion-texto").textContent = rec ? rec.texto : "";
}

function modoVivo() {
  return sesionVivaMontada !== null;
}

function pintarAcciones(fila) {
  const cont = document.getElementById("modal-previa-acciones");
  if (!fila) { cont.innerHTML = ""; return; }
  const { nombre, sitio, local, enVpn, enLista, vivo, online } = fila;
  // SPEC 18: manejarAccionFavorita (main.js) lee data-sitio para saber a
  // que sitio postear -- sin esto, las acciones del modal sobre una
  // modelo de chaturbate pegaban contra stripchat.
  const s = sitio || "stripchat";

  // en modo vivo, snapshot/sonido/pantalla-completa viven en la barra
  // flotante sobre el video (.transporte-vivo) -- ver montarVivo().
  let botonVivo = "";
  if (modoVivo()) {
    botonVivo = "";
  } else if (vivo) {
    botonVivo = `<button class="boton-chico" data-accion="ver-vivo" data-nombre="${nombre}" data-sitio="${s}">ver en vivo</button>`;
  } else if (online) {
    botonVivo = `<button class="boton-chico" data-accion="ver-vivo" data-nombre="${nombre}" data-sitio="${s}"
                   title="agrega la modelo a grabar.txt mientras mires; la quita sola al cerrar">ver en vivo</button>`;
  }

  cont.innerHTML = `
    ${botonVivo}
    <label class="conmutador" title="grabar">
      <input type="checkbox" data-accion="toggle-grabar" data-nombre="${nombre}" data-sitio="${s}" ${enLista ? "checked" : ""}>
      <span class="pista"></span>
    </label>
    <button class="boton-chico${s === "chaturbate" ? " oculta" : ""}" data-accion="toggle-vpn" data-nombre="${nombre}" data-sitio="${s}" data-vpn-activo="${enVpn ? "1" : "0"}">${enVpn ? "quitar VPN" : "marcar VPN"}</button>
    ${local
      ? `<button class="boton-chico peligro" data-accion="quitar-favorita" data-nombre="${nombre}" data-sitio="${s}">quitar</button>`
      : `<button class="boton-chico" data-accion="agregar-favorita" data-nombre="${nombre}" data-sitio="${s}">+ favoritas</button>`}`;
}

function pintarVariantes() {
  const cont = document.getElementById("modal-previa-variantes");
  if (!modoVivo() || variantesDisponibles.length <= 1) { cont.innerHTML = ""; return; }
  cont.innerHTML = variantesDisponibles.map(s => `
    <button class="boton-chico${s.sesion === sesionVivaMontada ? " activa" : ""}"
            data-accion="seleccionar-variante" data-sesion="${s.sesion}"
            data-sesion-audio="${s.sesion_audio || ""}">
      ${s.variante || s.calidad || "?"}
    </button>`).join("");
}

// -- la sesion viva: cuando aparece/rota/desaparece --------------------

function _sesionesDe(nombre, sitio = sitioAbierto) {
  if (!ultimoEstado || !ultimoEstado.vivo) return [];
  const clave = (nombre || "").toLowerCase();
  const sitioNormalizado = sitio || "stripchat";
  return ultimoEstado.vivo.filter(s =>
    (s.nombre || "").toLowerCase() === clave
    && (s.sitio || "stripchat") === sitioNormalizado);
}

function montarVivo(sesionRel, sesionAudioRel = null) {
  sesionVivaMontada = sesionRel;
  sesionAudioVivaMontada = sesionAudioRel || null;
  pararReloj();
  _ocultarErrorVivo();
  document.getElementById("modal-previa-imagen").classList.add("oculta");
  document.getElementById("modal-previa-sin-imagen").classList.add("oculta");
  document.getElementById("modal-previa-video").classList.remove("oculta");
  document.getElementById("modal-previa-transporte").classList.remove("oculta");
  reproductor.montar(sesionRel, sesionAudioRel);
  relojLatido.postMessage({ accion: esTemporal ? "arrancar" : "parar", ms: ESPERA_LATIDO_MS });
  // repintar ya mismo (no esperar al proximo poll de 2s): el boton pasa
  // de "ver en vivo" a nada (los controles ahora viven en la barra
  // flotante) y aparece el selector si hay mas de una variante.
  pintarAcciones(filaAbierta());
  pintarVariantes();
}

function _volverASnapshot() {
  if (!modoVivo()) return;
  reproductor.desmontar();
  sesionVivaMontada = null;
  sesionAudioVivaMontada = null;
  // El comentario del boton "salir-vivo" ya dice que volver al snapshot
  // NO cierra la vista temporal -- pero esto paraba el latido igual, asi
  // que a los CADUCIDAD_S=30s vivo_temporal.cosechar() la cerraba sola
  // (la sacaba de grabar.txt) con el modal todavia abierto. El latido
  // tiene que seguir mientras esTemporal siga activo; solo lo para
  // cerrarPrevia() de verdad.
  if (!esTemporal) relojLatido.postMessage({ accion: "parar" });
  document.getElementById("modal-previa-video").classList.add("oculta");
  document.getElementById("modal-previa-transporte").classList.add("oculta");
  _salirDePantallaCompleta();
  if (nombreAbierto) { pintarImagen(); arrancarReloj(); }
  sincronizar();
}

function _salirDePantallaCompleta() {
  if (document.fullscreenElement === document.getElementById("modal-previa-imagen-cont")) {
    document.exitFullscreen().catch(() => {});
  }
}

async function iniciarVivo(nombre, fila) {
  if (fila.vivo) {
    const disponibles = _sesionesDe(nombre, fila.sitio);
    const elegida = disponibles.find(s => s.elegida) || disponibles[0];
    if (elegida) montarVivo(elegida.sesion, elegida.sesion_audio);
    return;
  }
  // no hay sesion todavia: pedirla temporal (agrega a grabar.txt)
  try {
    const r = await enviar("/api/vivo/abrir", { nombre });
    esTemporal = !!r.temporal;
  } catch (err) {
    esTemporal = false;
  }
  // El reloj del servidor (vivo_temporal.abrir()) ya arranco a contar
  // CADUCIDAD_S=30s desde el POST de arriba -- pero el latido recien
  // empezaba a mandarse adentro de montarVivo(), que no corre hasta que
  // el supervisor/detector escriben la primera sesion en disco (puede
  // tardar mas de 30s: resolver id + master + primer segmento). Arrancarlo
  // ya mismo, con un latido inmediato, cierra ese hueco -- el worker
  // tolera un "arrancar" repetido (vuelve a postear el mismo intervalo).
  if (esTemporal) {
    enviarWS({ tipo: "latido", nombre });
    relojLatido.postMessage({ accion: "arrancar", ms: ESPERA_LATIDO_MS });
  }
  // el video se monta solo cuando aparezca la sesion -- ver sincronizarVivo(),
  // llamado en cada refresco de pintarEstado() (SSE o poll de 2s)
}

function sincronizarVivo() {
  if (!nombreAbierto) return;
  variantesDisponibles = _sesionesDe(nombreAbierto, sitioAbierto);

  if (modoVivo()) {
    const montada = variantesDisponibles.find(s => s.sesion === sesionVivaMontada);
    if (montada) {
      // Chaturbate publica el video y el audio por separado. Es normal que
      // el video aparezca en el primer estado y la carpeta de audio unos
      // instantes despues. Antes se devolvia solo por coincidir la sesion
      // de video y el reproductor quedaba mudo hasta la siguiente rotacion.
      // montar() ya es idempotente para la misma pareja; si acaba de llegar
      // sesion_audio, desmonta y la incorpora al MediaSource.
      const audioMontado = sesionAudioVivaMontada || null;
      const audioDisponible = montada.sesion_audio || null;
      if (audioMontado !== audioDisponible) {
        montarVivo(montada.sesion, audioDisponible);
      } else {
        pintarVariantes();
      }
      return;
    }
    // la sesion montada ya no esta (rotacion de calidad: el detector
    // cierra la carpeta vieja y abre una nueva con otro sello) -- si
    // sigue habiendo alguna sesion viva, saltar ahi directo. Antes esto
    // solo volvia a montar si esTemporal, asi que una modelo que ya se
    // estaba grabando desde antes (el caso mas comun) se quedaba para
    // siempre en el snapshot despues de la primera rotacion, con el
    // <video> de fondo pausado y sin src -- "sonido"/"silenciar" seguian
    // ahi (o parecia que si) controlando un reproductor ya abandonado.
    if (variantesDisponibles.length) {
      const elegida = variantesDisponibles.find(s => s.elegida) || variantesDisponibles[0];
      montarVivo(elegida.sesion, elegida.sesion_audio);
    } else {
      _volverASnapshot();
    }
    return;
  }

  // se pidio "ver en vivo" temporal y recien ahora aparecio la sesion
  if (esTemporal && variantesDisponibles.length) {
    const elegida = variantesDisponibles.find(s => s.elegida) || variantesDisponibles[0];
    montarVivo(elegida.sesion, elegida.sesion_audio);
  }
}

function sincronizar() {
  if (!nombreAbierto) return;
  const fila = filaAbierta();
  pintarCabecera(fila);
  pintarAcciones(fila);
  sincronizarVivo();
}

// Se llama desde pintarEstado() en cada refresco (SSE o poll de 2s) --
// asi el modal, si esta abierto, no se queda desactualizado aunque el
// cambio (grabando, VPN, favorita, sesion viva) haya venido de otro lado
// de la UI.
export function renderPrevia(estado) {
  ultimoEstado = estado;
  sincronizar();
}

// -- imagen (snapshot que se refresca solo) ---------------------------
// El worker manda un "tick" cada ESPERA_ENTRE_IMAGENES_MS sin importar
// el foco de la ventana; intentarPintarImagen() ignora el tick si la
// imagen anterior todavia esta cargando (cargandoImagen), asi nunca se
// amontonan pedidos si la red anda lenta.

function pararReloj() {
  relojImagen.postMessage({ accion: "parar" });
}

function arrancarReloj() {
  relojImagen.postMessage({ accion: "arrancar", ms: ESPERA_ENTRE_IMAGENES_MS });
}

// SPEC 18: chaturbate no tiene id numerico -- su snapshot se pide por
// username directo (chaturbate.url_snapshot), nunca hace falta resolver
// nada antes de poder pintarlo. "hayImagenDisponible" junta los dos
// casos sin que el resto del modulo tenga que distinguirlos.
function hayImagenDisponible() {
  return sitioAbierto === "chaturbate" ? !!nombreAbierto : !!idAbierto;
}

function intentarPintarImagen() {
  if (!nombreAbierto || !hayImagenDisponible() || cargandoImagen) return;
  pintarImagen();
}

function pintarImagen() {
  const img = document.getElementById("modal-previa-imagen");
  const sinImagen = document.getElementById("modal-previa-sin-imagen");
  // El video ya ocupa el mismo lugar del modal que esta imagen -- pintar
  // "sin imagen" encima (p.ej. cuando resolverIdYArrancar() no consigue un
  // id numerico) tapa el <video> mientras el modo vivo esta montado.
  if (modoVivo()) return;
  if (!hayImagenDisponible()) {
    img.classList.add("oculta");
    sinImagen.classList.remove("oculta");
    return;
  }
  cargandoImagen = true;
  // Cache-bust con la hora actual en los dos casos -- stripchat la lleva
  // en el propio path (snapshot/<id>/<epoch>), chaturbate en la query
  // (?room=<nombre>&_=<epoch>): jpeg.live.mmcdn.com ya cambia de
  // contenido solo, pero sin un parametro que varie el navegador podria
  // quedarse con una copia vieja entre pedidos.
  const ahora = Math.floor(Date.now() / 1000);
  const url = sitioAbierto === "chaturbate"
    ? "https://jpeg.live.mmcdn.com/stream?room=" + encodeURIComponent(nombreAbierto) + "&_=" + ahora
    : "https://img.doppiocdn.media/snapshot/" + idAbierto + "/" + ahora;
  img.onload = () => {
    cargandoImagen = false;
    // si en el medio se cambio a modo vivo, este pedido ya estaba en
    // vuelo desde antes -- mostrar el snapshot ahora taparia el video
    // (los dos ocupan el mismo lugar en el modal, uno de los dos queda
    // "pegado" encima). Nunca se puede cancelar un <img> en vuelo, asi
    // que el guard va aca, no en quien dispara el pedido.
    if (modoVivo()) return;
    img.classList.remove("oculta");
    sinImagen.classList.add("oculta");
  };
  img.onerror = () => {
    cargandoImagen = false;
    if (modoVivo()) return;
    img.classList.add("oculta");
    sinImagen.classList.remove("oculta");
  };
  img.src = urlImagenProxy(url, sitioAbierto);
}

// El id numerico puede venir ya resuelto (si la fila viene de
// "siguiendo", sesion.py ya lo trae) o hay que pedirlo -- una favorita
// puramente local no tiene id hasta que se consulta el sitio. Chaturbate
// no necesita nada de esto (ver hayImagenDisponible), pero igual arranca
// el reloj de refresco.
async function resolverIdYArrancar(nombre, sitio) {
  if (sitio === "chaturbate") {
    idAbierto = null;
  } else {
    const m = ultimoEstado && ultimoEstado.siguiendo && ultimoEstado.siguiendo.modelos
      ? ultimoEstado.siguiendo.modelos.find(x => (x.username || "").toLowerCase() === nombre.toLowerCase())
      : null;

    if (m && m.id) {
      idAbierto = m.id;
    } else {
      try {
        const r = await obtener("/api/sitio/estado?nombre=" + encodeURIComponent(nombre)
          + "&sitio=" + encodeURIComponent(sitio));
        idAbierto = r.id || null;
      } catch (err) {
        idAbierto = null;
      }
    }
  }

  if (nombreAbierto !== nombre) return;   // se cerro o cambio mientras se esperaba

  cargandoImagen = false;
  pintarImagen();   // primera, inmediata
  // montarVivo() ya paro este reloj (:154) -- si el modo vivo arranco
  // mientras se esperaba la respuesta de /api/sitio/estado, reactivarlo
  // aca pedia /api/sitio/imagen una vez por segundo en vano durante todo
  // el modo vivo.
  if (hayImagenDisponible() && !modoVivo()) arrancarReloj();
}

// -- abrir / cerrar -----------------------------------------------------

export function abrirPreviaNombre(nombre, sitio = "stripchat") {
  if (!nombre) return;

  nombreAbierto = nombre;
  sitioAbierto = sitio;
  idAbierto = null;
  esTemporal = false;
  sesionVivaMontada = null;
  sesionAudioVivaMontada = null;
  variantesDisponibles = [];
  // Cada apertura del modal arranca en silencio, a proposito -- aunque
  // la modelo anterior haya quedado con sonido. El <video> es un
  // elemento persistente (nunca se recrea) asi que su .muted sobrevive
  // solo entre aperturas si no se fuerza aca; esto es distinto de una
  // ROTACION de sesion (misma modelo, cambio de calidad) dentro de una
  // misma apertura, que si preserva el sonido elegido -- ver montarVivo()
  // y sincronizarVivo(), que nunca tocan .muted.
  document.getElementById("modal-previa-video").muted = true;
  document.getElementById("modal-previa-video").classList.add("oculta");
  document.getElementById("modal-previa-transporte").classList.add("oculta");
  _ocultarErrorVivo();
  sincronizar();
  pintarImagen();   // "sin imagen" hasta que resuelva el id
  document.getElementById("modal-previa").classList.remove("oculta");
  resolverIdYArrancar(nombre, sitio);
}

// Desde la pestana Grabando: ya se sabe que hay sesion viva (el boton
// "ver en vivo" de una tarjeta solo aparece cuando det.sesiones no esta
// vacio), asi que abre directo en video en vez de esperar el click extra
// adentro del modal.
export function abrirPreviaEnVivo(nombre, sitio = "stripchat") {
  abrirPreviaNombre(nombre, sitio);
  const fila = filaAbierta();
  if (fila) iniciarVivo(nombre, fila);
}

function abrirPrevia(tarjeta) {
  abrirPreviaNombre(tarjeta.dataset.nombre, tarjeta.dataset.sitio || "stripchat");
}

function cerrarPrevia() {
  // si esta vista agrego la modelo a grabar.txt (ver-vivo temporal), la
  // quita al cerrar -- cerrar() del lado del servidor ya se protege solo
  // si no era suya (ver vivo_temporal.py), pero igual solo se manda el
  // pedido cuando hacia falta.
  if (esTemporal && nombreAbierto) {
    enviar("/api/vivo/cerrar", { nombre: nombreAbierto }).catch(() => {});
  }
  reproductor.desmontar();
  relojLatido.postMessage({ accion: "parar" });
  _salirDePantallaCompleta();
  nombreAbierto = null;
  idAbierto = null;
  sitioAbierto = "stripchat";
  esTemporal = false;
  sesionVivaMontada = null;
  sesionAudioVivaMontada = null;
  variantesDisponibles = [];
  pararReloj();
  document.getElementById("modal-previa-video").classList.add("oculta");
  document.getElementById("modal-previa-transporte").classList.add("oculta");
  document.getElementById("modal-previa").classList.add("oculta");
}

export function iniciarPrevia() {
  // SPEC 18: la grilla de chaturbate es un contenedor aparte
  // (#favoritas-chaturbate) -- sin este segundo listener, sus tarjetas
  // nunca abrian el modal.
  const _abrirDesdeGrilla = (ev) => {
    // no abrir el modal si el click fue sobre un control de la tarjeta
    // (el conmutador de grabar, los botones de VPN/favoritas)
    if (ev.target.closest("[data-accion], .conmutador")) return;
    const tarjeta = ev.target.closest(".tarjeta-modelo");
    if (!tarjeta) return;
    abrirPrevia(tarjeta);
  };
  document.getElementById("favoritas").addEventListener("click", _abrirDesdeGrilla);
  document.getElementById("favoritas-chaturbate").addEventListener("click", _abrirDesdeGrilla);

  document.getElementById("modal-previa-cerrar").addEventListener("click", cerrarPrevia);

  // Solo cierra si el click EMPEZO en el fondo: antes alcanzaba con que
  // TERMINARA ahi, asi que arrastrar el slider de volumen (o seleccionar
  // texto) y soltar afuera del panel cerraba el modal entero -- el click
  // se despacha en el ancestro comun (#modal-previa) cuando el mousedown
  // y el mouseup caen en elementos distintos.
  let clickEmpezoEnElFondo = false;
  const modalPrevia = document.getElementById("modal-previa");
  modalPrevia.addEventListener("mousedown", (ev) => {
    clickEmpezoEnElFondo = ev.target === ev.currentTarget;
  });

  modalPrevia.addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget && clickEmpezoEnElFondo) { cerrarPrevia(); return; }  // fondo, no el panel
    const boton = ev.target.closest("[data-accion]");
    if (!boton || !nombreAbierto) return;
    const accion = boton.dataset.accion;
    if (accion === "ver-vivo") {
      const fila = filaAbierta();
      if (fila) iniciarVivo(nombreAbierto, fila);
    } else if (accion === "salir-vivo") {
      // volver al snapshot no cierra la vista temporal (si la hay): eso
      // solo pasa al cerrar el modal entero, en cerrarPrevia().
      _volverASnapshot();
    } else if (accion === "toggle-sonido") {
      const video = document.getElementById("modal-previa-video");
      // el label se actualiza solo via el listener de "volumechange"
      // (mas abajo) -- misma fuente de verdad que cuando se mueve el
      // slider, nunca dos lugares escribiendo el mismo texto.
      reproductor.silenciar(!video.muted);
    } else if (accion === "pantalla-completa") {
      const cont = document.getElementById("modal-previa-imagen-cont");
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else cont.requestFullscreen().catch(() => {});
    } else if (accion === "seleccionar-variante") {
      // La variante de Chaturbate tiene una segunda ruta de audio. Al
      // elegirla manualmente no se puede perder ese dato (el camino de
      // seleccion automatica ya llama montarVivo con ambas rutas).
      montarVivo(boton.dataset.sesion, boton.dataset.sesionAudio || null);
    }
  });

  // Cuando una nos salio de pantalla completa (ver abajo), Chromium
  // consume el Escape para eso -- pero el keydown igual burbujea al
  // document y, sin esta guarda, cerraba el modal entero: "salir de
  // pantalla completa" se sentia como "se cerro la vista en vivo y volvi
  // a Modelos". El boton de pantalla completa (mas abajo) ya chequeaba
  // esto; el Escape global no.
  let salioDePantallaCompleta = 0;
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) salioDePantallaCompleta = performance.now();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !nombreAbierto) return;
    if (document.fullscreenElement) return;
    // el orden entre este keydown y el fullscreenchange no esta
    // garantizado entre navegadores -- si la salida ya gano la carrera,
    // document.fullscreenElement arriba ya es null. Esta ventana corta
    // cubre ese caso.
    if (performance.now() - salioDePantallaCompleta < 300) return;
    cerrarPrevia();
  });

  // una sola fuente de verdad para el label del boton de sonido: el
  // evento nativo "volumechange" dispara tanto si lo cambio el boton
  // (mute/unmute) como el slider (arrastrar sube el volumen Y saca de
  // mudo, ver reproductor_vivo.js:volumen) -- sin esto habria que
  // repetir la misma logica en los dos manejadores.
  const video = document.getElementById("modal-previa-video");
  const botonSonido = document.getElementById("modal-previa-boton-sonido");
  video.addEventListener("volumechange", () => {
    botonSonido.textContent = (video.muted || video.volume === 0) ? "sonido" : "silenciar";
  });

  document.getElementById("modal-previa-volumen").addEventListener("input", (ev) => {
    reproductor.volumen(Number(ev.target.value) / 100);
  });

  document.addEventListener("fullscreenchange", () => {
    const boton = document.getElementById("modal-previa-boton-pantalla");
    const enPantallaCompleta = document.fullscreenElement === document.getElementById("modal-previa-imagen-cont");
    boton.textContent = enPantallaCompleta ? "salir de pantalla completa" : "pantalla completa";
  });
}
