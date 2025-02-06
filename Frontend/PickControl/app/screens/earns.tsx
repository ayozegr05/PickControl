import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import BottomBar from "../components/bottom-bar";
import Animated, { useSharedValue, withTiming } from "react-native-reanimated";

const GananciasPage = () => {
    const [apuestas, setApuestas] = useState([]);
    const [totalGanancias, setTotalGanancias] = useState(0);
    const animatedValue = useSharedValue(0);
    const [animatedGanancias, setAnimatedGanancias] = useState("0.00");

    const fetchApuestas = async () => {
        try {
            const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
            });

            const data = await response.json();
            console.log("Datos obtenidos:", data);

            setApuestas(data.picks);
            const ganancias = calcularTotalGanancias(data.picks);
            setTotalGanancias(ganancias);

            animatedValue.value = withTiming(ganancias, { duration: 1500 });
        } catch (error) {
            console.error("Error al obtener las apuestas:", error);
        }
    };

    const calcularTotalGanancias = (apuestas) => {
        let total = 0;
        apuestas.forEach((apuesta) => {
            if (apuesta.Acierto === "True") {
                total += (apuesta.CantidadApostada * apuesta.Cuota) - apuesta.CantidadApostada;
            } else if (apuesta.Acierto === "False") {
                total -= apuesta.CantidadApostada;
            }
        });
        return total;
    };

    useEffect(() => {
        fetchApuestas();
    }, []);

    useEffect(() => {
        const interval = setInterval(() => {
            setAnimatedGanancias(animatedValue.value.toFixed(2));
        }, 100);
        return () => clearInterval(interval);
    }, [animatedValue]);

    return (
        <View style={styles.container}>
            <Text style={styles.title}>Mis Ganancias</Text>
            <Text style={styles.subtitle}>Tus ganancias son:</Text>
        
            <Ionicons name="wallet" size={60} color="brown" />
            
           <View style={styles.iconContainer}> 
                {/* ✅ Mostramos el número animado en la UI */}
                <Animated.Text style={[styles.gananciasText, { color: totalGanancias >= 0 ? "green" : "red" }]}>
                    {animatedGanancias} €
                </Animated.Text>
                {/* ✅ Mostrar el icono dinámicamente según las ganancias */}
                {totalGanancias > 0 ? (
                    <Ionicons name="rocket" size={50} color="orange" style={styles.rocketIcon} />
                ) : (
                    <Ionicons name="trending-down" size={50} color="red" style={styles.rocketIcon} />
                )}
            </View>

            <BottomBar />
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        alignItems: "center",
        backgroundColor: "black",
    },
    title: {
        fontSize: 35,
        fontWeight: "bold",
        color: "orange",
        marginBottom: 50,
        marginTop: 50
    },
    subtitle: {
        fontSize: 20,
        color: "white",
        marginBottom: 20,
        fontWeight: 'bold',
        marginTop: 40
    },
    gananciasText: {
        fontSize: 32,
        fontWeight: "bold",
        marginTop: 20,
    },
    rocketIcon: {
        marginLeft: 20,
        marginTop:10
    },
    iconContainer: {
        flexDirection: 'row'
    }
});

export default GananciasPage;
