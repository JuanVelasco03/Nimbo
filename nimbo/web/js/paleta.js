// Paleta de comandos (Ctrl+K) y modal de ayuda ("?") -- SPEC 17, C13.
// Comparten el mismo patron de modal (.modal-fondo/.modal/.modal-cerrar)
// que ya usan modal-previa y modal-grabacion, y el mismo criterio de
// guarda por foco que main.js:iniciarAtajos (no robarle una tecla a un
// campo de texto).

import { enviar } from "./api.js";
import { abrirPreviaEnVivo } from "./vista_previa.js";

function irAPestana(nombreVista) {
  const boton = document.querySelector("nav button[data-vista='" + nombreVista + "']");
  if (boton) boton.click();
}

// Mismo criterio que main.js:primeraModeloGrabando -- leido del DOM
// (data-estado lo pone vista_grabando.js:actualizarTarjeta) para no
// importar nada de main.js ni duplicar el estado de la pestana
// "Grabando" en un tercer lugar.
function primeraModeloGrabando() {
  const tarjeta = document.querySelector('#tarjetas .tarjeta-modelo[data-estado="grabando"]');
  return tarjeta ? tarjeta.dataset.nombre : null;
}

const ACCIONES = [
  { etiqueta: "Ir a Grabando", atajo: "1", ejecutar: () => irAPestana("grabando") },
  { etiqueta: "Ir a Grabaciones", atajo: "2", ejecutar: () => irAPestana("grabaciones") },
  { etiqueta: "Ir a Modelos", atajo: "3", ejecutar: () => irAPestana("modelos") },
  { etiqueta: "Abrir Ajustes", atajo: "4", ejecutar: () => irAPestana("ajustes") },
  { etiqueta: "Ir a Registro", atajo: "5", ejecutar: () => irAPestana("registro") },
  {
    etiqueta: "Arrancar / parar la grabacion", atajo: "Espacio",
    ejecutar: () => {
      const boton = document.getElementById("boton-motor");
      if (!boton.disabled) boton.click();
    },
  },
  {
    etiqueta: "Ver en vivo la primera modelo grabando", atajo: "v",
    ejecutar: () => {
      const nombre = primeraModeloGrabando();
      if (nombre) abrirPreviaEnVivo(nombre);
    },
  },
  {
    etiqueta: "Buscar actualizaciones", atajo: "",
    ejecutar: () => {
      irAPestana("ajustes");
      enviar("/api/actualizacion/buscar", {}).catch(() => { /* el panel ya muestra el error */ });
    },
  },
];

// Todos los atajos reales, para el modal de ayuda: las acciones de
// arriba que tienen tecla propia, mas Ctrl+K/"?"/Escape, que no pasan
// por ACCIONES porque ya estamos adentro de su propio manejador cuando
// se disparan.
const ATAJOS_AYUDA = [
  ...ACCIONES.filter((a) => a.atajo).map((a) => ({ tecla: a.atajo, descripcion: a.etiqueta })),
  { tecla: "Ctrl+K", descripcion: "Paleta de comandos" },
  { tecla: "?", descripcion: "Esta ayuda" },
  { tecla: "Escape", descripcion: "Cerrar un modal" },
];

// -- paleta --------------------------------------------------------------

let indiceResaltado = 0;

function filtrarAcciones(texto) {
  const q = texto.trim().toLowerCase();
  if (!q) return ACCIONES;
  return ACCIONES.filter((a) => a.etiqueta.toLowerCase().includes(q));
}

function pintarPaleta(filtradas) {
  const cont = document.getElementById("paleta-lista");
  cont.innerHTML = "";
  filtradas.forEach((a, i) => {
    const fila = document.createElement("div");
    fila.className = "sugerencia" + (i === indiceResaltado ? " activa" : "");
    fila.innerHTML = `<span class="nombre-sug">${a.etiqueta}</span>`
      + (a.atajo ? `<span class="ayuda">${a.atajo}</span>` : "");
    fila.addEventListener("click", () => ejecutarYcerrar(a));
    cont.appendChild(fila);
  });
}

