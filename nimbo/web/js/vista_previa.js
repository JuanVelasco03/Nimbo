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

import { enviar, obtener } from "./api.js";
import { crearReproductor } from "./reproductor_vivo.js";
import { urlImagenProxy } from "./sitio.js";
import { fusionarFavoritas, estadoDeFila, grabacionDeFila } from "./vista_modelos.js";

const ESPERA_ENTRE_IMAGENES_MS = 1000;
const ESPERA_LATIDO_MS = 5000;

const relojImagen = new Worker(new URL("./temporizador_previa.worker.js", import.meta.url));
relojImagen.onmessage = (ev) => {
  if (ev.data === "tick") intentarPintarImagen();
};

const relojLatido = new Worker(new URL("./temporizador_previa.worker.js", import.meta.url));
relojLatido.onmessage = (ev) => {
  if (ev.data === "tick" && nombreAbierto && esTemporal) {
    enviar("/api/vivo/latido", { nombre: nombreAbierto }).catch(() => {});
  }
};

const reproductor = crearReproductor(
  document.getElementById("modal-previa-video"),
  {
    alErrar: () => { _volverASnapshot(); },
    alCambiarEstado: () => {},
  },
);

let ultimoEstado = null;
let nombreAbierto = null;
let idAbierto = null;
let cargandoImagen = false;
let esTemporal = false;       // esta vista agrego la modelo a grabar.txt
let sesionVivaMontada = null; // rel de la sesion que el reproductor tiene puesta
let variantesDisponibles = []; // sesiones vivas de esta modelo ahora mismo

function filaAbierta() {
  if (!ultimoEstado || !nombreAbierto) return null;
  const clave = nombreAbierto.toLowerCase();
  const fila = fusionarFavoritas(ultimoEstado).find(f => f.nombre.toLowerCase() === clave);
  if (fila) return fila;
  // fusionarFavoritas() solo conoce favoritas.json + estado.siguiendo --
  // una modelo agregada con "grabar directo" (solo en grabar.txt, nunca
  // favoriteada) no tiene fila ahi. El boton "ver en vivo" de la pestana
  // Grabando (vista_grabando.js) abre el modal igual para esas, asi que
  // si tiene sesion viva se arma una fila minima en vez de dejar el modal
  // en blanco -- "local:false" es la mejor suposicion posible (no se
  // sabe si es favorita; el boton +favoritas de todos modos es correcto).
  const sesiones = _sesionesDe(nombreAbierto);
  if (!sesiones.length) return null;
  return {
    nombre: nombreAbierto, local: false, siguiendo: false,
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
  const { nombre, local, enVpn, enLista, vivo, online } = fila;

  // en modo vivo, snapshot/sonido/pantalla-completa viven en la barra
  // flotante sobre el video (.transporte-vivo) -- ver montarVivo().
  let botonVivo = "";
  if (modoVivo()) {
    botonVivo = "";
  } else if (vivo) {
    botonVivo = `<button class="boton-chico" data-accion="ver-vivo" data-nombre="${nombre}">ver en vivo</button>`;
  } else if (online) {
    botonVivo = `<button class="boton-chico" data-accion="ver-vivo" data-nombre="${nombre}"
                   title="agrega la modelo a grabar.txt mientras mires; la quita sola al cerrar">ver en vivo</button>`;
  }

  cont.innerHTML = `
    ${botonVivo}
    <label class="conmutador" title="grabar">
      <input type="checkbox" data-accion="toggle-grabar" data-nombre="${nombre}" ${enLista ? "checked" : ""}>
      <span class="pista"></span>
    </label>
    <button class="boton-chico" data-accion="toggle-vpn" data-nombre="${nombre}" data-vpn-activo="${enVpn ? "1" : "0"}">${enVpn ? "quitar VPN" : "marcar VPN"}</button>
    ${local
      ? `<button class="boton-chico peligro" data-accion="quitar-favorita" data-nombre="${nombre}">quitar</button>`
      : `<button class="boton-chico" data-accion="agregar-favorita" data-nombre="${nombre}">+ favoritas</button>`}`;
}

function pintarVariantes() {
  const cont = document.getElementById("modal-previa-variantes");
  if (!modoVivo() || variantesDisponibles.length <= 1) { cont.innerHTML = ""; return; }
  cont.innerHTML = variantesDisponibles.map(s => `
    <button class="boton-chico${s.sesion === sesionVivaMontada ? " activa" : ""}"
            data-accion="seleccionar-variante" data-sesion="${s.sesion}">
      ${s.variante || s.calidad || "?"}
    </button>`).join("");
}

// -- la sesion viva: cuando aparece/rota/desaparece --------------------

function _sesionesDe(nombre) {
  if (!ultimoEstado || !ultimoEstado.vivo) return [];
  const clave = (nombre || "").toLowerCase();
  return ultimoEstado.vivo.filter(s => (s.nombre || "").toLowerCase() === clave);
}

function montarVivo(sesionRel) {
  sesionVivaMontada = sesionRel;
  pararReloj();
  document.getElementById("modal-previa-imagen").classList.add("oculta");
  document.getElementById("modal-previa-sin-imagen").classList.add("oculta");
  document.getElementById("modal-previa-video").classList.remove("oculta");
  document.getElementById("modal-previa-transporte").classList.remove("oculta");
  reproductor.montar(sesionRel);
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
  relojLatido.postMessage({ accion: "parar" });
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
    const disponibles = _sesionesDe(nombre);
    const elegida = disponibles.find(s => s.elegida) || disponibles[0];
    if (elegida) montarVivo(elegida.sesion);
    return;
  }
  // no hay sesion todavia: pedirla temporal (agrega a grabar.txt)
  try {
    const r = await enviar("/api/vivo/abrir", { nombre });
    esTemporal = !!r.temporal;
  } catch (err) {
    esTemporal = false;
  }
  // el video se monta solo cuando aparezca la sesion -- ver sincronizarVivo(),
  // llamado en cada refresco de pintarEstado() (SSE o poll de 2s)
}

