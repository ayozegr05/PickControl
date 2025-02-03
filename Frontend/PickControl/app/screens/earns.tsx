import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons"; // Si usas Ionicons

const GananciasPage = () => {
    return (
        <View style={styles.container}>
            {/* Título */}
            <Text style={styles.title}>Mis Ganancias</Text>
            
            {/* Icono de saco de dinero */}
            <Ionicons name="cash" size={60} color="#ff6710" />
            
            {/* Si tienes un valor de ganancias, lo mostrarías aquí */}
            {/* Por ahora no se muestra ningún valor específico, solo el título y el icono */}
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        backgroundColor: "#f2f2f2", // Fondo claro
    },
    title: {
        fontSize: 24,
        fontWeight: "bold",
        color: "#333", // Título en color oscuro
        marginBottom: 20,
    },
});

export default GananciasPage;
