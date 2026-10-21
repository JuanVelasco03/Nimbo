// Punto de entrada: arma el estado global, conecta la navegacion entre
// pestanas y conecta los botones cuya accion termina en un refrescar()
// del estado completo. Cada pestana sabe pintarse sola (ver vista_*.js);
// esto solo orquesta.

import { obtener, enviar, conectarWS } from "./api.js";
import { agregarLog, mostrarError } from "./registro.js";
import { aplicarSitio } from "./sitio.js";
import { renderTarjetas, renderBarraGlobal } from "./vista_grabando.js";
import { renderFavoritas, renderSesion, iniciarBuscador, iniciarPanelSesion, iniciarFiltros } from "./vista_modelos.js";
import { renderDirectivas, cargarPreferencias } from "./vista_ajustes.js";
import { revisarPreflight } from "./preflight.js";
import { renderPrevia, iniciarPrevia, abrirPreviaEnVivo } from "./vista_previa.js";
import { iniciarRegistro, entrarARegistro, salirDeRegistro } from "./vista_registro.js";
import { iniciarGrabaciones, entrarAGrabaciones, salirDeGrabaciones } from "./vista_grabaciones.js";

let ultimoEstado = null;
let ws = null;

function renderMotor(estado) {
  const m = estado.motor;
  const punto = document.getElementById("punto-estado");
  const boton = document.getElementById("boton-motor");
  const resumen = document.getElementById("resumen-estado");

  if (m.parando) {
    punto.className = "punto espera";
    resumen.textContent = "deteniendo...";
    boton.textContent = "Deteniendo...";
    boton.className = "principal";
    boton.disabled = true;
  } else if (m.detector_vivo && m.supervisor_vivo) {
    punto.className = "punto ok";
    resumen.textContent = "en marcha";
    boton.textContent = "Parar todo";
    boton.className = "principal parar";
    boton.disabled = false;
  } else if (m.detector_vivo || m.supervisor_vivo) {
    punto.className = "punto espera";
    resumen.textContent = "arrancando...";
    boton.textContent = "Parar todo";
    boton.className = "principal parar";
    boton.disabled = false;
  } else {
    punto.className = "punto mal";
    resumen.textContent = "detenido";
    boton.textContent = "Arrancar";
    boton.className = "principal";
    boton.disabled = false;
  }
}

function pintarEstado(estado) {
  renderMotor(estado);
  renderBarraGlobal(estado);
  renderTarjetas(estado);
  renderSesion(estado);
  renderFavoritas(estado);
  renderPrevia(estado);
  renderDirectivas(estado.lista.directivas || {});
}

// Reconciliacion puntual: ya no hay un setInterval corriendo esto cada
// 2s (el estado llega por push, ver conMensaje), pero sigue sirviendo
// como primera pintada antes de que el WebSocket conecte y como
// respaldo despues de cada accion que muta algo.
async function refrescar() {
  try {
    ultimoEstado = await obtener("/api/estado");
    pintarEstado(ultimoEstado);
  } catch (err) {
    document.getElementById("punto-estado").className = "punto mal";
    document.getElementById("resumen-estado").textContent = "sin conexion con Nimbo";
  }
}

// Reemplaza al /eventos de SSE: un solo canal bidireccional. "estado_inicial"
// y "estado" (delta, solo las secciones que cambiaron) se mezclan sobre
// ultimoEstado en vez de pisarlo entero -- asi los render* de abajo no
// necesitan saber que la mitad de las veces el objeto esta incompleto a
// proposito.
function conMensaje(msg) {
  if (msg.tipo === "estado_inicial") {
    ultimoEstado = msg.datos;
    pintarEstado(ultimoEstado);
  } else if (msg.tipo === "estado") {
    if (!ultimoEstado) return;   // todavia no llego el inicial: se ignora, ya viene completo
    ultimoEstado = { ...ultimoEstado, ...msg.datos };
    pintarEstado(ultimoEstado);
  } else if (msg.tipo === "sitio") {
    aplicarSitio(msg.datos);
    if (ultimoEstado) renderFavoritas(ultimoEstado);
  } else if (msg.tipo === "log") {
    agregarLog("linea-" + msg.datos.proceso, "[" + msg.datos.proceso + "] " + msg.datos.linea);
  } else if (msg.tipo === "aviso") {
    agregarLog("linea-aviso", msg.datos.texto);
  }
}

function conEstadoConexion(conectado) {
  if (!conectado) document.getElementById("punto-estado").className = "punto mal";
}

// -- navegacion entre pestanas -------------------------------------------
// Al entrar a "Modelos" se suscribe al canal "sitio" del WebSocket --
// el servidor es quien corre el refresco contra el sitio real ahora (ver
// servidor.py:_bucle_sitio); al salir se cancela, no tiene sentido
// seguir pidiendolo si nadie lo esta mirando. Mismo criterio con
// visibilitychange: la ventana sin foco tampoco necesita el refresco.

