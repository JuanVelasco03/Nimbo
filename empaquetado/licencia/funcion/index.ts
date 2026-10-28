// Edge Function "validar": recibe {clave, maquina} y devuelve un token
// firmado con Ed25519 si la licencia sirve.
//
// Ver specs\distribucion_instalador.md S6.1. El que verifica el token del
// otro lado es nimbo\licencia.py (firma.verificar), asi que el formato es
// exactamente: <payload-base64url>.<firma-base64url>, firmando los BYTES
// del payload tal como viajan.
//
// Variables de entorno que hay que configurar en la funcion:
//
//   LICENCIA_PRIV_D   clave privada Ed25519, base64url (de claves.py)
//   LICENCIA_PUB_X    clave publica Ed25519, base64url (la misma del paquete)
//
// SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY ya las inyecta Supabase.
//
// Se habla con la base por REST con la service_role (que salta RLS) en vez
// de importar supabase-js: son dos consultas y asi la funcion no tiene
// ninguna dependencia que pueda romperse sola.

// Cuanto vale el token que se entrega. Dias, no meses: es lo que acota
// cuanto sobrevive una licencia ya cortada (activa=false), porque offline
// el cliente solo mira la firma y el exp. Y no menos, porque es la gracia
// que cubre un corte de internet (ver S6.2).
const DIAS_TOKEN = 7;

const CABECERAS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function b64urlDecode(s: string): Uint8Array {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const base = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base), (c) => c.charCodeAt(0));
}

function b64urlEncode(b: Uint8Array): string {
  let s = "";
  for (const byte of b) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function error(mensaje: string, codigo = 400): Response {
  return new Response(JSON.stringify({ error: mensaje }), {
    status: codigo,
    headers: CABECERAS,
  });
}

let clavePrivada: CryptoKey | null = null;

// De donde sale el par Ed25519. Primero las variables de entorno; si no
// estan, la tabla licencia_claves. Los dos caminos existen porque las
// variables de la funcion solo se pueden poner desde el panel (la API no
// las expone), y esto se desplego por API -- asi que la clave arranco en
// la tabla. Mover despues al panel no rompe nada: el entorno gana.
async function parDeClaves(): Promise<{ d: string; x: string }> {
  const d = Deno.env.get("LICENCIA_PRIV_D");
  const x = Deno.env.get("LICENCIA_PUB_X");
  if (d && x) return { d, x };

  const r = await rest("/licencia_claves?id=eq.actual&select=privada,publica");
  if (!r.ok) throw new Error("no se pudo leer licencia_claves");
  const filas = await r.json();
  if (!filas.length) {
    throw new Error("no hay clave: ni LICENCIA_PRIV_D ni licencia_claves");
  }
  return { d: filas[0].privada, x: filas[0].publica };
}

async function cargarClave(): Promise<CryptoKey> {
  if (clavePrivada) return clavePrivada;
  const { d, x } = await parDeClaves();
  // JWK OKP: "d" es la semilla de 32 bytes (lo que genera claves.py) y
  // "x" la publica. WebCrypto no acepta pkcs8 armado a mano facil, jwk si.
  clavePrivada = await crypto.subtle.importKey(
    "jwk",
    { kty: "OKP", crv: "Ed25519", d, x, key_ops: ["sign"], ext: true },
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  return clavePrivada;
}

async function firmarToken(payload: Record<string, unknown>): Promise<string> {
  const clave = await cargarClave();
  const crudo = new TextEncoder().encode(JSON.stringify(payload));
  const firma = new Uint8Array(
    await crypto.subtle.sign({ name: "Ed25519" }, clave, crudo),
  );
  return b64urlEncode(crudo) + "." + b64urlEncode(firma);
}

async function rest(ruta: string, init?: RequestInit): Promise<Response> {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) throw new Error("faltan SUPABASE_URL / SERVICE_ROLE_KEY");
  const cabeceras: Record<string, string> = {
    apikey: key,
    Authorization: "Bearer " + key,
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) || {}),
  };
  return await fetch(url + "/rest/v1" + ruta, { ...init, headers: cabeceras });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CABECERAS });
  }
  if (req.method !== "POST") return error("solo POST", 405);

  let cuerpo: { clave?: string; maquina?: string };
  try {
    cuerpo = await req.json();
  } catch {
    return error("cuerpo invalido");
  }
  const clave = (cuerpo.clave || "").trim();
  const maquina = (cuerpo.maquina || "").trim();
  if (!clave) return error("falta la clave");
  if (!maquina) return error("falta el id de la maquina");

  // 1) buscar la licencia
  let fila: {
    clave: string;
    vence: string;
    maquina: string | null;
    activa: boolean;
  } | undefined;
  try {
    const r = await rest(
      "/licencias?clave=eq." + encodeURIComponent(clave) +
        "&select=clave,vence,maquina,activa",
    );
    if (!r.ok) return error("no se pudo consultar la licencia", 500);
    fila = (await r.json())[0];
  } catch (e) {
    return error("error del servidor: " + (e as Error).message, 500);
  }

  // Mismo mensaje para "no existe" y "desactivada": no hay por que
  // ayudar a distinguir una clave real de una inventada.
  if (!fila || !fila.activa) return error("clave no valida", 403);
  if (new Date(fila.vence).getTime() <= Date.now()) {
    return error("la licencia vencio el " + fila.vence.slice(0, 10), 403);
  }
  if (fila.maquina && fila.maquina !== maquina) {
    return error("esta clave ya se activo en otro equipo", 403);
  }

  // 2) atadura al primer equipo. El filtro maquina=is.null hace que dos
  // pedidos simultaneos no se pisen: el segundo no actualiza nada y cae
  // en la rama de "ya se activo en otro equipo" la proxima vez.
  if (!fila.maquina) {
    try {
      const r = await rest(
        "/licencias?clave=eq." + encodeURIComponent(clave) +
          "&maquina=is.null",
        {
          method: "PATCH",
          headers: { Prefer: "return=representation" },
          body: JSON.stringify({ maquina, ultimo_uso: new Date().toISOString() }),
        },
      );
      if (!r.ok) return error("no se pudo atar la licencia al equipo", 500);
      if ((await r.json()).length === 0) {
        return error("esta clave ya se activo en otro equipo", 403);
      }
    } catch (e) {
      return error("error del servidor: " + (e as Error).message, 500);
    }
  } else {
    // Solo para saber si se sigue usando; que falle no importa.
    rest("/licencias?clave=eq." + encodeURIComponent(clave), {
      method: "PATCH",
      body: JSON.stringify({ ultimo_uso: new Date().toISOString() }),
    }).catch(() => {});
  }

  // 3) el token. exp = min(vence de la licencia, ahora + DIAS_TOKEN):
  // nunca se entrega un token que sobreviva a la licencia.
  const ahora = Date.now() / 1000;
  const tope = new Date(fila.vence).getTime() / 1000;
  const exp = Math.min(tope, ahora + DIAS_TOKEN * 86400);

  try {
    const token = await firmarToken({
      clave,
      maquina,
      emitido: ahora,
      exp,
    });
    return new Response(JSON.stringify({ token, exp }), { headers: CABECERAS });
  } catch (e) {
    return error("no se pudo firmar: " + (e as Error).message, 500);
  }
});
