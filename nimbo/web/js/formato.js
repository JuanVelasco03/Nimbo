// Formato de numeros y tiempos para la interfaz. Sin estado, sin DOM.

export function formatoMB(bytes) {
  if (!bytes) return "0 MB";
  return (bytes / 1048576).toFixed(bytes > 100 * 1048576 ? 0 : 1) + " MB";
}

export function formatoGB(bytes) {
  return (bytes / (1024 ** 3)).toFixed(1) + " GB";
}

export function formatoDuracion(inicio) {
  if (!inicio) return "";
  const s = Math.max(0, Date.now() / 1000 - inicio);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), seg = Math.floor(s % 60);
  if (h > 0) return h + "h " + String(m).padStart(2, "0") + "m";
  return m + ":" + String(seg).padStart(2, "0");
}

// Duracion FIJA en segundos -> "1:50:48" / "9:12" -- a diferencia de
// formatoDuracion() de arriba, que mide el tiempo transcurrido desde un
// epoch hasta ahora (una grabacion en curso), esto es para un numero que
// ya no cambia (la duracion de un archivo ya terminado).
export function formatoHMS(segundos) {
  if (segundos == null) return "";
  const total = Math.max(0, Math.round(segundos));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  return m + ":" + String(s).padStart(2, "0");
}

// "20261019-100717" (como lo escribe el detector en el nombre del
// archivo) -> "19/10 10:07"
export function formatoFechaArchivo(fecha) {
  if (!fecha || fecha.length !== 15) return "";
  const an = fecha.slice(0, 4), mes = fecha.slice(4, 6), dia = fecha.slice(6, 8);
  const h = fecha.slice(9, 11), min = fecha.slice(11, 13);
  return dia + "/" + mes + " " + h + ":" + min;
}

export function inicialDe(nombre) {
  return (nombre || "?").charAt(0).toUpperCase();
}
