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
