// Banner de puesta a punto (preflight.py del lado del servidor): avisa
// si falta algo para que Nimbo funcione del todo.

import { obtener } from "./api.js";

export async function revisarPreflight() {
  try {
    const r = await obtener("/api/preflight");
    const fallas = r.filas.filter(f => !f.ok);
    const cont = document.getElementById("aviso-preflight");
    if (fallas.length === 0) { cont.classList.add("oculta"); return; }
    cont.innerHTML = "Falta algo para que Nimbo funcione del todo:<ul>" +
      fallas.map(f => `<li><strong>${f.nombre}:</strong> ${f.mensaje}` +
        (f.accion ? ` — <span class="accion">${f.accion}</span>` : "") + "</li>").join("") +
      "</ul>";
    cont.classList.remove("oculta");
  } catch (err) { /* si ni esto responde, ya se ve en el punto de estado */ }
}
