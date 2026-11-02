// Utilidades de formato compartidas.
//
// Antes duplicadas por pantalla: `formatDate` (parsed-picks),
// `formatearFecha` ([informante]), `cleanChannel` (auditoria +
// parsed-picks). La versión unificada conserva el comportamiento de
// cada sitio (ver src/utils/__tests__/format.test.ts).

/** "28/10/2026, 14:30" — la variante con hora de parsed-picks.
 * Input inválido se devuelve tal cual (comportamiento original). */
export function formatDateTime(isoDate: string): string {
  const date = new Date(isoDate);
  if (isNaN(date.getTime())) return isoDate;
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "28/10/26" — la variante corta (año 2 dígitos) de [informante]. */
export function formatDateShort(fecha: string | Date): string {
  const date = new Date(fecha);
  const dia = date.getDate().toString().padStart(2, "0");
  const mes = (date.getMonth() + 1).toString().padStart(2, "0");
  const año = date.getFullYear().toString().slice(-2);
  return `${dia}/${mes}/${año}`;
}

/** "28/10/2026" — fecha ES con año completo.
 * Sustituirá a los `toLocaleDateString()` sin locale que renderizaban
 * formato US ("7/28/2026") — bug registrado en ISSUES. */
export function formatDateES(fecha: string | Date): string {
  const date = new Date(fecha);
  if (isNaN(date.getTime())) return String(fecha);
  return date.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

/** Nombre de canal legible: quita emojis/separadores `|` y normaliza
 * espacios. Une TODOS los segmentos con texto (un canal "Dm7 ||
 * Allsports" y "Dm7 || GRATUITO" deben verse distintos, no ambos
 * "Dm7"). null/vacío → "desconocido". */
export function cleanChannel(name: string | null | undefined): string {
  if (!name) return "desconocido";
  return (
    name
      .split("|")
      .map((part) =>
        part
          .replace(/[^\p{L}\p{N}\s]/gu, " ")
          .replace(/\s+/g, " ")
          .trim()
      )
      .filter(Boolean)
      .join(" ") || "desconocido"
  );
}
