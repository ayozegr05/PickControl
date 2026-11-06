// Contrato de TipsterPicksCard (Fase D): la card "Picks del tipster"
// de [informante] — stats del canal, tabla de simples, sección de
// retos y combinadas expandibles con sus patas.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import TipsterPicksCard from "@/src/components/tipster-picks-card";
import { PickItem } from "@/src/types/pick.types";

const makePick = (over: Partial<PickItem> = {}): PickItem => ({
  id: 1,
  apuesta: "Over 2.5 goles",
  informante: "Canal A",
  tipoDeApuesta: "goles",
  acierto: "Pending",
  casa: "bet365",
  cantidadApostada: 2,
  cuota: 1.9,
  fecha: "2025-01-10T20:00:00.000Z",
  source: "telegram",
  evento: "Madrid vs Barça",
  ...over,
});

const baseProps = () => ({
  totalApuestas: 10,
  totalAciertos: 6,
  porcentajeAciertos: 60,
  ganancias: 8.5,
  yieldPct: 12.34,
  rows: [] as PickItem[],
  retos: [] as PickItem[],
  combinadas: [] as PickItem[],
  combinadasStats: { total: 0, aciertos: 0, ganancias: 0, yieldPct: 0 },
  onCorregir: jest.fn(),
  onPickPress: jest.fn(),
});

describe("TipsterPicksCard", () => {
  it("muestra título, stats y el botón de corregir", async () => {
    const props = baseProps();
    const screen = await render(<TipsterPicksCard {...props} />);

    expect(screen.getByText("Picks del tipster")).toBeTruthy();
    expect(screen.getByText("Total Picks:")).toBeTruthy();
    expect(screen.getByText("12.34%")).toBeTruthy();
    expect(screen.getByText("8.50u")).toBeTruthy();

    await fireEvent.press(screen.getByText("Corregir picks de Telegram"));
    expect(props.onCorregir).toHaveBeenCalled();
  });

  it("sin picks muestra el mensaje de vacío", async () => {
    const props = baseProps();
    const screen = await render(<TipsterPicksCard {...props} />);

    expect(
      screen.getByText("Todavía no hay picks extraídos de este canal.")
    ).toBeTruthy();
  });

  it("la tabla muestra las 7 cabeceras y pulsar una fila abre el detalle", async () => {
    const props = { ...baseProps(), rows: [makePick()] };
    const screen = await render(<TipsterPicksCard {...props} />);

    for (const h of [
      "Apuesta",
      "Acierto",
      "Fecha",
      "Mercado",
      "Cuota",
      "Stake",
      "Ganancia",
    ]) {
      expect(screen.getByText(h)).toBeTruthy();
    }

    await fireEvent.press(screen.getByText("Over 2.5 goles"));
    expect(props.onPickPress).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 })
    );
  });

  it("el scroll horizontal de la tabla muestra su indicador", async () => {
    // Misma queja que ApuestasTable: con el indicador oculto las
    // columnas fuera del viewport parecían recortadas sin remedio.
    const props = { ...baseProps(), rows: [makePick()] };
    const screen = await render(<TipsterPicksCard {...props} />);

    const findHorizontal = (
      node: {
        props?: Record<string, unknown>;
        children?: unknown[] | null;
      } | null
    ): { props?: Record<string, unknown> } | null => {
      if (!node) return null;
      if (node.props?.horizontal === true) return node;
      for (const child of node.children ?? []) {
        if (typeof child !== "object" || child === null) continue;
        const found = findHorizontal(
          child as { props?: Record<string, unknown>; children?: unknown[] }
        );
        if (found) return found;
      }
      return null;
    };
    const scroll = findHorizontal(screen.toJSON());
    expect(scroll).toBeTruthy();
    expect(scroll!.props?.showsHorizontalScrollIndicator).toBe(true);
  });

  it("un reto aparece en su sección y abre el detalle", async () => {
    const props = {
      ...baseProps(),
      retos: [makePick({ id: 9, esReto: true, apuesta: "Reto escalera" })],
    };
    const screen = await render(<TipsterPicksCard {...props} />);

    expect(screen.getByText("Retos")).toBeTruthy();
    await fireEvent.press(screen.getByText(/Reto escalera/));
    expect(props.onPickPress).toHaveBeenCalledWith(
      expect.objectContaining({ id: 9 })
    );
  });

  it("la combinada muestra stats y se expande al pulsar el chevron", async () => {
    const comb = makePick({
      id: 20,
      esCombinada: true,
      apuesta: "Combinada 2",
      cuota: 3.4,
      cuotaEfectiva: 2.8,
      patas: [
        {
          id: 21,
          orden: 0,
          seleccion: "Madrid gana",
          evento: "Madrid vs Barça",
          mercado: "1X2",
          acierto: true,
          anulada: false,
        },
      ],
    });
    const props = {
      ...baseProps(),
      combinadas: [comb],
      combinadasStats: {
        total: 1,
        aciertos: 0,
        ganancias: 0,
        yieldPct: 0,
      },
    };
    const screen = await render(<TipsterPicksCard {...props} />);

    expect(screen.getByText("Combinadas")).toBeTruthy();
    expect(screen.getByText(/1 combinadas/)).toBeTruthy();
    expect(screen.getByText(/cuota 2\.80 \(declarada 3\.40\)/)).toBeTruthy();
    // Las patas no se ven hasta expandir
    expect(screen.queryByText("Madrid gana")).toBeNull();

    await fireEvent.press(screen.getByLabelText("Expandir combinada"));
    expect(screen.getByText("Madrid gana")).toBeTruthy();
    expect(screen.getByText(/Madrid vs Barça · 1X2/)).toBeTruthy();
  });

  it("combinada anulada muestra su estado en las patas", async () => {
    const comb = makePick({
      id: 30,
      esCombinada: true,
      patas: [
        {
          id: 31,
          seleccion: "Empate",
          anulada: true,
          motivo_anulada: "push",
          acierto: null,
        },
      ],
    });
    const props = { ...baseProps(), combinadas: [comb] };
    const screen = await render(<TipsterPicksCard {...props} />);

    await fireEvent.press(screen.getByLabelText("Expandir combinada"));
    expect(screen.getByText("Empate")).toBeTruthy();
    expect(screen.getByText(/anulada/)).toBeTruthy();
    expect(screen.getByText("↩️")).toBeTruthy();
  });
});
