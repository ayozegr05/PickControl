// Caracterización de metricColor: la semántica compartida de los 3
// colorForYield/yieldColor duplicados (>0 verde, <0 rojo, 0 neutro).
import { colors, metricColor } from "../../theme";

describe("metricColor", () => {
  it("positivo → success, negativo → danger, cero → neutro", () => {
    expect(metricColor(5)).toBe(colors.success);
    expect(metricColor(0.01)).toBe(colors.success);
    expect(metricColor(-3)).toBe(colors.danger);
    expect(metricColor(0)).toBe(colors.textSecondary);
  });
});

// Contrato de tokens ampliados (Bloque 2): cubren los literales
// neutros y los tintes semánticos que quedaban hardcodeados.
describe("tokens de tema extendidos", () => {
  it("incluye negro puro y los tintes semánticos", () => {
    for (const name of [
      "black",
      "warning",
      "warningBg",
      "successBg",
      "dangerBg",
      "info",
      "infoBg",
      "purple",
      "purpleBg",
      "purpleText",
      "medalGold",
      "medalSilver",
      "medalBronze",
    ] as const) {
      expect(colors[name]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("los fondos tintados oscilan en la gama dark de la app", () => {
    // Los *Bg son fondos oscuros teñidos: ningún canal supera 0x60.
    for (const key of [
      "warningBg",
      "successBg",
      "dangerBg",
      "infoBg",
      "purpleBg",
    ] as const) {
      const hex = colors[key].replace("#", "");
      const [r, g, b] = [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
      ];
      expect(Math.max(r, g, b)).toBeLessThanOrEqual(0x60);
    }
  });
});
