// Banner de puesta a punto (preflight.py del lado del servidor): avisa
// si falta algo para que Nimbo funcione del todo.
//
// Una falla puede traer un "arreglo" ({ruta, boton}): un POST que la
// resuelve sin que el usuario tenga que correr nada a mano. Hoy lo usa
// solo el certificado de mitmproxy (ver preflight.instalar_certificado).

import { obtener, enviar } from "./api.js";

function pintarFalla(f) {
  const li = document.createElement("li");
  const titulo = document.createElement("strong");
  titulo.textContent = f.nombre + ":";
  li.append(titulo, " " + f.mensaje);
  if (f.accion) {
    const acc = document.createElement("span");
    acc.className = "accion";
    acc.textContent = f.accion;
    li.append(" — ", acc);
  }
  if (f.arreglo && f.arreglo.ruta) {
    const boton = document.createElement("button");
    boton.className = "boton-arreglo";
    boton.textContent = f.arreglo.boton || "Arreglar";
    boton.addEventListener("click", async () => {
      boton.disabled = true;
      const antes = boton.textContent;
      boton.textContent = "...";
      try {
        const r = await enviar(f.arreglo.ruta, {});
        if (r && r.ok) {
          // Volver a preguntar: si de verdad se arreglo, la fila
          // desaparece sola y con ella el banner entero.
          await revisarPreflight();
          return;
        }
        boton.textContent = antes;
        boton.disabled = false;
        if (r && r.mensaje) {
          const detalle = document.createElement("div");
          detalle.className = "accion";
          detalle.textContent = r.mensaje;
          li.append(detalle);
        }
      } catch (err) {
        boton.textContent = antes;
        boton.disabled = false;
      }
    });
    li.append(" ", boton);
  }
  return li;
}

export async function revisarPreflight() {
  try {
    const r = await obtener("/api/preflight");
    const fallas = r.filas.filter(f => !f.ok);
    const cont = document.getElementById("aviso-preflight");
    if (fallas.length === 0) { cont.classList.add("oculta"); return; }
    cont.textContent = "";
    const titulo = document.createElement("div");
    titulo.textContent = "Falta algo para que Nimbo funcione del todo:";
    const ul = document.createElement("ul");
    for (const f of fallas) ul.append(pintarFalla(f));
    cont.append(titulo, ul);
    cont.classList.remove("oculta");
  } catch (err) { /* si ni esto responde, ya se ve en el punto de estado */ }
}
