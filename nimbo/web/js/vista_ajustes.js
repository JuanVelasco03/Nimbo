// Pestana "Ajustes": las directivas de grabar.txt y las preferencias
// propias de Nimbo (ajustes.py / config.json, no grabar.txt).

import { obtener, enviar } from "./api.js";
import { mostrarError } from "./registro.js";

const DIRECTIVAS = [
  { clave: "calidad", etiqueta: "Calidad", tipo: "texto", defecto: "max",
    ayuda: "max = la mejor que ofrezca cada modelo. Tambien se puede fijar, por ejemplo: 1080p,720p" },
  { clave: "codec", etiqueta: "Codec", tipo: "texto", defecto: "max",
    ayuda: "max = cualquiera. Tambien se puede fijar: av1,h264" },
  { clave: "forzar", etiqueta: "Esconder calidades bajas", tipo: "bool", defecto: "1",
    ayuda: "Le esconde al navegador las calidades mas bajas, asi el reproductor no tiene a donde bajar." },
  { clave: "variantes", etiqueta: "Variantes a grabar", tipo: "select", defecto: "1",
    opciones: [["1", "una sola (la mejor)"], ["todas", "todas a la vez"]],
    ayuda: "Una sola variante por stream, o grabar todas las calidades a la vez." },
  { clave: "espera", etiqueta: "Espera antes de elegir calidad (s)", tipo: "numero", defecto: "6",
    ayuda: "Segundos para descubrir que calidades ofrece el stream antes de elegir." },
  { clave: "cambio", etiqueta: "Seguir cambios de calidad (s)", tipo: "numero", defecto: "8",
    ayuda: "Si el reproductor cambia de calidad: segundos que se sostiene antes de seguirlo." },
  { clave: "borrosas", etiqueta: "Grabar el stream censurado", tipo: "bool", defecto: "0",
    ayuda: "Graba tambien el stream borroso de los shows privados (es otro video, no una calidad mas baja)." },
  { clave: "privado", etiqueta: "Altura del relleno de privado", tipo: "texto", defecto: "160p",
    ayuda: "Por debajo de esta altura se considera que la modelo esta en un show privado. 0 = desactivado (lo graba igual)." },
  { clave: "fallback", etiqueta: "Grabar la mejor disponible si no hay la pedida", tipo: "bool", defecto: "1",
    ayuda: "Si ninguna calidad ofrecida coincide con lo pedido, graba la mejor que haya." },
  { clave: "unir", etiqueta: "Unir al terminar", tipo: "bool", defecto: "1",
    ayuda: "Une los segmentos en un .mp4 final al cerrar la grabacion." },
  { clave: "inactividad", etiqueta: "Cerrar tras inactividad (s)", tipo: "numero", defecto: "120",
    ayuda: "Cierra y une si no llegan segmentos nuevos en este tiempo. 0 = nunca." },
];

export function renderDirectivas(valores) {
  const cont = document.getElementById("directivas");
  if (cont.childElementCount > 0) {
    // ya esta construido: solo actualizar valores, para no perder el foco
    // mientras el usuario esta escribiendo.
    for (const d of DIRECTIVAS) {
      const campo = document.getElementById("directiva-" + d.clave);
      if (!campo || campo === document.activeElement) continue;
      const actual = valores[d.clave] ?? d.defecto;
      if (d.tipo === "bool") campo.checked = actual === "1";
      else if (campo.value !== actual) campo.value = actual;
    }
    return;
  }
  cont.innerHTML = "";
  for (const d of DIRECTIVAS) {
    const actual = valores[d.clave] ?? d.defecto;
    const div = document.createElement("div");
    div.className = "directiva";
    let control = "";
    if (d.tipo === "bool") {
      control = `<div class="fila-check">
        <label class="conmutador">
          <input type="checkbox" id="directiva-${d.clave}" ${actual === "1" ? "checked" : ""}>
          <span class="pista"></span>
        </label>
      </div>`;
    } else if (d.tipo === "select") {
      control = `<select id="directiva-${d.clave}">` +
        d.opciones.map(([v, t]) => `<option value="${v}" ${v === actual ? "selected" : ""}>${t}</option>`).join("") +
        `</select>`;
    } else if (d.tipo === "numero") {
      control = `<input type="number" id="directiva-${d.clave}" value="${actual}" min="0">`;
    } else {
      control = `<input type="text" id="directiva-${d.clave}" value="${actual}">`;
    }
    div.innerHTML = `<label>${d.etiqueta}</label><div class="ayuda">${d.ayuda}</div>${control}`;
    cont.appendChild(div);

    const campo = div.querySelector("#directiva-" + d.clave);
    const evento = d.tipo === "bool" || d.tipo === "select" ? "change" : "blur";
    campo.addEventListener(evento, () => {
      const valor = d.tipo === "bool" ? (campo.checked ? "1" : "0") : campo.value;
      enviar("/api/lista/directiva", { clave: d.clave, valor }).catch(mostrarError);
    });
  }
}

// -- preferencias de Nimbo --------------------------------------------

const PREFERENCIAS = [
  { clave: "arrancar_al_abrir", etiqueta: "Arrancar la grabacion al abrir Nimbo",
    ayuda: "Si esta apagado, Nimbo abre pero no arranca nada hasta que lo pidas." },
  { clave: "ver_navegador", etiqueta: "Mostrar el navegador que graba",
    ayuda: "Util para ver que esta pasando. Normalmente conviene dejarlo apagado." },
  { clave: "bandeja", etiqueta: "Icono en la bandeja del sistema",
    ayuda: "Permite reabrir Nimbo o pararlo sin dejar la ventana abierta. Hace falta reiniciar Nimbo para que el cambio tome efecto." },
  { clave: "inicio_automatico", etiqueta: "Arrancar Nimbo con Windows",
    ayuda: "Agrega un acceso directo chico a la carpeta de Inicio de Windows." },
];

export async function cargarPreferencias() {
  try {
    const datos = await obtener("/api/preferencias");
    const cont = document.getElementById("preferencias");
    cont.innerHTML = "";
    for (const p of PREFERENCIAS) {
      const div = document.createElement("div");
      div.className = "directiva";
      div.innerHTML = `<label>${p.etiqueta}</label><div class="ayuda">${p.ayuda}</div>
        <div class="fila-check">
          <label class="conmutador">
            <input type="checkbox" id="pref-${p.clave}" ${datos[p.clave] ? "checked" : ""}>
            <span class="pista"></span>
          </label>
        </div>`;
      cont.appendChild(div);
      div.querySelector("input").addEventListener("change", async (ev) => {
        try {
          if (p.clave === "inicio_automatico") {
            await enviar("/api/sistema/inicio_automatico", { activo: ev.target.checked });
          } else {
            await enviar("/api/preferencias", { clave: p.clave, valor: ev.target.checked });
          }
        } catch (err) { mostrarError(err); }
      });
    }
  } catch (err) { mostrarError(err); }
}
