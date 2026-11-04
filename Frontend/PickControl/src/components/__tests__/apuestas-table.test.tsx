// Contrato de ApuestasTable (Fase D): la tabla "Mis apuestas
// siguiéndolo" de [informante] — 8 columnas, fila con icono de
// resultado (❓ editable en pendientes), fecha editable, papelera y
// ganancia calculada con gananciaApuesta.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import ApuestasTable from "@/src/components/apuestas-table";
import { PickItem } from "@/src/types/pick.types";

// Nodo host con `horizontal` (el ScrollView de la tabla) dentro del
// árbol JSON — en RNTL 14 las queries solo ven elementos host.
type JsonNode = {
  props?: Record<string, unknown>;
  children?: (JsonNode | string)[] | null;
};
const findHorizontalScroll = (node: JsonNode | null): JsonNode | null => {
  if (!node) return null;
  if (node.props?.horizontal === true) return node;
  for (const child of node.children ?? []) {
    if (typeof child === "string") continue;
    const found = findHorizontalScroll(child);
    if (found) return found;
  }
  return null;
};

const makeApuesta = (over: Partial<PickItem> = {}): PickItem => ({
  id: 1,
  apuesta: "Over 2.5 goles",
  informante: "Canal A",
  tipoDeApuesta: "goles",
  acierto: "Pending",
  casa: "bet365",
  cantidadApostada: 10,
  cuota: 2.0,
  fecha: "2025-01-10T20:00:00.000Z",
  source: "manual",
  ...over,
});

const baseProps = () => ({
  onPronostico: jest.fn(),
  onFecha: jest.fn(),
  onEliminar: jest.fn(),
});

describe("ApuestasTable", () => {
  it("renderiza las 8 cabeceras", async () => {
    const props = baseProps();
    const screen = await render(
      <ApuestasTable rows={[makeApuesta()]} {...props} />
    );

    for (const h of [
      "Apuesta",
      "Acierto",
      "Fecha",
      "Tipo de Apuesta",
      "Cuota",
      "Cant. Apostada",
      "Ganancia",
      "Acciones",
    ]) {
      expect(screen.getByText(h)).toBeTruthy();
    }
  });

  it("el scroll horizontal muestra su indicador (columnas a la vista)", async () => {
    // Con el indicador oculto la última columna parecía "cortada":
    // el usuario no veía que había tabla fuera del viewport.
    const props = baseProps();
    const screen = await render(
      <ApuestasTable rows={[makeApuesta()]} {...props} />
    );

    const scroll = findHorizontalScroll(screen.toJSON());
    expect(scroll).toBeTruthy();
    expect(scroll!.props?.showsHorizontalScrollIndicator).toBe(true);
  });

  it("la fila muestra datos, cuota con 2 decimales y ganancia", async () => {
    const props = baseProps();
    // Ganada: 10€ a cuota 1.9 → +9€ (importes distintos para poder
    // distinguir cantidad apostada de ganancia).
    const screen = await render(
      <ApuestasTable
        rows={[makeApuesta({ acierto: "True", cuota: 1.9 })]}
        {...props}
      />
    );

    expect(screen.getByText("Over 2.5 goles")).toBeTruthy();
    expect(screen.getByText("goles")).toBeTruthy();
    expect(screen.getByText("1.90")).toBeTruthy();
    expect(screen.getByText(/10\.00/)).toBeTruthy();
    expect(screen.getByText(/9\.00/)).toBeTruthy();
    expect(screen.getByText("✅")).toBeTruthy();
  });

  it("pendiente muestra ❓ pulsable → onPronostico con la apuesta", async () => {
    const props = baseProps();
    const apuesta = makeApuesta();
    const screen = await render(
      <ApuestasTable rows={[apuesta]} {...props} />
    );

    await fireEvent.press(screen.getByText("❓"));
    expect(props.onPronostico).toHaveBeenCalledWith(apuesta);
  });

  it("acertada/fallada muestran iconos no pulsables", async () => {
    const props = baseProps();
    const screen = await render(
      <ApuestasTable
        rows={[
          makeApuesta({ id: 1, acierto: "True" }),
          makeApuesta({ id: 2, acierto: "False" }),
        ]}
        {...props}
      />
    );

    expect(screen.getByText("✅")).toBeTruthy();
    expect(screen.getByText("❌")).toBeTruthy();
    expect(screen.queryByText("❓")).toBeNull();
  });

  it("pulsar la fecha avisa con la apuesta (date picker)", async () => {
    const props = baseProps();
    const apuesta = makeApuesta();
    const screen = await render(
      <ApuestasTable rows={[apuesta]} {...props} />
    );

    await fireEvent.press(screen.getByLabelText("Editar fecha"));
    expect(props.onFecha).toHaveBeenCalledWith(apuesta);
  });

  it("la papelera avisa con la apuesta", async () => {
    const props = baseProps();
    const apuesta = makeApuesta();
    const screen = await render(
      <ApuestasTable rows={[apuesta]} {...props} />
    );

    await fireEvent.press(screen.getByLabelText("Eliminar apuesta"));
    expect(props.onEliminar).toHaveBeenCalledWith(apuesta);
  });
});
