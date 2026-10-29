// Pestana "Modelos": la grilla de favoritas y el buscador con
// autocompletado contra el sitio real (ver sitio.js / sitio.py).

import { obtener, bandera } from "./api.js";
import { inicialDe } from "./formato.js";
import { cacheSitio, sincronizarMiniatura, urlImagenProxy } from "./sitio.js";
import { agregarLog } from "./registro.js";

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
  // El "generado" del propio detector.json/directo.json fusionado -- se
  // guarda en cada fila para que grabacionDeFila() mida la edad de
  // ultimoSegmento contra ESE reloj, nunca contra Date.now() (ver
  // edadSegmento mas abajo).
  const generado = (estado.detector && estado.detector.generado) || 0;

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
      // hora del ultimo segmento REAL (puente.py/grabador_directo.py) y
      // si el grabador esta congelado esperando volver a show publico --
      // sin esto "grabando" no distingue de "grabando pero sin bytes".
      ultimoSegmento: s && s.ultimo_segmento,
      esperando: !!(s && s.esperando),
      generado,
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

// Cuanto hace que llego el ultimo segmento real, medido contra el reloj
// del propio proceso que escribio el JSON ("generado"), nunca contra
// Date.now(): el reloj del cliente y el del detector/grabador no son el
// mismo, y asi el badge no se vuelve loco si el JSON queda viejo (eso lo
// cubre estado_captura.py marcando la fuente como "sin_dato" aparte).
// -> segundos, o null si no hay suficiente dato para decidir.
export const SIN_SENAL_S = 20;

export function edadSegmento(ultimoSegmento, generado) {
  if (!ultimoSegmento || !generado) return null;
  return generado - ultimoSegmento;
}

// Indicador de grabacion. null si no hay nada que mostrar (ni listada, ni
// grabando, ni guardada).
//
// OJO: ya NO es independiente del status del sitio. El sitio (estadoSitio,
// via siguiendo.json/supervisor.json -- se refresca cada 1-5s) es una señal
// MAS RAPIDA y MAS DIRECTA que "esperando" (el flag que publica el propio
// grabador, que recien lo nota en su propio ciclo de @inactividad/30s): si
// el sitio YA dice p2p/privado/group show/ticket show, no hay nada
// entrando de verdad aunque la sesion siga abierta unos segundos mas (el
// margen de @inactividad antes de que grabador_directo.py la cierre) -- y
// mostrar "REC" en ese momento es mentira, aunque sea por poco tiempo.
// -> true si el sitio YA dice que no es show publico (p2p/privado/group
// show/ticket show). Compartido por el badge, el filtro "Grabando" y el
// orden de la lista -- los tres necesitan el mismo criterio de "esto no
// cuenta como grabando de verdad", no cada uno el suyo.
export function sitioEsNoPublico(fila) {
  return ESTADOS_PRIVADOS.has((fila.estadoSitio || "").toLowerCase());
}

export function grabacionDeFila(fila) {
  const sitioNoPublico = sitioEsNoPublico(fila);

  if (fila.grabando && !sitioNoPublico) {
    const edad = edadSegmento(fila.ultimoSegmento, fila.generado);
    if (edad !== null && edad > SIN_SENAL_S) {
      return { clase: "mal", texto: "SIN SENAL" };
    }
    return { clase: "grabando", texto: "REC" };
  }
  // Fuera de show publico (lo sepamos por el sitio o por el propio
  // grabador): no se muestra como GRABANDO/REC aunque la sesion siga
  // tecnicamente abierta -- es la misma "en lista, esperando volver a
  // publico" que si nunca hubiera empezado a grabar.
  if (sitioNoPublico || fila.esperando) {
    if (fila.enLista) return { clase: "espera", texto: "EN LISTA" };
    return null;
  }
  if (fila.privadoDetector) return { clase: "privado", texto: "GUARDADO" };
  if (fila.detectado) return { clase: "espera", texto: "CONECTANDO" };
  if (fila.enLista) return { clase: "espera", texto: "EN LISTA" };
  return null;
}

