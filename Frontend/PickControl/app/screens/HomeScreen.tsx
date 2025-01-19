import React from "react";
import { Text, View, StyleSheet, ScrollView } from "react-native";

const fechas = ['2025-01-17', '2025-01-18'];

const informantes = [
    { id: '1', nombre: 'Dm7 Gratuito', pronosticos: ['Acierto', 'Fallo'] },
    { id: '2', nombre: 'Dm7 AllSports', pronosticos: ['Fallo', 'Acierto'] },
    { id: '3', nombre: 'Mr Bet', pronosticos: ['Acierto', 'Fallo'] },
    { id: '4', nombre: 'AllSportPick', pronosticos: ['Acierto', 'Acierto'] },
    { id: '5', nombre: 'Apuesta Diaria', pronosticos: ['Fallo', 'Fallo'] },
    { id: '6', nombre: 'FutbetPro Free', pronosticos: ['Fallo', 'Acierto'] },
    { id: '7', nombre: 'FutbetPro VIP Instagram', pronosticos: ['Acierto', 'Fallo'] },
];

const Main = () => {
    const cellWidth = 120; // Ancho fijo para todas las celdas

    // Función para renderizar los pronósticos con símbolos
    const renderPronostico = (resultado) => {
        if (resultado === 'Acierto') {
            return <Text style={[styles.icon, { color: 'green' }]}>✔️</Text>;
        }
        return <Text style={[styles.icon, { color: 'red' }]}>❌</Text>;
    };

    return (
        <View style={styles.container}>
            <Text style={styles.title}>Pick Control</Text>

            {/* Tabla */}
            <ScrollView horizontal>
                <View>
                    {/* Encabezados */}
                    <View style={styles.header}>
                        <Text style={[styles.cell, styles.headerCell, styles.border, { width: cellWidth }]}>
                            Fecha
                        </Text>
                        {informantes.map((informante) => (
                            <Text
                                key={informante.id}
                                style={[styles.cell, styles.headerCell, styles.border, { width: cellWidth }]}
                            >
                                {informante.nombre}
                            </Text>
                        ))}
                    </View>

                    {/* Filas de Pronósticos */}
                    {fechas.map((fecha, index) => (
                        <View key={index} style={styles.row}>
                            {/* Fecha como primera celda */}
                            <Text style={[styles.cell, styles.dateCell, styles.border, { width: cellWidth }]}>
                                {fecha}
                            </Text>
                            {informantes.map((informante) => (
                                <View
                                    key={informante.id}
                                    style={[styles.cell, styles.border, { width: cellWidth }]}
                                >
                                    {renderPronostico(informante.pronosticos[index])}
                                </View>
                            ))}
                        </View>
                    ))}
                </View>
            </ScrollView>
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
        backgroundColor: '#ddd',
        borderBottomWidth: 1,
        borderColor: '#ccc',
    },
    headerCell: {
        fontSize: 18,
        fontWeight: 'bold',
        textAlign: 'center',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#e0e0e0',
    },
    row: {
        flexDirection: 'row',
    },
    cell: {
        fontSize: 14,
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
});

export default Main;
