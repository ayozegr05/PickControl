// Contrato del componente JugarModal (Fase D): el formulario "Yo
// también la jugué" de la pantalla [informante] — tres inputs (la
// validación cantidad/cuota queda en la pantalla, igual que hoy) y
// botón de registro con spinner mientras envía.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import JugarModal from "@/src/components/jugar-modal";

const baseProps = () => ({
  visible: true,
  loading: false,
  onClose: jest.fn(),
  onSubmit: jest.fn(),
});

describe("JugarModal", () => {
  it("muestra el título, la pista y los tres campos", async () => {
    const props = baseProps();
    const screen = await render(<JugarModal {...props} />);

    expect(screen.getByText("Yo también la jugué")).toBeTruthy();
    expect(screen.getByText(/pueden diferir de las del tipster/)).toBeTruthy();
    expect(
      screen.getByPlaceholderText("Cantidad apostada (obligatorio)")
    ).toBeTruthy();
    expect(
      screen.getByPlaceholderText("Cuota conseguida (opcional)")
    ).toBeTruthy();
    expect(screen.getByPlaceholderText("Casa (opcional)")).toBeTruthy();
  });

  it("al registrar envía los tres valores introducidos", async () => {
    const props = baseProps();
    const screen = await render(<JugarModal {...props} />);

    await fireEvent.changeText(
      screen.getByPlaceholderText("Cantidad apostada (obligatorio)"),
      "12,5"
    );
    await fireEvent.changeText(
      screen.getByPlaceholderText("Cuota conseguida (opcional)"),
      "2.10"
    );
    await fireEvent.changeText(
      screen.getByPlaceholderText("Casa (opcional)"),
      "bet365"
    );
    await fireEvent.press(screen.getByText("Registrar apuesta"));

    expect(props.onSubmit).toHaveBeenCalledWith({
      cantidad: "12,5",
      cuota: "2.10",
      casa: "bet365",
    });
  });

  it("loading sustituye el botón por un spinner (no se puede enviar)", async () => {
    const props = { ...baseProps(), loading: true };
    const screen = await render(<JugarModal {...props} />);

    expect(screen.queryByText("Registrar apuesta")).toBeNull();
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("visible=false no muestra el formulario", async () => {
    const props = { ...baseProps(), visible: false };
    const screen = await render(<JugarModal {...props} />);

    expect(
      screen.queryByPlaceholderText("Cantidad apostada (obligatorio)")
    ).toBeNull();
  });
});
