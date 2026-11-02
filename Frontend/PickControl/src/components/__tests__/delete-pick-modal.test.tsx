// Contrato de DeletePickModal (Fase D): el modal "Eliminar Apuesta"
// de [informante] — confirmación con un único botón de borrado.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import DeletePickModal from "@/src/components/delete-pick-modal";

const baseProps = () => ({
  visible: true,
  onClose: jest.fn(),
  onConfirm: jest.fn(),
});

describe("DeletePickModal", () => {
  it("muestra el título y el texto de confirmación", async () => {
    const props = baseProps();
    const screen = await render(<DeletePickModal {...props} />);

    expect(screen.getByText("Eliminar Apuesta")).toBeTruthy();
    expect(
      screen.getByText("¿Estás seguro de que deseas eliminar esta apuesta?")
    ).toBeTruthy();
  });

  it("el botón de papelera confirma el borrado", async () => {
    const props = baseProps();
    const screen = await render(<DeletePickModal {...props} />);

    await fireEvent.press(screen.getByLabelText("Eliminar apuesta"));
    expect(props.onConfirm).toHaveBeenCalled();
  });

  it("el botón de cerrar llama a onClose sin borrar", async () => {
    const props = baseProps();
    const screen = await render(<DeletePickModal {...props} />);

    await fireEvent.press(screen.getByLabelText("Cerrar"));
    expect(props.onClose).toHaveBeenCalled();
    expect(props.onConfirm).not.toHaveBeenCalled();
  });
});
