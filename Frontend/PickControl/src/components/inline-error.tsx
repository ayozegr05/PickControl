// Estado de error compartido: icono, mensaje y botón Reintentar.
// Patrón único para las pantallas — antes cada una tragaba el fallo
// de red distinto: silencio (my-picks), "sin datos" ([informante])
// o un 0.00€ falso (earns).
import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import { colors } from "../theme";

interface ErrorStateProps {
  /** Detalle bajo el mensaje — por defecto el genérico. */
  message?: string;
  onRetry: () => void;
}

export function ErrorState({
  message = "Error al cargar los datos",
  onRetry,
}: ErrorStateProps) {
  return (
    <View style={styles.container}>
      <MaterialCommunityIcons
        name="cloud-alert-outline"
        size={42}
        color="#bbb"
      />
      <Text style={styles.message}>{message}</Text>
      <TouchableOpacity
        style={styles.retryButton}
        onPress={onRetry}
        accessibilityLabel="Reintentar"
        accessibilityRole="button"
      >
        <Text style={styles.retryText}>Reintentar</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 48,
    paddingHorizontal: 32,
  },
  message: {
    color: "#bbb",
    fontSize: 15,
    textAlign: "center",
    marginTop: 14,
    marginBottom: 20,
  },
  retryButton: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    paddingHorizontal: 28,
    paddingVertical: 10,
  },
  retryText: {
    color: colors.onPrimary,
    fontWeight: "600",
    fontSize: 15,
  },
});
