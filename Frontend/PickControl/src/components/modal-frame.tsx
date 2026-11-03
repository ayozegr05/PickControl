// Chrome compartido de los modales de la app: fondo translúcido,
// tarjeta centrada y botón de cerrar en la esquina. Extraído en la
// Fase D — antes el mismo bloque Modal>View>View>closeButton estaba
// duplicado en 4 modales de [informante].tsx.
import React from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";

interface ModalFrameProps {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
}

export default function ModalFrame({
  visible,
  onClose,
  children,
}: ModalFrameProps) {
  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={visible}
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        style={styles.modalContainer}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={styles.modalContent}>
          <TouchableOpacity
            style={styles.closeButton}
            onPress={onClose}
            accessibilityLabel="Cerrar"
          >
            <Text style={styles.closeButtonText}>
              <MaterialCommunityIcons name="close" size={26} color="white" />
            </Text>
          </TouchableOpacity>
          {children}
        </View>
      </KeyboardAvoidingView>
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
});
