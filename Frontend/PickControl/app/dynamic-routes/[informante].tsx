import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, ActivityIndicator } from "react-native";
import { useLocalSearchParams } from "expo-router"; // Obtener los parámetros de búsqueda
import BottomBar from "../components/bottom-bar"; // Importar la BottomBar
import { PieChart, LineChart } from "react-native-chart-kit";
import { Dimensions } from "react-native";
import Svg from 'react-native-svg';
import numeral from 'numeral';




export default function InformantDetail() {
  const { informante } = useLocalSearchParams(); // Obtener el parámetro dinámico
  const [data, setData] = useState(null); // Para almacenar la respuesta del backend
  const [loading, setLoading] = useState(true); // Para manejar el estado de carga

  const screenWidth = Dimensions.get("window").width; // Usado para hacer el gráfico responsivo

  // Hacer fetch a la API con los datos del informante
  useEffect(() => {
    const fetchInformanteData = async () => {
      try {
        const response = await fetch(`http://192.168.1.71:3000/informante/${informante}`);
        const result = await response.json();
        setData(result);
      } catch (error) {
        console.error("Error al obtener datos del informante:", error);
      } finally {
        setLoading(false); // Terminamos de cargar
      }
    };

    fetchInformanteData();
  }, [informante]);

  // Si aún estamos cargando, mostramos un indicador de carga
  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#4CAF50" />
        <Text>Cargando información...</Text>
      </View>
    );
  }

  // Si no hay datos, mostramos un mensaje de error
  if (!data) {
    return (
      <View style={styles.container}>
        <Text>No se encontraron datos para este informante.</Text>
      </View>
    );
  }

  // Extraemos los datos de la respuesta
  const { totalApuestas, totalAciertos, ganancias, porcentajeAciertos, apuestas } = data;

  // Función para calcular las ganancias de cada apuesta (en euros)
  const calcularGanancia = (cantidadApostada, cuota, acierto) => {
    // Si la apuesta es un acierto, multiplicamos la cantidad apostada por la cuota
    if (acierto === "True") {
      return (cantidadApostada * cuota).toFixed(2);  // Ganancia positiva
    } 
    // Si la apuesta ha fallado, restamos la cantidad apostada
    return (-cantidadApostada).toFixed(2);  // Ganancia negativa
  };

  // Función para renderizar los pronósticos con símbolos
  const renderPronostico = (acierto) => {
    if (acierto === "True") {
      return <Text style={[styles.icon, { color: "green" }]}>✔️</Text>;
    } else if (acierto === "False") {
      return <Text style={[styles.icon, { color: "red" }]}>❌</Text>;
    }
    return <Text style={[styles.icon, { color: "gray" }]}>❓</Text>;
  };

  // Función para calcular las ganancias totales basadas en las apuestas
  const calcularGananciasTotales = () => {
    return apuestas.reduce((total, apuesta) => {
      return total + parseFloat(calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto));
    }, 0).toFixed(2);
  };

  // Datos para el gráfico de pastel (Aciertos vs Errores)
  const pieChartData = [
    {
      name: "Aciertos",
      population: totalAciertos,
      color: "green",
      legendFontColor: "white",
      legendFontSize: 15,
    },
    {
      name: "Errores",
      population: totalApuestas - totalAciertos,
      color: "red",
      legendFontColor: "white",
      legendFontSize: 15,
    },
  ];

  // Datos para el gráfico de líneas (Evolución de las ganancias)
  const lineChartData = {
    labels: apuestas.map((_, index) => `Apuesta ${index + 1}`), // Etiquetas
    datasets: [
      {
        data: apuestas.map(apuesta =>
          parseFloat(calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto))
        ), // Solo números, sin símbolos
        strokeWidth: 2,
        color: (opacity = 1) => `rgb(255, 0, 0), ${opacity})`, // Color de la línea
      },
    ],
  };

  // Configuración general de los gráficos
