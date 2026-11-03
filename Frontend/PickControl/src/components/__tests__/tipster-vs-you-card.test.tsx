// Contrato de TipsterVsYouCard (Fase D): la card "Tipster vs. tú" de
// [informante] — comparativa publicado vs real: yield, % acierto,
// picks, pendientes y ganancia (unidades vs euros).
import React from "react";
import { render } from "@testing-library/react-native";
import TipsterVsYouCard from "@/src/components/tipster-vs-you-card";

const tipster = {
  yieldPct: 12.34,
  totalApuestas: 10,
  porcentajeAciertos: 60,
  ganancias: 8.5,
  pendientes: 2,
};

const usuario = {
  yieldPct: -3.2,
  totalApuestas: 4,
  porcentajeAciertos: 25,
  ganancias: -1.5,
  pendientes: 1,
};

describe("TipsterVsYouCard", () => {
  it("muestra cabecera, las 5 métricas y la nota de unidades", async () => {
    const screen = await render(
      <TipsterVsYouCard tipster={tipster} usuario={usuario} />
    );

    expect(screen.getByText("Tipster vs. tú")).toBeTruthy();
    expect(screen.getByText("El tipster")).toBeTruthy();
    expect(screen.getByText("Tú")).toBeTruthy();
    for (const label of [
      "Yield",
      "% acierto",
      "Picks",
      "Pendientes",
      "Ganancia",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText(/unidades \(u\) según su stake/)).toBeTruthy();
  });

  it("muestra los valores de ambas columnas con su formato", async () => {
    const screen = await render(
      <TipsterVsYouCard tipster={tipster} usuario={usuario} />
    );

    expect(screen.getByText("12.34%")).toBeTruthy();
    expect(screen.getByText("-3.20%")).toBeTruthy();
    expect(screen.getByText("60.0%")).toBeTruthy();
    expect(screen.getByText("25.0%")).toBeTruthy();
    expect(screen.getByText("8.50u")).toBeTruthy();
    expect(screen.getByText("-1.50€")).toBeTruthy();
  });

  it("sin apuestas de un lado muestra '—' en yield/%/ganancia", async () => {
    const screen = await render(
      <TipsterVsYouCard
        tipster={{ ...tipster, totalApuestas: 0 }}
        usuario={usuario}
      />
    );

    // El tipster sin picks: yield, %acierto y ganancia → "—"
    expect(screen.getAllByText("—").length).toBe(3);
    // La columna del usuario sigue con datos
    expect(screen.getByText("-3.20%")).toBeTruthy();
  });
});
