// Miniaturas y datos en vivo del sitio real (sitio.py del lado del
// servidor). cacheSitio: nombre en minuscula -> {avatar, online, estado}.
// Ya no lo llena este modulo pidiendo /api/sitio/* por su cuenta: el
// servidor corre ese refresco el mismo (canal "sitio" del WebSocket, ver
// servidor.py:_bucle_sitio) y aplicarSitio() solo mezcla lo que llega --
// main.js suscribe/desuscribe ese canal al entrar/salir de "Modelos". Si
// el sitio cambio su endpoint de busqueda, el canal no manda nada y la
// vista se queda en la inicial como antes -- nunca rompe nada, solo deja
// de verse tan lindo.

import { conToken } from "./api.js";
import { inicialDe } from "./formato.js";

export const cacheSitio = {};

// Las miniaturas del sitio (sobre todo el snapshot en vivo de
// img.doppiocdn.media) exigen un Referer que el navegador nunca manda
// solo -- por eso no van directo a <img src="url-del-sitio">, pasan por
// /api/sitio/imagen, que las pide del lado de Nimbo con las cabeceras
// que hacen falta (ver sitio.py: Sitio.imagen).
export function urlImagenProxy(url) {
  return conToken("/api/sitio/imagen?url=" + encodeURIComponent(url));
}

export function construirMiniatura(nombre) {
  const info = cacheSitio[(nombre || "").toLowerCase()];
  if (info && info.avatar) {
    return `<img src="${urlImagenProxy(info.avatar)}" alt="" loading="lazy" onerror="this.remove()">`;
  }
  return inicialDe(nombre);
}

// Mezcla lo que llego por el canal "sitio" del WebSocket -- {nombre:
// {avatar?, online?, estado?}}, una entrada por cada favorita o modelo
// sin estado que el servidor pudo resolver en esa vuelta (ver
// servidor.py:_refrescar_sitio) -- sin pisar los campos que ya estaban.
export function aplicarSitio(datos) {
  for (const [clave, info] of Object.entries(datos || {})) {
    cacheSitio[clave] = { ...cacheSitio[clave], ...info };
  }
}
