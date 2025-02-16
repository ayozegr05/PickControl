import React, { useState, useEffect, useRef } from "react";
import { Text, View, StyleSheet, ScrollView, TouchableOpacity, Modal, Alert } from "react-native";
import BottomBar from "./components/bottom-bar";
import { useRouter } from "expo-router";
import TopBar from "./components/top-bar";
import { useAuth } from "./context/AuthContext";
import { Video } from 'expo-av';
import { MaterialCommunityIcons } from '@expo/vector-icons';

const Main = () => {
    const [apuestas, setApuestas] = useState([]);
    const [informantes, setInformantes] = useState([]);
    const [apuestasPendientes, setApuestasPendientes] = useState([]);
    const [modalVisible, setModalVisible] = useState(false);
    const [selectedApuesta, setSelectedApuesta] = useState(null);
    const [ranking, setRanking] = useState([]); // Estado para el ranking
    const [showTopBar, setShowTopBar] = useState(true); // Estado para controlar la visibilidad de TopBar
    const lastScrollY = useRef(0); // Para guardar la última posición de scroll
    const scrollViewRef = useRef<ScrollView>(null); // Referencia para el ScrollView

    const router = useRouter();
    const { isAuthenticated } = useAuth();

    const cellWidth = 105;

    const renderPronostico = (acierto) => {
        if (acierto === 'True') {
            return <Text style={[styles.icon, { color: 'green', fontSize: 21 }]}>✅ </Text>;
        } else if (acierto === 'False') {
            return <Text style={[styles.icon, { color: 'red' }]}>❌</Text>;
        }
        return <Text>-</Text>;
    };

    const fetchApuestas = async () => {
        if (!isAuthenticated) return;
        try {
            const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`,{
            method: 'GET',
            headers: {
            'Content-Type': 'application/json',
        },
            });
            const data = await response.json();
            console.log("Datos obtenidos:", data);

            const apuestasPorInformante = {};
            data.picks.forEach((pick) => {
                const { Informante, Acierto, Apuesta, _id, Fecha } = pick;
                if (!apuestasPorInformante[Informante]) {
                    apuestasPorInformante[Informante] = [];
                }
                apuestasPorInformante[Informante].push({ _id, Apuesta, Acierto, Fecha });
            });

            setApuestas(apuestasPorInformante);
            const informantesList = Object.keys(apuestasPorInformante);
            setInformantes(informantesList);
        } catch (error) {
            console.error("Error al obtener las apuestas:", error);
        }
    };

    const calcularRanking = (apuestasPorInformante) => {
        const rankingArray = Object.entries(apuestasPorInformante).map(([informante, apuestas]) => {
            // Filtrar apuestas que no estén en estado "Pending"
            const apuestasFinalizadas = apuestas.filter(apuesta => apuesta.Acierto !== "Pending");
            const totalApuestasFinalizadas = apuestasFinalizadas.length;
            const totalAciertos = apuestasFinalizadas.filter(apuesta => apuesta.Acierto === 'True').length;
            const porcentajeAcierto = totalApuestasFinalizadas > 0 ? (totalAciertos / totalApuestasFinalizadas) * 100 : 0;
    
            return {
                informante,
                porcentajeAcierto: porcentajeAcierto.toFixed(2), // 2 decimales
            };
        });
    
        // Ordenar por porcentaje de aciertos descendente y tomar los 5 primeros
        const topRanking = rankingArray.sort((a, b) => b.porcentajeAcierto - a.porcentajeAcierto).slice(0, 5);
    
        setRanking(topRanking);
    };
    

    useEffect(() => {
        fetchApuestas();
    }, [isAuthenticated]);  

    useEffect(() => {
        if (Object.keys(apuestas).length > 0) {
            const pendientes = calcularApuestasPendientes(apuestas);
            setApuestasPendientes(pendientes);
            calcularRanking(apuestas);
        }
    }, [apuestas]);

    const handleInformantePress = (informante) => {
        console.log("Navegando hacia:", informante);  
        router.push(`/dynamic-routes/${informante}`);
    };

    const getMaxApuestas = () => {
        if (Object.keys(apuestas).length === 0) {
            return 0;
        }

        const apuestasCount = Object.values(apuestas).map((apuestas) => apuestas.length);
        const max = Math.max(...apuestasCount);
        console.log("Número máximo de apuestas:", max);
        return max > 0 ? max : 0;
    };

    const handleActualizarApuesta = (apuesta) => {
        console.log("Apuesta seleccionada:", apuesta);
        setSelectedApuesta(apuesta); // Guardamos la apuesta seleccionada
        setModalVisible(true); // Mostramos el modal
    };

    const actualizarApuesta = async (acierto) => {
        if (!selectedApuesta) return;

        try {
            const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuesta/${selectedApuesta._id}`, {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ Acierto: acierto }),
            });

            const data = await response.json();
            if (response.ok) {
                console.log("Apuesta actualizada:", data);
                // Alert.alert('Apuesta actualizada con éxito');
                fetchApuestas(); // Volvemos a cargar las apuestas
                setModalVisible(false); // Cerramos el modal
            } else {
                console.error("Error al actualizar la apuesta:", data);
            }
        } catch (error) {
            console.error("Error en la solicitud de actualización:", error);
        }
    };

    const calcularApuestasPendientes = (apuestasPorInformante) => {
        const pendientes = [];
    
        // Recorremos cada informante y sus apuestas
        Object.entries(apuestasPorInformante).forEach(([informante, apuestas]) => {
            apuestas.forEach((apuesta) => {
                // Solo añadimos las apuestas con 'Acierto' no definido
                if (apuesta.Acierto == 'Pending') {
                    pendientes.push({ ...apuesta, Informante: informante });
                }
            });
        });
    
        console.log("Apuestas Pendientes:", pendientes); // Verificamos que las apuestas pendientes se están obteniendo correctamente
        return pendientes;
    }; 
    
    // Manejo de scroll para mostrar/ocultar el TopBar
    const handleScroll = (event) => {
        const currentOffset = event.nativeEvent.contentOffset.y;

        if (currentOffset > lastScrollY.current) {
            // Si estamos desplazándonos hacia arriba
            setShowTopBar(true);
        } else if (currentOffset < lastScrollY.current && currentOffset > 50) {
            // Si estamos desplazándonos hacia abajo
            setShowTopBar(false);
        }

        lastScrollY.current = currentOffset; // Actualizamos la posición del scroll
    };
    

    const maxApuestas = getMaxApuestas();
    console.log("maxApuestas:", maxApuestas);

    if (!isAuthenticated) {
        console.log('Renderizando vista no autenticada');
        try {
            const videoSource = require('../assets/video/intro.mp4');
            return (
                <View style={[styles.container, { paddingHorizontal: 0 }]}>
                    <TopBar />
                    <Video
                        source={videoSource}
                        style={styles.video}
                        resizeMode="cover"
                        shouldPlay={true}
                        isLooping={true}
                        useNativeControls={false}
                        isMuted={true}
                        onLoad={() => console.log('Video cargado correctamente')}
                        onError={(error) => console.error('Error al cargar el video:', error)}
                    />
                    <View style={styles.overlay} />
                    
                    {/* Grid de características */}
                    <View style={styles.featuresSection}>
                    <View style={styles.titleContainer}>
                        <MaterialCommunityIcons 
                            name="crown" 
                            size={40} 
                            color="#ff9f1c"
                            style={styles.titleIcon}
                        />
                        <Text style={styles.sectionTitle}>Toma el Control</Text>
                    </View>
                        <View style={styles.featuresGrid}>
                            <View style={styles.featureCard}>
                                <MaterialCommunityIcons name="chart-areaspline" size={32} color="#ff9f1c" />
                                <Text style={styles.featureTitle}>Estadísticas Avanzadas</Text>
                                <Text style={styles.featureText}>
                                    ROI por tipster y casa de apuestas
                                </Text>
                            </View>

                            <View style={styles.featureCard}>
                                <MaterialCommunityIcons name="account-group" size={32} color="#ff9f1c" />
                                <Text style={styles.featureTitle}>Tus Tipsters</Text>
                                <Text style={styles.featureText}>
                                    Evalúa rentabilidad histórica y consistencia
                                </Text>
                            </View>

                            <View style={styles.featureCard}>
                                <MaterialCommunityIcons name="bell-alert" size={32} color="#ff9f1c" />
                                <Text style={styles.featureTitle}>Sistema de Alertas</Text>
                                <Text style={styles.featureText}>
                                    Detecta odds infladas y cambios sospechosos
                                </Text>
                            </View>

                            <View style={styles.featureCard}>
                                <MaterialCommunityIcons name="wallet" size={32} color="#ff9f1c" />
                                <Text style={styles.featureTitle}>Gestión de Bankroll</Text>
                                <Text style={styles.featureText}>
                                    Límites automáticos de riesgo por apuesta
                                </Text>
                            </View>
                        </View>
                    </View>

                    <TouchableOpacity 
                        style={styles.joinButton}
                        activeOpacity={0.8}
                        onPress={() => router.push("/screens/login")}
                    >
                        <Text style={styles.joinButtonText}>Start</Text>
                    </TouchableOpacity>
                </View>
            );
        } catch (error) {
            console.error('Error al cargar el recurso del video:', error);
            return (
                <View style={[styles.container, { paddingHorizontal: 0 }]}>
                    <TopBar />
                    <View style={styles.videoContainer}>
                        <Text style={{ color: 'white' }}>Error al cargar el video</Text>
                    </View>
                </View>
            );
        }
    }

    return (
        <View style={styles.container}>
            <TopBar />
            <ScrollView
                ref={scrollViewRef}
                contentContainerStyle={[
                    styles.scrollContainer,
                    isAuthenticated ? { 
                        paddingHorizontal: 15,
                        paddingBottom: 80  // Espacio extra para el BottomBar
                    } : null
                ]}
                onScroll={handleScroll}
                scrollEventThrottle={16}
            >
                {/* Tabla de pronósticos */}
                <View style={styles.cardContainer}>
                    <ScrollView 
                        horizontal 
                        showsHorizontalScrollIndicator={false}
                        contentContainerStyle={{ flexGrow: 0 }}
                        style={{ width: '100%' }}  // Asegurar que el ScrollView ocupe todo el ancho
                    >
                        <View style={styles.tableContainer}>
                            <View style={styles.header}>
                                <Text style={[styles.headerCell, { width: cellWidth }]}>
                                    Fecha
                                </Text>
                                {informantes.map((informante, index) => (
                                    <View key={index} style={[styles.cell, { width: cellWidth }]}>
                                        <TouchableOpacity
                                            onPress={() => handleInformantePress(informante)}
                                            style={styles.informanteButton}
                                        >
                                            <Text style={styles.buttonText}>{informante}</Text>
                                        </TouchableOpacity>
                                    </View>
                                ))}
                            </View>
                            <ScrollView 
                                style={styles.rowsContainer}
                                showsVerticalScrollIndicator={true}
                                nestedScrollEnabled={true}
                            >
                                {[...Array(maxApuestas)].map((_, index) => (
                                    <View key={index} style={styles.row}>
                                        <Text style={[styles.cell, styles.dateCell, { width: cellWidth }]}>
                                        {informantes.some(inf => {
                                            const apuestasInformante = apuestas[inf];
                                            return apuestasInformante && apuestasInformante[index] && apuestasInformante[index].Fecha;
                                        })
                                            ? (() => {
                                                // Obtenemos la primera fecha válida de la fila
                                                const fechaEncontrada = informantes
                                                    .map(inf => {
                                                    const apuestasInformante = apuestas[inf];
                                                    return apuestasInformante ? apuestasInformante[index]?.Fecha : null;
                                                    })
                                                    .find(fecha => fecha !== null);
                                                const fechaObj = new Date(fechaEncontrada);
                                                // Si la fecha no es válida, usamos la fecha de hoy
                                                if (isNaN(fechaObj)) {
                                                    return new Date().toLocaleDateString();
                                                } else {
                                                    return fechaObj.toLocaleDateString();
                                                }
                                            })()
                                            : '-'}
                                        </Text>
                                        {informantes.map((informante) => {
                                            const apuestasInformante = apuestas[informante];
                                            const apuestaInformante = apuestasInformante ? apuestasInformante[index] : null;
                                            return (
                                                <View key={informante} style={[styles.cell, { width: cellWidth }]}>
                                                    {apuestaInformante ? renderPronostico(apuestaInformante.Acierto) : <Text>-</Text>}
                                                </View>
                                            );
                                        })}
                                    </View>
                                ))}
                            </ScrollView>
                        </View>
                    </ScrollView>
                </View>

                {apuestasPendientes.length > 0 && (
                    <View style={styles.card}>
                        <Text style={styles.cardTitle}>Apuestas Pendientes</Text>
                        <Text style={styles.subtitleContainer}>
                            Tienes <Text style={styles.cardSubtitle}>{apuestasPendientes.length}</Text> apuestas pendientes
                        </Text>

                        <ScrollView 
                            style={styles.pendingList}
                            showsVerticalScrollIndicator={true}
                            nestedScrollEnabled={true}
                        >
                        {apuestasPendientes.map((apuesta, index) => (
                            <View style={styles.pendingItem}>
                            {/* Columna de Apuesta */}
                            <View style={styles.column}>
                                <Text style={styles.pendingText}>Apuesta</Text>
                                <Text style={styles.pendingTextValue}>{apuesta.Apuesta}</Text>
                            </View>
                        
                            {/* Columna de Informante */}
                            <View style={styles.column}>
                                <Text style={styles.pendingText}>Informante</Text>
                                <Text style={styles.pendingTextValue}>{apuesta.Informante}</Text>
                            </View>
                        
                            {/* Columna del botón de actualización */}
                            <View style={styles.columnButton}>
                                {apuesta.Acierto !== 'True' && apuesta.Acierto !== 'False' && (
                                    <TouchableOpacity 
                                        style={styles.updateButton} 
                                        onPress={() => handleActualizarApuesta(apuesta, apuesta.Informante)}
                                    >
                                        <Text style={styles.updateButtonText}>❓</Text>
                                    </TouchableOpacity>
                                )}
                            </View>
                        </View>
                        
                        ))}
                    </ScrollView>

                    </View>
                )}

                {/* Card de Ranking */}
            {ranking.length > 0 && (
                <View style={styles.rankingContainer}>
                    <Text style={styles.cardTitle}>Ranking de Tipsters</Text>
                    <Text style={styles.subtitleContainer}>Top 5 por porcentaje de aciertos</Text>

                    {ranking.map((rank, index) => (
                        <View key={index} style={styles.rankingItem}>
                            <Text style={styles.rankingText}>
                                {index + 1}. {rank.informante}
                            </Text>
                            <Text style={styles.rankingPercentage}>
                                {rank.porcentajeAcierto}% de aciertos
                            </Text>
                        </View>
                    ))}
                </View>
            )}
            </ScrollView>
            {isAuthenticated && <BottomBar />}
            {modalVisible && selectedApuesta && (
                <Modal
                    animationType="slide"
                    transparent={true}
                    visible={modalVisible}
                    onRequestClose={() => setModalVisible(false)}
                >
                    <View style={styles.modalContainer}>
                        <View style={styles.modalContent}>
                            <TouchableOpacity
                                style={styles.closeButton}
                                onPress={() => setModalVisible(false)}
                            >
                                <Text style={styles.closeButtonText}>✖️</Text>
                            </TouchableOpacity>
                            <Text style={styles.modalText}>
                                ¿Cuál es el resultado de la apuesta?
                            </Text>
                            <View style={styles.modalButtons}>
                                <TouchableOpacity
                                    style={[styles.choiceButton, { backgroundColor: "green" }]}
                                    onPress={() => actualizarApuesta("True")}
                                >
                                    <Text style={styles.choiceButtonText}>✔️</Text>
                                </TouchableOpacity>
                                <TouchableOpacity
                                    style={[styles.choiceButton, { backgroundColor: "red" }]}
                                    onPress={() => actualizarApuesta("False")}
                                >
                                    <Text style={styles.choiceButtonText}>❌</Text>
                                </TouchableOpacity>
                            </View>
                        </View>
                    </View>
                </Modal>
            )}
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: '#1a1a1a',
    },
    botbarContainer:{
        flex: 1,
    },
    scrollContainer: {
        flexGrow: 1,
    },
    cardContainer: {
        backgroundColor: '#303030',
        borderRadius: 10,
        shadowColor: '#000',
        alignItems: 'center',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.8,
        shadowRadius: 2,
        marginTop: 80,
        padding: 15,
        margin: 0,
        overflow: 'hidden',
        maxHeight: 500, // Añadido maxHeight
    },
    card: {
        backgroundColor: '#303030',
        borderRadius: 10,
        padding: 15,
        marginTop: 20,
        width: '100%',
        maxHeight: 300, // Añadido maxHeight
    },
    pendingList: {
        maxHeight: 200, // Limitar altura del ScrollView de apuestas pendientes
    },
    rankingContainer: {
        backgroundColor: '#303030',
        borderRadius: 10,
        padding: 15,
        marginTop: 20,
        width: '100%',
        maxHeight: 300, // Añadido maxHeight
    },
    tableContainer: {
        backgroundColor: '#ffdaa4',
        flexDirection: 'column',
        flexGrow: 1,
        alignSelf: 'center',  
        borderRadius: 8,
    },
    rowsContainer: {
        flexGrow: 0,
    },
    header: {
        flexDirection: 'row',
        backgroundColor: '#186720',
        height: 80,
        borderTopRightRadius: 8,
        borderTopLeftRadius: 8,
    },
    row: {
        flexDirection: 'row',
        height: 70,
    },
    cell: {
        height: '100%',  
        justifyContent: 'center',
        textAlign: 'center',
        alignItems: 'center',
        verticalAlign: 'middle',
        padding: 8,
        fontSize: 18,
        fontWeight: 'bold',
        color: 'dark',
    },
    headerCell: {
        fontSize: 20,
        fontWeight: 'bold',
        textAlign: 'center',
        backgroundColor: '#186720',
        verticalAlign: 'middle',
        color: 'white',
        borderTopLeftRadius: 8,
    },
    informanteButton: {
        backgroundColor: '#186720',
        paddingHorizontal: 10,
        paddingVertical: 8,
        borderRadius: 10,
        justifyContent: 'center',
        alignItems: 'center',
        width: '100%',
        height: 70,  
    },
    buttonText: {
        color: 'white',
        fontWeight: 'bold',
        fontSize: 20,
        textAlign: 'center',
    },
    cardTitle: {
        fontSize: 24,
        fontWeight: 'bold',
        color: 'white',
        marginBottom: 10,
        textAlign: 'center',
    },
    cardSubtitle: {
        fontSize: 18,
        color: 'red',
        fontWeight: 'bold',
        textAlign: 'center',
        marginBottom: 10,
    },
    subtitleContainer:{
        fontSize: 18,
        textAlign: 'center',
        color: '#f3b02b',
        marginBottom: 10
    },
    pendingList: {
        marginBottom: 20,
    },
    pendingItem: {
        flexDirection: 'row', // Organiza las columnas en fila
        alignItems: 'center', // Mantiene la alineación vertical
        justifyContent: 'space-between', // Distribuye las columnas
        padding: 10,
        borderBottomWidth: 1,
        borderColor: '#ccc',
        width: '100%',
    },
    
    column: {
        flex: 1, // Cada columna ocupa el mismo ancho
        alignItems: 'center', // Asegura alineación central
        minHeight: 60, // Mantiene todas las filas con la misma altura
    },
    
    columnButton: {
        width: 40, // Ajusta el ancho del botón para que no desajuste la tabla
        alignItems: 'center',
    },
    
    pendingText: {
        fontSize: 16,
        fontWeight: 'bold',
        color: 'white',
        textAlign: 'center',
    },
    
    pendingTextValue: {
        color: '#1dbb2e',
        textAlign: 'center',
        marginTop: 5,
        flexWrap: 'wrap', // Permite saltos de línea
        maxWidth: '90%', // Controla el ancho para evitar desbordes
    },
    
    updateButton: {
        paddingHorizontal: 5,
        paddingVertical: 5,
        marginTop: 10,
        backgroundColor: 'white',
        borderRadius: 5,
    },
    
    updateButtonText: {
        fontSize: 18,
        fontWeight: 'bold',
        color: 'black',
    },
    

    videoContainer: {
        flex: 1,
        backgroundColor: '#1a1a1a',
        width: '100%',
        position: 'relative',
    },
    video: {
        width: '100%',
        height: '100%',
        backgroundColor: '#1a1a1a',
    },
    overlay: {
        ...StyleSheet.absoluteFillObject,
        backgroundColor: 'rgba(0, 0, 0, 0.7)',
    },
    joinButton: {
        position: 'absolute',
        bottom: '10%',
        alignSelf: 'center',
        backgroundColor: '#ff9f1c',
        paddingHorizontal: 30,
        paddingVertical: 18,
        borderRadius: 30,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.3,
        shadowRadius: 10,
        elevation: 8,
        borderWidth: 2,
        borderColor: 'rgba(255, 255, 255, 0.2)',
    },
    joinButtonText: {
        color: '#fff',
        fontSize: 20,
        fontWeight: '700',
        textTransform: 'uppercase',
        letterSpacing: 1,
        textShadowColor: 'rgba(0, 0, 0, 0.3)',
        textShadowOffset: { width: -1, height: 1 },
        textShadowRadius: 5,
    },
    modalContainer: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        backgroundColor: "rgba(0, 0, 0, 0.5)",
    },
    modalContent: {
        backgroundColor: "white",
        borderRadius: 10,
        padding: 20,
        alignItems: "center",
        width: 300,
    },
    modalText: {
        fontSize: 18,
        marginBottom: 20,
    },
    modalButtons: {
        flexDirection: "row",
        justifyContent: "space-around",
        width: "100%",
    },
    choiceButton: {
        padding: 10,
        borderRadius: 5,
        width: 60,
        alignItems: "center",
    },
    choiceButtonText: {
        color: "white",
        fontSize: 18,
        fontWeight: "bold",
    },
    closeButton: {
        position: 'absolute',
        top: 10,
        right: 10,
        width: 30,
        height: 30,
        borderRadius: 15,
        backgroundColor: 'red',
        justifyContent: 'center',
        alignItems: 'center',
    },
    closeButtonText: {
        fontSize: 18,
        color: "white",
    },
    rankingItem: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        paddingVertical: 5,
        borderBottomWidth: 1,
        borderColor: '#ccc',
    },
    rankingText: {
        fontSize: 16,
        fontWeight: 'bold',
        color: 'white',
    },
    rankingPercentage: {
        fontSize: 16,
        color: '#4CAF50',
    },
    featuresSection: {
        marginTop: -50,
        marginBottom: 655,
        paddingHorizontal: 15,
        position: 'absolute',
        top: '20%',
        width: '100%',
    },
    titleContainer: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 70,
        backgroundColor: 'rgba(45, 45, 45, 0.8)',
        paddingVertical: 20,
        paddingHorizontal: 25,
        borderRadius: 20,
        shadowColor: 'darkgray',
        shadowOffset: { width: 0, height: 0 },
        shadowOpacity: 0.5,
        shadowRadius: 20,
        elevation: 8,
        borderWidth: 1,
        borderColor: 'rgba(255, 159, 28, 0.3)',
    },
    titleIcon: {
        marginRight: 10,
        transform: [{ rotate: '45deg' }]
    },
    sectionTitle: {
        fontSize: 30,
        fontWeight: '900',
        color: '#ffffff',
        textAlign: 'center',
        textTransform: 'uppercase',
        letterSpacing: 2,
        textShadowColor: 'rgba(255, 159, 28, 0.6)',
        textShadowOffset: { width: -2, height: 2 },
        textShadowRadius: 15,
        opacity: 0.95,
    },
    featuresGrid: {
        flexDirection: 'row',
        flexWrap: 'wrap',
        justifyContent: 'space-between',
        paddingVertical: 10,
    },
    featureCard: {
        width: '48%',
        backgroundColor: 'rgba(45, 45, 45, 0.8)',
        borderRadius: 15,
        padding: 15,
        marginBottom: 15,
        alignItems: 'center',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.25,
        shadowRadius: 3.84,
        elevation: 5,
        minHeight: 150,
        borderColor: 'rgba(255, 159, 28, 0.3)',
        borderWidth: 1,
    },
    featureTitle: {
        color: '#fff',
        fontSize: 16,
        fontWeight: 'bold',
        marginVertical: 10,
        textAlign: 'center',
    },
    featureText: {
        color: '#ddd',
        fontSize: 13,
        textAlign: 'center',
        lineHeight: 18,
    },
});

export default Main;