// -- filtros -----------------------------------------------------------
// Multi-seleccion (OR entre los prendidos); ninguno prendido = se ven
// todas. Cada filtro es un predicado sobre una fila ya fusionada.
const FILTROS = [
  // sitioEsNoPublico(): si el sitio ya dice que no es show publico, no
  // cuenta como "grabando" aunque f.grabando (la sesion interna) todavia
  // este abierta esperando el margen de @inactividad -- si no, el filtro
  // "Grabando" seguia trayendo modelos en privado/p2p.
  { clave: "grabando", etiqueta: "Grabando", test: f => f.grabando && !sitioEsNoPublico(f) },
  { clave: "en-lista", etiqueta: "En lista", test: f => f.enLista },
  // f._infoSitio lo precalcula renderBarraFiltros() una sola vez por
  // fila (ver mas abajo) -- fallback a estadoDeFila(f) si algun llamador
  // externo corre este test sin pasar por ahi.
  { clave: "publico", etiqueta: "Show publico", test: f => (f._infoSitio || estadoDeFila(f)).clase === "publico" },
  { clave: "privado", etiqueta: "Privado", test: f => (f.estadoSitio || "").toLowerCase() === "private" || (f.estadoSitio || "").toLowerCase() === "virtualprivate" },
  { clave: "p2p", etiqueta: "P2P", test: f => (f.estadoSitio || "").toLowerCase() === "p2p" },
  { clave: "p2p-voice", etiqueta: "P2P voice", test: f => (f.estadoSitio || "").toLowerCase() === "p2pvoice" },
  { clave: "group-show", etiqueta: "Group show", test: f => (f.estadoSitio || "").toLowerCase() === "groupshow" },
  { clave: "ticket-show", etiqueta: "Ticket show", test: f => (f.estadoSitio || "").toLowerCase() === "ticketshow" },
  { clave: "offline", etiqueta: "Offline", test: f => (f._infoSitio || estadoDeFila(f)).clase === "offline" },
  { clave: "vpn", etiqueta: "VPN", test: f => f.enVpn },
  { clave: "siguiendo", etiqueta: "Siguiendo", test: f => f.siguiendo },
];

const filtrosActivos = new Set();
let ultimoEstadoModelos = null;

// Pide que la proxima renderFavoritas() reacomode de verdad el orden
// fisico de la grilla (reordenarse SOLA mientras el usuario esta
// tildando conmutadores abajo del todo es justo lo que no se quiere --
// ver el comentario largo en renderFavoritas). Se prende al cambiar los
// chips de prioridad y al entrar a la pestana; se apaga sola al final de
// cada render.
let pedirReordenModelos = true;
export function marcarReordenModelos() { pedirReordenModelos = true; }

// Orden fijo cuando varios de estos filtros estan prendidos a la vez: no
// se mezclan en la lista -- primero las que graban de verdad, despues las
// que estan en lista (pero no grabando), despues las que estan en show
// publico (pero ninguna de las anteriores). Una fila que no matchea
// ninguno de los tres (pero si algun otro filtro activo, p.ej. VPN) queda
// al final, en el orden que ya traia -- sort() es estable.
const ORDEN_PRIORIDAD = ["grabando", "en-lista", "publico"];

function rangoPrioridad(fila) {
  for (let i = 0; i < ORDEN_PRIORIDAD.length; i++) {
    const clave = ORDEN_PRIORIDAD[i];
    if (!filtrosActivos.has(clave)) continue;
    const filtro = FILTROS.find(f => f.clave === clave);
    if (filtro && filtro.test(fila)) return i;
  }
  return ORDEN_PRIORIDAD.length;
}

