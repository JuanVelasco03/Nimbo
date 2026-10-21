// Reloj del refresco de la vista previa, en un Web Worker a proposito:
// el hilo principal frena setTimeout/setInterval cuando la pestana/
// ventana pierde el foco o pasa un rato en segundo plano (Chromium
// throttling -- el mismo problema que supervisor.py del grabador pelea
// del otro lado con su GUION_VISIBLE). Un worker no se frena por eso,
// asi que el "tick" sigue llegando aunque Nimbo no tenga el foco --
// justo el caso de uso de una herramienta de monitoreo.
//
// Este worker solo avisa "tick" cada tantos ms -- no toca la red ni el
// DOM, eso sigue en vista_previa.js (un worker no tiene document).

let temporizador = null;

self.onmessage = (ev) => {
  const { accion, ms } = ev.data || {};
  if (accion === "arrancar") {
    if (temporizador) clearInterval(temporizador);
    temporizador = setInterval(() => self.postMessage("tick"), ms);
  } else if (accion === "parar") {
    if (temporizador) { clearInterval(temporizador); temporizador = null; }
  }
};
