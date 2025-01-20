import React from "react";
import { View, TouchableOpacity, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

const BottomBar = () => {
    const router = useRouter();

    return (
        <View style={styles.container}>
            {/* Botón de Inicio */}
            <TouchableOpacity onPress={() => router.push("/")}>
                <Ionicons name="home" size={32} color="green" />
            </TouchableOpacity>

            {/* Botón de Añadir */}
            <TouchableOpacity onPress={() => router.push("/screens/add-pick")}>
                <Ionicons name="add-circle" size={32} color="green" />
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
        backgroundColor: "#f5f5f5",
        borderTopWidth: 1,
        borderColor: "#ccc",
        position: "absolute",
        bottom: 0,
    },
});

export default BottomBar;
