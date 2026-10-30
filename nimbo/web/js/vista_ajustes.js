// Pestana "Ajustes": las directivas de grabar.txt y las preferencias
// propias de Nimbo (ajustes.py / config.json, no grabar.txt).

import { obtener, enviar } from "./api.js";
import { mostrarError } from "./registro.js";
import { formatoGB } from "./formato.js";

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
  { clave: "directo", etiqueta: "Usar navegador para grabar", tipo: "bool", invertido: true, defecto: "0",
    ayuda: "Marcado: graba con el navegador (como siempre, mas probado). Desmarcado: graba directo, "
         + "sin abrir pestanas (mas liviano) -- aplica a TODAS las modelos por igual. Si el protocolo "
         + "directo se degrada para alguna, agregala a mano a @navegador= en grabar.txt." },
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
      if (d.tipo === "bool") campo.checked = d.invertido ? actual !== "1" : actual === "1";
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
      const marcado = d.invertido ? actual !== "1" : actual === "1";
      control = `<div class="fila-check">
        <label class="conmutador">
          <input type="checkbox" id="directiva-${d.clave}" ${marcado ? "checked" : ""}>
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
      let valor;
      if (d.tipo === "bool") {
        const marcado = campo.checked;
        valor = d.invertido ? (marcado ? "0" : "1") : (marcado ? "1" : "0");
      } else {
        valor = campo.value;
      }
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

// -- Carpetas y discos (config.json: dir_segmentos, dir_grabaciones, ------
// union_modo, dir_union -- ver SPEC 12) ------------------------------------

function _escAtributo(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

const CAMPOS_CARPETA = [
  { clave: "dir_segmentos", etiquetaEspacio: "segmentos",
    etiqueta: "Segmentos (streams)",
    ayuda: "Donde se escriben los segmentos crudos mientras se graba. "
         + "Vacio = la carpeta de siempre (<raiz>\\datos\\streams)." },
  { clave: "dir_grabaciones", etiquetaEspacio: "grabaciones",
    etiqueta: "Grabaciones (.mp4 final)",
    ayuda: "Donde queda el .mp4 ya unido. "
         + "Vacio = la carpeta de siempre (<raiz>\\datos\\grabaciones)." },
];

async function _guardarRuta(clave, valor, mensajeEl) {
  try {
    await enviar("/api/rutas", { clave, valor });
    mensajeEl.textContent = "";
    mensajeEl.classList.add("oculta");
    return true;
  } catch (err) {
    mensajeEl.textContent = (err && err.message) || "no se pudo guardar";
    mensajeEl.classList.remove("oculta");
    return false;
  }
}

async function _examinar(clave, campo, mensajeEl) {
  try {
    const r = await enviar("/api/rutas/examinar", { clave });
    if (r.ruta) {
      campo.value = r.ruta;
      await _guardarRuta(clave, r.ruta, mensajeEl);
    }
  } catch (err) { mostrarError(err); }
}

export async function cargarRutas() {
  try {
    const datos = await obtener("/api/rutas");
    const cont = document.getElementById("rutas");
    cont.innerHTML = "";

    for (const c of CAMPOS_CARPETA) {
      const div = document.createElement("div");
      div.className = "directiva";
      div.innerHTML = `<label>${c.etiqueta}</label><div class="ayuda">${c.ayuda}</div>
        <div class="fila-check">
          <input type="text" id="ruta-${c.clave}" style="flex:1"
            value="${_escAtributo(datos[c.clave])}" placeholder="auto">
          <button type="button" class="boton-chico" id="examinar-${c.clave}">Examinar</button>
        </div>
        <div class="ayuda" id="espacio-${c.clave}"></div>
        <div class="alerta oculta" id="mensaje-${c.clave}"></div>
        <div class="alerta oculta" id="motivo-${c.clave}"></div>`;
      cont.appendChild(div);

      const campo = div.querySelector("#ruta-" + c.clave);
      const mensaje = div.querySelector("#mensaje-" + c.clave);
      campo.addEventListener("blur", () => _guardarRuta(c.clave, campo.value, mensaje));
      div.querySelector("#examinar-" + c.clave)
        .addEventListener("click", () => _examinar(c.clave, campo, mensaje));
    }

    // Union: select de modo + carpeta, visible solo con modo "carpeta".
    const divUnion = document.createElement("div");
    divUnion.className = "directiva";
    divUnion.innerHTML = `<label>Union (escenario temporal)</label>
      <div class="ayuda">Donde se copian los segmentos antes de unirlos, si eso
        ayuda. "Automatico" es el %TEMP% del sistema, igual que siempre.</div>
      <select id="ruta-union_modo">
        <option value="auto">Automatico (como hoy)</option>
        <option value="carpeta">Carpeta elegida</option>
        <option value="apagado">Apagado (leer siempre del disco de origen)</option>
      </select>
      <div class="fila-check mt-2 oculta" id="fila-dir_union">
        <input type="text" id="ruta-dir_union" style="flex:1"
          value="${_escAtributo(datos.dir_union)}" placeholder="carpeta del escenario">
        <button type="button" class="boton-chico" id="examinar-dir_union">Examinar</button>
      </div>
      <div class="ayuda" id="espacio-dir_union"></div>
      <div class="alerta oculta" id="mensaje-dir_union"></div>`;
    cont.appendChild(divUnion);

    const selectModo = divUnion.querySelector("#ruta-union_modo");
    const filaUnion = divUnion.querySelector("#fila-dir_union");
    const campoUnion = divUnion.querySelector("#ruta-dir_union");
    const mensajeUnion = divUnion.querySelector("#mensaje-dir_union");
    selectModo.value = datos.union_modo || "auto";
    filaUnion.classList.toggle("oculta", selectModo.value !== "carpeta");

    selectModo.addEventListener("change", async () => {
      filaUnion.classList.toggle("oculta", selectModo.value !== "carpeta");
      await _guardarRuta("union_modo", selectModo.value, mensajeUnion);
    });
    campoUnion.addEventListener("blur", () => _guardarRuta("dir_union", campoUnion.value, mensajeUnion));
    divUnion.querySelector("#examinar-dir_union")
      .addEventListener("click", () => _examinar("dir_union", campoUnion, mensajeUnion));
  } catch (err) { mostrarError(err); }
}

// Sigue al estado del motor (deshabilita segmentos/grabaciones mientras
// corre, SPEC 12) y al canal "disco" (espacio libre). No construye nada:
// si cargarRutas todavia no armo la seccion, no hay nada que actualizar.
export function renderRutas(estado) {
  const cont = document.getElementById("rutas");
  if (!cont || cont.childElementCount === 0) return;

  const m = estado.motor || {};
  const bloqueado = !!(m.detector_vivo || m.supervisor_vivo || m.directo_vivo || m.parando);
  for (const c of CAMPOS_CARPETA) {
    const campo = document.getElementById("ruta-" + c.clave);
    const boton = document.getElementById("examinar-" + c.clave);
    const motivo = document.getElementById("motivo-" + c.clave);
    if (!campo) continue;
    if (campo !== document.activeElement) campo.disabled = bloqueado;
    if (boton) boton.disabled = bloqueado;
    if (motivo) {
      motivo.textContent = bloqueado
        ? "el motor esta corriendo -- parala para cambiar esta carpeta" : "";
      motivo.classList.toggle("oculta", !bloqueado);
    }
  }

  const disco = estado.disco || [];
  for (const c of CAMPOS_CARPETA) {
    const el = document.getElementById("espacio-" + c.clave);
    if (!el) continue;
    const fila = disco.find(d => d.etiqueta === c.etiquetaEspacio);
    el.textContent = fila
      ? "espacio libre: " + formatoGB(fila.libres) + " de " + formatoGB(fila.total) : "";
  }
}
