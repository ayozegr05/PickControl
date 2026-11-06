// Card base del tema con las 3 variantes reales de la app:
// "default" (surface redondeada), "glass" (translúcida con sombra —
// la de informante/tipster-cards) y "alt" (surfaceAlt — my-picks).
import React from "react";
import {
  StyleProp,
  StyleSheet,
  TouchableOpacity,
  View,
  ViewStyle,
} from "react-native";
import { colors } from "@/src/theme";

type Props = {
  children?: React.ReactNode;
  variant?: "default" | "glass" | "alt" | "outlined";
  style?: StyleProp<ViewStyle>;
  onPress?: () => void;
  testID?: string;
};

export function Card({
  children,
  variant = "default",
  style,
  onPress,
  testID,
}: Props) {
  const cardStyle = [styles[variant], style];
  if (onPress) {
    return (
      <TouchableOpacity testID={testID} style={cardStyle} onPress={onPress}>
        {children}
      </TouchableOpacity>
    );
  }
  return (
    <View testID={testID} style={cardStyle}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  default: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
  },
  glass: {
    backgroundColor: "rgba(33, 33, 33, 0.5)",
    borderRadius: 10,
    padding: 20,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
    marginBottom: 20,
  },
  alt: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 10,
    padding: 15,
  },
  outlined: {
    backgroundColor: colors.background,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    borderColor: colors.border,
    borderWidth: 1,
  },
});
