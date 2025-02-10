import React, { useState, useEffect, useRef } from "react";
import { Text, View, StyleSheet, ScrollView, TouchableOpacity, Modal, Alert } from "react-native";
import BottomBar from "./components/bottom-bar";
import { useRouter } from "expo-router";
import TopBar from "./components/top-bar";

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
        console.log('API BASE URL:', process.env.EXPO_PUBLIC_API_BASE_URL);
        fetchApuestas();
    }, []);  // Solo se ejecuta una vez cuando el componente se monta

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
                Alert.alert('Apuesta actualizada con éxito');
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

    return (
        <View style={styles.botbarContainer}>
        <View style={styles.container}>
            {showTopBar && <TopBar />}
            {/* ScrollView Vertical para toda la página */}
            <ScrollView
                contentContainerStyle={[styles.scrollContainer, { marginTop: showTopBar ? 60 : 0 }]} 
                onScroll={handleScroll} // Detectamos el scroll
                scrollEventThrottle={16} // Controla la frecuencia con la que se llama a handleScroll
                ref={scrollViewRef} // Asociamos la referencia
            >
            <Text style={styles.title}>Pick Control</Text>
                {/* Tabla */}
                <ScrollView horizontal style={styles.tableContainer}>
                    <View>
                        <View style={styles.header}>
                            <Text style={[styles.cell, styles.headerCell, styles.border, { width: cellWidth, paddingTop: 26 }]}>
                                Fecha
                            </Text>
                            {informantes.map((informante, index) => (
                                <View key={index} style={[styles.cell, styles.border, { width: cellWidth }]}>
                                    <TouchableOpacity
                                        onPress={() => handleInformantePress(informante)}
                                        style={styles.informanteButton}
                                    >
                                        <Text style={styles.buttonText}>{informante}</Text>
                                    </TouchableOpacity>
                                </View>
                            ))}
                        </View>

                        {[...Array(maxApuestas)].map((_, index) => (
                            <View key={index} style={styles.row}>
                                <Text style={[styles.cell, styles.dateCell, styles.border, { width: cellWidth }]}>
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
                                        <View key={informante} style={[styles.cell, styles.border, { width: cellWidth }]}>
                                            {apuestaInformante ? renderPronostico(apuestaInformante.Acierto) : <Text>-</Text>}
                                        </View>
                                    );
                                })}
                            </View>
                        ))}
                    </View>
                </ScrollView>

                {apuestasPendientes.length > 0 && (
                    <View style={styles.card}>
                        <Text style={styles.cardTitle}>Apuestas Pendientes</Text>
                        <Text style={styles.subtitleContainer}>
                            Tienes <Text style={styles.cardSubtitle}>{apuestasPendientes.length}</Text> apuestas pendientes
                        </Text>


                        <ScrollView style={styles.pendingList}>
                        {apuestasPendientes.map((apuesta, index) => (
                            <View key={index} style={styles.pendingItem}>
                                {/* Contenedor para Apuesta y su dato */}
                                <View style={styles.pendingRow}>
                                    <Text style={styles.pendingText}>Apuesta</Text>
                                    <Text style={styles.pendingTextValue}>{apuesta.Apuesta}</Text>
                                </View>
                                
                                {/* Contenedor para Informante y su dato */}
                                <View style={styles.pendingRow}>
                                    <Text style={styles.pendingText}>Informante</Text>
                                    <Text style={styles.pendingTextValue}>{apuesta.Informante}</Text>
                                </View>

                                {/* Mostrar ícono de interrogación si la apuesta está pendiente */}
                                {apuesta.Acierto !== 'True' && apuesta.Acierto !== 'False' && (
                                    <TouchableOpacity 
                                        style={styles.updateButton} 
                                        onPress={() => handleActualizarApuesta(apuesta, apuesta.Informante)}
                                    >
                                        <Text style={styles.updateButtonText}>❓</Text>
                                    </TouchableOpacity>
                                )}
                            </View>
                        ))}
                    </ScrollView>

                    </View>
                )}

                {/* Card de Ranking */}
            {ranking.length > 0 && (
                <View style={styles.card}>
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
            {/* Modal */}
            {modalVisible && (
                <Modal
                    transparent={true}
                    animationType="slide"
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
            </ScrollView>
            </View>

            {/* Bottom Bar */}
            <BottomBar />
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        padding: 10,
        backgroundColor: '#2F4F4F',
        alignItems: 'center',
        justifyContent: 'flex-end',
        width: '100%'
    },
    botbarContainer:{
        flex: 1
    },
    scrollContainer: {
        flexGrow: 1,
        paddingBottom: 120,  
        paddingHorizontal:10
    },
    title: {
        fontSize: 45,
        fontWeight: 'bold',
        marginBottom: 20,
        color: '#e3931f',
        textAlign: 'center',
    },
    tableContainer: {
        marginBottom: 20, // Espacio entre la tabla y las apuestas pendientes
        backgroundColor: '#ffdaa4',
    },
    header: {
        flexDirection: 'row',
        backgroundColor: '#186720',
        borderBottomWidth: 1,
        borderColor: '#ccc',
    },
    headerCell: {
        fontSize: 18,
        fontWeight: 'bold',
        textAlign: 'center',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#186720',
        color: 'white',
    },
    row: {
        flexDirection: 'row',
    },
    cell: {
        fontSize: 18,
        textAlign: 'center',
        alignItems: 'center',
        justifyContent: 'center',
        paddingVertical: 10,
        paddingHorizontal: 5,
    },
    dateCell: {
        fontWeight: 'bold',
        backgroundColor: '#ffdaa4',
    },
    border: {
        borderWidth: 1,
        borderColor: '#ccc',
    },
    icon: {
        fontSize: 20,
        textAlign: 'center',
    },
    informanteButton: {
        backgroundColor: '#186720',
        paddingHorizontal: 10,
        paddingVertical: 5,
        borderRadius: 10,
        justifyContent: 'center',
        alignItems: 'center',
        width: '100%',
        minHeight: 40,
    },
    buttonText: {
        color: 'white',
        fontWeight: 'bold',
        fontSize: 18,
        textAlign: 'center',
    },
    card: {
        margin: 10,
        padding: 15,
        backgroundColor: '#212121',
        borderRadius: 10,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.8,
        shadowRadius: 2,
        elevation: 5,
    },
    cardTitle: {
        fontSize: 24, // Más grande
        fontWeight: 'bold',
        color: 'white',
        marginBottom: 10,
        textAlign: 'center', // Centrado
    },
    cardSubtitle: {
        fontSize: 18,
        color: 'red', // Color destacado
        fontWeight: 'bold', // Negrita para resaltar
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
        padding: 10,
        borderBottomWidth: 1,
        borderColor: '#ccc',
        flexDirection: 'row', // Para alinear los elementos en fila
        alignItems: 'center',
        justifyContent: 'space-between', // Separar los textos del botón de actualización
    },
    pendingText: {
        fontSize: 16,
        color: 'white',
        flex: 1, // Para ocupar el espacio disponible
    },
    pendingTextValue:{
        color: '#1dbb2e'
    },
    updateButton: {
        marginLeft: 10, // Espacio entre el texto y el botón
        paddingHorizontal: 10,
        paddingVertical: 5,
        backgroundColor: 'white', // Color llamativo para el botón
        borderRadius: 5,
    },
    updateButtonText: {
        fontSize: 18,
        fontWeight: 'bold',
        color: 'white',
        textAlign: 'center',
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
});

export default Main;
