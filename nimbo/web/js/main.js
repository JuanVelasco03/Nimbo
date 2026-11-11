// Punto de entrada: arma el estado global, conecta la navegacion entre
// pestanas y conecta los botones cuya accion termina en un refrescar()
// del estado completo. Cada pestana sabe pintarse sola (ver vista_*.js);
// esto solo orquesta.

import { obtener, enviar, conectarWS } from "./api.js";
import { agregarLog, mostrarError } from "./registro.js";
import { aplicarSitio } from "./sitio.js";
import { renderTarjetas, renderBarraGlobal } from "./vista_grabando.js";
import { renderFavoritas, renderSesion, iniciarBuscador, iniciarPanelSesion, iniciarFiltros, iniciarLogin, iniciarSeccionesSitio, marcarReordenModelos, marcarReubicarModelo } from "./vista_modelos.js";
import { renderDirectivas, cargarPreferencias, renderRutas, cargarRutas, cargarNotificaciones, cargarAccesoLan, iniciarAccesoLan, cargarActualizaciones } from "./vista_ajustes.js";
import { revisarPreflight } from "./preflight.js";
import { renderSalud } from "./salud.js";
import { renderPrevia, iniciarPrevia, abrirPreviaEnVivo } from "./vista_previa.js";
import { iniciarRegistro, entrarARegistro, salirDeRegistro } from "./vista_registro.js";
import { iniciarGrabaciones, entrarAGrabaciones, salirDeGrabaciones } from "./vista_grabaciones.js";
import { iniciarPaletaYAyuda } from "./paleta.js";

let ultimoEstado = null;
let ws = null;

function renderMotor(estado) {
  const m = estado.motor;
  const transporte = document.getElementById("estado-transporte");
  const boton = document.getElementById("boton-motor");
  const resumen = document.getElementById("resumen-estado");

  let clave, texto, botonTexto, botonClase, deshabilitado;
  if (m.parando) {
    clave = "espera"; texto = "deteniendo...";
    botonTexto = "Deteniendo..."; botonClase = "principal"; deshabilitado = true;
  } else if (m.reiniciando) {
    clave = "espera"; texto = "reiniciando el " + m.reiniciando + "...";
    botonTexto = "Parar todo"; botonClase = "principal parar"; deshabilitado = false;
  } else if (m.rendido && m.rendido.length) {
    clave = "mal"; texto = "el " + m.rendido.join(", ") + " no arranca -- revisa el registro";
    botonTexto = "Arrancar"; botonClase = "principal"; deshabilitado = false;
  } else if (!m.detector_vivo && !m.supervisor_vivo && m.proximo_reintento
              && m.proximo_reintento > Date.now() / 1000) {
    clave = "espera"; texto = "reintento en " + Math.ceil(m.proximo_reintento - Date.now() / 1000) + "s";
    botonTexto = "Arrancar"; botonClase = "principal"; deshabilitado = false;
  } else if (m.detector_vivo && m.supervisor_vivo) {
    clave = "ok"; texto = "en marcha";
    botonTexto = "Parar todo"; botonClase = "principal parar"; deshabilitado = false;
  } else if (m.detector_vivo || m.supervisor_vivo) {
    clave = "espera"; texto = "arrancando...";
    botonTexto = "Parar todo"; botonClase = "principal parar"; deshabilitado = false;
  } else {
    clave = "mal"; texto = "detenido";
    botonTexto = "Arrancar"; botonClase = "principal"; deshabilitado = false;
  }

  // el punto y el texto de al lado visten SIEMPRE el mismo color de senal
  // (ver tailwind.css ".estado-transporte"): el vistazo rapido no deberia
  // depender de distinguir un circulo de 10px.
  document.getElementById("punto-estado").className = "punto " + clave;
  transporte.className = "estado-transporte " + clave;
  resumen.textContent = texto;
  boton.textContent = botonTexto;
  boton.className = botonClase;
  boton.disabled = deshabilitado;
}

// Que secciones de `estado` mira cada render. El delta del WebSocket trae
// solo las que cambiaron (servidor.py:_bucle_estado), asi que no hace
// falta repintar las vistas que no dependen de ninguna de ellas.
//
// Ojo con lo que esto NO arregla: "detector" cambia en CADA tick (los
// contadores de bytes de detector.json/directo.json), asi que las tres
// vistas caras igual corren una vez por segundo. Lo que las hace baratas
// es que reconcilian in-place en vez de reconstruirse (ver
// vista_grabando.js / vista_modelos.js y specs\congelado_interfaz.md);
// esta tabla solo saca del camino a las que ademas no tienen por que
// mirar.
const DEPENDE = [
  [renderMotor, ["motor"]],
  [renderBarraGlobal, ["disco", "detector"]],
  [renderTarjetas, ["lista", "detector", "supervisor"]],
  [renderSesion, ["sesion", "siguiendo", "login", "sesion_chaturbate", "siguiendo_chaturbate"]],
  [renderFavoritas, ["lista", "favoritas", "siguiendo", "siguiendo_chaturbate", "detector", "supervisor"]],
  [renderPrevia, ["detector", "lista", "favoritas", "siguiendo", "supervisor"]],
  [(estado) => renderDirectivas(estado.lista.directivas || {}), ["lista"]],
  [renderRutas, ["motor", "disco"]],
  [renderSalud, ["salud"]],
];

