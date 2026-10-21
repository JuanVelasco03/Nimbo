// Pestana "Grabaciones": lista lo que ya quedo terminado en
// F:\grabador\grabaciones (via /api/grabaciones, ver nimbo/grabaciones.py).
// Agrupa por sesion -- un recorte que el detector tuvo que partir en
// varias partes (-seg1, -seg2...) aparece como UN grupo con varias
// partes, no como archivos sueltos sin relacion entre si.
//
// Reproducir usa el <video> nativo del navegador contra
// /api/grabaciones/video (soporta Range: se puede saltar a la mitad de
// un archivo de varios GB). Eliminar siempre manda a la Papelera de
// reciclaje del lado del servidor (nunca un borrado permanente) -- ver
// grabaciones.py: _a_la_papelera.

import { obtener, enviar, conToken } from "./api.js";
import { formatoMB, formatoGB, formatoHMS, formatoFechaArchivo } from "./formato.js";
import { construirMiniatura } from "./sitio.js";

let ultimoListado = null;
let intervaloRefresco = null;
let filtro = "";
let orden = "recientes";
const expandidos = new Set();   // claves de grupo con las partes desplegadas

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

function filaParte(g, p) {
  const duracion = p.duracion_seg != null ? formatoHMS(p.duracion_seg) : "calculando...";
  const etiquetaParte = p.seg != null ? "parte " + p.seg : "archivo";
  return `
    <div class="fila-parte" data-archivo="${p.archivo}">
      <span class="parte-etiqueta">${etiquetaParte}</span>
      <span class="parte-detalle">${duracion} · ${formatoMB(p.tamano_bytes)}</span>
      <div class="acciones-tarjeta">
        <button class="boton-chico" data-accion="reproducir-parte" data-archivo="${p.archivo}">reproducir</button>
        <button class="boton-chico" data-accion="carpeta" data-archivo="${p.archivo}">carpeta</button>
        <button class="boton-chico peligro" data-accion="eliminar-parte" data-archivo="${p.archivo}">eliminar</button>
      </div>
    </div>`;
}

function filaGrupo(g) {
  const multiple = g.partes.length > 1;
  const expandido = expandidos.has(g.clave);
  const calidad = [g.calidad, g.codec].filter(Boolean).join(" ");
  const primera = g.partes[0];

  return `
    <div class="fila-grabacion" data-clave="${g.clave}">
      <div class="miniatura-grande">${construirMiniatura(g.modelo)}</div>
      <div class="cuerpo-grabacion">
        <div class="fila-nombre">
          <span class="nombre" title="${g.modelo}">${g.modelo}</span>
          ${calidad ? `<span class="etiqueta-calidad">${calidad}</span>` : ""}
          ${multiple ? `<span class="etiqueta-partes">${g.partes.length} partes</span>` : ""}
        </div>
        <div class="detalle-grabacion">
          ${formatoFechaArchivo(g.fecha)} · ${textoDuracionGrupo(g)} · ${formatoMB(g.tamano_total_bytes)}
        </div>
      </div>
      <div class="acciones-tarjeta">
        <button class="boton-chico" data-accion="reproducir-grupo" data-clave="${g.clave}">reproducir</button>
        <button class="boton-chico" data-accion="carpeta" data-archivo="${primera.archivo}">carpeta</button>
        <button class="boton-chico peligro" data-accion="eliminar-grupo" data-clave="${g.clave}">eliminar</button>
        ${multiple ? `<button class="boton-chico" data-accion="expandir" data-clave="${g.clave}">${expandido ? "ocultar partes" : "ver partes"}</button>` : ""}
      </div>
    </div>
    ${multiple && expandido ? `<div class="partes-grupo">${g.partes.map(p => filaParte(g, p)).join("")}</div>` : ""}`;
}

export function renderGrabaciones() {
  const cont = document.getElementById("grabaciones-lista");
  if (!ultimoListado) return;
  const grupos = ultimoListado.grupos.filter(grupoVisible).slice().sort((a, b) => claveOrdenar(a) - claveOrdenar(b));

  const r = ultimoListado.resumen;
  document.getElementById("grabaciones-resumen").textContent =
    r.archivos + " archivo" + (r.archivos === 1 ? "" : "s") + " · " + formatoGB(r.bytes);

  if (grupos.length === 0) {
    cont.innerHTML = '<div class="vacio">'
      + (ultimoListado.grupos.length === 0 ? "Todavia no hay grabaciones terminadas." : "Ninguna coincide con la busqueda.")
      + "</div>";
    return;
  }
  cont.innerHTML = grupos.map(filaGrupo).join("");
}

