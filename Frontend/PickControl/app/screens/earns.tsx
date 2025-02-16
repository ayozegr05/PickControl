import React, { useEffect, useState, useRef } from "react";
import { View, Text, StyleSheet, ScrollView } from "react-native";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";
import BottomBar from "../components/bottom-bar";
import Animated, { useSharedValue, withTiming } from "react-native-reanimated";
import LottieView from 'lottie-react-native';
import TopBar from "../components/top-bar";

const GananciasPage = () => {
    const [apuestas, setApuestas] = useState([]);
    const [totalGanancias, setTotalGanancias] = useState(0);
    const [gananciasInformante, setGananciasInformante] = useState({});
    const [gananciasBookmaker, setGananciasBookmaker] = useState({});
    const animatedValue = useSharedValue(0);
    const [animatedGanancias, setAnimatedGanancias] = useState("0.00");
    const rocketRef = useRef(null);
    const loseRef = useRef(null);

    const fetchApuestas = async () => {
        try {
            const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
            });

            const data = await response.json();
            setApuestas(data.picks);
            
            // Calcular todas las ganancias
            const ganancias = calcularTotalGanancias(data.picks);
            const porInformante = calcularGananciasInformante(data.picks);
            const porBookmaker = calcularGananciasBookmaker(data.picks);
            
            setTotalGanancias(ganancias);
            setGananciasInformante(porInformante);
            setGananciasBookmaker(porBookmaker);

            animatedValue.value = withTiming(ganancias, { duration: 1500 });
        } catch (error) {
            console.error("Error al obtener las apuestas:", error);
        }
    };

    const calcularGananciasInformante = (apuestas) => {
        const ganancias = {};
        apuestas.forEach((apuesta) => {
            if (!ganancias[apuesta.Informante]) {
                ganancias[apuesta.Informante] = 0;
            }
            if (apuesta.Acierto === "True") {
                ganancias[apuesta.Informante] += (apuesta.CantidadApostada * apuesta.Cuota) - apuesta.CantidadApostada;
            } else if (apuesta.Acierto === "False") {
                ganancias[apuesta.Informante] -= apuesta.CantidadApostada;
            }
        });
        return ganancias;
    };

    const calcularGananciasBookmaker = (apuestas) => {
        const ganancias = {};
        apuestas.forEach((apuesta) => {
            if (!ganancias[apuesta.Casa]) {
                ganancias[apuesta.Casa] = 0;
            }
            if (apuesta.Acierto === "True") {
                ganancias[apuesta.Casa] += (apuesta.CantidadApostada * apuesta.Cuota) - apuesta.CantidadApostada;
            } else if (apuesta.Acierto === "False") {
                ganancias[apuesta.Casa] -= apuesta.CantidadApostada;
            }
        });
        return ganancias;
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
        // Iniciar la animación correspondiente
        if (totalGanancias > 0 && rocketRef.current) {
            rocketRef.current.play();
        } else if (totalGanancias <= 0 && loseRef.current) {
            loseRef.current.play();
        }
    }, [totalGanancias]);

    useEffect(() => {
        const interval = setInterval(() => {
            setAnimatedGanancias(animatedValue.value.toFixed(2));
        }, 100);
        return () => clearInterval(interval);
    }, [animatedValue]);

    return (
        <View style={styles.container}>
            <TopBar />  
            <View style={styles.titleContainer}>
                <Text style={styles.title}>Mis Ganancias</Text>
                <Ionicons name="wallet" size={35} color="brown" style={styles.titleIcon} />
            </View>

            <ScrollView style={styles.scrollView}>
                <Text style={styles.subtitle}>Tus ganancias totales son:</Text>
                
                <View style={styles.iconContainer}> 
                    <Animated.Text style={[styles.gananciasText, { color: totalGanancias >= 0 ? "green" : "red" }]}>
                        {animatedGanancias} €
                    </Animated.Text>
                    {totalGanancias > 0 ? (
                        <View style={styles.rocketContainer}>
                            <LottieView
                                ref={rocketRef}
                                source={require('../../assets/animations/rocket.json')}
                                style={styles.lottieRocket}
                                autoPlay
                                loop
                            />
                        </View>
                    ) : (
                        <View style={styles.loseContainer}>
                            <LottieView
                                ref={loseRef}
                                source={require('../../assets/animations/lose2.json')}
                                style={styles.lottieLose}
                                autoPlay
                                loop
                            />
                        </View>
                    )}
                </View>

                {/* Ganancias por Informante */}
                <Text style={styles.sectionTitle}>Por Informante:</Text>
                {Object.entries(gananciasInformante).map(([informante, ganancia]) => (
                    <View key={informante} style={styles.itemContainer}>
                        <Text style={styles.itemName}>{informante}</Text>
                        <Text style={[styles.itemValue, { color: ganancia >= 0 ? "green" : "red" }]}>
                            {ganancia.toFixed(2)} €
                        </Text>
                    </View>
                ))}

                {/* Ganancias por Casa de Apuestas */}
                <Text style={styles.sectionTitle}>Por Casa de Apuestas:</Text>
                {Object.entries(gananciasBookmaker).map(([bookmaker, ganancia]) => (
                    <View key={bookmaker} style={styles.itemContainer}>
                        <Text style={styles.itemName}>{bookmaker}</Text>
                        <Text style={[styles.itemValue, { color: ganancia >= 0 ? "green" : "red" }]}>
                            {ganancia.toFixed(2)} €
                        </Text>
                    </View>
                ))}
            </ScrollView>

            <BottomBar />
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: "black",
    },
    scrollView: {
        flex: 1,
        paddingHorizontal: 20,
    },
    titleContainer: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        marginTop: 80,
        marginBottom: 20,
    },
    title: {
        fontSize: 35,
        fontWeight: "bold",
        color: "orange",
        marginRight: 10,
    },
    titleIcon: {
        marginTop: 5,
    },
    subtitle: {
        fontSize: 20,
        color: "white",
        marginBottom: 20,
        fontWeight: 'bold',
        marginTop: 20,
        textAlign: 'center',
    },
    sectionTitle: {
        fontSize: 22,
        color: "orange",
        marginTop: 30,
        marginBottom: 15,
        fontWeight: 'bold',
    },
    gananciasText: {
        fontSize: 32,
        fontWeight: "bold",
        marginTop: 20,
    },
    rocketIcon: {
        marginLeft: 20,
        marginTop: 10,
    },
    iconContainer: {
        flexDirection: 'row',
        justifyContent: 'center',
        alignItems: 'center',
        marginBottom: 20,
    },
    itemContainer: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        backgroundColor: '#1E1E1E',
        padding: 15,
        borderRadius: 10,
        marginBottom: 10,
    },
    itemName: {
        color: 'white',
        fontSize: 16,
        fontWeight: '500',
    },
    itemValue: {
        fontSize: 18,
        fontWeight: 'bold',
    },
    rocketContainer: {
        position: 'relative',
        marginLeft: 20,
        width: 100,
        height: 100,
    },
    loseContainer: {
        position: 'relative',
        marginLeft: 20,
        width: 100,
        height: 100,
    },
    lottieRocket: {
        width: '100%',
        height: '100%',
    },
    lottieLose: {
        width: '100%',
        height: '100%',
    },
});

export default GananciasPage;
