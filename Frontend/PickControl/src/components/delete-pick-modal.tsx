// Modal "Eliminar Apuesta", extraído de
// app/dynamic-routes/[informante].tsx (Fase D): confirmación de
// borrado con un único botón de papelera.
import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import ModalFrame from "@/src/components/modal-frame";
import { colors } from "@/src/theme";

interface DeletePickModalProps {
  visible: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export default function DeletePickModal({
  visible,
  onClose,
  onConfirm,
}: DeletePickModalProps) {
  return (
    <ModalFrame visible={visible} onClose={onClose}>
      <Text style={styles.modalTitle}>Eliminar Apuesta</Text>
      <Text style={styles.modalText}>
        ¿Estás seguro de que deseas eliminar esta apuesta?
      </Text>
      <View style={styles.modalButtons}>
        <TouchableOpacity
          style={[styles.modalButton, { backgroundColor: colors.danger }]}
          onPress={onConfirm}
          accessibilityLabel="Eliminar apuesta"
        >
          <Text style={styles.modalButtonText}>
            <MaterialCommunityIcons
              name="trash-can-outline"
              size={26}
              color="white"
            />
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
