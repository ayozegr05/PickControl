// Formulario "Yo también la jugué", extraído de
// app/dynamic-routes/[informante].tsx (Fase D). Mantiene los tres
// inputs como estado interno (pre-rellenables con `initialValues`) y
// devuelve los textos tal cual a `onSubmit` — la validación y el
// parseo de cantidad/cuota siguen en la pantalla, igual que antes.
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";

export interface JugarFormValues {
  cantidad: string;
  cuota: string;
  casa: string;
}

interface JugarModalProps {
  visible: boolean;
  loading: boolean;
  initialValues?: Partial<JugarFormValues>;
  onClose: () => void;
  onSubmit: (values: JugarFormValues) => void;
}

export default function JugarModal({
  visible,
  loading,
  initialValues,
  onClose,
  onSubmit,
}: JugarModalProps) {
  const [cantidad, setCantidad] = useState("");
  const [cuota, setCuota] = useState("");
  const [casa, setCasa] = useState("");

  // Al abrirse se resetean los campos con los valores iniciales del
  // pick (cuota/casa del tipster, editables porque la real puede
  // diferir de la publicada).
  useEffect(() => {
    if (visible) {
      setCantidad(initialValues?.cantidad ?? "");
      setCuota(initialValues?.cuota ?? "");
      setCasa(initialValues?.casa ?? "");
    }
    // Solo al abrirse: los initialValues llegan junto a visible=true.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={visible}
      onRequestClose={onClose}
    >
      <View style={styles.modalContainer}>
        <View style={styles.modalContent}>
          <TouchableOpacity style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeButtonText}>
              <MaterialCommunityIcons name="close" size={26} color="white" />
            </Text>
          </TouchableOpacity>

          <Text style={styles.modalTitle}>Yo también la jugué</Text>
          <Text style={styles.jugarHint}>
            Cantidad, cuota y casa reales — pueden diferir de las del tipster y
            así se compara el yield publicado con el tuyo.
          </Text>

          <TextInput
            style={styles.jugarInput}
            placeholder="Cantidad apostada (obligatorio)"
            placeholderTextColor="#888"
            keyboardType="numeric"
            value={cantidad}
            onChangeText={setCantidad}
          />
          <TextInput
            style={styles.jugarInput}
            placeholder="Cuota conseguida (opcional)"
            placeholderTextColor="#888"
            keyboardType="numeric"
            value={cuota}
            onChangeText={setCuota}
          />
          <TextInput
            style={styles.jugarInput}
            placeholder="Casa (opcional)"
            placeholderTextColor="#888"
            value={casa}
            onChangeText={setCasa}
          />

          <TouchableOpacity
            style={[styles.jugarButton, loading && { opacity: 0.6 }]}
            onPress={() => onSubmit({ cantidad, cuota, casa })}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator size="small" color="white" />
            ) : (
              <Text style={styles.jugarButtonText}>Registrar apuesta</Text>
            )}
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "rgba(0, 0, 0, 0.7)",
  },
  modalContent: {
    backgroundColor: "#303030",
    borderRadius: 15,
    padding: 20,
    width: "80%",
    alignItems: "center",
    elevation: 5,
    position: "relative",
  },
  closeButton: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: "#F44336",
    justifyContent: "center",
    alignItems: "center",
    zIndex: 1,
  },
  closeButtonText: {
    color: "white",
    fontSize: 16,
    fontWeight: "bold",
  },
  modalTitle: {
    fontSize: 24,
    fontWeight: "bold",
    color: "white",
    marginTop: 20,
    marginBottom: 15,
  },
  jugarHint: {
    color: "#aaa",
    fontSize: 12,
    textAlign: "center",
    marginBottom: 12,
  },
  jugarInput: {
    alignSelf: "stretch",
    height: 44,
    backgroundColor: "white",
    borderRadius: 6,
    paddingHorizontal: 10,
    marginVertical: 6,
    fontSize: 15,
    color: "#000",
  },
  jugarButton: {
    backgroundColor: "#ff9f1c",
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 8,
    marginTop: 6,
    marginBottom: 4,
    alignSelf: "center",
  },
  jugarButtonText: {
    color: "#0d0d0d",
    fontSize: 15,
    fontWeight: "bold",
  },
});
