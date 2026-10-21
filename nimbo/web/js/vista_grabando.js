// Pestana "Grabando": la grilla de tarjetas con el estado real de cada
// modelo en grabar.txt (detector.json + supervisor.json) y la barra de
// espacio en disco / tunel.

import { formatoMB, formatoDuracion, formatoGB } from "./formato.js";
import { construirMiniatura } from "./sitio.js";

function estadoVivoSitio(estado) {
  // mismo vocabulario que ESTADOS_PUBLICOS+ESTADOS_PRIVADOS de
  // vista_modelos.js -- "p2pvoice" se agrega aca tambien para que esta
  // pestana no la muestre como "OFFLINE"/"SIN SENAL" (el supervisor no
  // le abre pagina -- no esta en ESTADOS_VIVOS de supervisor.py -- pero
  // el status igual llega por "sup.estado" y hoy caia a una rama que no
  // le correspondia).
  return ["public", "live", "groupshow", "p2p", "p2pvoice", "private", "ticketshow"]
    .includes((estado || "").toLowerCase());
}

function construirTarjeta(nombre, det, sup, enVpn) {
  let clase = "offline", texto = "detectando...", detalle = "";

  if (det && det.grabando) {
    clase = "grabando";
    texto = "GRABANDO";
    detalle = [det.elegida || det.mejor || "", formatoDuracion(det.inicio), formatoMB(det.bytes)]
      .filter(Boolean).join(" · ");
  } else if (det && det.privado) {
    clase = "privado";
    texto = "SHOW PRIVADO";
    detalle = "guardado, esperando a que vuelva";
  } else if (sup && sup.pagina_abierta && !sup.alto) {
    clase = "online";
    texto = "ABRIENDO LA PAGINA";
    detalle = "esperando el video...";
  } else if (sup && sup.pagina_abierta && sup.alto) {
    clase = "online";
    texto = "CONECTANDO";
    detalle = "reproduciendo " + sup.alto + "p, decidiendo calidad...";
  } else if (sup && estadoVivoSitio(sup.estado)) {
    clase = "online";
    texto = "EN LINEA";
    detalle = "abriendo...";
  } else if (sup && sup.fallos > 0) {
    clase = "mal";
    texto = "SIN SENAL";
    detalle = "reintentando...";
  } else if (sup) {
    clase = "offline";
    texto = "OFFLINE";
    detalle = "vigilando cada 60s";
  }

  const div = document.createElement("div");
  // mismo layout que .tarjeta-modelo (pestana Modelos): miniatura grande
  // arriba en vez de la chica al costado -- las reglas de color del
  // .riel por data-estado ya cubren las dos clases (ver tailwind.css).
  div.className = "tarjeta-modelo";
  div.dataset.estado = clase;
  div.innerHTML = `
    <div class="riel"></div>
    <div class="miniatura-grande">${construirMiniatura(nombre)}</div>
    <div class="cuerpo">
      <div class="fila-nombre">
        <span class="nombre" title="${nombre}">${nombre}</span>
        ${enVpn ? '<span class="etiqueta-vpn">VPN</span>' : ""}
      </div>
      <div class="estado-badge ${clase}"><span class="bola"></span>${texto}</div>
      <div class="detalle">${detalle}</div>
      <div class="acciones-tarjeta">
        ${det && det.sesiones && det.sesiones.length
          ? `<button class="boton-chico" data-accion="ver-vivo" data-nombre="${nombre}">ver en vivo</button>`
          : ""}
        <button class="boton-chico peligro" data-accion="quitar" data-nombre="${nombre}">Parar / quitar</button>
      </div>
    </div>`;
  return div;
}

export function renderTarjetas(estado) {
  const cont = document.getElementById("tarjetas");
  const nombres = estado.lista.seleccion.filter(n => !/^\d+$/.test(n) && n !== "*");
  if (nombres.length === 0) {
    cont.innerHTML = '<div class="vacio">Nadie en grabar.txt todavia. Agrega una modelo desde la pestana "Modelos".</div>';
    return;
  }
  const streams = (estado.detector && estado.detector.streams) || [];
  const emisiones = (estado.supervisor && estado.supervisor.emisiones) || [];
  const vpn = new Set(estado.lista.vpn || []);

  cont.innerHTML = "";
  for (const nombre of nombres) {
    const det = streams.find(s => (s.modelo || "").toLowerCase() === nombre.toLowerCase());
    const sup = emisiones.find(e => e.nombre.toLowerCase() === nombre.toLowerCase());
    cont.appendChild(construirTarjeta(nombre, det, sup, vpn.has(nombre.toLowerCase())));
  }
}

export function renderBarraGlobal(estado) {
  const disco = estado.disco;
  document.getElementById("info-disco").textContent = disco
    ? "espacio libre: " + formatoGB(disco.libres) + " de " + formatoGB(disco.total)
    : "espacio: -";
  const det = estado.detector;
  const resumenTunel = document.getElementById("resumen-tunel");
  if (det && det.tunel_activo) {
    resumenTunel.textContent = "tunel: " + det.pedidos_tunel + " pedidos/min";
    resumenTunel.className = det.pedidos_tunel > 40 ? "alerta" : "";
  } else {
    resumenTunel.textContent = "";
  }
}
