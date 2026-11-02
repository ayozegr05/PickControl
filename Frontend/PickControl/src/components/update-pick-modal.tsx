// Modal "Actualizar Apuesta", extraído de
// app/dynamic-routes/[informante].tsx (Fase D): liquida una apuesta
// manual pendiente como acertada o fallada. Los botones son
// icon-only — llevan accessibilityLabel para a11y y tests.
import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import ModalFrame from "@/src/components/modal-frame";
import { Acierto } from "@/src/types/pick.types";

interface UpdatePickModalProps {
  visible: boolean;
  onClose: () => void;
  onResult: (acierto: Extract<Acierto, "True" | "False">) => void;
}

export default function UpdatePickModal({
  visible,
  onClose,
  onResult,
}: UpdatePickModalProps) {
  return (
    <ModalFrame visible={visible} onClose={onClose}>
      <Text style={styles.modalTitle}>Actualizar Apuesta</Text>
      <Text style={styles.modalText}>¿La apuesta fue correcta?</Text>
      <View style={styles.modalButtons}>
        <TouchableOpacity
          style={[styles.modalButton, { backgroundColor: "#4CAF50" }]}
          onPress={() => onResult("True")}
          accessibilityLabel="Apuesta acertada"
        >
          <Text style={styles.modalButtonText}>
            <MaterialCommunityIcons name="check" size={26} color="white" />
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.modalButton, { backgroundColor: "#F44336" }]}
          onPress={() => onResult("False")}
          accessibilityLabel="Apuesta fallada"
        >
          <Text style={styles.modalButtonText}>
            <MaterialCommunityIcons name="close" size={32} color="white" />
          </Text>
        </TouchableOpacity>
      </View>
    </ModalFrame>
  );
}

const styles = StyleSheet.create({
  modalTitle: {
    fontSize: 24,
    fontWeight: "bold",
    color: "white",
    marginTop: 20,
    marginBottom: 15,
  },
  modalText: {
    fontSize: 18,
    color: "white",
    marginBottom: 20,
    textAlign: "center",
  },
  modalButtons: {
    flexDirection: "row",
    justifyContent: "space-around",
    width: "100%",
    marginTop: 10,
  },
  modalButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    minWidth: 100,
    alignItems: "center",
    marginHorizontal: 10,
  },
  modalButtonText: {
    color: "white",
    fontSize: 16,
    fontWeight: "bold",
  },
});
