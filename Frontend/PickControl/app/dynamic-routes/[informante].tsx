import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, Modal, TouchableOpacity, Alert } from "react-native";
import { useLocalSearchParams } from "expo-router"; // Obtener los parámetros de búsqueda
import BottomBar from "../components/bottom-bar"; // Importar la BottomBar
import { PieChart, LineChart } from "react-native-chart-kit";
import { Dimensions } from "react-native";
import { useRouter } from "expo-router";

export default function InformantDetail() {

  const { informante } = useLocalSearchParams(); // Obtener el parámetro dinámico
  const [data, setData] = useState(null); // Para almacenar la respuesta del backend
  const [loading, setLoading] = useState(true); // Para manejar el estado de carga
  const [modalUpdateVisible, setModalUpdateVisible] = useState(false);
  const [modalDeleteVisible, setModalDeleteVisible] = useState(false);
  const [selectedApuesta, setSelectedApuesta] = useState("");

  const router = useRouter();
  const screenWidth = Dimensions.get("window").width; // Usado para hacer el gráfico responsivo

  // Hacer fetch a la API con los datos del informante
  useEffect(() => {
    const fetchInformanteData = async () => {
      try {
        const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/informante/${informante}`);
        const result = await response.json();
        console.log('INFORMACION INFORMANTE: ', result);
        setData(result);
      } catch (error) {
        console.error("Error al obtener datos del informante:", error);
      } finally {
        setLoading(false); // Terminamos de cargar
      }
    };

    fetchInformanteData();
  }, [informante]);

  const eliminarApuesta = async (id) => {
    try {
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas/${id}`, {
        method: "DELETE",
      });
  
      if (response.ok) {
        const result = await response.json();
        
        // Actualizar las apuestas en el estado
        const updatedApuestas = data.apuestas.filter(apuesta => apuesta._id !== id);
        
        // Si no hay más apuestas, vaciar la lista
        setData({ ...data, apuestas: updatedApuestas });
  
        if (updatedApuestas.length === 0) {
          // Si ya no quedan apuestas, muestra un alerta y redirige a inicio al presionar OK
          Alert.alert(
            "Éxito", 
            "La última apuesta ha sido eliminada. Serás redirigido al inicio.",
            [
              {
                text: "OK",
                onPress: () => {
                  // Redirige a la página principal cuando el usuario presiona "OK"
                  router.push("/");
                }
              }
            ]
          );
        } else {
          // Si aún quedan apuestas, solo muestra un alerta de éxito normal
          Alert.alert("Éxito", "La apuesta ha sido eliminada exitosamente.");
        }
        
        setModalDeleteVisible(false);
      } else {
        Alert.alert("Error", "Hubo un problema al eliminar la apuesta.");
      }
    } catch (error) {
      console.error("Error al eliminar la apuesta:", error);
      Alert.alert("Error", "Hubo un problema al eliminar la apuesta.");
    }
  };
  
  


  // Función para manejar el modal de eliminación
  const handleEliminarPress = (apuesta) => {
    setSelectedApuesta(apuesta);
    setModalDeleteVisible(true); 
  };

  // Si aún estamos cargando, mostramos un indicador de carga
  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#4CAF50" />
        <Text style={styles.loadingText}>Cargando información...</Text>
      </View>
    );
  }
  // Si no hay datos, mostramos un mensaje de error
  if (!data) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>No se encontraron datos para este informante.</Text>
      </View>
    );
  }

  // Extraemos los datos de la respuesta
  const { totalApuestas, totalAciertos, porcentajeAciertos, apuestas } = data;

  // Función para calcular las ganancias de cada apuesta (en euros)
  const calcularGanancia = (cantidadApostada, cuota, acierto) => {
    if (!apuestas || apuestas.length === 0) {
      return 0; // Devolvemos número en lugar de string
    }
    if (acierto === "Pending") {
      return 0; 
    } if (acierto === "True") {
      return Number((cantidadApostada * (cuota - 1)).toFixed(2)); // Convertimos a número
    } 
    return Number((-cantidadApostada).toFixed(2)); // Convertimos a número
  };

  // Función para calcular las ganancias acumuladas
  const calcularGananciaAcumulada = (apuestas) => {
    if (!apuestas || apuestas.length === 0) {
      return [0];
    }
    let acumulado = 0;
    return apuestas.map((apuesta) => {
      const ganancia = calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto);
      acumulado += ganancia;
      return Number(acumulado.toFixed(2)); // Aseguramos que devolvemos un número con 2 decimales
    });
  };

  // Función para renderizar los pronósticos con símbolos
  const renderPronostico = (acierto, apuesta) => {
    if (acierto === "True") {
      return <Text style={[styles.icon, { color: "green", fontSize: 18 }]}>✅</Text>;
    } else if (acierto === "False") {
      return <Text style={[styles.icon, { color: "red" }]}>❌</Text>;
    }
    return (
      <TouchableOpacity onPress={() => { 
        setSelectedApuesta(apuesta); 
        setModalUpdateVisible(true); 
      }}>
        <Text style={[styles.icon, { color: "gray" }]}>❓</Text>
      </TouchableOpacity>
    );
  };
  

  // Función para calcular las ganancias totales
  const calcularGananciasTotales = () => {
    if (!apuestas || apuestas.length === 0) {
      return <Text>0€</Text>;
    }
    const total = apuestas.reduce((acc, apuesta) => {
      return acc + calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto);
    }, 0);
    return <Text>{total.toFixed(2)}€</Text>;
  };

  const actualizarApuesta = async (id, acierto) => {
    if (!id) {
      console.error("El ID de la apuesta es nulo o indefinido.");
      return;
    }
    console.log("Actualizar Apuesta - ID:", id, "Acierto:", acierto); 
    try {
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuesta/${id}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ Acierto: acierto }),
      });

      if (response.ok) {
        const result = await response.json();
        
        // Actualizamos las apuestas en el estado
        const updatedApuestas = data.apuestas.map((apuesta) =>
          apuesta._id === id ? { ...apuesta, Acierto: acierto } : apuesta
        );

        // Recalcular estadísticas
        const totalAciertos = updatedApuestas.filter(a => a.Acierto === "True").length;
        const porcentajeAciertos = (totalAciertos / updatedApuestas.length) * 100;

        // Actualizar el estado completo con las nuevas apuestas y estadísticas
        setData((prevData) => ({
          ...prevData,
          apuestas: updatedApuestas,
          totalAciertos,
          porcentajeAciertos,
        }));

        setModalUpdateVisible(false); // Cerramos el modal
      } else {
        console.error("Error al actualizar la apuesta:", await response.text());
      }
    } catch (error) {
      console.error("Error al actualizar la apuesta:", error);
    }
  };

  // Función para formatear la fecha
  const formatearFecha = (fecha: string) => {
    return new Date(fecha).toLocaleDateString('es-ES', {
      day: '2-digit',
      month: '2-digit',
      year: '2-digit'
    });
  };

  // Datos para el gráfico de líneas (Evolución de las ganancias)
  const lineChartData = {
    labels: apuestas.map((apuesta) => formatearFecha(apuesta.Fecha)),
    datasets: [
      {
        data: calcularGananciaAcumulada(apuestas),
        strokeWidth: 2,
        color: (opacity = 1) => `rgba(255, 0, 0, ${opacity})`,
      },
    ],
  };

  // Configuración general de los gráficos
  const chartConfig = {
    backgroundColor: "#000000",
    backgroundGradientFrom: "#212121",
    backgroundGradientTo: "#424242",
    decimalPlaces: 2,
    color: (opacity = 1) => `rgba(255, 255, 255, ${opacity})`,
    labelColor: (opacity = 1) => `rgba(255, 255, 255, ${opacity})`,
    style: {
      borderRadius: 16,
    },
    propsForLabels: {
      fontSize: 8,
    }
  };

  return (
    <View style={styles.botbarcontainer}>
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
            <Text style={styles.value}>{calcularGananciasTotales()}</Text>  
          </View>
        </View>
        <Modal
          animationType="slide"
          transparent={true}
          visible={modalUpdateVisible}
          onRequestClose={() => setModalUpdateVisible(false)}
        >
          <View style={styles.modalContainer}>
            <View style={styles.modalContent}>
              <TouchableOpacity 
                style={styles.closeButton} 
                onPress={() => setModalUpdateVisible(false)}
              >
                <Text style={styles.closeButtonText}>✖</Text>
              </TouchableOpacity>

              <Text style={styles.modalTitle}>Actualizar Apuesta</Text>
              <Text style={styles.modalText}>¿La apuesta fue correcta?</Text>
              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, { backgroundColor: "green" }]}
                  onPress={() => actualizarApuesta(selectedApuesta._id, "True")}
                >
                  <Text style={styles.modalButtonText}>✔️</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modalButton, { backgroundColor: "red" }]}
                  onPress={() => actualizarApuesta(selectedApuesta._id, "False")}
                >
                  <Text style={styles.modalButtonText}>❌</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>

          Gráfico de Pastel (Aciertos vs Errores)
        <View style={styles.chartContainer}>
            <Text style={styles.graphicTitle}>Porcentaje Aciertos</Text>
            <PieChart
              data={[
                {
                  name: "Aciertos",
                  population: porcentajeAciertos,
                  color: "green",
                  legendFontColor: "white",
                  legendFontSize: 15,
                },
                {
                  name: "Errores",
                  population: 100 - porcentajeAciertos,
                  color: "red",
                  legendFontColor: "white",
                  legendFontSize: 15,
                },
              ]}
              width={screenWidth - 80} // Ajusta el gráfico al tamaño de la pantalla
              height={150}
              chartConfig={chartConfig}
              accessor={"population"}
              backgroundColor={"transparent"}
              paddingLeft={"15"} 
              center={[0, -15]}
            />
        </View>  

         Gráfico de Líneas (Evolución de las Ganancias)
        <View style={styles.chartContainer}>
          <Text style={styles.graphicTitle}>Ganancias</Text>
          <LineChart
            data={lineChartData}
            width={screenWidth - 40}
            height={220}
            chartConfig={chartConfig}
            bezier
            withVerticalLabels={true}
            withHorizontalLabels={true}
            yAxisLabel=""
            yAxisSuffix="€"
          />

        </View>  

        <Text style={styles.cardTitle}>Aciertos y Errores</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={styles.table}>
            <View style={styles.tableRow}>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Apuesta</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Acierto</Text>
              </View>
              <View style={[styles.tableHeaderCell, styles.border]}>
                <Text style={styles.tableHeaderText}>Fecha</Text> 
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

            Filas de datos
          {apuestas.map((apuesta, index) => (
            <View key={index} style={[styles.tableRow, index % 2 === 0 ? styles.evenRow : styles.oddRow]}>
              <View style={[styles.tableCell, styles.border]}>
                <TouchableOpacity onPress={() => handleEliminarPress(apuesta)}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.Apuesta}</Text>
                </TouchableOpacity>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text>{renderPronostico(apuesta.Acierto, apuesta)}</Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                  <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{formatearFecha(apuesta.Fecha)}</Text>
                </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{apuesta.TipoDeApuesta}</Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: 'bold' }]}>
                  {Number(apuesta.Cuota).toFixed(2)}
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: 'bold' }]}>
                  {Number(apuesta.CantidadApostada).toFixed(2)}<Text>€</Text>
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: 'bold' }]}>
                  {Number(calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto)).toFixed(2)}<Text>€</Text>
                </Text>
              </View>
            </View>
          ))}
          </View>
        </ScrollView>
      </ScrollView>


      <Modal
        animationType="slide"
        transparent={true}
        visible={modalDeleteVisible}
        onRequestClose={() => setModalDeleteVisible(false)}
      > 
        <View style={styles.modalContainer}>
          <View style={styles.modalContent}> 
            
            <TouchableOpacity 
              style={styles.closeButton} 
              onPress={() => setModalDeleteVisible(false)}
            >
              <Text style={styles.closeButtonText}>✖</Text>
            </TouchableOpacity> 

            <Text style={styles.modalTitle}>¿Estás seguro de eliminar esta apuesta?</Text>
            <View style={styles.modalButtons}>
              <TouchableOpacity
                style={[styles.modalButton, { backgroundColor: "green" }]}
                onPress={() => eliminarApuesta(selectedApuesta._id)}  // Eliminamos la apuesta al hacer clic en "Sí"
              > 
                <Text style={styles.modalButtonText}>Sí</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalButton, { backgroundColor: "red" }]}
                onPress={() => setModalDeleteVisible(false)}  // Cerramos el modal sin eliminar la apuesta
              >
                <Text style={styles.modalButtonText}>No</Text> 
              </TouchableOpacity> 
            </View> 
          </View> 
        </View> 
      </Modal> 
      </View>
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
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: "#000",
  },
  errorText: {
    fontSize: 18,
    color: "#ffba57",
    textAlign: "center",
    marginVertical: 20,
  },
  loadingText: {
    fontSize: 18,
    color: "#4CAF50",
    textAlign: "center",
    marginVertical: 20,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#fff',
    marginBottom: 15,
    textAlign: 'center',
  },
  modalText: {
    fontSize: 16,
    color: '#fff',
    marginBottom: 20,
    textAlign: 'center',
  },
  closeButtonText: {
    fontSize: 20,
    color: '#fff',
    textAlign: 'center',
  },
  modalButtonText: {
    fontSize: 18,
    color: '#fff',
    textAlign: 'center',
  },
  botbarcontainer: {
    height: '100%'
  },
  title: {
    fontSize: 32,
    fontWeight: "bold",
    marginVertical: 30,
    textAlign: "center",
    color: "#ffba57",
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
    backgroundColor: "#2e2e2e",
    borderRadius: 10,
    borderColor: 'white',
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
    borderBottomColor: "#444",
  },
  tableHeaderCell: {
    width: 150, // Ajusta el tamaño de cada celda
    paddingVertical: 12,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#2a7032",
    borderBottomWidth: 2,
    borderBottomColor: "#fff", // Borde inferior blanco
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
     backgroundColor: '#ffdaa4'
  },
  cellText: {
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center',
    color:'black'
  },
  icon: {
    fontSize: 17,
    textAlign: "center",
  },
  border: {
    borderWidth: 1,
    borderColor: "#555",
  },
  evenRow: {
    backgroundColor: "#f9f9f9",
  },
  oddRow: {
    backgroundColor: "#ffffff",
  },
  chartContainer: {
    paddingTop: 20,
    alignItems: "center",
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
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 5,
  },
  graphicTitle: {
    color: 'white',
    fontSize: 23,
    marginBottom: 20,
    fontWeight: 'bold'
  },
});
