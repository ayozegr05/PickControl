// Contrato del estado de error/vacío compartido: mensaje +
// reintentar. Antes cada pantalla tragaba el error distinto
// (silencio, "sin datos" o un 0.00€ falso).
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

import { ErrorState } from "../inline-error";

describe("ErrorState", () => {
  it("muestra el mensaje y el botón Reintentar", async () => {
    const screen = await render(<ErrorState onRetry={jest.fn()} />);

    expect(screen.getByText("Error al cargar los datos")).toBeTruthy();
    expect(screen.getByLabelText("Reintentar")).toBeTruthy();
  });

  it("llama a onRetry al pulsar Reintentar", async () => {
    const onRetry = jest.fn();
    const screen = await render(<ErrorState onRetry={onRetry} />);

    await fireEvent.press(screen.getByLabelText("Reintentar"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("permite un detalle opcional bajo el mensaje", async () => {
    const screen = await render(
      <ErrorState message="Sin conexión" onRetry={jest.fn()} />
    );

    expect(screen.getByText("Sin conexión")).toBeTruthy();
    expect(screen.queryByText("Error al cargar los datos")).toBeNull();
  });
});
