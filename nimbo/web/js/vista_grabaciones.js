// Pestana "Grabaciones": lista lo que ya quedo terminado en
// F:\grabador\grabaciones (via /api/grabaciones, ver nimbo/grabaciones.py).
// Agrupa por sesion -- un recorte que el detector tuvo que partir en
// varias partes (-seg1, -seg2...) aparece como UN grupo con varias
// partes, no como archivos sueltos sin relacion entre si.
//
// Grilla de tarjetas (no lista): la portada es un fotograma real del
// .mp4 (ver nimbo/miniaturas.py + /api/grabaciones/miniatura), no el
// avatar del sitio -- a diferencia de Modelos/Grabando, esta pestana no
// suscribe el canal "sitio" del WebSocket, asi que cacheSitio casi
// siempre estaba vacio aca y la fila quedaba con solo la inicial.
//
// Reproducir usa el <video> nativo del navegador contra
// /api/grabaciones/video (soporta Range: se puede saltar a la mitad de
// un archivo de varios GB). Eliminar siempre manda a la Papelera de
// reciclaje del lado del servidor (nunca un borrado permanente) -- ver
// grabaciones.py: _a_la_papelera.

import { obtener, enviar, conToken } from "./api.js";
import { formatoMB, formatoGB, formatoHMS, formatoFechaArchivo, inicialDe } from "./formato.js";
import { mostrarError } from "./registro.js";

let ultimoListado = null;
let ultimoStreams = null;
let intervaloRefresco = null;
let filtro = "";
let orden = "recientes";

// -- parte abierta en el modal de reproduccion --------------------------
let grupoAbierto = null;   // el grupo completo, para poder cambiar de parte
let archivoAbierto = null;

function tamanoGrupo(g) {
  return g.tamano_total_bytes;
}

function claveOrdenar(g) {
  if (orden === "tamano") return -tamanoGrupo(g);
  if (orden === "duracion") return -(g.duracion_total_seg || 0);
  if (orden === "viejas") return g.inicio_mtime;
  return -g.inicio_mtime;   // "recientes" (por defecto)
}

function grupoVisible(g) {
  if (!filtro) return true;
  return g.modelo.toLowerCase().includes(filtro);
}

function textoDuracionGrupo(g) {
  if (g.duracion_total_seg == null) return "calculando...";
  const texto = formatoHMS(g.duracion_total_seg);
  return g.duracion_parcial ? texto + "+" : texto;
}

// -- la portada: fotograma real del .mp4, con el mismo fade de SPEC 05 --
//
// Mismo motivo que sincronizarMiniatura (sitio.js): un <img> recreado se
// vuelve a pedir siempre, y con el repintado cada 4s (entrarAGrabaciones)
// eso era una tormenta de GET a /api/grabaciones/miniatura por tarjeta
// por tick. Comparar por src y no reemplazar "por si cambio" es lo que
// hace que un repintado sin novedades cueste cero red.

function urlMiniatura(g) {
  const primera = g.partes[0];
  return conToken("/api/grabaciones/miniatura?archivo=" + encodeURIComponent(g.miniatura)
    + "&v=" + encodeURIComponent(primera.mtime));
}

function sincronizarPoster(contenedor, g) {
  const img = contenedor.querySelector("img");

  if (!g.miniatura) {
    const inicial = inicialDe(g.modelo);
    if (img || contenedor.textContent !== inicial) contenedor.textContent = inicial;
    return;
  }

  const url = urlMiniatura(g);
  if (img) {
    if (img.getAttribute("src") !== url) {
      img.classList.add("cargando");
      img.onload = () => img.classList.remove("cargando");
      img.setAttribute("src", url);
    }
    return;
  }
  const nuevo = document.createElement("img");
  nuevo.alt = "";
  nuevo.loading = "lazy";
  nuevo.decoding = "async";
  nuevo.classList.add("cargando");
  nuevo.onerror = () => nuevo.remove();
  nuevo.onload = () => nuevo.classList.remove("cargando");
  nuevo.setAttribute("src", url);
  contenedor.textContent = "";
  contenedor.appendChild(nuevo);
}

// -- la tarjeta: se crea una vez y despues se actualiza in-place ---------
// Mismo patron que vista_grabando.js:crearTarjeta/actualizarTarjeta --
// los nodos opcionales (badge de partes, etiqueta de calidad) estan
// siempre en el DOM y se prenden/apagan con .oculta.

