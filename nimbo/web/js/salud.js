// Franja persistente de salud (SPEC 16): disco, motor rendido y camino
// directo degradado -- hermana de #aviso-preflight (preflight.js), pero
// esa mira que falte instalar y esta mira el estado en marcha.
//
// Se arma con innerHTML a proposito (ver Decisiones del spec): estado.salud
// solo entra al delta del WebSocket cuando la lista cambio de verdad, asi
// que esto no corre al ritmo de 1 Hz, y ningun campo trae un <img>.

function pintarAlerta(a) {
  return `<div class="item nivel-${a.nivel}">`
       + `<span class="titulo">${a.titulo}:</span> ${a.texto}`
       + `</div>`;
}

export function renderSalud(estado) {
  const cont = document.getElementById("aviso-salud");
  const alertas = estado.salud || [];
  if (alertas.length === 0) {
    cont.classList.add("oculta");
    cont.innerHTML = "";
    return;
  }
  cont.innerHTML = alertas.map(pintarAlerta).join("");
  cont.classList.remove("oculta");
}
