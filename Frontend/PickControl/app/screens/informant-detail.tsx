import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView } from "react-native";
import { useSearchParams } from "expo-router";

const InformanteDetails = () => {
    const { informante } = useSearchParams(); // Obtener el nombre del informante desde la ruta
    const [informanteData, setInformanteData] = useState(null); // Estado para almacenar los datos del informante
    const [loading, setLoading] = useState(true); // Estado para controlar la carga de los datos

    // Función para obtener los datos del informante
    const fetchInformanteData = async () => {
        try {
            const response = await fetch(`http://192.168.1.71:3000/informante/${informante}`); 
            const data = await response.json();
            setInformanteData(data);
            setLoading(false);
        } catch (error) {
            console.error("Error al obtener los datos del informante:", error);
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchInformanteData();
    }, [informante]); // Se ejecuta cuando cambia el nombre del informante

    if (loading) {
        return (
            <View style={styles.container}>
                <Text style={styles.text}>Cargando...</Text>
            </View>
        );
    }

    return (
        <View style={styles.container}>
            <Text style={styles.text}>Detalles del Informante: {informante}</Text>

            {informanteData ? (
                <ScrollView contentContainerStyle={styles.tableContainer}>
                    {/* Tabla de datos */}
                    <View style={styles.row}>
                        <Text style={styles.cell}>Total Apuestas</Text>
                        <Text style={styles.cell}>{informanteData.totalApuestas}</Text>
                    </View>
                    <View style={styles.row}>
                        <Text style={styles.cell}>Total Aciertos</Text>
                        <Text style={styles.cell}>{informanteData.totalAciertos}</Text>
                    </View>
                    <View style={styles.row}>
                        <Text style={styles.cell}>Ganancias</Text>
                        <Text style={styles.cell}>${informanteData.ganancias.toFixed(2)}</Text>
                    </View>
                    <View style={styles.row}>
                        <Text style={styles.cell}>Porcentaje Aciertos</Text>
                        <Text style={styles.cell}>{informanteData.porcentajeAciertos.toFixed(2)}%</Text>
                    </View>
                </ScrollView>
            ) : (
                <Text style={styles.subText}>No se encontraron datos</Text>
            )}
        </View>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        padding: 20,
        backgroundColor: "#f9f9f9",
    },
    text: {
        fontSize: 24,
        fontWeight: "bold",
        color: "purple",
    },
    subText: {
        fontSize: 18,
        marginTop: 10,
        color: "gray",
    },
    tableContainer: {
        marginTop: 20,
        width: "100%",
        padding: 10,
    },
    row: {
        flexDirection: "row",
        justifyContent: "space-between",
        paddingVertical: 8,
        borderBottomWidth: 1,
        borderColor: "#ccc",
    },
    cell: {
        fontSize: 18,
        fontWeight: "bold",
        textAlign: "left",
    },
});

export default InformanteDetails;
