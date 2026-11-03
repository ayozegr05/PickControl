// Contrato del componente ParsedPickCard (Fase D): la tarjeta de un
// pick extraído de Telegram con sus estados de resultado y botones de
// corrección. Test-first: escrito antes de extraer el componente de
// app/(tabs)/parsed-picks.tsx.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import ParsedPickCard from "@/src/components/parsed-pick-card";
import { ParsedPick } from "@/src/api/parsed-picks.api";

const makePick = (over: Partial<ParsedPick> = {}): ParsedPick => ({
  id: 1,
  es_apuesta: true,
  apuesta: "Over 2.5 goles",
  deporte: "futbol",
  evento: "Madrid vs Barça",
  mercado: "goles",
  seleccion: "más de 2.5",
  cuota: 1.9,
  stake: 2,
  casa: "bet365",
  informante: "Canal A | FREE",
  explicacion: null,
  metodo: "llm",
  confianza: 0.9,
  fecha_evento: "2025-01-10T20:00:00.000Z",
  linea: 2.5,
  acierto: null,
  anulada: false,
  motivo_anulada: null,
  verificado_por: null,
  informante_id: 7,
  raw_message_id: 1,
  created_at: "2025-01-09T10:00:00.000Z",
  es_reto: false,
  es_combinada: false,
  combinada_id: null,
  orden: null,
  cuota_efectiva: null,
  patas: [],
  fuera_ventana: false,
  ...over,
});

const baseProps = () => ({
  canCorrect: true,
  onCorregir: jest.fn(),
  onCorregirPata: jest.fn(),
  onChannelPress: jest.fn(),
});

describe("ParsedPickCard", () => {
  it("renderiza título, canal, cuota/stake y método", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick()} {...props} />
    );

    expect(screen.getByText("Over 2.5 goles")).toBeTruthy();
    expect(screen.getByText("Canal A FREE")).toBeTruthy();
    expect(screen.getByText(/Cuota: 1.9 \| Stake: 2/)).toBeTruthy();
    expect(screen.getByText(/Método: llm \| Confianza: 0.9/)).toBeTruthy();
  });

  it("pick sin apuesta muestra 'Sin apuesta'", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick({ apuesta: null })} {...props} />
    );

    expect(screen.getByText("Sin apuesta")).toBeTruthy();
  });

  it("pulsar el canal avisa con el nombre completo del informante", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick()} {...props} />
    );

    await fireEvent.press(screen.getByText("Canal A FREE"));
    expect(props.onChannelPress).toHaveBeenCalledWith("Canal A | FREE");
  });

  it.each([
    [null, "Pendiente"],
    [true, "Acertó"],
    [false, "Falló"],
  ])("muestra el resultado correcto (acierto=%s → %s)", async (acierto, texto) => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard
        pick={makePick({ acierto: acierto as boolean | null })}
        {...props}
      />
    );

    expect(screen.getByText(new RegExp(`Resultado:.*${texto}`))).toBeTruthy();
  });

  it("anulada con motivo muestra 'Anulada · motivo'", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard
        pick={makePick({ anulada: true, motivo_anulada: "push" })}
        {...props}
      />
    );

    expect(screen.getByText(/Anulada · push/)).toBeTruthy();
  });

  it("fuera_ventana muestra el aviso de no-reintento", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick({ fuera_ventana: true })} {...props} />
    );

    expect(
      screen.getByText(/fuera de ventana — ya no se reintenta/)
    ).toBeTruthy();
  });

  it("los 4 botones de corrección llaman a onCorregir con su payload", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick()} {...props} />
    );
    await fireEvent.press(screen.getByText("Acertó"));
    expect(props.onCorregir).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      { acierto: true }
    );

    await fireEvent.press(screen.getByText("Falló"));
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      acierto: false,
    });

    await fireEvent.press(screen.getByText("Anulada"));
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      anulada: true,
    });

    // "Pendiente" aparece en el resultado del pick y en el botón:
    // el botón es el último.
    const pendientes = screen.getAllByText("Pendiente");
    await fireEvent.press(pendientes[pendientes.length - 1]);
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      acierto: null,
      anulada: false,
    });
  });

  it("combinada muestra las patas con su estado y botones por pata", async () => {
    const pata = makePick({
      id: 20,
      seleccion: "Madrid gana",
      evento: "Madrid vs Barça",
      orden: 0,
      acierto: true,
    });
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard
        pick={makePick({
          es_combinada: true,
          cuota: 3.4,
          cuota_efectiva: 2.8,
          patas: [pata],
        })}
        {...props}
      />
    );

    expect(screen.getByText(/Patas \(1\)/)).toBeTruthy();
    expect(screen.getByText(/cuota efectiva 2.8/)).toBeTruthy();
    expect(screen.getByText(/1\. Madrid gana/)).toBeTruthy();
    // "Acertó" aparece dos veces: el estado de la pata y el botón de
    // corrección del padre.
    expect(screen.getAllByText(/Acertó/).length).toBeGreaterThanOrEqual(2);

    await fireEvent.press(screen.getByText("❌"));
    expect(props.onCorregirPata).toHaveBeenCalledWith(
      expect.objectContaining({ id: 20 }),
      { acierto: false }
    );
  });

  it("showAll añade la línea 'Es apuesta: Sí'", async () => {
    const props = baseProps();
    const screen = await render(
      <ParsedPickCard pick={makePick()} showAll {...props} />
    );

    expect(screen.getByText(/Es apuesta: Sí/)).toBeTruthy();
  });

  it("sin canCorrect oculta botones de corrección (pick y patas)", async () => {
    const pata = makePick({ id: 20, seleccion: "Madrid gana", orden: 0 });
    const screen = await render(
      <ParsedPickCard
        pick={makePick({ es_combinada: true, patas: [pata] })}
        {...baseProps()}
        canCorrect={false}
      />
    );

    // El estado "Pendiente" sigue visible (padre + pata), sin botones.
    expect(screen.getAllByText("Pendiente").length).toBe(2);
    expect(screen.queryByText("Acertó")).toBeNull();
    expect(screen.queryByText("Falló")).toBeNull();
    expect(screen.queryByText("Anulada")).toBeNull();
    expect(screen.queryByText("✅")).toBeNull();
    expect(screen.queryByText("❌")).toBeNull();
    expect(screen.queryByText("↩️")).toBeNull();
    expect(screen.queryByText("❓")).toBeNull();
  });
});
