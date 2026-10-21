// Miniaturas y datos en vivo del sitio real (sitio.py del lado del
// servidor). cacheSitio: nombre en minuscula -> {avatar, online}. Se
// llena en segundo plano (ver actualizarCacheSitio); si el sitio cambio
// su endpoint de busqueda esto se queda vacio y todo cae a la inicial
// como antes -- nunca rompe nada, solo deja de verse tan lindo.

import { obtener, conToken } from "./api.js";
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

// Busca cada nombre contra /api/sitio/buscar y actualiza cacheSitio con
// lo que haya exacto. No toca el DOM ni sabe de "estado" -- quien llama
// decide cuando volver a pintar con los datos frescos.
export async function actualizarCacheSitio(nombres) {
  await Promise.allSettled(nombres.map(async (nombre) => {
    const r = await obtener("/api/sitio/buscar?q=" + encodeURIComponent(nombre));
    const exacto = r.resultados.find(
      m => m.username.toLowerCase() === nombre.toLowerCase());
    if (exacto) {
      const clave = nombre.toLowerCase();
      cacheSitio[clave] = { ...cacheSitio[clave], avatar: exacto.avatar, online: exacto.online };
    }
  }));
}

// El buscador no trae "status" (public/private/p2p/...), asi que una
// favorita puramente local -- sin dato de "siguiendo" ni de supervisor --
// no tiene de donde sacarlo salvo pidiendolo puntual. Quien llama decide
// a cuales nombres les falta el dato (ver main.js: solo los que no vienen
// ya resueltos por estado.siguiendo o estado.supervisor).
export async function actualizarEstadosSitio(nombres) {
  await Promise.allSettled(nombres.map(async (nombre) => {
    const clave = nombre.toLowerCase();
    try {
      const r = await obtener("/api/sitio/estado?nombre=" + encodeURIComponent(nombre));
      cacheSitio[clave] = { ...cacheSitio[clave], estado: r.estado || "" };
    } catch (err) {
      // sin dato -- la fila se queda en "SIN DATO" hasta el proximo intento
    }
  }));
}
