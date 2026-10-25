// Pestana "Modelos": la grilla de favoritas y el buscador con
// autocompletado contra el sitio real (ver sitio.js / sitio.py).

import { obtener, bandera } from "./api.js";
import { inicialDe } from "./formato.js";
import { cacheSitio, sincronizarMiniatura, urlImagenProxy } from "./sitio.js";

// Favoritas locales (favoritas.json) + las que vienen de la cuenta real
// del sitio y estan en linea ahora (estado.siguiendo, ver sesion.py) --
// fusionadas en una sola lista, sin duplicar por nombre. Exportada: la
// usa tambien vista_previa.js para saber que mostrar en el modal.
//
// "grabando"/"online" ya NO significan "esta en grabar.txt" -- eso ahora
// es "enLista". La verdad de si esta grabando de VERDAD sale de
// estado.detector.streams[] (lo mismo que ya usa vista_grabando.js), asi
// que una modelo listada pero todavia no grabando muestra su status real
// en vez de un "GRABANDO" falso.
export function fusionarFavoritas(estado) {
  const enLista = new Set((estado.lista.seleccion || []).map(n => n.toLowerCase()));
  const vpn = new Set(estado.lista.vpn || []);
  const siguiendo = (estado.siguiendo && estado.siguiendo.modelos) || [];
  const streams = (estado.detector && estado.detector.streams) || [];
  const emisiones = (estado.supervisor && estado.supervisor.emisiones) || [];

  const streamPorNombre = new Map(streams.map(s => [(s.modelo || "").toLowerCase(), s]));
  const emisionPorNombre = new Map(emisiones.map(e => [(e.nombre || "").toLowerCase(), e]));

  function datosDeteccion(clave) {
    const s = streamPorNombre.get(clave);
    return {
      grabando: !!(s && s.grabando), privadoDetector: !!(s && s.privado),
      // ya entran segmentos de esta modelo pero el detector todavia no
      // elige variante (ventana de @espera, detectar_m3u8.py) -- sin esto
      // el badge salta de "EN LISTA" a "REC" de golpe al cabo de @espera
      // segundos, como si no hubiera pasado nada mientras tanto.
      detectado: !!(s && !s.grabando && s.estado === "detectado"),
      // sesion viva de verdad en disco (datos\streams\...) -- lo que
      // habilita el boton "ver en vivo" del modal de previa, distinto de
      // "grabando" (que tarda en confirmarse: ver @espera/@cambio del
      // detector) y de estar en grabar.txt.
      vivo: !!(s && s.sesiones && s.sesiones.length),
    };
  }

  // estadoSitio: siguiendo (cuenta real) > supervisor (vigila todo lo de
  // grabar.txt) > cache del buscador/estado puntual -- el primero que
  // tenga dato manda, nunca se mezclan a medio camino.
  function estadoSitioDe(clave, deSiguiendo) {
    if (deSiguiendo) return deSiguiendo;
    const sup = emisionPorNombre.get(clave);
    if (sup && sup.estado) return sup.estado;
    const cache = cacheSitio[clave];
    if (cache && cache.estado) return cache.estado;
    return "";
  }

  const filas = [];
  const porClave = new Map();

  for (const nombre of (estado.favoritas || [])) {
    const clave = nombre.toLowerCase();
    if (porClave.has(clave)) continue;
    const det = datosDeteccion(clave);
    const estadoSitio = estadoSitioDe(clave, "");
    const cache = cacheSitio[clave];
    const fila = {
      nombre, local: true, siguiendo: false,
      enLista: enLista.has(clave), enVpn: vpn.has(clave),
      estadoSitio,
      online: !!(cache && cache.online) || estadoVivo(estadoSitio),
      ...det,
    };
    porClave.set(clave, fila);
    filas.push(fila);
  }

  for (const m of siguiendo) {
    if (!m.online || !m.username) continue;
    const clave = m.username.toLowerCase();
    if (m.avatar) cacheSitio[clave] = { ...cacheSitio[clave], avatar: m.avatar, online: true };
    const det = datosDeteccion(clave);
    const existente = porClave.get(clave);
    if (existente) {
      existente.siguiendo = true;
      existente.online = true;
      existente.estadoSitio = m.estado || existente.estadoSitio;
      Object.assign(existente, det);
      continue;
    }
    const fila = {
      nombre: m.username, local: false, siguiendo: true,
      enLista: enLista.has(clave), enVpn: vpn.has(clave),
      estadoSitio: m.estado || "", online: true,
      ...det,
    };
    porClave.set(clave, fila);
    filas.push(fila);
  }

  return filas;
}