async function cargar() {
  try {
    ultimoListado = await obtener("/api/grabaciones");
  } catch (err) {
    document.getElementById("grabaciones-lista").innerHTML =
      '<div class="vacio">no se pudo leer la lista de grabaciones</div>';
    return;
  }
  renderGrabaciones();
}

// -- modal de reproduccion -----------------------------------------------
// Un <video> nativo no puede concatenar varios .mp4 en una sola
// reproduccion continua sin MediaSource (fuera de alcance para "basico")
// -- en vez de eso, con sesiones de varias partes el modal trae un
// selector chico para saltar de una parte a la otra.

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
  pintarSelectorPartes();
}

function pintarSelectorPartes() {
  const cont = document.getElementById("modal-grabacion-partes");
  if (!grupoAbierto || grupoAbierto.partes.length <= 1) { cont.innerHTML = ""; return; }
  cont.innerHTML = grupoAbierto.partes.map(p => `
    <button class="boton-chico${p.archivo === archivoAbierto ? " activa" : ""}"
            data-accion="seleccionar-parte" data-archivo="${p.archivo}">
      parte ${p.seg != null ? p.seg : "?"}
    </button>`).join("");
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

// -- acciones (reproducir / carpeta / eliminar / expandir) ----------------

function grupoPorClave(clave) {
  return ultimoListado && ultimoListado.grupos.find(g => g.clave === clave);
}

async function eliminarConConfirmacion(archivos, descripcion) {
  if (!confirm("Eliminar " + descripcion + "?\n\nVa a la Papelera de reciclaje, no se pierde para siempre."))
    return;
  try {
    const r = await enviar("/api/grabaciones/borrar", { archivos });
    if (Object.keys(r.errores || {}).length) {
      alert("No se pudo eliminar: " + Object.values(r.errores).join(", "));
    }
  } catch (err) {
    alert("Error al eliminar: " + err.message);
  }
  cargar();
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

  document.getElementById("grabaciones-lista").addEventListener("click", async (ev) => {
    const boton = ev.target.closest("[data-accion]");
    if (!boton) return;
    const accion = boton.dataset.accion;
    const archivo = boton.dataset.archivo;
    const clave = boton.dataset.clave;

    if (accion === "expandir") {
      if (expandidos.has(clave)) expandidos.delete(clave);
      else expandidos.add(clave);
      renderGrabaciones();
    } else if (accion === "reproducir-grupo") {
      const g = grupoPorClave(clave);
      if (g) abrirModal(g, g.partes[0].archivo);
    } else if (accion === "reproducir-parte") {
      const g = ultimoListado.grupos.find(gr => gr.partes.some(p => p.archivo === archivo));
      if (g) abrirModal(g, archivo);
    } else if (accion === "carpeta") {
      try { await enviar("/api/grabaciones/abrir_carpeta", { archivo }); } catch (err) { /* ignorar */ }
    } else if (accion === "eliminar-grupo") {
      const g = grupoPorClave(clave);
      if (!g) return;
      await eliminarConConfirmacion(g.partes.map(p => p.archivo),
        g.modelo + " (" + g.partes.length + " parte" + (g.partes.length > 1 ? "s" : "") + ", " + formatoMB(g.tamano_total_bytes) + ")");
    } else if (accion === "eliminar-parte") {
      await eliminarConConfirmacion([archivo], "esta parte");
    }
  });

  document.getElementById("modal-grabacion-cerrar").addEventListener("click", cerrarModal);
  document.getElementById("modal-grabacion").addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) cerrarModal();
  });
  document.getElementById("modal-grabacion-partes").addEventListener("click", (ev) => {
    const boton = ev.target.closest("[data-accion='seleccionar-parte']");
    if (boton) cargarParteEnVideo(boton.dataset.archivo);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && grupoAbierto) cerrarModal();
  });
}

export function entrarAGrabaciones() {
  cargar();
  if (intervaloRefresco) clearInterval(intervaloRefresco);
  // Refresco periodico: sobre todo para que las duraciones que se estan
  // calculando en segundo plano (ver grabaciones.py) aparezcan solas sin
  // que haga falta tocar nada.
  intervaloRefresco = setInterval(cargar, 4000);
}

export function salirDeGrabaciones() {
  if (intervaloRefresco) { clearInterval(intervaloRefresco); intervaloRefresco = null; }
}
