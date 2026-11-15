// Tabla unica de estados del sitio para todo el frontend (SPEC 24).
//
// Antes estaba copiada en vista_modelos.js (ESTADOS_PUBLICOS/
// ESTADOS_PRIVADOS/TEXTO_*/FILTROS) y vista_grabando.js
// (estadoVivoSitio), y cada estado nuevo habia que sumarlo a mano en
// todos -- asi quedo virtualPrivate como "privado" en la UI y "no en
// vivo" en el grabador. Su contraparte del lado Python es
// nucleo\scripts\mouflon.py (ESTADOS_PUBLICOS/ESTADOS_NO_PUBLICOS/
// ESTADOS_VIVOS): el frontend no puede importar Python y el nucleo no
// importa nada de nimbo\, asi que son dos fuentes, cada una unica en su
// lado. Si se suma un estado de stripchat, va en las DOS.
//
// clave = el status del sitio en minuscula (stripchat manda camelCase:
// "groupShow" -> "groupshow"). clase = la clase de color del badge
// ("publico" | "privado" | "online" | "offline"); filtro = la clave del
// chip de la barra de filtros que la incluye (null = ninguno propio).

export const ESTADOS = {
  // -- stripchat (user.status) --
  public:         { clase: "publico", texto: "SHOW PUBLICO", filtro: "publico" },
  live:           { clase: "publico", texto: "EN VIVO",      filtro: "publico" },
  private:        { clase: "privado", texto: "EN PRIVADO",   filtro: "privado" },
  virtualprivate: { clase: "privado", texto: "EN PRIVADO",   filtro: "privado" },
  p2p:            { clase: "privado", texto: "P2P",          filtro: "p2p" },
  p2pvoice:       { clase: "privado", texto: "P2P VOICE",    filtro: "p2p-voice" },
  groupshow:      { clase: "privado", texto: "GROUP SHOW",   filtro: "group-show" },
  ticketshow:     { clase: "privado", texto: "TICKET SHOW",  filtro: "ticket-show" },
  // conectada pero sin transmitir: no esta "en vivo" para el grabador
  idle:           { clase: "online",  texto: "EN LINEA",     filtro: null },
  off:            { clase: "offline", texto: "OFFLINE",      filtro: "offline" },
  // -- chaturbate (room_status) --
  // "public"/"private"/"offline"/"hidden" vistos en vivo
  // (pruebas\RESULTADOS.md, seccion 8); el resto es el vocabulario
  // conocido del sitio, todavia sin muestra propia. "hidden" es la camara
  // oculta de chaturbate (app CrazyTicket: "La camara esta oculta"), asi
  // que comparte badge y chip con la de stripchat. Para el grabador de
  // chaturbate todo lo que no es "public" es "nada que grabar" (SPEC 18)
  // -- esto es solo interfaz.
  offline:              { clase: "offline", texto: "OFFLINE",     filtro: "offline" },
  group:                { clase: "privado", texto: "GROUP SHOW",  filtro: "group-show" },
  hidden:               { clase: "privado", texto: "CAM OCULTA",  filtro: "cam-oculta" },
  away:                 { clase: "privado", texto: "AUSENTE",     filtro: null },
  "password protected": { clase: "privado", texto: "CON CLAVE",   filtro: null },
};

// Stripchat con user.status publico pero la camara oculta (CrazyTicket /
// "Hidden Cam"): lo publica grabador_directo.py como cam_oculta. No es
// show publico -- nada de REC ni del chip "Show publico".
export const CAM_OCULTA = { clase: "privado", texto: "CAM OCULTA", filtro: "cam-oculta" };

// Chips de estado de la barra de filtros, en orden. soloStripchat: no
// aparece en la barra de chaturbate.
export const FILTROS_ESTADO = [
  { clave: "privado",     etiqueta: "Privado" },
  { clave: "p2p",         etiqueta: "P2P" },
  { clave: "p2p-voice",   etiqueta: "P2P voice" },
  { clave: "group-show",  etiqueta: "Group show" },
  { clave: "ticket-show", etiqueta: "Ticket show" },
  { clave: "cam-oculta",  etiqueta: "Cam oculta" },
];

function normal(estadoSitio) {
  return (estadoSitio || "").toLowerCase();
}

// -> { clase, texto, filtro, clave, titulo? } o null si el estado no esta
// en la tabla (el llamador decide EN LINEA / SIN DATO, como siempre).
// camOculta gana: el sitio dice "public" justamente durante ese show.
export function infoEstado(estadoSitio, camOculta) {
  if (camOculta) {
    return { ...CAM_OCULTA, clave: "cam-oculta", titulo: String(camOculta) };
  }
  const s = normal(estadoSitio);
  const e = Object.prototype.hasOwnProperty.call(ESTADOS, s) ? ESTADOS[s] : null;
  return e ? { ...e, clave: s } : null;
}

export function esPublico(estadoSitio, camOculta) {
  const i = infoEstado(estadoSitio, camOculta);
  return !!i && i.clase === "publico";
}

// "En vivo pero no hay nada publico que grabar": privado/p2p/group/
// ticket/cam oculta (y away/hidden/con clave de chaturbate).
export function esNoPublico(estadoSitio, camOculta) {
  const i = infoEstado(estadoSitio, camOculta);
  return !!i && i.clase === "privado";
}

export function estaVivo(estadoSitio, camOculta) {
  return esPublico(estadoSitio, camOculta) || esNoPublico(estadoSitio, camOculta);
}
