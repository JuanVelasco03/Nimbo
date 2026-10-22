// Pestana "Registro": historial completo de estado\eventos_detector.jsonl
// / eventos_supervisor.jsonl / eventos_directo.jsonl / eventos_motor.jsonl,
// via /api/registro (ver nimbo/registro.py).
// Distinto de la consola del footer (registro.js): esta no se toca --
// sigue mostrando solo las ultimas lineas en vivo por SSE. Esta pestana
// lee el historial de disco, con filtro y paginado "cargar mas antiguas".
//
// setInterval del hilo principal (no un Web Worker como vista_previa.js):
// a proposito. Si la ventana pierde el foco y Chromium frena el timer,
// un visor de log que nadie esta mirando no necesita seguir pidiendo --
// lo contrario de la vista previa, que si tiene que seguir refrescando
// en segundo plano.

import { obtener } from "./api.js";

let cargado = false;
let cursorMasViejo = null;
let hayMasAntiguas = false;
let intervaloSeguir = null;

// Cambiar fuente/nivel/busqueda uno detras de otro (o el intervalo de
// "seguir" cayendo justo encima de un cambio de filtro) puede disparar
// dos pedidos a la vez -- sin esto, el que responde ultimo gana aunque
// sea el mas viejo, y una respuesta sin filtrar pisa a una filtrada.
// Cada carga se numera; si al volver ya hay una mas nueva en vuelo, se
// descarta en silencio.
let secuencia = 0;

function parametrosActuales() {
  const fuente = document.getElementById("registro-fuente").value;
  const nivel = document.getElementById("registro-nivel").value;
  const q = document.getElementById("registro-buscar").value.trim();
  return { fuente, nivel, q };
}

function formatoHora(t) {
  return new Date(t * 1000).toLocaleTimeString();
}

function filaHtml(fila) {
  const nivel = (fila.nivel || "INFO").toLowerCase();
  const div = document.createElement("div");
  div.className = "fila-registro nivel-" + nivel;
  div.innerHTML = `<span class="hora">${formatoHora(fila.t)}</span>`
    + `<span class="prefijo">${fila.prefijo || ""}</span>`
    + `<span class="mensaje">${fila.mensaje || ""}</span>`;
  return div;
}

function estabaAlFondo(lista) {
  return lista.scrollHeight - lista.scrollTop - lista.clientHeight < 40;
}

// Trae la pagina mas nueva (sin cursor) y reemplaza todo -- se usa al
// entrar a la pestana, al cambiar fuente/nivel/busqueda, y en cada tick
// de "seguir".
async function cargarDesdeElPrincipio() {
  const miSecuencia = ++secuencia;
  const lista = document.getElementById("registro-lista");
  const { fuente, nivel, q } = parametrosActuales();
  let r;
  try {
    r = await obtener("/api/registro?fuente=" + encodeURIComponent(fuente)
      + "&nivel=" + encodeURIComponent(nivel) + "&q=" + encodeURIComponent(q)
      + "&limite=400");
  } catch (err) {
    if (miSecuencia !== secuencia) return;   // ya hay una carga mas nueva en curso
    lista.innerHTML = '<div class="vacio">no se pudo leer el registro</div>';
    return;
  }
  if (miSecuencia !== secuencia) return;   // una carga mas nueva ya empezo; esta se descarta
  const alFondo = lista.childElementCount === 0 || estabaAlFondo(lista);
  lista.innerHTML = "";
  if (r.filas.length === 0) {
    lista.innerHTML = '<div class="vacio">sin lineas (todavia, o el filtro no encuentra nada)</div>';
  } else {
    for (const fila of r.filas) lista.appendChild(filaHtml(fila));
  }
  cursorMasViejo = r.cursor;
  hayMasAntiguas = r.hay_mas;
  document.getElementById("registro-mas-antiguas").classList.toggle("oculta", !hayMasAntiguas);
  document.getElementById("registro-conteo").textContent = r.filas.length + " lineas";
  if (alFondo) lista.scrollTop = lista.scrollHeight;
}

// "cargar mas antiguas": pide con el cursor devuelto y PREPENDE, sin
// tocar lo que ya esta pintado -- ancla el scroll a la fila que era la
// mas vieja antes de prependear, para que la vista no salte.
async function cargarMasAntiguas() {
  if (!hayMasAntiguas || cursorMasViejo == null) return;
  const miSecuencia = secuencia;   // sin cambiar de filtro mientras esta en vuelo
  const lista = document.getElementById("registro-lista");
  const { fuente, nivel, q } = parametrosActuales();
  const boton = document.getElementById("registro-mas-antiguas");
  boton.disabled = true;
  let r;
  try {
    r = await obtener("/api/registro?fuente=" + encodeURIComponent(fuente)
      + "&nivel=" + encodeURIComponent(nivel) + "&q=" + encodeURIComponent(q)
      + "&cursor=" + cursorMasViejo + "&limite=400");
  } catch (err) {
    boton.disabled = false;
    return;
  }
  if (miSecuencia !== secuencia) { boton.disabled = false; return; }   // cambio el filtro mientras esperaba: el cursor ya no vale
  const ancla = lista.firstElementChild;
  const alturaAntes = lista.scrollHeight;
  const fragment = document.createDocumentFragment();
  for (const fila of r.filas) fragment.appendChild(filaHtml(fila));
  if (ancla) lista.insertBefore(fragment, ancla);
  else lista.appendChild(fragment);
  lista.scrollTop += lista.scrollHeight - alturaAntes;   // mantiene la posicion visual
  cursorMasViejo = r.cursor;
  hayMasAntiguas = r.hay_mas;
  boton.classList.toggle("oculta", !hayMasAntiguas);
  boton.disabled = false;
}

function pararSeguir() {
  if (intervaloSeguir) { clearInterval(intervaloSeguir); intervaloSeguir = null; }
}

function arrancarSeguir() {
  pararSeguir();
  if (document.getElementById("registro-seguir").checked) {
    intervaloSeguir = setInterval(cargarDesdeElPrincipio, 3000);
  }
}

// Llamado desde el hook de navegacion de main.js: entra = carga + arranca
// "seguir" si esta tildado; sale = corta el intervalo (nadie esta
// mirando, no hace falta seguir pidiendo).
export function entrarARegistro() {
  if (!cargado) {
    cargado = true;
    cargarDesdeElPrincipio();
  }
  arrancarSeguir();
}

export function salirDeRegistro() {
  pararSeguir();
}

export function iniciarRegistro() {
  document.getElementById("registro-fuente").addEventListener("change", cargarDesdeElPrincipio);
  document.getElementById("registro-nivel").addEventListener("change", cargarDesdeElPrincipio);
  document.getElementById("registro-refrescar").addEventListener("click", cargarDesdeElPrincipio);
  document.getElementById("registro-mas-antiguas").addEventListener("click", cargarMasAntiguas);
  document.getElementById("registro-seguir").addEventListener("change", arrancarSeguir);

  let temporizadorBusqueda = null;
  document.getElementById("registro-buscar").addEventListener("input", () => {
    clearTimeout(temporizadorBusqueda);
    temporizadorBusqueda = setTimeout(cargarDesdeElPrincipio, 300);
  });
}