function ejecutarYcerrar(accion) {
  cerrarPaleta();
  accion.ejecutar();
}

function abrirPaleta() {
  const campo = document.getElementById("paleta-campo");
  indiceResaltado = 0;
  campo.value = "";
  document.getElementById("modal-paleta").classList.remove("oculta");
  pintarPaleta(ACCIONES);
  campo.focus();
}

function cerrarPaleta() {
  document.getElementById("modal-paleta").classList.add("oculta");
  // Sin esto el foco se queda en el campo (ahora oculto, pero sigue
  // siendo document.activeElement): cualquier guarda de "estoy en un
  // campo de texto" -- la de este mismo modulo para "?", la de
  // main.js:iniciarAtajos para 1-5/Espacio/v -- quedaria bloqueada para
  // siempre despues de usar la paleta una sola vez.
  document.getElementById("paleta-campo").blur();
}

function paletaAbierta() {
  return !document.getElementById("modal-paleta").classList.contains("oculta");
}

function iniciarPaleta() {
  const campo = document.getElementById("paleta-campo");
  campo.addEventListener("input", (ev) => {
    indiceResaltado = 0;
    pintarPaleta(filtrarAcciones(ev.target.value));
  });
  campo.addEventListener("keydown", (ev) => {
    const filtradas = filtrarAcciones(campo.value);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      indiceResaltado = Math.min(indiceResaltado + 1, filtradas.length - 1);
      pintarPaleta(filtradas);
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      indiceResaltado = Math.max(indiceResaltado - 1, 0);
      pintarPaleta(filtradas);
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      const elegida = filtradas[indiceResaltado];
      if (elegida) ejecutarYcerrar(elegida);
    } else if (ev.key === "Escape") {
      cerrarPaleta();
    }
  });
  document.getElementById("modal-paleta-cerrar").addEventListener("click", cerrarPaleta);
  document.getElementById("modal-paleta").addEventListener("click", (ev) => {
    if (ev.target.id === "modal-paleta") cerrarPaleta();   // click fuera del panel
  });
}

// -- ayuda -----------------------------------------------------------------

function pintarAyuda() {
  document.getElementById("ayuda-lista").innerHTML = ATAJOS_AYUDA.map((a) => `
    <div class="ajuste-fila">
      <span>${a.descripcion}</span>
      <kbd class="font-mono text-xs text-atenuado">${a.tecla}</kbd>
    </div>`).join("");
}

function abrirAyuda() {
  pintarAyuda();
  document.getElementById("modal-ayuda").classList.remove("oculta");
}

function cerrarAyuda() {
  document.getElementById("modal-ayuda").classList.add("oculta");
}

function ayudaAbierta() {
  return !document.getElementById("modal-ayuda").classList.contains("oculta");
}

function iniciarAyuda() {
  document.getElementById("modal-ayuda-cerrar").addEventListener("click", cerrarAyuda);
  document.getElementById("modal-ayuda").addEventListener("click", (ev) => {
    if (ev.target.id === "modal-ayuda") cerrarAyuda();
  });
}

// -- atajos de este modulo: Ctrl+K, "?", Escape -----------------------

function enCampoDeTexto() {
  const el = document.activeElement;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

export function iniciarPaletaYAyuda() {
  iniciarPaleta();
  iniciarAyuda();
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (paletaAbierta()) cerrarPaleta();
      else if (ayudaAbierta()) cerrarAyuda();
      return;
    }
    // Ctrl+K: a diferencia de "?", vale incluso con el foco en un campo
    // de texto -- es el mismo criterio de cualquier paleta de comandos
    // (lleva modificador, no se puede escribir "k" con Ctrl apretado).
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      if (!paletaAbierta()) abrirPaleta();
      return;
    }
    if (ev.key === "?" && !enCampoDeTexto() && !paletaAbierta() && !ayudaAbierta()) {
      ev.preventDefault();
      abrirAyuda();
    }
  });
}
