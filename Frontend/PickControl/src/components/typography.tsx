// Tipografía de pantalla: título (primary bold, tamaño variable) y
// subtítulo (textSecondary). Los tamaños heredados varían por
// pantalla (22/24/28/32/35) — `size` lo parametriza sin forzar una
// unificación visual que habría que validar en dispositivo.
import React from "react";
import { StyleProp, StyleSheet, Text, TextStyle } from "react-native";
import { colors } from "@/src/theme";

type Props = {
  children: React.ReactNode;
  size?: number;
  style?: StyleProp<TextStyle>;
};

export function ScreenTitle({ children, size = 22, style }: Props) {
  return (
    <Text style={[styles.title, { fontSize: size }, style]}>{children}</Text>
  );
}

export function ScreenSubtitle({ children, size = 14, style }: Props) {
  return (
    <Text style={[styles.subtitle, { fontSize: size }, style]}>{children}</Text>
  );
}

const styles = StyleSheet.create({
  title: {
    color: colors.primary,
    fontWeight: "bold",
  },
  subtitle: {
    color: colors.textSecondary,
  },
});
