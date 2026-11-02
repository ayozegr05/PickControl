// Contrato de UpdatePickModal (Fase D): el modal "Actualizar Apuesta"
// de [informante] — dos botones icon-only (acertada/fallada) que
// devuelven el Acierto elegido por onResult.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import UpdatePickModal from "@/src/components/update-pick-modal";

const baseProps = () => ({
  visible: true,
  onClose: jest.fn(),
  onResult: jest.fn(),
});

describe("UpdatePickModal", () => {
  it("muestra el título y la pregunta", async () => {
    const props = baseProps();
    const screen = await render(<UpdatePickModal {...props} />);

    expect(screen.getByText("Actualizar Apuesta")).toBeTruthy();
    expect(screen.getByText("¿La apuesta fue correcta?")).toBeTruthy();
  });

  it("el botón verde devuelve 'True' y el rojo 'False'", async () => {
    const props = baseProps();
    const screen = await render(<UpdatePickModal {...props} />);

    await fireEvent.press(screen.getByLabelText("Apuesta acertada"));
    expect(props.onResult).toHaveBeenCalledWith("True");

    await fireEvent.press(screen.getByLabelText("Apuesta fallada"));
    expect(props.onResult).toHaveBeenCalledWith("False");
  });

  it("el botón de cerrar llama a onClose", async () => {
    const props = baseProps();
    const screen = await render(<UpdatePickModal {...props} />);

    await fireEvent.press(screen.getByLabelText("Cerrar"));
    expect(props.onClose).toHaveBeenCalled();
  });
});