const chartConfig = {
  backgroundColor: "#000000", // Fondo negro
  backgroundGradientFrom: "#212121", // Degradado oscuro
  backgroundGradientTo: "#424242", // Más claro hacia abajo
  decimalPlaces: 2,
  color: (opacity = 1) => `rgba(255, 255, 255, ${opacity})`, // Texto blanco
  labelColor: (opacity = 1) => `rgba(255, 255, 255, ${opacity})`, // Etiquetas en blanco
  style: {
    borderRadius: 16,
  },
  formatYLabel: (value) => `${value}€`, // Etiqueta en euros
};

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollView}>
        <Text style={styles.title}>{informante}</Text>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Estadísticas</Text>
          <View style={styles.row}>
            <Text style={styles.label}>Total Apuestas:</Text>
            <Text style={styles.value}>{totalApuestas}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Total Aciertos:</Text>
            <Text style={styles.value}>{totalAciertos}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Porcentaje de Aciertos:</Text>
            <Text style={styles.value}>{porcentajeAciertos.toFixed(2)}%</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.label}>Ganancias Totales:</Text>
            <Text style={styles.value}>{`${calcularGananciasTotales()}€`}</Text>  
          </View>
        </View>

        {/* Gráfico de Pastel (Aciertos vs Errores) */}
        <View style={styles.chartContainer}>
          <PieChart
            data={pieChartData}
            width={screenWidth - 40} // Ajusta el gráfico al tamaño de la pantalla
            height={220}
            chartConfig={chartConfig}
            accessor={"population"}
            backgroundColor={"transparent"}
            paddingLeft={"15"}
            center={[10, 20]}
          />
        </View>

        {/* Gráfico de Líneas (Evolución de las Ganancias) */}
        <View style={styles.chartContainer}>
        <LineChart
          data={lineChartData}
          width={screenWidth - 40} // Ajusta el gráfico al tamaño de la pantalla
          height={220}
          chartConfig={chartConfig}
          fromZero={true}
        />

        </View>

        {/* Tabla de Aciertos y Errores */}
        <Text style={styles.cardTitle}>Aciertos y Errores</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={styles.table}>
            {/* Encabezados de la tabla */}
            <View style={styles.tableRow}>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Apuesta</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Acierto</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Tipo de Apuesta</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Cuota</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Cant. Apostada</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Ganancia</Text>
              </View>
            </View>

            {/* Filas de datos */}
            {apuestas.map((apuesta, index) => (
              <View key={index} style={[styles.tableRow, index % 2 === 0 ? styles.evenRow : styles.oddRow]}>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.Apuesta}</Text>
                </View>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{renderPronostico(apuesta.Acierto)}</Text>
                </View>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.TipoDeApuesta}</Text>
                </View>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.Cuota}</Text>
                </View>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.CantidadApostada}</Text>
                </View>
                <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{`${calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto)}€`}</Text>  {/* Cambio aquí para tener en cuenta si la apuesta ha fallado */}
                </View>
              </View>
            ))}
          </View>
        </ScrollView>
      </ScrollView>

      {/* BottomBar siempre visible al fondo */}
      <BottomBar />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#000",
    padding: 20,
  },
  scrollView: {
    paddingBottom: 80, // Espacio suficiente para que BottomBar no se superponga
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  title: {
    fontSize: 32,
    fontWeight: "bold",
    marginVertical: 30,
    textAlign: "center",
    color: "green",
  },
  card: {
    backgroundColor: "white",
    padding: 20,
    borderRadius: 10,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
    marginBottom: 20,
  },
  cardTitle: {
    fontSize: 25,
    fontWeight: "bold",
    marginBottom: 30,
    textAlign: "center",
    color: "black",
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  label: {
    fontSize: 18,
    color: "#777",

  },
  value: {
    fontSize: 16,
    fontWeight: "bold",
    color: "#333",
  },
  table: {
    backgroundColor: "white",
    borderRadius: 10,
    padding: 15,
    marginBottom: 20,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
  },
  tableRow: {
    flexDirection: "row",
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
  },
  tableHeaderCell: {
    width: 150, // Ajusta el tamaño de cada celda
    paddingVertical: 12,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#4CAF50",
  },
  tableHeaderText: {
    fontSize: 16,
    fontWeight: "bold",
    textAlign: "center",
    color: "white",
  },
  tableCell: {
    width: 150, // Ajusta el tamaño de cada celda
    paddingVertical: 10,
    paddingHorizontal: 15,  // Añadido más espacio horizontal
    justifyContent: 'center',
    alignItems: 'center',
    textAlign: 'center',
  },
  cellText: {
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center',
  },
  icon: {
    fontSize: 16,
    textAlign: "center",
  },
  border: {
    borderWidth: 1,
    borderColor: "#ccc",
  },
  evenRow: {
    backgroundColor: "#f9f9f9",
  },
  oddRow: {
    backgroundColor: "#ffffff",
  },
  chartContainer: {
    marginBottom: 20,
    alignItems: "center",
  },
});
