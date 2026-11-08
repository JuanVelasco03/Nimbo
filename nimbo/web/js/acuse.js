// Acuse de recibo de una escritura que no se ve en ningun otro lado.
//
// Casi toda accion de Nimbo se confirma sola: tildar "grabar" en una
// tarjeta hace aparecer EN LISTA unos segundos despues, "Arrancar" cambia
// el transporte del header, "+ favoritas" hace aparecer una tarjeta. Las
// de la pestana Ajustes no: un toggle de preferencias o una directiva
// escriben config.json / grabar.txt y en la pantalla no pasa
// absolutamente nada -- no habia ninguna forma de saber si quedo
// guardado, ni de enterarse si el POST fallo (el error iba al registro
// del footer, que en esa pestana ni se esta mirando).
//
// El destello es de color, sin movimiento: por eso sobrevive a
// prefers-reduced-motion (ver tailwind.css, el bloque del final) -- lo
// que se reduce es el movimiento, no el enterarse de lo que paso.

const CLASES = ["acuse-ok", "acuse-mal"];

function destellar(el, clase) {
  if (!el) return;
  el.classList.remove(...CLASES);
  // Sin leer una propiedad de layout, quitar y volver a poner la clase en
  // el mismo tick no reinicia nada: el navegador nunca ve el estado
  // intermedio, asi que dos guardados seguidos sobre el mismo control
  // destellaban una sola vez.
  void el.offsetWidth;
  el.classList.add(clase);
  // El segundo destello cancela la animacion del primero, asi que ese
  // listener no llega a dispararse ahi -- pero si en el animationend
  // siguiente, donde solo quita una clase que ya no esta. Idempotente, y
  // se desengancha solo (once).
  el.addEventListener("animationend", () => el.classList.remove(clase), { once: true });
}

export function acusarOk(el) { destellar(el, "acuse-ok"); }
export function acusarMal(el) { destellar(el, "acuse-mal"); }
