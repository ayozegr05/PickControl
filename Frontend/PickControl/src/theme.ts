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
  onPrimary: "#1a1a1a",
  text: "#ffffff",
  textSecondary: "#aaaaaa",
  textMuted: "#777777",
  success: "#4caf50",
  danger: "#f44336",
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
