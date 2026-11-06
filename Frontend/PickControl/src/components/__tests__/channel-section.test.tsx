// Contrato de ChannelSection (Fase D): sección del overview de
// parsed-picks por canal — cabecera con nombre + "Ver todos →",
// picks simples, secciones "Combinadas" y "Retos", y estado vacío.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import ChannelSection from "@/src/components/channel-section";
import { ParsedPick } from "@/src/types/parsed-pick.types";

const pick = (over: Partial<ParsedPick> = {}): ParsedPick => ({
  id: 7,
  es_apuesta: true,
  apuesta: null,
  deporte: "futbol",
  evento: "A vs B",
  mercado: "1X2",
  seleccion: "Gana A",
  cuota: 1.85,
  stake: 1,
  casa: null,
  informante: "Otro",
  explicacion: null,
  metodo: "llm",
  confianza: 0.9,
  fecha_evento: "2025-11-20T18:00:00",
  linea: null,
  acierto: null,
  anulada: false,
  motivo_anulada: null,
  verificado_por: null,
  informante_id: 1,
  raw_message_id: 1,
  created_at: "2025-11-20T10:00:00",
  es_reto: false,
  es_combinada: false,
  combinada_id: null,
  orden: null,
  cuota_efectiva: null,
  patas: [],
  fuera_ventana: false,
  ...over,
});

const cardProps = {
  showAll: false,
  canCorrect: true,
  onCorregir: jest.fn(),
  onCorregirPata: jest.fn(),
  onChannelPress: jest.fn(),
};

describe("ChannelSection", () => {
  it("cabecera con nombre, 'Ver todos →' llama onVerTodos", async () => {
    const onVerTodos = jest.fn();
    const s = await render(
      <ChannelSection
        group={{
          key: "canal-a",
          informanteId: 1,
          name: "Canal A",
          picks: [pick()],
        }}
        cardProps={cardProps}
        onVerTodos={onVerTodos}
      />
    );
    expect(s.getByText("Canal A")).toBeTruthy();
    await fireEvent.press(s.getByText("Ver todos →"));
    expect(onVerTodos).toHaveBeenCalled();
  });

  it("sin picks muestra el estado vacío", async () => {
    const s = await render(
      <ChannelSection
        group={{ key: "vacio", informanteId: 1, name: "Vacio", picks: [] }}
        cardProps={cardProps}
        onVerTodos={jest.fn()}
      />
    );
    expect(s.getByText("Sin picks extraídos con este filtro.")).toBeTruthy();
  });

  it("agrupa simples, combinadas y retos con sus títulos", async () => {
    const s = await render(
      <ChannelSection
        group={{
          key: "g",
          informanteId: 1,
          name: "G",
          picks: [
            pick({ id: 1, apuesta: "Simple A" }),
            pick({ id: 2, es_combinada: true, apuesta: "Combi B" }),
            pick({ id: 3, es_reto: true, apuesta: "Reto C" }),
          ],
        }}
        cardProps={cardProps}
        onVerTodos={jest.fn()}
      />
    );
    expect(s.getByText("Combinadas")).toBeTruthy();
    expect(s.getByText("Retos")).toBeTruthy();
    expect(s.getByText(/Simple A/)).toBeTruthy();
    expect(s.getByText(/Combi B/)).toBeTruthy();
    expect(s.getByText(/Reto C/)).toBeTruthy();
  });

  it("sin informanteId no muestra 'Ver todos →'", async () => {
    const s = await render(
      <ChannelSection
        group={{ key: "g", informanteId: null, name: "G", picks: [pick()] }}
        cardProps={cardProps}
        onVerTodos={jest.fn()}
      />
    );
    expect(s.queryByText("Ver todos →")).toBeNull();
  });
});
