// Cliente HTTP minimo contra servidor.py: token en la URL, JSON para
// adentro y para afuera. Mas un cliente chico para /ws (ver conectarWS),
// que reemplazo al /eventos de SSE: ahora es el canal por el que llega
// el estado (por secciones, no el estado completo cada vez) y los logs
// del motor, y por el que se manda el latido de "ver en vivo" temporal y
// la suscripcion al refresco del sitio real.

export const TOKEN = new URLSearchParams(location.search).get("t") || "";

export function conToken(ruta) {
  const sep = ruta.includes("?") ? "&" : "?";
  return ruta + sep + "t=" + encodeURIComponent(TOKEN);
}

export async function obtener(ruta) {
  const r = await fetch(conToken(ruta));
  if (!r.ok) throw new Error(ruta + " -> " + r.status);
  return r.json();
}

export async function enviar(ruta, cuerpo) {
  const r = await fetch(conToken(ruta), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo || {}),
  });
  if (!r.ok) {
    const datos = await r.json().catch(() => ({}));
    throw new Error(datos.error || (ruta + " -> " + r.status));
  }
  return r.json();
}

// -- WebSocket --------------------------------------------------------
// A diferencia de EventSource (que reconecta solo), WebSocket no trae
// reconexion propia -- por eso el backoff a mano. onMensaje recibe el
// objeto ya parseado ({tipo, datos, t}); onEstadoConexion(bool) avisa si
// el canal esta arriba (lo usa main.js para el punto rojo).
export function conectarWS(onMensaje, onEstadoConexion) {
  let ws = null;
  let intento = 0;
  let cerrado = false;

  function conectar() {
    const url = conToken("/ws").replace(/^http/, "ws");
    ws = new WebSocket(url);
    ws.onopen = () => { intento = 0; onEstadoConexion(true); };
    ws.onmessage = (ev) => {
      try { onMensaje(JSON.parse(ev.data)); }
      catch (err) { /* frame que no se pudo parsear: se ignora */ }
    };
    ws.onclose = () => {
      onEstadoConexion(false);
      if (cerrado) return;
      const espera = Math.min(5000, 500 * 2 ** intento);
      intento++;
      setTimeout(conectar, espera);
    };
    ws.onerror = () => { try { ws.close(); } catch (err) { /* ya cerrado */ } };
  }
  conectar();

  const cliente = {
    enviar(obj) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    },
    cerrar() {
      cerrado = true;
      if (ws) ws.close();
    },
  };
  _ws = cliente;
  return cliente;
}

// El cliente WS vivo, para modulos que necesitan mandar un mensaje
// liviano (el latido de vivo_temporal, en vista_previa.js) sin que
// main.js tenga que pasarselo a mano por cada import.
let _ws = null;
export function enviarWS(obj) {
  if (_ws) _ws.enviar(obj);
}
