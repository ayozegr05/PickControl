import React from "react";
import { View, TouchableOpacity, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

const BottomBar = () => {
    const router = useRouter();

    return (
        <View style={styles.container}>
            {/* Botón de Atrás */}
            <TouchableOpacity onPress={() => router.back()}>
                <Ionicons name="arrow-back" size={32} color="orange" />
            </TouchableOpacity>

            {/* Botón de Inicio */}
            <TouchableOpacity onPress={() => router.push("/")}>
                <Ionicons name="home" size={32} color="orange" />
            </TouchableOpacity>

            {/* Botón de Añadir */}
            <TouchableOpacity onPress={() => router.push("/screens/add-pick")}>
                <Ionicons name="add-circle" size={32} color="orange" />
            </TouchableOpacity>
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flexDirection: "row",
        justifyContent: "space-around",
        alignItems: "center",
        height: 60,
        width: '100%',
        backgroundColor: "#131313",
        position: "absolute",
        bottom: 0,
    },
});

export default BottomBar;