function sincronizarVivo() {
  if (!nombreAbierto) return;
  variantesDisponibles = _sesionesDe(nombreAbierto);

  if (modoVivo()) {
    const sigueViva = variantesDisponibles.some(s => s.sesion === sesionVivaMontada);
    if (sigueViva) { pintarVariantes(); return; }
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
      montarVivo(elegida.sesion);
    } else {
      _volverASnapshot();
    }
    return;
  }

  // se pidio "ver en vivo" temporal y recien ahora aparecio la sesion
  if (esTemporal && variantesDisponibles.length) {
    const elegida = variantesDisponibles.find(s => s.elegida) || variantesDisponibles[0];
    montarVivo(elegida.sesion);
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

function intentarPintarImagen() {
  if (!nombreAbierto || !idAbierto || cargandoImagen) return;
  pintarImagen();
}

function pintarImagen() {
  const img = document.getElementById("modal-previa-imagen");
  const sinImagen = document.getElementById("modal-previa-sin-imagen");
  if (!idAbierto) {
    img.classList.add("oculta");
    sinImagen.classList.remove("oculta");
    return;
  }
  cargandoImagen = true;
  const url = "https://img.doppiocdn.media/snapshot/" + idAbierto + "/" + Math.floor(Date.now() / 1000);
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
  img.src = urlImagenProxy(url);
}

// El id numerico puede venir ya resuelto (si la fila viene de
// "siguiendo", sesion.py ya lo trae) o hay que pedirlo -- una favorita
// puramente local no tiene id hasta que se consulta el sitio.
async function resolverIdYArrancar(nombre) {
  const m = ultimoEstado && ultimoEstado.siguiendo && ultimoEstado.siguiendo.modelos
    ? ultimoEstado.siguiendo.modelos.find(x => (x.username || "").toLowerCase() === nombre.toLowerCase())
    : null;

  if (m && m.id) {
    idAbierto = m.id;
  } else {
    try {
      const r = await obtener("/api/sitio/estado?nombre=" + encodeURIComponent(nombre));
      idAbierto = r.id || null;
    } catch (err) {
      idAbierto = null;
    }
  }

  if (nombreAbierto !== nombre) return;   // se cerro o cambio mientras se esperaba

  cargandoImagen = false;
  pintarImagen();   // primera, inmediata
  if (idAbierto) arrancarReloj();
}

// -- abrir / cerrar -----------------------------------------------------

export function abrirPreviaNombre(nombre) {
  if (!nombre) return;

  nombreAbierto = nombre;
  idAbierto = null;
  esTemporal = false;
  sesionVivaMontada = null;
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
  sincronizar();
  pintarImagen();   // "sin imagen" hasta que resuelva el id
  document.getElementById("modal-previa").classList.remove("oculta");
  resolverIdYArrancar(nombre);
}

// Desde la pestana Grabando: ya se sabe que hay sesion viva (el boton
// "ver en vivo" de una tarjeta solo aparece cuando det.sesiones no esta
// vacio), asi que abre directo en video en vez de esperar el click extra
// adentro del modal.
export function abrirPreviaEnVivo(nombre) {
  abrirPreviaNombre(nombre);
  const fila = filaAbierta();
  if (fila) iniciarVivo(nombre, fila);
}

function abrirPrevia(tarjeta) {
  abrirPreviaNombre(tarjeta.dataset.nombre);
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
  esTemporal = false;
  sesionVivaMontada = null;
  variantesDisponibles = [];
  pararReloj();
  document.getElementById("modal-previa-video").classList.add("oculta");
  document.getElementById("modal-previa-transporte").classList.add("oculta");
  document.getElementById("modal-previa").classList.add("oculta");
}

export function iniciarPrevia() {
  document.getElementById("favoritas").addEventListener("click", (ev) => {
    // no abrir el modal si el click fue sobre un control de la tarjeta
    // (el conmutador de grabar, los botones de VPN/favoritas)
    if (ev.target.closest("[data-accion], .conmutador")) return;
    const tarjeta = ev.target.closest(".tarjeta-modelo");
    if (!tarjeta) return;
    abrirPrevia(tarjeta);
  });

  document.getElementById("modal-previa-cerrar").addEventListener("click", cerrarPrevia);

  document.getElementById("modal-previa").addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) { cerrarPrevia(); return; }  // fondo, no el panel
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
      montarVivo(boton.dataset.sesion);
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && nombreAbierto) cerrarPrevia();
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
