// Contenedor raíz de pantalla: flex:1 + fondo del tema.
// tone="black" cubre las pantallas que heredaron el fondo profundo
// (parsed-picks, auditoria, earns, analysis, informante).
import React from "react";
import { StyleProp, StyleSheet, View, ViewStyle } from "react-native";
import { colors } from "@/src/theme";

type Props = {
  children?: React.ReactNode;
  /** "background" (por defecto) o "black" para el fondo profundo. */
  tone?: "background" | "black";
  style?: StyleProp<ViewStyle>;
  testID?: string;
};

export function Screen({
  children,
  tone = "background",
  style,
  testID,
}: Props) {
  return (
    <View
      testID={testID}
      style={[styles.base, tone === "black" && styles.black, style]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    flex: 1,
    backgroundColor: colors.background,
  },
  black: {
    backgroundColor: colors.black,
  },
});
