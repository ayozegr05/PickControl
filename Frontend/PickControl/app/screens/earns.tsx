import React, { useEffect, useState, useRef } from "react";
import { View, Text, StyleSheet, ScrollView, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons} from "@expo/vector-icons";
import BottomBar from "@/src/components/bottom-bar";
import LottieView from 'lottie-react-native';
import TopBar from "@/src/components/top-bar";
import { listPicks } from "@/src/api/picks.api";
import { PickItem } from "@/src/types/pick.types";

const GananciasPage = () => {
    const [apuestas, setApuestas] = useState<PickItem[]>([]);
    const [totalGanancias, setTotalGanancias] = useState(0);
    const [gananciasInformante, setGananciasInformante] = useState<Record<string, number>>({});
    const [gananciasBookmaker, setGananciasBookmaker] = useState<Record<string, number>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [dataLoaded, setDataLoaded] = useState(false);
    const animatedValue = useRef(new Animated.Value(0)).current;
    const [animatedGanancias, setAnimatedGanancias] = useState("0.00");
    const rocketRef = useRef<LottieView>(null);
    const loseRef = useRef<LottieView>(null);
    const loadingRef = useRef<LottieView>(null);
    const insets = useSafeAreaInsets();

    const fetchApuestas = async () => {
        if (dataLoaded) return; // Evitar múltiples cargas si ya tenemos datos
        
        try {
            const picks = await listPicks();

            // Procesar todos los datos antes de actualizar estados
            const ganancias = calcularTotalGanancias(picks);
            const porInformante = calcularGananciasInformante(picks);
            const porBookmaker = calcularGananciasBookmaker(picks);
            
            // Actualizar todos los estados juntos
            setApuestas(picks);
            setTotalGanancias(ganancias);
            setGananciasInformante(porInformante);
            setGananciasBookmaker(porBookmaker);
            setDataLoaded(true);

            // Iniciar la animación después de que los datos estén listos
            Animated.timing(animatedValue, {
                toValue: ganancias,
                duration: 1500,
                useNativeDriver: false
            }).start(() => {
                setIsLoading(false);
            });
        } catch (error) {
            console.error("Error al obtener las apuestas:", error);
            setIsLoading(false);
            setDataLoaded(true);
        }
    };

    const calcularGananciasInformante = (apuestas: PickItem[]) => {
        const ganancias: Record<string, number> = {};
        apuestas.forEach((apuesta) => {
            if (!ganancias[apuesta.informante]) {
                ganancias[apuesta.informante] = 0;
            }
            ganancias[apuesta.informante] += apuesta.ganancia ?? 0;
        });
        return ganancias;
    };

    const calcularGananciasBookmaker = (apuestas: PickItem[]) => {
        const ganancias: Record<string, number> = {};
        apuestas.forEach((apuesta) => {
            if (!ganancias[apuesta.casa]) {
                ganancias[apuesta.casa] = 0;
            }
            ganancias[apuesta.casa] += apuesta.ganancia ?? 0;
        });
        return ganancias;
    };

    const calcularTotalGanancias = (apuestas: PickItem[]) => {
        return apuestas.reduce((total, apuesta) => total + (apuesta.ganancia ?? 0), 0);
    };

    useEffect(() => {
        fetchApuestas();
    }, []); // Solo se ejecuta una vez al montar el componente

    useEffect(() => {
        const listener = animatedValue.addListener(({ value }) => {
            setAnimatedGanancias(value.toFixed(2));
        });
        return () => {
            animatedValue.removeListener(listener);
            setIsLoading(true); // Resetear el estado de carga al desmontar
            setDataLoaded(false);
        };
    }, [animatedValue]);

    // No iniciar las animaciones hasta que los datos estén completamente cargados
    useEffect(() => {
        if (!isLoading && dataLoaded) {
            // Iniciar la animación de las ganancias
            animatedValue.setValue(0);
            Animated.timing(animatedValue, {
                toValue: totalGanancias,
                duration: 1500,
                useNativeDriver: false,
            }).start(() => {
                // Una vez terminada la animación, reproducir la animación de rocket o lose
                if (totalGanancias > 0 && rocketRef.current) {
                    rocketRef.current.play();
                } else if (totalGanancias <= 0 && loseRef.current) {
                    loseRef.current.play();
                }
            });
        }
    }, [isLoading, dataLoaded, totalGanancias]);
    

    return (
        <View style={styles.container}>
            <TopBar />  
            <View style={[styles.titleContainer, { marginTop: 80 + insets.top }]}>
                <Text style={styles.title}>Mis Ganancias</Text>
                <Ionicons name="wallet" size={35} color="brown" style={styles.titleIcon} />
            </View>

            <ScrollView
                style={styles.scrollView}
                contentContainerStyle={{ paddingBottom: 80 + insets.bottom }}
            >
                {isLoading ? (
                    <View style={styles.loadingContainer}>
                        <Text style={styles.loadingText}>Cargando datos...</Text>
                        <LottieView
                            ref={loadingRef}
                            source={require('../../assets/animations/loading.json')}
                            style={styles.loadingAnimation}
                            autoPlay
                            loop
                        />
                    </View>
                ) : (
                    <>
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
                    </>
                )}
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
    loadingContainer: {
        flex: 1,
        justifyContent: 'center',
        alignItems: 'center',
        height: '100%',
        minHeight: 500,
        gap: 10,
        marginTop: 40
    },
    loadingText: {
        color: '#FFFFFF',
        fontSize: 18,
        textAlign: 'left',
        marginBottom: -70,
        marginStart: 5
    },
    loadingAnimation: {
        width: 150,
        height: 150,
    },
});

export default GananciasPage;
