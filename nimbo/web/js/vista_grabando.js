// Pestana "Grabando": la grilla de tarjetas con el estado real de cada
// modelo en grabar.txt (detector.json + supervisor.json) y la barra de
// espacio en disco / tunel.

import { bandera } from "./api.js";
import { formatoMB, formatoDuracion, formatoGB } from "./formato.js";
import { sincronizarMiniatura } from "./sitio.js";

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

// -- el estado que muestra una tarjeta (puro: ni DOM ni efectos) --------

function estadoDeTarjeta(det, sup) {
  let clase = "offline", texto = "detectando...", detalle = "";

  if (det && det.grabando) {
    clase = "grabando";
    texto = "GRABANDO";
    detalle = [det.elegida || det.mejor || "", formatoDuracion(det.inicio), formatoMB(det.bytes)]
      .filter(Boolean).join(" · ");
  } else if (det && det.estado === "detectado") {
    // ya entran segmentos pero el detector todavia no elige variante
    // (ventana de @espera) -- mismo hueco que vista_modelos.js tapa con
    // "CONECTANDO" en el badge chico; aca usamos otro texto para no
    // repetir el "CONECTANDO" que ya significa otra cosa mas abajo
    // (pagina abierta + video reproduciendo, antes de que llegue nada
    // al detector).
    clase = "online";
    texto = "ELIGIENDO CALIDAD";
    detalle = "ya llegan segmentos, decidiendo que variante grabar...";
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

  return { clase, texto, detalle };
}

// -- la tarjeta: se crea una vez y despues se actualiza in-place ---------
//
// Por que no se reconstruye con innerHTML en cada refresco, que es lo que
// hacia hasta el 25/10/2026: el delta de estado llega una vez por segundo
// (los contadores de bytes de detector.json cambian siempre), asi que un
// innerHTML aca es la grilla entera recreada a 1 Hz -- y cada <img>
// recreado es un pedido nuevo a /api/sitio/imagen, o sea un HTTPS
// saliente al CDN por modelo por segundo. Con 6-8 modelos eso llenaba el
// tope de 6 sockets por host de Chromium y los botones dejaban de
// responder. Ver specs\congelado_interfaz.md.
//
// Los nodos opcionales (la etiqueta VPN, el boton "ver en vivo") estan
// siempre en el DOM y se prenden/apagan con .oculta: asi la estructura no
// cambia nunca y actualizar es solo escribir texto y clases.

function crearTarjeta(nombre) {
  const div = document.createElement("div");
  div.className = "tarjeta-modelo";
  div.dataset.nombre = nombre;
  // mismo layout que .tarjeta-modelo (pestana Modelos): miniatura grande
  // arriba en vez de la chica al costado -- las reglas de color del
  // .riel por data-estado ya cubren las dos clases (ver tailwind.css).
  div.innerHTML = `
    <div class="riel"></div>
    <div class="miniatura-grande"></div>
    <div class="cuerpo">
      <div class="fila-nombre">
        <span class="nombre"></span>
        <span class="etiqueta-vpn oculta">VPN</span>
      </div>
      <div class="estado-badge"><span class="bola"></span><span class="texto"></span></div>
      <div class="detalle"></div>
      <div class="acciones-tarjeta">
        <button class="boton-chico oculta" data-accion="ver-vivo">ver en vivo</button>
        <button class="boton-chico peligro" data-accion="quitar">Parar / quitar</button>
      </div>
    </div>`;
  const nom = div.querySelector(".nombre");
  nom.textContent = nombre;
  nom.title = nombre;
  for (const b of div.querySelectorAll("[data-accion]")) b.dataset.nombre = nombre;
  return div;
}

// Escribe solo lo que cambio. Comparar antes de asignar no es cosmetico:
// escribir el mismo textContent igual invalida el layout de ese nodo, y
// esto corre una vez por segundo por tarjeta.
function poner(nodo, texto) {
  if (nodo.textContent !== texto) nodo.textContent = texto;
}

function actualizarTarjeta(el, nombre, det, sup, enVpn) {
  const { clase, texto, detalle } = estadoDeTarjeta(det, sup);

  if (el.dataset.estado !== clase) el.dataset.estado = clase;
  sincronizarMiniatura(el.querySelector(".miniatura-grande"), nombre);

  const badge = el.querySelector(".estado-badge");
  const claseBadge = "estado-badge " + clase;
  if (badge.className !== claseBadge) badge.className = claseBadge;
  poner(badge.querySelector(".texto"), texto);
  poner(el.querySelector(".detalle"), detalle);

  el.querySelector(".etiqueta-vpn").classList.toggle("oculta", !enVpn);
  const vivo = !!(det && det.sesiones && det.sesiones.length);
  el.querySelector("[data-accion='ver-vivo']").classList.toggle("oculta", !vivo);
}

// -- la grilla ----------------------------------------------------------

// nombre -> elemento, para reconciliar en vez de recrear. Se vacia junto
// con el contenedor en los casos "vacio", o quedarian elementos huerfanos
// apuntando a nodos que ya no estan en el documento.
const tarjetas = new Map();

function vaciar(cont, html) {
  tarjetas.clear();
  cont.innerHTML = html;
}

export function renderTarjetas(estado) {
  const cont = document.getElementById("tarjetas");
  const nombres = estado.lista.seleccion.filter(n => !/^\d+$/.test(n) && n !== "*");
  if (nombres.length === 0) {
    vaciar(cont, '<div class="vacio">Nadie en grabar.txt todavia. Agrega una modelo desde la pestana "Modelos".</div>');
    return;
  }
  const streams = (estado.detector && estado.detector.streams) || [];
  const emisiones = (estado.supervisor && estado.supervisor.emisiones) || [];
  const vpn = new Set(estado.lista.vpn || []);

  // El "antes" del experimento (localStorage.nimbo_tarjetas_in_place =
  // "0"): la reconstruccion completa de antes del 25/10/2026, para poder
  // medir las dos variantes sobre la misma instalacion y la misma red.
  // No es un fallback -- ver specs\congelado_interfaz.md.
  if (!bandera("nimbo_tarjetas_in_place", true)) {
    tarjetas.clear();
    cont.innerHTML = "";
    for (const nombre of nombres) {
      const el = crearTarjeta(nombre);
      actualizarTarjeta(el, nombre,
                        streams.find(s => (s.modelo || "").toLowerCase() === nombre.toLowerCase()),
                        emisiones.find(e => e.nombre.toLowerCase() === nombre.toLowerCase()),
                        vpn.has(nombre.toLowerCase()));
      cont.appendChild(el);
    }
    return;
  }

  if (cont.querySelector(".vacio")) cont.innerHTML = "";   // venia del caso vacio

  const vistos = new Set();
  for (const nombre of nombres) {
    vistos.add(nombre);
    let el = tarjetas.get(nombre);
    if (!el) {
      el = crearTarjeta(nombre);
      tarjetas.set(nombre, el);
    }
    actualizarTarjeta(el, nombre,
                      streams.find(s => (s.modelo || "").toLowerCase() === nombre.toLowerCase()),
                      emisiones.find(e => e.nombre.toLowerCase() === nombre.toLowerCase()),
                      vpn.has(nombre.toLowerCase()));
    // appendChild sobre un nodo que ya esta en el DOM lo MUEVE al final,
    // no lo recrea: la <img> no se vuelve a pedir. Haciendolo en el orden
    // de `nombres` queda ordenado igual que grabar.txt, y para el caso
    // normal (nada cambio de lugar) el navegador no toca nada.
    cont.appendChild(el);
  }
  for (const [nombre, el] of tarjetas) {
    if (vistos.has(nombre)) continue;
    el.remove();
    tarjetas.delete(nombre);
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