function crearTarjeta(clave) {
  const div = document.createElement("div");
  div.className = "tarjeta-grabacion";
  div.dataset.clave = clave;
  div.innerHTML = `
    <button class="superficie-play" data-accion="reproducir-grupo" type="button">
      <div class="miniatura-grande"></div>
      <span class="etiqueta-partes-flot oculta"></span>
      <div class="marca-tiempo">
        <span class="duracion"></span>
        <span class="tamano"></span>
      </div>
    </button>
    <div class="cuerpo-grabacion">
      <div class="fila-nombre">
        <span class="nombre"></span>
        <span class="etiqueta-calidad oculta"></span>
      </div>
      <div class="detalle-grabacion"></div>
      <div class="acciones-tarjeta">
        <button class="boton-chico" data-accion="carpeta">carpeta</button>
        <button class="boton-chico peligro" data-accion="eliminar-grupo">eliminar</button>
      </div>
    </div>`;
  div._nodos = {
    play: div.querySelector(".superficie-play"),
    mini: div.querySelector(".miniatura-grande"),
    partes: div.querySelector(".etiqueta-partes-flot"),
    duracion: div.querySelector(".marca-tiempo .duracion"),
    tamano: div.querySelector(".marca-tiempo .tamano"),
    nombre: div.querySelector(".nombre"),
    calidad: div.querySelector(".etiqueta-calidad"),
    detalle: div.querySelector(".detalle-grabacion"),
    carpeta: div.querySelector("[data-accion='carpeta']"),
    eliminar: div.querySelector("[data-accion='eliminar-grupo']"),
  };
  return div;
}

function poner(nodo, texto) {
  if (nodo.textContent !== texto) nodo.textContent = texto;
}

function actualizarTarjeta(el, g) {
  const n = el._nodos;
  sincronizarPoster(n.mini, g);

  const multiple = g.partes.length > 1;
  n.partes.classList.toggle("oculta", !multiple);
  if (multiple) poner(n.partes, g.partes.length + " partes");
  n.play.setAttribute("aria-label", "Reproducir " + g.modelo);

  poner(n.duracion, textoDuracionGrupo(g));
  poner(n.tamano, formatoMB(g.tamano_total_bytes));

  poner(n.nombre, g.modelo);
  n.nombre.title = g.modelo;

  const calidad = [g.calidad, g.codec].filter(Boolean).join(" ");
  n.calidad.classList.toggle("oculta", !calidad);
  if (calidad) poner(n.calidad, calidad);

  poner(n.detalle, formatoFechaArchivo(g.fecha));

  n.carpeta.dataset.archivo = g.partes[0].archivo;
  n.eliminar.dataset.clave = g.clave;
  n.play.dataset.clave = g.clave;
}

// -- la grilla ------------------------------------------------------------

const tarjetas = new Map();   // clave -> elemento

export function renderGrabaciones() {
  const cont = document.getElementById("grabaciones-lista");
  if (!ultimoListado) return;
  const grupos = ultimoListado.grupos.filter(grupoVisible).slice().sort((a, b) => claveOrdenar(a) - claveOrdenar(b));

  const r = ultimoListado.resumen;
  document.getElementById("grabaciones-resumen").textContent =
    r.archivos + " archivo" + (r.archivos === 1 ? "" : "s") + " · " + formatoGB(r.bytes);

  if (grupos.length === 0) {
    tarjetas.clear();
    cont.innerHTML = '<div class="vacio">'
      + (ultimoListado.grupos.length === 0 ? "Todavia no hay grabaciones terminadas." : "Ninguna coincide con la busqueda.")
      + "</div>";
    return;
  }

  if (cont.querySelector(".vacio")) cont.innerHTML = "";   // venia del caso vacio

  const vistos = new Set();
  for (const g of grupos) {
    vistos.add(g.clave);
    let el = tarjetas.get(g.clave);
    if (!el) {
      el = crearTarjeta(g.clave);
      tarjetas.set(g.clave, el);
    }
    actualizarTarjeta(el, g);
    // appendChild sobre un nodo que ya esta en el DOM lo MUEVE, no lo
    // recrea -- la <img> de la portada no se vuelve a pedir.
    cont.appendChild(el);
  }
  for (const [clave, el] of tarjetas) {
    if (vistos.has(clave)) continue;
    el.remove();
    tarjetas.delete(clave);
  }
}