// "status" que trae el sitio -> mismo vocabulario de la pestana Grabando
// (ESTADOS_VIVOS de supervisor.py: public/live/groupShow/p2p/private/
// ticketShow, camelCase -- se compara en minuscula). "p2pVoice" no esta
// en ESTADOS_VIVOS del grabador (asi que el supervisor no abre pagina
// para ese estado -- eso es decision del grabador, no se toca desde
// aca), pero del lado de Nimbo igual hay que mostrarlo como privado y
// no como "EN LINEA" generico: es el mismo show p2p, solo sin video.
const ESTADOS_PUBLICOS = new Set(["public", "live"]);
const ESTADOS_PRIVADOS = new Set(["private", "virtualprivate", "p2p", "p2pvoice", "groupshow", "ticketshow"]);
const TEXTO_PRIVADO = {
  private: "EN PRIVADO", virtualprivate: "EN PRIVADO",
  p2p: "P2P", p2pvoice: "P2P VOICE", groupshow: "GROUP SHOW", ticketshow: "TICKET SHOW",
};
const TEXTO_PUBLICO = { public: "SHOW PUBLICO", live: "EN VIVO" };

export function estadoVivo(estadoSitio) {
  const s = (estadoSitio || "").toLowerCase();
  return ESTADOS_PUBLICOS.has(s) || ESTADOS_PRIVADOS.has(s);
}

// Unicamente el status real del sitio -- nunca "GRABANDO" aca, eso es
// grabacionDeFila(). Asi una modelo puede estar EN PRIVADO y grabando a
// la vez, y se ven las dos cosas en vez de que una tape a la otra.
export function estadoDeFila(fila) {
  const s = (fila.estadoSitio || "").toLowerCase();
  if (ESTADOS_PUBLICOS.has(s)) return { clase: "publico", texto: TEXTO_PUBLICO[s], clave: s };
  if (ESTADOS_PRIVADOS.has(s)) return { clase: "privado", texto: TEXTO_PRIVADO[s], clave: s };
  if (fila.online) return { clase: "online", texto: "EN LINEA", clave: "online" };
  if (s) return { clase: "offline", texto: "SIN DATO", clave: "offline" };
  return { clase: "offline", texto: "OFFLINE", clave: "offline" };
}

// Indicador de grabacion, totalmente aparte del status del sitio. null si
// no hay nada que mostrar (ni listada, ni grabando, ni guardada).
export function grabacionDeFila(fila) {
  if (fila.grabando) return { clase: "grabando", texto: "REC" };
  if (fila.privadoDetector) return { clase: "privado", texto: "GUARDADO" };
  if (fila.detectado) return { clase: "espera", texto: "CONECTANDO" };
  if (fila.enLista) return { clase: "espera", texto: "EN LISTA" };
  return null;
}

// -- filtros -----------------------------------------------------------
// Multi-seleccion (OR entre los prendidos); ninguno prendido = se ven
// todas. Cada filtro es un predicado sobre una fila ya fusionada.
const FILTROS = [
  { clave: "grabando", etiqueta: "Grabando", test: f => f.grabando },
  { clave: "en-lista", etiqueta: "En lista", test: f => f.enLista },
  { clave: "publico", etiqueta: "Show publico", test: f => estadoDeFila(f).clase === "publico" },
  { clave: "privado", etiqueta: "Privado", test: f => (f.estadoSitio || "").toLowerCase() === "private" || (f.estadoSitio || "").toLowerCase() === "virtualprivate" },
  { clave: "p2p", etiqueta: "P2P", test: f => (f.estadoSitio || "").toLowerCase() === "p2p" },
  { clave: "p2p-voice", etiqueta: "P2P voice", test: f => (f.estadoSitio || "").toLowerCase() === "p2pvoice" },
  { clave: "group-show", etiqueta: "Group show", test: f => (f.estadoSitio || "").toLowerCase() === "groupshow" },
  { clave: "ticket-show", etiqueta: "Ticket show", test: f => (f.estadoSitio || "").toLowerCase() === "ticketshow" },
  { clave: "offline", etiqueta: "Offline", test: f => estadoDeFila(f).clase === "offline" },
  { clave: "vpn", etiqueta: "VPN", test: f => f.enVpn },
  { clave: "siguiendo", etiqueta: "Siguiendo", test: f => f.siguiendo },
];

const filtrosActivos = new Set();
let ultimoEstadoModelos = null;