// Nodos de los chips, cacheados una sola vez: antes renderBarraFiltros
// hacia 11 filas.filter() completos (uno por filtro, cada uno recorriendo
// TODAS las filas y recalculando estadoDeFila() para "publico"/"offline")
// mas 22 querySelector por tick (uno por boton, uno por contador) -- a
// 1 Hz, con N modelos eso es ~11*N comparaciones repetidas y 22
// busquedas en el DOM por segundo. Ahora es una sola pasada sobre las
// filas (estadoDeFila se calcula una vez por fila, no una vez por
// filtro) y cero querySelector fuera de la construccion inicial.
const chipsFiltro = new Map();   // clave -> {boton, cuenta}
let elConteoModelos = null;

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
    chipsFiltro.clear();
  }
  if (chipsFiltro.size === 0) {
    for (const f of FILTROS) {
      const boton = cont.querySelector(`[data-filtro="${f.clave}"]`);
      chipsFiltro.set(f.clave, { boton, cuenta: boton.querySelector(".cuenta") });
    }
    elConteoModelos = document.getElementById("conteo-modelos");
  }

  const cuentas = new Array(FILTROS.length).fill(0);
  const haySeleccion = filtrosActivos.size > 0;
  const visibles = [];
  for (const fila of filas) {
    // estadoDeFila(fila) se calcula UNA vez por fila aca (lo usan los
    // filtros "publico"/"offline" via fila._infoSitio), no una vez por
    // cada uno de los 11 filtros.
    fila._infoSitio = estadoDeFila(fila);
    let pasa = !haySeleccion;
    for (let i = 0; i < FILTROS.length; i++) {
      if (!FILTROS[i].test(fila)) continue;
      cuentas[i]++;
      if (filtrosActivos.has(FILTROS[i].clave)) pasa = true;
    }
    if (pasa) visibles.push(fila);
  }

  for (let i = 0; i < FILTROS.length; i++) {
    const f = FILTROS[i];
    const { boton, cuenta } = chipsFiltro.get(f.clave);
    const n = cuentas[i];
    if (cuenta.textContent !== String(n)) cuenta.textContent = n;
    boton.classList.toggle("prendido", filtrosActivos.has(f.clave));
    boton.classList.toggle("en-cero", n === 0);
  }
  elConteoModelos.textContent = visibles.length + " de " + filas.length;
  return visibles;
}