async function cargar() {
  try {
    ultimoListado = await obtener("/api/grabaciones");
  } catch (err) {
    tarjetas.clear();
    document.getElementById("grabaciones-lista").innerHTML =
      '<div class="vacio">no se pudo leer la lista de grabaciones</div>';
    return;
  }
  renderGrabaciones();
}

// -- resumen de datos\streams\ y el boton "Limpiar segmentos" ------------
// Recorte aparte del de /api/grabaciones: el escaneo de streams\ no es
// gratis, asi que se pide solo al entrar a la pestana y despues de un
// barrido manual, nunca dentro del setInterval de 4s de arriba.

function renderStreams() {
  const boton = document.getElementById("streams-limpiar");
  const span = document.getElementById("streams-resumen");
  if (!ultimoStreams) { span.textContent = ""; boton.disabled = true; return; }
  const s = ultimoStreams;
  span.textContent = s.sesiones + " sesion" + (s.sesiones === 1 ? "" : "es")
    + " sin limpiar (" + formatoGB(s.bytes) + ")";
  boton.disabled = s.bytes_borrables === 0;
  boton.textContent = s.bytes_borrables > 0
    ? "Limpiar segmentos (" + formatoGB(s.bytes_borrables) + ")"
    : "Limpiar segmentos";
}

async function cargarStreams() {
  try {
    ultimoStreams = await obtener("/api/streams");
  } catch (err) {
    ultimoStreams = null;
  }
  renderStreams();
}

async function limpiarStreams() {
  const boton = document.getElementById("streams-limpiar");
  boton.disabled = true;
  try {
    await enviar("/api/streams/limpiar", {});
  } catch (err) {
    mostrarError(err);
  }
  await cargarStreams();
}

// -- modal de reproduccion -----------------------------------------------
// Un <video> nativo no puede concatenar varios .mp4 en una sola
// reproduccion continua sin MediaSource (fuera de alcance para "basico")
// -- en vez de eso, con sesiones de varias partes el modal trae una
// lista chica para saltar de una parte a la otra (y, ahi, carpeta/
// eliminar por parte: la tarjeta de la grilla ya no tiene lugar para un
// panel desplegable).

