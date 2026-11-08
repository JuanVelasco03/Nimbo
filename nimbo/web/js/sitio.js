// Miniaturas y datos en vivo del sitio real (sitio.py del lado del
// servidor). cacheSitio: "sitio/nombre en minuscula" -> {avatar, online,
// estado}. SPEC 18: la clave lleva el sitio adelante (claveCache) porque
// dos modelos homonimas de sitios distintos no pueden compartir cache de
// miniatura -- una pisaria el avatar de la otra sin ningun error visible.
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

export function claveCache(sitio, nombre) {
  return (sitio || "stripchat") + "/" + (nombre || "").toLowerCase();
}

// Las miniaturas del sitio (sobre todo el snapshot en vivo de
// img.doppiocdn.media/jpeg.live.mmcdn.com) exigen un Referer que el
// navegador nunca manda solo -- por eso no van directo a
// <img src="url-del-sitio">, pasan por /api/sitio/imagen, que las pide
// del lado de Nimbo con las cabeceras que hacen falta (ver sitio.py:
// SitioBase.imagen). SPEC 18: "sitio" decide que HOSTS_IMAGEN_PERMITIDOS
// aplica del lado del servidor -- sin pasarlo, una URL de chaturbate se
// probaba contra la lista blanca de stripchat y siempre daba 404.
export function urlImagenProxy(url, sitio = "stripchat") {
  return conToken("/api/sitio/imagen?url=" + encodeURIComponent(url)
    + "&sitio=" + encodeURIComponent(sitio));
}

export function construirMiniatura(nombre, sitio = "stripchat") {
  const info = cacheSitio[claveCache(sitio, nombre)];
  if (info && info.avatar) {
    return `<img src="${urlImagenProxy(info.avatar, sitio)}" alt="" loading="lazy" onerror="this.remove()">`;
  }
  return inicialDe(nombre);
}

// Pone al dia la miniatura de una tarjeta YA construida, SIN recrear el
// <img> si la URL no cambio.
//
// Esto es la linea que corta la tormenta de pedidos que congelaba la
// ventana (specs\congelado_interfaz.md): un <img> recreado se vuelve a
// pedir siempre, y ese pedido es un GET a /api/sitio/imagen, que del lado
// del servidor es un HTTPS saliente al CDN ocupando un hilo del pool. Con
// la grilla reconstruida a 1 Hz eso eran N pedidos por segundo, para
// siempre. Comparar por src (y no reemplazar nunca "por si cambio") es lo
// que hace que un repintado cueste cero red.
//
// Nota: compara contra img.getAttribute("src"), no img.src -- el segundo
// devuelve la URL absoluta ya resuelta por el navegador y nunca seria
// igual a la relativa que arma urlImagenProxy().
export function sincronizarMiniatura(contenedor, nombre, sitio = "stripchat") {
  const info = cacheSitio[claveCache(sitio, nombre)];
  const img = contenedor.querySelector("img");

  if (!info || !info.avatar) {
    // sin avatar: la inicial. Si ya esta puesta, no se toca (escribir el
    // mismo textContent igual invalida el layout de ese nodo).
    const inicial = inicialDe(nombre);
    if (img || contenedor.textContent !== inicial) contenedor.textContent = inicial;
    return;
  }

  const url = urlImagenProxy(info.avatar, sitio);
  if (img) {
    if (img.getAttribute("src") !== url) {
      img.classList.add("cargando");
      img.onload = () => img.classList.remove("cargando");
      img.setAttribute("src", url);
    }
    return;
  }
  // onerror="this.remove()" igual que construirMiniatura: si la miniatura
  // no carga, la tarjeta se queda sin ella y el proximo sincronizar vuelve
  // a intentarlo (ya no hay <img> que encontrar).
  const nuevo = document.createElement("img");
  nuevo.alt = "";
  nuevo.loading = "lazy";
  nuevo.classList.add("cargando");
  nuevo.onerror = () => nuevo.remove();
  nuevo.onload = () => nuevo.classList.remove("cargando");
  nuevo.setAttribute("src", url);
  contenedor.textContent = "";
  contenedor.appendChild(nuevo);
}

// Mezcla lo que llego por el canal "sitio" del WebSocket -- {"sitio/nombre":
// {avatar?, online?, estado?}}, una entrada por cada favorita o modelo
// sin estado que el servidor pudo resolver en esa vuelta (ver
// servidor.py:_refrescar_sitio) -- sin pisar los campos que ya estaban.
export function aplicarSitio(datos) {
  for (const [clave, info] of Object.entries(datos || {})) {
    cacheSitio[clave] = { ...cacheSitio[clave], ...info };
  }
}
