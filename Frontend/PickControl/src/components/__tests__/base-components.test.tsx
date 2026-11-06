// Contrato de los componentes base (Bloque 2): fijan API y tokens
// antes de migrar las pantallas. Cada uno debe (a) renderizar su
// contenido, (b) aplicar el token de tema correcto y (c) aceptar
// estilo extra sin romper el base.
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { StyleSheet, Text, TextStyle, ViewStyle } from "react-native";
import { colors } from "@/src/theme";
import { Screen } from "../screen";
import { Card } from "../card";
import { ScreenTitle, ScreenSubtitle } from "../typography";
import { FormInput } from "../form-input";

const flat = (node: { props: { style?: unknown } }): ViewStyle & TextStyle =>
  StyleSheet.flatten(node.props.style as ViewStyle & TextStyle);

describe("Screen", () => {
  it("renderiza children con fondo por defecto", async () => {
    const { getByText, getByTestId } = await render(
      <Screen testID="s">
        <Text>hola</Text>
      </Screen>
    );
    expect(getByText("hola")).toBeTruthy();
    expect(flat(getByTestId("s")).backgroundColor).toBe(colors.background);
  });

  it('tone="black" usa el fondo profundo', async () => {
    const { getByTestId } = await render(<Screen testID="s" tone="black" />);
    expect(flat(getByTestId("s")).backgroundColor).toBe(colors.black);
  });

  it("fusiona style extra manteniendo el base", async () => {
    const { getByTestId } = await render(
      <Screen testID="s" style={{ padding: 20 }} />
    );
    const st = flat(getByTestId("s"));
    expect(st.flex).toBe(1);
    expect(st.padding).toBe(20);
  });
});

describe("Card", () => {
  it("variante por defecto: surface redondeada", async () => {
    const { getByTestId } = await render(
      <Card testID="c">
        <Text>x</Text>
      </Card>
    );
    const st = flat(getByTestId("c"));
    expect(st.backgroundColor).toBe(colors.surface);
    expect(st.borderRadius).toBe(12);
  });

  it('variant="glass": translúcida con sombra', async () => {
    const { getByTestId } = await render(<Card testID="c" variant="glass" />);
    const st = flat(getByTestId("c"));
    expect(st.backgroundColor).toBe("rgba(33, 33, 33, 0.5)");
    expect(st.shadowColor).toBe(colors.black);
  });

  it("con onPress la card es pulsable", async () => {
    const onPress = jest.fn();
    const { getByTestId } = await render(<Card testID="c" onPress={onPress} />);
    fireEvent.press(getByTestId("c"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('variant="outlined": fondo base con borde', async () => {
    const { getByTestId } = await render(
      <Card testID="c" variant="outlined" />
    );
    const st = flat(getByTestId("c"));
    expect(st.backgroundColor).toBe(colors.background);
    expect(st.borderColor).toBe(colors.border);
    expect(st.borderWidth).toBe(1);
  });

  it('variant="alt": surfaceAlt', async () => {
    const { getByTestId } = await render(<Card testID="c" variant="alt" />);
    expect(flat(getByTestId("c")).backgroundColor).toBe(colors.surfaceAlt);
  });
});

describe("typography", () => {
  it("ScreenTitle: primary bold con tamaño configurable", async () => {
    const { getByText } = await render(<ScreenTitle size={28}>T</ScreenTitle>);
    const st = flat(getByText("T") as never);
    expect(st.color).toBe(colors.primary);
    expect(st.fontWeight).toBe("bold");
    expect(st.fontSize).toBe(28);
  });

  it("ScreenTitle usa tamaño 22 por defecto", async () => {
    const { getByText } = await render(<ScreenTitle>T</ScreenTitle>);
    const st = flat(getByText("T") as never);
    expect(st.fontSize).toBe(22);
  });

  it("ScreenSubtitle: textSecondary", async () => {
    const { getByText } = await render(<ScreenSubtitle>S</ScreenSubtitle>);
    const st = flat(getByText("S") as never);
    expect(st.color).toBe(colors.textSecondary);
  });
});

describe("FormInput", () => {
  it("aplica el estilo de input del tema y propaga props de TextInput", async () => {
    const { getByPlaceholderText } = await render(
      <FormInput placeholder="Email" testID="i" />
    );
    const el = getByPlaceholderText("Email");
    const st = flat(el as never);
    expect(st.backgroundColor).toBe(colors.surfaceAlt);
    expect(st.color).toBe(colors.text);
    expect(st.borderRadius).toBe(10);
  });
});