function pararVideo() {
  const video = document.getElementById("modal-grabacion-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
}

function cargarParteEnVideo(archivo) {
  archivoAbierto = archivo;
  const video = document.getElementById("modal-grabacion-video");
  video.src = conToken("/api/grabaciones/video?archivo=" + encodeURIComponent(archivo));
  video.play().catch(() => {});   // el navegador puede negar el autoplay; no es un error real
  pintarListaPartes();
}

function filaParte(p) {
  const duracion = p.duracion_seg != null ? formatoHMS(p.duracion_seg) : "calculando...";
  const etiquetaParte = p.seg != null ? "parte " + p.seg : "archivo";
  const activa = p.archivo === archivoAbierto;
  return `
    <div class="fila-parte${activa ? " activa" : ""}" data-archivo="${p.archivo}">
      <span class="parte-etiqueta">${etiquetaParte}</span>
      <span class="parte-detalle">${duracion} · ${formatoMB(p.tamano_bytes)}</span>
      <div class="acciones-tarjeta">
        <button class="boton-chico${activa ? " activa" : ""}" data-accion="seleccionar-parte" data-archivo="${p.archivo}">reproducir</button>
        <button class="boton-chico" data-accion="carpeta-parte" data-archivo="${p.archivo}">carpeta</button>
        <button class="boton-chico peligro" data-accion="eliminar-parte" data-archivo="${p.archivo}">eliminar</button>
      </div>
    </div>`;
}

function pintarListaPartes() {
  const cont = document.getElementById("modal-grabacion-partes");
  if (!grupoAbierto || grupoAbierto.partes.length <= 1) { cont.innerHTML = ""; return; }
  cont.innerHTML = grupoAbierto.partes.map(filaParte).join("");
}

function abrirModal(grupo, archivo) {
  grupoAbierto = grupo;
  document.getElementById("modal-grabacion-nombre").textContent = grupo.modelo;
  document.getElementById("modal-grabacion").classList.remove("oculta");
  cargarParteEnVideo(archivo);
}

function cerrarModal() {
  pararVideo();
  grupoAbierto = null;
  archivoAbierto = null;
  document.getElementById("modal-grabacion").classList.add("oculta");
}

// -- acciones (reproducir / carpeta / eliminar) ---------------------------

function grupoPorClave(clave) {
  return ultimoListado && ultimoListado.grupos.find(g => g.clave === clave);
}

async function eliminarConConfirmacion(archivos, descripcion) {
  if (!confirm("Eliminar " + descripcion + "?\n\nVa a la Papelera de reciclaje, no se pierde para siempre."))
    return false;
  try {
    const r = await enviar("/api/grabaciones/borrar", { archivos });
    if (Object.keys(r.errores || {}).length) {
      alert("No se pudo eliminar: " + Object.values(r.errores).join(", "));
    }
  } catch (err) {
    alert("Error al eliminar: " + err.message);
  }
  await cargar();
  return true;
}

export function iniciarGrabaciones() {
  document.getElementById("grabaciones-buscar").addEventListener("input", (ev) => {
    filtro = ev.target.value.trim().toLowerCase();
    renderGrabaciones();
  });

  document.getElementById("grabaciones-orden").addEventListener("change", (ev) => {
    orden = ev.target.value;
    renderGrabaciones();
  });

  document.getElementById("streams-limpiar").addEventListener("click", limpiarStreams);

  document.getElementById("grabaciones-lista").addEventListener("click", async (ev) => {
    const boton = ev.target.closest("[data-accion]");
    if (!boton) return;
    const accion = boton.dataset.accion;
    const archivo = boton.dataset.archivo;
    const clave = boton.dataset.clave;

    if (accion === "reproducir-grupo") {
      const g = grupoPorClave(clave);
      if (g) abrirModal(g, g.partes[0].archivo);
    } else if (accion === "carpeta") {
      try { await enviar("/api/grabaciones/abrir_carpeta", { archivo }); } catch (err) { /* ignorar */ }
    } else if (accion === "eliminar-grupo") {
      const g = grupoPorClave(clave);
      if (!g) return;
      await eliminarConConfirmacion(g.partes.map(p => p.archivo),
        g.modelo + " (" + g.partes.length + " parte" + (g.partes.length > 1 ? "s" : "") + ", " + formatoMB(g.tamano_total_bytes) + ")");
    }
  });

  document.getElementById("modal-grabacion-cerrar").addEventListener("click", cerrarModal);
  document.getElementById("modal-grabacion").addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) cerrarModal();
  });
  document.getElementById("modal-grabacion-partes").addEventListener("click", async (ev) => {
    const boton = ev.target.closest("[data-accion]");
    if (!boton) return;
    const accion = boton.dataset.accion;
    const archivo = boton.dataset.archivo;

    if (accion === "seleccionar-parte") {
      cargarParteEnVideo(archivo);
    } else if (accion === "carpeta-parte") {
      try { await enviar("/api/grabaciones/abrir_carpeta", { archivo }); } catch (err) { /* ignorar */ }
    } else if (accion === "eliminar-parte") {
      const ultimaParte = grupoAbierto && grupoAbierto.partes.length <= 1;
      const borrado = await eliminarConConfirmacion([archivo], "esta parte");
      if (borrado && ultimaParte) cerrarModal();
      else if (borrado) {
        // el grupo sigue existiendo con menos partes: refrescar la lista
        // del modal contra los datos ya recargados por eliminarConConfirmacion
        grupoAbierto = grupoPorClave(grupoAbierto.clave) || grupoAbierto;
        if (archivo === archivoAbierto) cargarParteEnVideo(grupoAbierto.partes[0].archivo);
        else pintarListaPartes();
      }
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !grupoAbierto) return;
    // mismo motivo que vista_previa.js: si hay OTRO elemento en pantalla
    // completa (el modal de previa tiene boton propio para eso), este
    // Escape global no tiene que cerrar este modal tambien.
    if (document.fullscreenElement) return;
    cerrarModal();
  });
}

export function entrarAGrabaciones() {
  cargar();
  cargarStreams();
  if (intervaloRefresco) clearInterval(intervaloRefresco);
  // Refresco periodico: sobre todo para que las duraciones y portadas que
  // se estan calculando en segundo plano (ver grabaciones.py/miniaturas.py)
  // aparezcan solas sin que haga falta tocar nada.
  intervaloRefresco = setInterval(cargar, 4000);
}

export function salirDeGrabaciones() {
  if (intervaloRefresco) { clearInterval(intervaloRefresco); intervaloRefresco = null; }
}
