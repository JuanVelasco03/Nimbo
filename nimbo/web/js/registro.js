// La consola del footer: todo lo que se agrega ahi pasa por aca.

const TOPE = 300;

export function agregarLog(clase, texto) {
  const log = document.getElementById("log");
  const linea = document.createElement("div");
  linea.className = clase;
  linea.textContent = texto;
  log.appendChild(linea);
  while (log.childElementCount > TOPE) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

export function mostrarError(err) {
  agregarLog("linea-aviso", "error: " + err.message);
}
