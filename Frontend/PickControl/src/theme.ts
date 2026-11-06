// Paleta única de la app: dark + naranja de marca.
//
// Antes convivían 4 paletas (naranja/dark, verde #2ecc71, verde/beige
// en tablas y rojo-anaranjado #FF4500). Toda pantalla nueva debe usar
// estos tokens; los literales antiguos se migran progresivamente.
export const colors = {
  background: "#1a1a1a",
  surface: "#2a2a2a",
  surfaceAlt: "#242424",
  input: "#3a3a3a",
  border: "#3f3f3f",
  bar: "#131313",
  primary: "#ff9f1c",
  primarySoft: "#ffba57",
  onPrimary: "#1a1a1a",
  text: "#ffffff",
  textSecondary: "#aaaaaa",
  textMuted: "#777777",
  success: "#4caf50",
  danger: "#f44336",
  // Negro puro: overlays y fondos de vídeo (más oscuro que `background`).
  black: "#000000",
  // Ámbar semántico (avisos, pendientes, cuotas) y su fondo tintado.
  warning: "#ff9800",
  warningBg: "#332a1a",
  // Fondos tintados de acierto/fallo (badges, filas de resultado).
  successBg: "#1e3a24",
  dangerBg: "#3a1e1e",
  // Información/enlaces en dark y su fondo tintado.
  info: "#7ab3ff",
  infoBg: "#2d3a4f",
  // Acento morado de retos/combinadas y su familia de fondos/texto.
  purple: "#b388ff",
  purpleBg: "#2a2140",
  purpleText: "#e8e0f5",
  // Medallas del ranking de informantes.
  medalGold: "#b8860b",
  medalSilver: "#7d8590",
  medalBronze: "#8c5a2b",
} as const;

export type ColorName = keyof typeof colors;

/** Color semántico de una métrica: positivo verde, negativo rojo,
 * cero neutro. Sustituye a los `colorForYield`/`yieldColor` duplicados
 * por toda la app (quedan usos pendientes en pantallas no migradas). */
export function metricColor(value: number): string {
  if (value > 0) return colors.success;
  if (value < 0) return colors.danger;
  return colors.textSecondary;
}