function filaPasaFiltros(fila) {
  if (filtrosActivos.size === 0) return true;
  for (const clave of filtrosActivos) {
    const filtro = FILTROS.find(f => f.clave === clave);
    if (filtro && filtro.test(fila)) return true;
  }
  return false;
}

function renderBarraFiltros(filas) {
  const cont = document.getElementById("barra-filtros-modelos");
  // Se arma una sola vez; despues solo se actualizan contadores y la
  // clase .prendido -- reconstruir el innerHTML cada 2s (un render mas,
  // via refrescar()) haria parpadear la barra y perder el click en curso.
  if (cont.childElementCount === 0) {
    cont.innerHTML = FILTROS.map(f => `
      <button class="chip-filtro" data-filtro="${f.clave}" type="button">
        ${f.etiqueta} <span class="cuenta"></span>
      </button>`).join("")
      + '<span id="conteo-modelos" class="ml-auto font-mono text-[0.78rem] text-atenuado"></span>';
  }

  let visibles = 0;
  for (const f of FILTROS) {
    const n = filas.filter(f.test).length;
    const boton = cont.querySelector(`[data-filtro="${f.clave}"]`);
    boton.querySelector(".cuenta").textContent = n;
    boton.classList.toggle("prendido", filtrosActivos.has(f.clave));
    boton.classList.toggle("en-cero", n === 0);
  }
  for (const fila of filas) if (filaPasaFiltros(fila)) visibles++;
  document.getElementById("conteo-modelos").textContent = visibles + " de " + filas.length;
}

export function iniciarFiltros() {
  document.getElementById("barra-filtros-modelos").addEventListener("click", (ev) => {
    const boton = ev.target.closest("[data-filtro]");
    if (!boton) return;
    const clave = boton.dataset.filtro;
    if (filtrosActivos.has(clave)) filtrosActivos.delete(clave);
    else filtrosActivos.add(clave);
    if (ultimoEstadoModelos) renderFavoritas(ultimoEstadoModelos);
  });
}

// -- la tarjeta de una modelo: se crea una vez y se actualiza in-place ---
//
// Mismo motivo que en vista_grabando.js (ver specs\congelado_interfaz.md):
// renderFavoritas corre en cada delta de estado -- una vez por segundo --
// y hasta el 25/10/2026 reconstruia el contenedor entero con innerHTML.
// Cada <img> recreado es un pedido nuevo a /api/sitio/imagen (un HTTPS
// saliente al CDN del lado del servidor), y con 32 modelos seguidas en la
// grilla eso saturaba el tope de 6 sockets por host de Chromium: los
// botones -- incluido "Actualizar", que vive en esta misma pestana --
// dejaban de responder.
//
// Todo lo opcional (SIGUIENDO, VPN, el segundo badge, los botones que
// dependen de `local`) esta siempre en el DOM y se prende/apaga con
// .oculta, asi la estructura no cambia nunca.

const tarjetasModelos = new Map();   // nombre -> elemento

function crearTarjetaModelo(nombre) {
  const div = document.createElement("div");
  div.className = "tarjeta-modelo";
  div.dataset.nombre = nombre;
  div.innerHTML = `
    <div class="riel"></div>
    <div class="miniatura-grande"></div>
    <div class="cuerpo">
      <div class="fila-nombre">
        <span class="nombre"></span>
        <span class="etiqueta-sigue oculta">SIGUIENDO</span>
        <span class="etiqueta-vpn oculta">VPN</span>
      </div>
      <div class="fila-estados">
        <div class="estado-badge" data-badge="sitio"><span class="bola"></span><span class="texto"></span></div>
        <div class="estado-badge oculta" data-badge="rec"><span class="bola"></span><span class="texto"></span></div>
      </div>
      <div class="acciones-tarjeta">
        <label class="conmutador" title="grabar">
          <input type="checkbox" data-accion="toggle-grabar">
          <span class="pista"></span>
        </label>
        <button class="boton-chico" data-accion="toggle-vpn"></button>
        <button class="boton-chico peligro oculta" data-accion="quitar-favorita">quitar</button>
        <button class="boton-chico oculta" data-accion="agregar-favorita">+ favoritas</button>
      </div>
    </div>`;
  const nom = div.querySelector(".nombre");
  nom.textContent = nombre;
  nom.title = nombre;
  for (const b of div.querySelectorAll("[data-accion]")) b.dataset.nombre = nombre;
  return div;
}

function ponerTexto(nodo, texto) {
  if (nodo.textContent !== texto) nodo.textContent = texto;
}