function enVistaModelos() {
  const vista = document.getElementById("vista-modelos");
  return vista && !vista.classList.contains("oculta");
}

document.addEventListener("visibilitychange", () => {
  if (!enVistaModelos()) return;
  ws.enviar(document.hidden
    ? { tipo: "cancelar", lote: "sitio" }
    : { tipo: "suscribir", canales: ["sitio"] });
});

function iniciarNavegacion() {
  for (const boton of document.querySelectorAll("nav button")) {
    boton.addEventListener("click", () => {
      for (const b of document.querySelectorAll("nav button")) b.classList.remove("activa");
      for (const v of document.querySelectorAll(".vista")) v.classList.add("oculta");
      boton.classList.add("activa");
      document.getElementById("vista-" + boton.dataset.vista).classList.remove("oculta");

      ws.enviar({ tipo: "cancelar", lote: "sitio" });
      salirDeRegistro();
      salirDeGrabaciones();

      if (boton.dataset.vista === "modelos") {
        ws.enviar({ tipo: "suscribir", canales: ["sitio"] });
      } else if (boton.dataset.vista === "registro") {
        entrarARegistro();
      } else if (boton.dataset.vista === "grabaciones") {
        entrarAGrabaciones();
      }
    });
  }
}

// -- acciones que mutan el estado y despues refrescan ---------------------

// Compartido entre la grilla de favoritas y el modal de vista previa
// (vista_previa.js): los mismos cuatro data-accion, siempre sobre
// "nombre" + un refrescar() al final.
async function manejarAccionFavorita(ev) {
  const nombre = ev.target.dataset.nombre;
  if (!nombre) return;
  try {
    if (ev.target.dataset.accion === "toggle-grabar") {
      if (ev.target.checked) await enviar("/api/lista/agregar", { nombre });
      else await enviar("/api/lista/quitar", { nombre });
    } else if (ev.target.dataset.accion === "toggle-vpn") {
      const activo = ev.target.dataset.vpnActivo !== "1";
      await enviar("/api/lista/vpn", { nombre, activo });
    } else if (ev.target.dataset.accion === "quitar-favorita") {
      await enviar("/api/favoritas/quitar", { nombre });
    } else if (ev.target.dataset.accion === "agregar-favorita") {
      await enviar("/api/favoritas/agregar", { nombre });
    } else {
      return;   // click en la tarjeta/modal que no es ninguna de estas acciones
    }
  } catch (err) { mostrarError(err); }
  refrescar();
}

function iniciarAcciones() {
  document.getElementById("boton-motor").addEventListener("click", async () => {
    if (!ultimoEstado) return;
    const m = ultimoEstado.motor;
    try {
      if (m.detector_vivo || m.supervisor_vivo) await enviar("/api/motor/parar", {});
      else await enviar("/api/motor/arrancar", {});
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("tarjetas").addEventListener("click", async (ev) => {
    const botonVivo = ev.target.closest("[data-accion='ver-vivo']");
    if (botonVivo) { abrirPreviaEnVivo(botonVivo.dataset.nombre); return; }
    const boton = ev.target.closest("[data-accion='quitar']");
    if (!boton) return;
    try {
      await enviar("/api/lista/quitar", { nombre: boton.dataset.nombre });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  // Las tarjetas de la grilla y el modal de vista previa tienen los
  // mismos controles (data-accion/data-nombre identicos) -- un solo
  // manejador para los dos, asi no hay dos copias de esta logica.
  document.getElementById("favoritas").addEventListener("click", manejarAccionFavorita);
  document.getElementById("modal-previa").addEventListener("click", manejarAccionFavorita);

  document.getElementById("boton-agregar-favorita").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/favoritas/agregar", { nombre: campo.value.trim() });
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-grabar-directo").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/lista/agregar", { nombre: campo.value.trim() });
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-guardar-sesion").addEventListener("click", async () => {
    const campo = document.getElementById("campo-curl");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/sesion/guardar", { curl: campo.value });
      campo.value = "";
      document.getElementById("panel-sesion").classList.add("oculta");
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-olvidar-sesion").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/olvidar", {});
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-actualizar-siguiendo").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/actualizar", {});
    } catch (err) { mostrarError(err); }
    refrescar();
  });
}

// -- arranque ---------------------------------------------------------

iniciarNavegacion();
iniciarAcciones();
iniciarBuscador();
iniciarPanelSesion();
iniciarFiltros();
iniciarPrevia();
iniciarRegistro();
iniciarGrabaciones();

refrescar();
ws = conectarWS(conMensaje, conEstadoConexion);
revisarPreflight();
cargarPreferencias();
