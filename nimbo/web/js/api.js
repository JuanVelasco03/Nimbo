// Cliente HTTP minimo contra servidor.py: token en la URL, JSON para
// adentro y para afuera.

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