function pintarBadge(badge, info) {
  if (!info) {
    badge.classList.add("oculta");
    return;
  }
  const clase = "estado-badge " + info.clase;
  // className se pisa entero (incluido el .oculta) a proposito: la clase
  // de color es parte de la identidad del badge, no un modificador.
  if (badge.className !== clase) badge.className = clase;
  ponerTexto(badge.querySelector(".texto"), info.texto);
}

function actualizarTarjetaModelo(el, fila) {
  const { nombre, local, siguiendo, enVpn, enLista } = fila;
  const info = estadoDeFila(fila);
  const rec = grabacionDeFila(fila);

  const estado = rec && rec.clase === "grabando" ? "grabando" : info.clase;
  if (el.dataset.estado !== estado) el.dataset.estado = estado;
  sincronizarMiniatura(el.querySelector(".miniatura-grande"), nombre);

  el.querySelector(".etiqueta-sigue").classList.toggle("oculta", !siguiendo);
  el.querySelector(".etiqueta-vpn").classList.toggle("oculta", !enVpn);

  pintarBadge(el.querySelector("[data-badge='sitio']"), info);
  pintarBadge(el.querySelector("[data-badge='rec']"), rec);

  // No pisar el control que el usuario esta tocando justo ahora -- mismo
  // criterio que vista_ajustes.js:renderDirectivas, que ya salteaba el
  // campo con el foco para no comerse lo que se estaba escribiendo. Aca
  // importa igual: el click en el conmutador dispara un POST y un
  // refrescar(), y pisarle el checked en el medio lo haria parpadear.
  const check = el.querySelector("[data-accion='toggle-grabar']");
  if (check !== document.activeElement && check.checked !== enLista) {
    check.checked = enLista;
  }

  const botonVpn = el.querySelector("[data-accion='toggle-vpn']");
  botonVpn.dataset.vpnActivo = enVpn ? "1" : "0";
  ponerTexto(botonVpn, enVpn ? "quitar VPN" : "marcar VPN");

  el.querySelector("[data-accion='quitar-favorita']").classList.toggle("oculta", !local);
  el.querySelector("[data-accion='agregar-favorita']").classList.toggle("oculta", local);
}

function vaciarFavoritas(cont, html) {
  tarjetasModelos.clear();
  cont.innerHTML = html;
}

export function renderFavoritas(estado) {
  ultimoEstadoModelos = estado;
  const cont = document.getElementById("favoritas");
  const filas = fusionarFavoritas(estado);

  renderBarraFiltros(filas);

  const visibles = filas.filter(filaPasaFiltros);
  // con el filtro "Grabando" prendido, las que de verdad estan grabando
  // van primero -- el filtro ya las incluye a todas (es OR con el resto
  // de filtros prendidos), esto solo las prioriza arriba de la lista.
  // sort() es estable: dentro de cada grupo (grabando / no grabando) el
  // orden que ya traian no se mezcla.
  if (filtrosActivos.has("grabando")) {
    visibles.sort((a, b) => (b.grabando ? 1 : 0) - (a.grabando ? 1 : 0));
  }

  if (filas.length === 0) {
    vaciarFavoritas(cont, '<div class="vacio">Sin favoritas todavia. Agrega un nombre arriba, '
      + 'o pega tu sesion para ver a quien segui en linea.</div>');
    return;
  }
  if (visibles.length === 0) {
    vaciarFavoritas(cont, '<div class="vacio">Ninguna favorita coincide con el filtro elegido.</div>');
    return;
  }

  // El "antes" del experimento, igual que en vista_grabando.js:
  // localStorage.nimbo_tarjetas_in_place = "0" vuelve a reconstruir todo.
  if (!bandera("nimbo_tarjetas_in_place", true)) {
    tarjetasModelos.clear();
    cont.innerHTML = "";
    for (const fila of visibles) {
      const el = crearTarjetaModelo(fila.nombre);
      actualizarTarjetaModelo(el, fila);
      cont.appendChild(el);
    }
    return;
  }

  if (cont.querySelector(".vacio")) cont.innerHTML = "";   // venia del caso vacio

  const vistos = new Set();
  for (const fila of visibles) {
    vistos.add(fila.nombre);
    let el = tarjetasModelos.get(fila.nombre);
    if (!el) {
      el = crearTarjetaModelo(fila.nombre);
      tarjetasModelos.set(fila.nombre, el);
    }
    actualizarTarjetaModelo(el, fila);
    // appendChild sobre un nodo que ya esta en el DOM lo MUEVE, no lo
    // recrea: la <img> no se vuelve a pedir. Recorriendo `visibles` en
    // orden queda ordenado, y si nada cambio de lugar el navegador no
    // toca nada.
    cont.appendChild(el);
  }
  for (const [nombre, el] of tarjetasModelos) {
    if (vistos.has(nombre)) continue;
    el.remove();
    tarjetasModelos.delete(nombre);
  }
}

