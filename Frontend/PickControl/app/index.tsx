import React, { useState, useEffect } from "react";
import { Text, View, StyleSheet, ScrollView, TouchableOpacity } from "react-native";
import BottomBar from "./components/bottom-bar";
import { useRouter } from "expo-router";  // Importamos useRouter

const Main = () => {
    const [apuestas, setApuestas] = useState([]);  // Estado para almacenar las apuestas obtenidas
    const [informantes, setInformantes] = useState([]); // Informantes que obtendremos de la API

    const router = useRouter(); // Usamos useRouter para navegación programática

    const cellWidth = 160; // Ampliamos el ancho de la celda para que los nombres largos no se dividan

    // Función para renderizar los pronósticos con símbolos
    const renderPronostico = (acierto) => {
        if (acierto === 'True') {
            return <Text style={[styles.icon, { color: 'green' }]}>✔️</Text>;
        } else if (acierto === 'False') {
            return <Text style={[styles.icon, { color: 'red' }]}>❌</Text>;
        }
        return <Text>-</Text>;  // Si no hay acierto (es null o vacío), mostramos "-"
    };

    // Función para obtener las apuestas de la API
    const fetchApuestas = async () => {
        try {
            const response = await fetch('http://192.168.1.71:3000/apuestas');  
            const data = await response.json();

            // Revisamos la estructura de la respuesta para depurar
            console.log("Datos obtenidos:", data);

            // Creamos un conjunto para los informantes y un objeto para las apuestas
            const apuestasPorInformante = {};
            data.picks.forEach((pick) => {
                const { Informante, Acierto, Apuesta } = pick;
                if (!apuestasPorInformante[Informante]) {
                    apuestasPorInformante[Informante] = [];
                }
                apuestasPorInformante[Informante].push({ Apuesta, Acierto });
            });

            // Establecemos las apuestas y los informantes
            setApuestas(apuestasPorInformante);

            // Extraemos los nombres de los informantes
            const informantesList = Object.keys(apuestasPorInformante);
            setInformantes(informantesList);
        } catch (error) {
            console.error("Error al obtener las apuestas:", error);
        }
    };

    useEffect(() => {
        fetchApuestas();
    }, []);

    const handleInformantePress = (informante) => {
        console.log("Navegando hacia:", informante);  
        router.push(`/dynamic-routes/${informante}`); 
    };
    

    // Función para obtener el número máximo de apuestas de un informante
    const getMaxApuestas = () => {
        // Si apuestas está vacío, evitamos calcular
        if (Object.keys(apuestas).length === 0) {
            return 0; // No hay apuestas, por lo tanto, no hay filas que mostrar
        }

        const apuestasCount = Object.values(apuestas).map((apuestas) => apuestas.length);
        const max = Math.max(...apuestasCount);
        console.log("Número máximo de apuestas:", max);  // Para depurar
        return max > 0 ? max : 0; // Aseguramos que no sea negativo o NaN
    };

    const maxApuestas = getMaxApuestas(); // Número de filas necesarias
    console.log("maxApuestas:", maxApuestas);  // Verificamos el valor de maxApuestas

    return (
        <View style={styles.container}>
            <Text style={styles.title}>Pick Control</Text>

            {/* Tabla */}
            <ScrollView horizontal>
                <View>
                    {/* Encabezados */}
                    <View style={styles.header}>
                        {/* Columna de fecha */}
                        <Text style={[styles.cell, styles.headerCell, styles.border, { width: cellWidth }]}>
                            Fecha
                        </Text>

                        {/* Columnas de informantes convertidas en botones dentro de celdas */}
                        {informantes.map((informante, index) => (
                            <View 
                                key={index} 
                                style={[styles.cell, styles.border, { width: cellWidth }]}>
                                
                                {/* Usamos TouchableOpacity para navegación programática */}
                                <TouchableOpacity
                                    onPress={() => handleInformantePress(informante)}  // Navegación usando useRouter
                                    style={styles.informanteButton}
                                >
                                    <Text style={styles.buttonText}>{informante}</Text>
                                </TouchableOpacity>
                            </View>
                        ))}
                    </View>

                    {/* Filas de pronósticos */}
                    {[...Array(maxApuestas)].map((_, index) => (
                        <View key={index} style={styles.row}>
                            {/* Columna de fecha vacía */}
                            <Text style={[styles.cell, styles.dateCell, styles.border, { width: cellWidth }]}></Text>

                            {/* Mostrar los aciertos o errores de las apuestas de cada informante */}
                            {informantes.map((informante) => {
                                const apuestasInformante = apuestas[informante];
                                const apuestaInformante = apuestasInformante ? apuestasInformante[index] : null;
                                return (
                                    <View
                                        key={informante}
                                        style={[styles.cell, styles.border, { width: cellWidth }]}>
                                        {apuestaInformante ? renderPronostico(apuestaInformante.Acierto) : <Text>-</Text>}
                                    </View>
                                );
                            })}
                        </View>
                    ))}
                </View>
            </ScrollView>

            <BottomBar />
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        padding: 10,
        backgroundColor: '#f5f5f5',
        alignItems: 'center',
    },
    title: {
        fontSize: 32,
        fontWeight: 'bold',
        marginBottom: 20,
        color: 'green',
        textAlign: 'center',
    },
    header: {
        flexDirection: 'row',
        backgroundColor: '#4CAF50',  // Mismo color que el botón
        borderBottomWidth: 1,
        borderColor: '#ccc',
    },
    headerCell: {
        fontSize: 18,  // Reduzco el tamaño del texto
        fontWeight: 'bold',
        textAlign: 'center',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#4CAF50',  // Mismo color que el botón
        color: 'white',  // Texto blanco
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
        backgroundColor: '#f9f9f9',
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
        backgroundColor: '#4CAF50', // El mismo color que el encabezado y los botones
        paddingHorizontal: 10,
        paddingVertical: 5,
        borderRadius: 10, // Bordes redondeados
        justifyContent: 'center',
        alignItems: 'center',
        width: '100%', // Hace que el botón ocupe el 100% del ancho de la celda
        minHeight: 40, // Asegura que tenga una altura mínima
    },
    buttonText: {
        color: 'white',
        fontWeight: 'bold',
        fontSize: 18, // Un tamaño más pequeño para el texto
        textAlign: 'center',
        textOverflow: 'ellipsis', // Para evitar que el texto se divida
        whiteSpace: 'nowrap', // Mantiene el texto en una sola línea
    },
});

export default Main;