export function iniciarFiltros() {
  document.getElementById("barra-filtros-modelos").addEventListener("click", (ev) => {
    const boton = ev.target.closest("[data-filtro]");
    if (!boton) return;
    const clave = boton.dataset.filtro;
    if (filtrosActivos.has(clave)) filtrosActivos.delete(clave);
    else filtrosActivos.add(clave);
    // Cambiar el filtro SI es el borde explicito donde corresponde
    // reacomodar todo -- ver pedirReordenModelos.
    marcarReordenModelos();
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
  // Resuelto UNA vez por tarjeta, no una vez por tick: actualizarTarjetaModelo
  // corre a 1 Hz (ver el comentario de mas abajo) y antes hacia ~12
  // querySelector por tarjeta en cada vuelta -- con 48 modelos en
  // siguiendo.json son ~600 busquedas en el DOM por segundo que no hacian
  // falta, porque ningun nodo de una tarjeta ya creada cambia de lugar.
  div._nodos = {
    mini: div.querySelector(".miniatura-grande"),
    sigue: div.querySelector(".etiqueta-sigue"),
    vpn: div.querySelector(".etiqueta-vpn"),
    badgeSitio: div.querySelector("[data-badge='sitio']"),
    badgeRec: div.querySelector("[data-badge='rec']"),
    check: div.querySelector("[data-accion='toggle-grabar']"),
    botonVpn: div.querySelector("[data-accion='toggle-vpn']"),
    quitar: div.querySelector("[data-accion='quitar-favorita']"),
    agregar: div.querySelector("[data-accion='agregar-favorita']"),
  };
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
  // fila._infoSitio ya lo calculo renderBarraFiltros() para esta misma
  // fila en esta misma vuelta -- evita una tercera corrida de
  // estadoDeFila() por tarjeta por tick.
  const info = fila._infoSitio || estadoDeFila(fila);
  const rec = grabacionDeFila(fila);

  const n = el._nodos;

  const estado = rec && rec.clase === "grabando" ? "grabando" : info.clase;
  if (el.dataset.estado !== estado) el.dataset.estado = estado;
  sincronizarMiniatura(n.mini, nombre);

  n.sigue.classList.toggle("oculta", !siguiendo);
  n.vpn.classList.toggle("oculta", !enVpn);

  pintarBadge(n.badgeSitio, info);
  pintarBadge(n.badgeRec, rec);

  // No pisar el control que el usuario esta tocando justo ahora -- mismo
  // criterio que vista_ajustes.js:renderDirectivas, que ya salteaba el
  // campo con el foco para no comerse lo que se estaba escribiendo. Aca
  // importa igual: el click en el conmutador dispara un POST y un
  // refrescar(), y pisarle el checked en el medio lo haria parpadear.
  if (n.check !== document.activeElement && n.check.checked !== enLista) {
    n.check.checked = enLista;
  }

  n.botonVpn.dataset.vpnActivo = enVpn ? "1" : "0";
  ponerTexto(n.botonVpn, enVpn ? "quitar VPN" : "marcar VPN");

  n.quitar.classList.toggle("oculta", !local);
  n.agregar.classList.toggle("oculta", local);
}

function vaciarFavoritas(cont, html) {
  tarjetasModelos.clear();
  cont.innerHTML = html;
}

export function renderFavoritas(estado) {
  ultimoEstadoModelos = estado;
  const cont = document.getElementById("favoritas");
  const filas = fusionarFavoritas(estado);

  // El array que devuelve ya viene filtrado: una sola pasada sobre
  // `filas` sirve para los contadores de la barra Y para esta lista, en
  // vez de que cada uno recorra todo de nuevo con su propio criterio.
  const visibles = renderBarraFiltros(filas);
  // Grabando / En lista / Show publico son OR entre si (el filtro ya los
  // incluye a todos en `visibles`), pero el usuario los quiere agrupados
  // y no mezclados: ver ORDEN_PRIORIDAD/rangoPrioridad mas arriba.
  const hayPrioridad = ORDEN_PRIORIDAD.some(c => filtrosActivos.has(c));
  if (hayPrioridad) {
    visibles.sort((a, b) => rangoPrioridad(a) - rangoPrioridad(b));
  }
  // Guardado por fila para que el bucle de abajo no tenga que
  // recalcularlo: una tarjeta nueva se inserta segun este rango, pero una
  // que ya estaba no se mueve salvo que pedirReordenModelos este prendido
  // (ver mas abajo).
  for (const fila of visibles) fila._rango = hayPrioridad ? rangoPrioridad(fila) : 0;

  if (filas.length === 0) {
    vaciarFavoritas(cont, '<div class="vacio">Sin favoritas todavia. Agrega un nombre arriba, '
      + 'o pega tu sesion para ver a quien segui en linea.</div>');
    pedirReordenModelos = true;   // la proxima vez que haya filas, reagrupar de cero
    return;
  }
  if (visibles.length === 0) {
    vaciarFavoritas(cont, '<div class="vacio">Ninguna favorita coincide con el filtro elegido.</div>');
    pedirReordenModelos = true;
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
    const esNueva = !el;
    if (esNueva) {
      el = crearTarjetaModelo(fila.nombre);
      tarjetasModelos.set(fila.nombre, el);
    }
    actualizarTarjetaModelo(el, fila);
    el._rango = fila._rango;

    if (pedirReordenModelos) {
      // Borde explicito (cambio de filtro, entrar a la pestana, primera
      // pintada): reagrupar todo de cero. appendChild sobre un nodo que
      // ya esta en el DOM lo MUEVE, no lo recrea -- la <img> no se vuelve
      // a pedir. Recorriendo `visibles` ya ordenado por rango, si nada
      // cambio de lugar el navegador no toca nada.
      cont.appendChild(el);
    } else if (esNueva) {
      // Tarjeta que recien aparece (una modelo nueva en linea): se
      // inserta en la posicion que le toca por rango SIN mover ninguna
      // de las que ya estaban -- eso es lo que el usuario esta mirando
      // mientras tilda conmutadores de a una, y moverlas bajo el cursor
      // es justo el problema que esto evita (ver pedirReordenModelos).
      let destino = null;
      for (const hijo of cont.children) {
        if (hijo !== el && (hijo._rango || 0) > el._rango) { destino = hijo; break; }
      }
      cont.insertBefore(el, destino);
    }
    // si no es nueva y no se pidio reorden: no se toca -- ni una
    // operacion de DOM, ni para las que no cambiaron de rango ni para
    // las que si (esas esperan al proximo borde explicito).
  }
  for (const [nombre, el] of tarjetasModelos) {
    if (vistos.has(nombre)) continue;
    el.remove();
    tarjetasModelos.delete(nombre);
  }
  pedirReordenModelos = false;
}

// -- estado de la sesion de la cuenta real ----------------------------------

export function renderSesion(estado) {
  const punto = document.getElementById("punto-sesion");
  const resumen = document.getElementById("resumen-sesion");
  const sesion = estado.sesion || { activa: false, guardado: null };
  const siguiendo = estado.siguiendo;

  // El click en "Actualizar" (main.js) deja un timestamp en el dataset
  // del boton y lo deshabilita. En cuanto siguiendo.json tiene un
  // "generado" mas nuevo que ESE click, la vuelta de verdad termino --
  // recien ahi se rehabilita y se avisa. Antes no habia ningun momento
  // en que el usuario supiera que la actualizacion ya estaba lista (ni
  // que habia fallado): el boton se podia apretar de nuevo en cualquier
  // momento y el unico rastro era la linea "ya hay una actualizacion en
  // curso" si llegabas a pisar la vuelta anterior.
  const botonAct = document.getElementById("boton-actualizar-siguiendo");
  if (botonAct && botonAct.dataset.actualizando) {
    const desde = Number(botonAct.dataset.actualizando);
    if (siguiendo && (siguiendo.generado || 0) > desde) {
      delete botonAct.dataset.actualizando;
      botonAct.disabled = false;
      const enLinea = (siguiendo.modelos || []).length;
      agregarLog("linea-ok", siguiendo.autenticado
        ? "actualizacion lista -- " + enLinea + " en linea"
        : "actualizacion lista -- " + (siguiendo.error || "sesion caducada"));
    }
  }

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

// Sin innerHTML: m.username viene del endpoint no oficial del sitio
// (sitio.py), no de RE_NOMBRE -- un username con HTML adentro no tiene
// que poder ejecutar nada en una ventana que lleva el token en la URL.
function renderSugerencias(resultados) {
  const cont = document.getElementById("sugerencias");
  cont.replaceChildren();
  if (!resultados.length) {
    const vacio = document.createElement("div");
    vacio.className = "vacio text-[0.85rem]";
    vacio.textContent = "sin resultados";
    cont.append(vacio);
  } else {
    for (const m of resultados) {
      const fila = document.createElement("div");
      fila.className = "sugerencia";
      fila.dataset.nombre = m.username;

      const miniatura = document.createElement("div");
      miniatura.className = "miniatura";
      if (m.avatar) {
        const img = document.createElement("img");
        img.setAttribute("src", urlImagenProxy(m.avatar));
        img.setAttribute("alt", "");
        img.setAttribute("loading", "lazy");
        img.addEventListener("error", () => img.remove(), { once: true });
        miniatura.append(img);
      } else {
        miniatura.textContent = inicialDe(m.username);
      }

      const nombre = document.createElement("span");
      nombre.className = "nombre-sug";
      nombre.textContent = m.username;

      const punto = document.createElement("span");
      punto.className = "punto-online" + (m.online ? " si" : "");
      punto.title = m.online ? "en linea" : "sin dato / offline";

      fila.append(miniatura, nombre, punto);
      cont.append(fila);
    }
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