// -- estado de la sesion de la cuenta real ----------------------------------

export function renderSesion(estado) {
  const punto = document.getElementById("punto-sesion");
  const resumen = document.getElementById("resumen-sesion");
  const sesion = estado.sesion || { activa: false, guardado: null };
  const siguiendo = estado.siguiendo;

  if (siguiendo && siguiendo.autenticado === false) {
    punto.className = "punto mal";
    resumen.textContent = "la sesion caduco -- volve a pegarla";
  } else if (!sesion.activa) {
    punto.className = "punto mal";
    resumen.textContent = sesion.guardado ? "sesion vencida -- volve a pegarla" : "sin sesion";
  } else if (siguiendo && siguiendo.autenticado) {
    const enLinea = (siguiendo.modelos || []).length;
    punto.className = "punto ok";
    resumen.textContent = "sesion activa · " + enLinea + " en linea";
  } else {
    punto.className = "punto espera";
    resumen.textContent = "sesion guardada -- apreta Actualizar";
  }
}

// Solo el mostrar/ocultar el panel de pegado: guardar/olvidar/actualizar
// mutan el estado y terminan en un refrescar(), asi que se conectan en
// main.js (mismo criterio que boton-agregar-favorita).
export function iniciarPanelSesion() {
  document.getElementById("boton-toggle-sesion").addEventListener("click", () => {
    document.getElementById("panel-sesion").classList.toggle("oculta");
  });
}

// -- buscador con autocompletado -------------------------------------------

let temporizadorBusqueda = null;

function ocultarSugerencias() {
  document.getElementById("sugerencias").classList.add("oculta");
}

function renderSugerencias(resultados) {
  const cont = document.getElementById("sugerencias");
  if (!resultados.length) {
    cont.innerHTML = '<div class="vacio text-[0.85rem]">sin resultados</div>';
  } else {
    cont.innerHTML = resultados.map(m => `
      <div class="sugerencia" data-nombre="${m.username}">
        <div class="miniatura">${m.avatar ? `<img src="${urlImagenProxy(m.avatar)}" alt="" loading="lazy" onerror="this.remove()">` : inicialDe(m.username)}</div>
        <span class="nombre-sug">${m.username}</span>
        <span class="punto-online ${m.online ? "si" : ""}" title="${m.online ? "en linea" : "sin dato / offline"}"></span>
      </div>`).join("");
  }
  cont.classList.remove("oculta");
}

// Conecta el campo "nombre-nuevo" con el buscador. Las acciones que de
// verdad mutan algo (+favoritas, Grabar ahora) se conectan aparte, en
// main.js, porque terminan en un refrescar() del estado global.
export function iniciarBuscador() {
  const campo = document.getElementById("nombre-nuevo");

  campo.addEventListener("input", (ev) => {
    const valor = ev.target.value.trim();
    clearTimeout(temporizadorBusqueda);
    if (valor.length < 2) { ocultarSugerencias(); return; }
    temporizadorBusqueda = setTimeout(async () => {
      try {
        const r = await obtener("/api/sitio/buscar?q=" + encodeURIComponent(valor));
        // los resultados de esta misma busqueda alimentan tambien la
        // cache de miniaturas, para no pedirlas dos veces
        for (const m of r.resultados) {
          cacheSitio[m.username.toLowerCase()] = { ...cacheSitio[m.username.toLowerCase()], avatar: m.avatar, online: m.online };
        }
        renderSugerencias(r.resultados);
      } catch (err) { ocultarSugerencias(); }
    }, 300);
  });

  campo.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") document.getElementById("boton-grabar-directo").click();
    if (ev.key === "Escape") ocultarSugerencias();
  });

  document.getElementById("sugerencias").addEventListener("click", (ev) => {
    const fila = ev.target.closest(".sugerencia");
    if (!fila) return;
    campo.value = fila.dataset.nombre;
    ocultarSugerencias();
  });

  document.addEventListener("click", (ev) => {
    if (!ev.target.closest(".agregar-rapido-cont")) ocultarSugerencias();
  });
}