// secciones = null -> pintar todo (primera pintada, estado_inicial, y
// cualquier refrescar() puntual: ahi llega el estado completo).
function pintarEstado(estado, secciones) {
  for (const [render, claves] of DEPENDE) {
    if (secciones && !claves.some(c => secciones.has(c))) continue;
    render(estado);
  }
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
    document.getElementById("estado-transporte").className = "estado-transporte mal";
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
    pintarEstado(ultimoEstado, new Set(Object.keys(msg.datos)));
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
  if (!conectado) {
    document.getElementById("punto-estado").className = "punto mal";
    document.getElementById("estado-transporte").className = "estado-transporte mal";
  }
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
        // Borde explicito: al volver a esta pestana si corresponde
        // reagrupar por prioridad (ver vista_modelos.js:pedirReordenModelos).
        marcarReordenModelos();
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
  // SPEC 18: cada boton de una tarjeta lleva su sitio en el dataset (ver
  // vista_modelos.js:crearTarjetaModelo) -- sin esto, cualquier accion
  // sobre una tarjeta de chaturbate pegaba contra stripchat (el default
  // del servidor para los clientes de antes de este spec).
  const sitio = ev.target.dataset.sitio || "stripchat";
  try {
    if (ev.target.dataset.accion === "toggle-grabar") {
      if (ev.target.checked) await enviar("/api/lista/agregar", { nombre, sitio });
      // SPEC 20: el sitio tambien al quitar. SPEC 18 cubrio el lado
      // "agregar" y dejo este sin cubrir (coherente con que el backend
      // todavia no lo aceptaba), asi que destildar "grabar" en una tarjeta
      // de chaturbate dejaba el nombre en @chaturbate= -- invisible, y
      // marcado para la proxima vez que se agregara como stripchat.
      else await enviar("/api/lista/quitar", { nombre, sitio });
      // en las dos direcciones: tildar sube hacia "Grabando", destildar
      // baja hacia "Show publico" (o la saca de la grilla), y las dos
      // tardan lo mismo en confirmarse del lado del detector.
      marcarReubicarModelo(nombre);
    } else if (ev.target.dataset.accion === "toggle-vpn") {
      const activo = ev.target.dataset.vpnActivo !== "1";
      await enviar("/api/lista/vpn", { nombre, activo });
    } else if (ev.target.dataset.accion === "quitar-favorita") {
      await enviar("/api/favoritas/quitar", { nombre, sitio });
    } else if (ev.target.dataset.accion === "agregar-favorita") {
      await enviar("/api/favoritas/agregar", { nombre, sitio });
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
      const r = m.detector_vivo || m.supervisor_vivo
        ? await enviar("/api/motor/parar", {})
        : await enviar("/api/motor/arrancar", {});
      if (r && r.ya_corriendo) {
        agregarLog("linea-aviso", "ya hay una orden en curso para el motor -- espera");
      }
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("tarjetas").addEventListener("click", async (ev) => {
    const botonVivo = ev.target.closest("[data-accion='ver-vivo']");
    if (botonVivo) {
      abrirPreviaEnVivo(botonVivo.dataset.nombre, botonVivo.dataset.sitio || "stripchat");
      return;
    }
    const boton = ev.target.closest("[data-accion='quitar']");
    if (!boton) return;
    try {
      // El mismo `|| "stripchat"` que usa ver-vivo seis lineas arriba: el
      // dataset.sitio lo escribe vista_grabando.js:actualizarTarjeta.
      await enviar("/api/lista/quitar", {
        nombre: boton.dataset.nombre,
        sitio: boton.dataset.sitio || "stripchat",
      });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  // Las tarjetas de la grilla y el modal de vista previa tienen los
  // mismos controles (data-accion/data-nombre identicos) -- un solo
  // manejador para los dos, asi no hay dos copias de esta logica. SPEC 18:
  // la grilla de chaturbate usa el mismo manejador (sus tarjetas ya llevan
  // data-sitio="chaturbate", ver vista_modelos.js:crearTarjetaModelo).
  document.getElementById("favoritas").addEventListener("click", manejarAccionFavorita);
  document.getElementById("favoritas-chaturbate").addEventListener("click", manejarAccionFavorita);
  document.getElementById("modal-previa").addEventListener("click", manejarAccionFavorita);

  document.getElementById("boton-agregar-favorita").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/favoritas/agregar", { nombre: campo.value.trim(), sitio: "stripchat" });
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-grabar-directo").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo");
    const nombre = campo.value.trim();
    if (!nombre) return;
    try {
      await enviar("/api/lista/agregar", { nombre, sitio: "stripchat" });
      marcarReubicarModelo(nombre);
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  // SPEC 18: la barra de agregar de chaturbate -- sin buscador (sin
  // endpoint de catalogo sin cuenta, ver sitio_chaturbate.py), solo el
  // campo + los mismos dos botones que la de stripchat.
  document.getElementById("boton-agregar-favorita-cb").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo-cb");
    const nombre = campo.value.trim();
    if (!nombre) return;
    try {
      await enviar("/api/favoritas/agregar", { nombre, sitio: "chaturbate" });
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-grabar-directo-cb").addEventListener("click", async () => {
    const campo = document.getElementById("nombre-nuevo-cb");
    const nombre = campo.value.trim();
    if (!nombre) return;
    try {
      await enviar("/api/lista/agregar", { nombre, sitio: "chaturbate" });
      marcarReubicarModelo(nombre);
      campo.value = "";
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  // Click en una sugerencia del buscador (vista_modelos.js:iniciarBuscador
  // ya oculta la lista y fija el campo): un listener aparte por sitio
  // sobre #sugerencias/#sugerencias-cb, porque iniciarBuscador() no
  // importa enviar()/refrescar() a proposito -- mismo patron que
  // manejarAccionFavorita.
  document.getElementById("sugerencias").addEventListener("click", async (ev) => {
    const fila = ev.target.closest(".sugerencia");
    if (!fila || !fila.dataset.nombre) return;
    try {
      await enviar("/api/favoritas/agregar", { nombre: fila.dataset.nombre, sitio: "stripchat" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  // SPEC 21: chaturbate con cuenta tambien tiene buscador (ver
  // sitio_chaturbate.py), con el mismo patron que stripchat arriba.
  document.getElementById("sugerencias-cb").addEventListener("click", async (ev) => {
    const fila = ev.target.closest(".sugerencia");
    if (!fila || !fila.dataset.nombre) return;
    try {
      await enviar("/api/favoritas/agregar", { nombre: fila.dataset.nombre, sitio: "chaturbate" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-login").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/login/iniciar", { sitio: "stripchat" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-cancelar-login").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/login/cancelar", {});
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-guardar-sesion").addEventListener("click", async () => {
    const campo = document.getElementById("campo-curl");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/sesion/guardar", { curl: campo.value, sitio: "stripchat" });
      campo.value = "";
      document.getElementById("panel-sesion").classList.add("oculta");
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-olvidar-sesion").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/olvidar", { sitio: "stripchat" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-actualizar-siguiendo").addEventListener("click", async (ev) => {
    await iniciarActualizarSiguiendo(ev.currentTarget, "stripchat");
  });

  // SPEC 21: la misma barra de sesion, para la cuenta de chaturbate --
  // sufijo "-cb" en los ids (ver index.html) y "sitio: chaturbate" en el
  // cuerpo de cada POST (las rutas /api/sesion/* ya lo aceptan, default
  // "stripchat" para no romper a un cliente viejo).
  document.getElementById("boton-login-cb").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/login/iniciar", { sitio: "chaturbate" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-cancelar-login-cb").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/login/cancelar", {});
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-guardar-sesion-cb").addEventListener("click", async () => {
    const campo = document.getElementById("campo-curl-cb");
    if (!campo.value.trim()) return;
    try {
      await enviar("/api/sesion/guardar", { curl: campo.value, sitio: "chaturbate" });
      campo.value = "";
      document.getElementById("panel-sesion-cb").classList.add("oculta");
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-olvidar-sesion-cb").addEventListener("click", async () => {
    try {
      await enviar("/api/sesion/olvidar", { sitio: "chaturbate" });
    } catch (err) { mostrarError(err); }
    refrescar();
  });

  document.getElementById("boton-actualizar-siguiendo-cb").addEventListener("click", async (ev) => {
    await iniciarActualizarSiguiendo(ev.currentTarget, "chaturbate");
  });
}

// Extraido para que lo compartan el boton de Stripchat y el de
// Chaturbate (SPEC 21) -- unico cambio real entre los dos es el "sitio"
// que se manda y, por lo tanto, cual de los dos siguiendo(_chaturbate).json
// mira renderSesion() para saber cuando la vuelta termino.
async function iniciarActualizarSiguiendo(boton, sitio) {
  try {
    // 202 {"ya_corriendo": true} = el dedupe de servidor.py:_lanzar_tarea
    // descarto este click porque la vuelta anterior todavia corre (hasta
    // sesion.TOPE_TOTAL_S, o el watchdog de TOPE_TAREA si quedo colgada).
    // Antes se descartaba igual pero sin decirlo, y se veia como un
    // boton roto.
    const r = await enviar("/api/sesion/actualizar", { sitio });
    if (r && r.ya_corriendo) {
      agregarLog("linea-aviso", "ya hay una actualizacion en curso -- espera a que termine");
    } else {
      // El timestamp queda en el dataset para que renderSesion()
      // (vista_modelos.js) sepa cuando la vuelta de verdad termino --
      // ver siguiendo.generado alla. boton.disabled se cae solo desde
      // ahi, o a los 90s por el watchdog del servidor en el peor caso.
      boton.dataset.actualizando = String(Date.now() / 1000);
      boton.disabled = true;
      // Red de seguridad del lado del cliente: si siguiendo.json nunca
      // llega a actualizarse (la tarea murio sin escribir nada, o esta
      // pestana se quedo sola sin otro click que dispare el watchdog
      // del servidor), el boton no tiene que quedar deshabilitado para
      // siempre. Mismo tope que sesion.TOPE_TOTAL_S + margen.
      setTimeout(() => {
        if (boton.dataset.actualizando) {
          delete boton.dataset.actualizando;
          boton.disabled = false;
        }
      }, 60000);
    }
  } catch (err) { mostrarError(err); }
  refrescar();
}

// -- atajos de teclado globales (SPEC 17, C13) -------------------------
// 1-5 cambian de pestana en el orden del nav (ver index.html), Espacio
// alterna arrancar/parar (el mismo boton que ya tiene su propio click),
// "v" abre la previa de la primera modelo que este grabando. Ctrl+K
// (paleta de comandos) y "?" (ayuda) viven en paleta.js -- comparten el
// mismo patron de modal, asi que van juntos ahi.

const PESTANA_POR_TECLA = {
  "1": "grabando", "2": "grabaciones", "3": "modelos", "4": "ajustes", "5": "registro",
};

function enCampoDeTexto() {
  const el = document.activeElement;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

function otroModalAbierto() {
  const paleta = document.getElementById("modal-paleta");
  const ayuda = document.getElementById("modal-ayuda");
  return !paleta.classList.contains("oculta") || !ayuda.classList.contains("oculta");
}

// Leido del DOM (data-estado lo pone vista_grabando.js:actualizarTarjeta)
// y no de ultimoEstado.detector a proposito: asi paleta.js puede usar el
// mismo criterio para su propia accion "ver en vivo" sin importar nada
// de aca (evita un ciclo main.js <-> paleta.js).
function primeraModeloGrabando() {
  const tarjeta = document.querySelector('#tarjetas .tarjeta-modelo[data-estado="grabando"]');
  return tarjeta ? { nombre: tarjeta.dataset.nombre, sitio: tarjeta.dataset.sitio || "stripchat" } : null;
}

function iniciarAtajos() {
  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;   // Ctrl+K es de paleta.js
    if (enCampoDeTexto() || otroModalAbierto()) return;

    if (ev.key in PESTANA_POR_TECLA) {
      const boton = document.querySelector("nav button[data-vista='" + PESTANA_POR_TECLA[ev.key] + "']");
      if (boton) boton.click();
    } else if (ev.key === " ") {
      // preventDefault: Espacio sobre un <button> con foco ya lo clickea
      // solo (comportamiento nativo) -- sin esto, con el foco todavia en
      // "boton-motor" despues de un click previo, esta misma tecla lo
      // dispararia DOS veces (arrancar y parar en el mismo golpe).
      ev.preventDefault();
      const boton = document.getElementById("boton-motor");
      if (!boton.disabled) boton.click();
    } else if (ev.key === "v" || ev.key === "V") {
      const fila = primeraModeloGrabando();
      if (fila) abrirPreviaEnVivo(fila.nombre, fila.sitio);
    }
  });
}

// -- arranque ---------------------------------------------------------

iniciarNavegacion();
iniciarAcciones();
iniciarBuscador();
iniciarPanelSesion();
iniciarLogin();
iniciarFiltros();
iniciarSeccionesSitio();
iniciarPrevia();
iniciarRegistro();
iniciarGrabaciones();
iniciarAccesoLan();
iniciarAtajos();
iniciarPaletaYAyuda();

refrescar();
ws = conectarWS(conMensaje, conEstadoConexion);
revisarPreflight();
cargarPreferencias();
cargarRutas();
cargarNotificaciones();
cargarAccesoLan();
cargarActualizaciones();
