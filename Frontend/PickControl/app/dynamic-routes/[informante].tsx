import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, Modal, TouchableOpacity, Alert } from "react-native";
import { useLocalSearchParams } from "expo-router"; // Obtener los parámetros de búsqueda
import BottomBar from "../components/bottom-bar"; // Importar la BottomBar
import { Dimensions } from "react-native";
import { useRouter } from "expo-router";
import TopBar from "../components/top-bar";
import { MaterialCommunityIcons } from '@expo/vector-icons';
import DateTimePicker from '@react-native-community/datetimepicker';

export default function InformantDetail() {

  const { informante } = useLocalSearchParams(); // Obtener el parámetro dinámico
  const [data, setData] = useState(null); // Para almacenar la respuesta del backend
  const [loading, setLoading] = useState(true); // Para manejar el estado de carga
  const [apuestas, setApuestas] = useState([]);
  const [modalUpdateVisible, setModalUpdateVisible] = useState(false);
  const [modalDeleteVisible, setModalDeleteVisible] = useState(false);
  const [selectedApuesta, setSelectedApuesta] = useState("");
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [selectedApuestaToUpdate, setSelectedApuestaToUpdate] = useState(null);
  const [periodoSeleccionado, setPeriodoSeleccionado] = useState('todo');

  const router = useRouter();
  const screenWidth = Dimensions.get("window").width; // Usado para hacer el gráfico responsivo

  // Hacer fetch a la API con los datos del informante
  const fetchInformanteData = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/informante/${informante}`);
      const result = await response.json();
      console.log('INFORMACION INFORMANTE ACTUALIZADA: ', result);
      setData(result);
      setApuestas(result.apuestas || []);
    } catch (error) {
      console.error("Error al obtener datos del informante:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInformanteData();
  }, [informante]);

  useEffect(() => {
    if (data) {
      const { totalApuestas, totalAciertos, porcentajeAciertos, apuestas: apuestasData } = data;
      setApuestas(apuestasData);
    }
  }, [data]);

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
  const { totalApuestas, totalAciertos, porcentajeAciertos, apuestas: apuestasData } = data;
  
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

  // Función para formatear la fecha según el período
  const formatearFecha = (fecha) => {
    const date = new Date(fecha);
    const dia = date.getDate().toString().padStart(2, '0');
    const mes = (date.getMonth() + 1).toString().padStart(2, '0');
    const año = date.getFullYear().toString().slice(-2);
    return `${dia}/${mes}/${año}`;
  };

  // Función para agrupar datos por mes si es necesario
  const agruparDatos = (apuestas) => {
    if (periodoSeleccionado === 'año') {
      const datosPorMes = {};
      
      // Agrupar datos por mes
      apuestas.forEach(apuesta => {
        const fecha = new Date(apuesta.Fecha);
        const mes = fecha.getMonth();
        if (!datosPorMes[mes]) {
          datosPorMes[mes] = {
            ganancias: [],
            fecha: new Date(fecha.getFullYear(), mes, 1) // Primer día del mes
          };
        }
        datosPorMes[mes].ganancias.push(calcularGanancia(apuesta.CantidadApostada, apuesta.Cuota, apuesta.Acierto));
      });

      // Calcular media por mes
      return Object.entries(datosPorMes)
        .map(([mes, datos]) => ({
          Fecha: datos.fecha,
          gananciaMedia: datos.ganancias.reduce((a, b) => a + b, 0) / datos.ganancias.length
        }))
        .sort((a, b) => a.Fecha - b.Fecha);
    }
    
    return apuestas;
  };

  // Función para filtrar las apuestas según el período seleccionado
  const apuestasFiltradas = () => {
    if (!apuestas) return [];

    let apuestasFiltradas = [...apuestas];
    const ahora = new Date();

    switch(periodoSeleccionado) {
      case 'semana':
        const unaSemanaMenos = new Date(ahora.setDate(ahora.getDate() - 7));
        apuestasFiltradas = apuestas.filter(apuesta => new Date(apuesta.Fecha) >= unaSemanaMenos);
        break;
      case 'mes':
        const unMesMenos = new Date(ahora.setMonth(ahora.getMonth() - 1));
        apuestasFiltradas = apuestas.filter(apuesta => new Date(apuesta.Fecha) >= unMesMenos);
        break;
      case 'año':
        const unAñoMenos = new Date(ahora.setFullYear(ahora.getFullYear() - 1));
        apuestasFiltradas = apuestas.filter(apuesta => new Date(apuesta.Fecha) >= unAñoMenos);
        break;
      default:
        // 'todo' - no aplicamos filtro
        break;
    }

    // Ordenar por fecha ascendente
    return apuestasFiltradas.sort((a, b) => new Date(a.Fecha).getTime() - new Date(b.Fecha).getTime());
  };


  // Función para calcular el espaciado según el período
  const calcularEspaciado = () => {
    switch(periodoSeleccionado) {
      case 'semana':
        return 40; // Más espacio entre puntos para semana
      case 'mes':
        return 30; // Espacio medio para mes
      case 'año':
        return 20; // Menos espacio para año
      case 'todo':
        return 50; // El espaciado original para todos
      default:
        return 30;
    }
  };


  const handleFechaPress = (apuesta) => {
    setSelectedApuestaToUpdate(apuesta);
    setShowDatePicker(true);
};

const handleDateChange = async (event, selectedDate) => {
    setShowDatePicker(false);
    
    // Si el evento es 'dismissed' o no hay fecha seleccionada, significa que se canceló
    if (event.type === 'dismissed' || !selectedDate) {
        return; // No hacemos nada si se canceló
    }

    if (selectedDate && selectedApuestaToUpdate) {
        try {
            const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuesta/${selectedApuestaToUpdate._id}`, {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ Fecha: selectedDate.toISOString() }),
            });

            if (response.ok) {
                console.log('Fecha actualizada en el servidor');
                // Esperar a que se complete la recarga de datos
                await fetchInformanteData();
                console.log('Datos del informante recargados');
                Alert.alert("Éxito", "Fecha actualizada correctamente");
            } else {
                const errorData = await response.json();
                console.error('Error en la respuesta del servidor:', errorData);
                Alert.alert("Error", "No se pudo actualizar la fecha");
            }
        } catch (error) {
            console.error("Error en la petición:", error);
            Alert.alert("Error", "Ocurrió un error al actualizar la fecha");
        }
    }
};

  return (
    <View style={styles.botbarcontainer}>
      <TopBar />
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
                <Text style={styles.closeButtonText}> 
                  <MaterialCommunityIcons name="close" size={26} color="white" />
                </Text>
              </TouchableOpacity>
              <Text style={styles.modalTitle}>Actualizar Apuesta</Text>
              <Text style={styles.modalText}>¿La apuesta fue correcta?</Text>
              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, { backgroundColor: "#4CAF50" }]}
                  onPress={() => actualizarApuesta(selectedApuesta._id, "True")}
                >
                  <Text style={styles.modalButtonText}> 
                    <MaterialCommunityIcons name="check" size={26} color="white" />
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modalButton, { backgroundColor: "#F44336" }]}
                  onPress={() => actualizarApuesta(selectedApuesta._id, "False")}
                >
                  <Text style={styles.modalButtonText}>
                    <MaterialCommunityIcons name="close" size={32} color="white" />
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>


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
          {apuestasFiltradas().map((apuesta, index) => (
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
                  <TouchableOpacity onPress={() => handleFechaPress(apuesta)} style={styles.fechaContainer}>
                    <Text style={[styles.cellText, { fontWeight: 'bold' }]}>{formatearFecha(apuesta.Fecha)}</Text>
                    <MaterialCommunityIcons name="calendar-edit" size={16} color="#ff9f1c" style={styles.calendarIcon} />
                  </TouchableOpacity>
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
              <Text style={styles.closeButtonText}>
                <MaterialCommunityIcons name="close" size={26} color="white" />
              </Text>
            </TouchableOpacity> 

            <Text style={styles.modalTitle}>Eliminar Apuesta</Text>
            <Text style={styles.modalText}>¿Estás seguro de que deseas eliminar esta apuesta?</Text>
            <View style={styles.modalButtons}>
              <TouchableOpacity
                style={[styles.modalButton, { backgroundColor: "#F44336" }]}
                onPress={() => eliminarApuesta(selectedApuesta._id)}  // Eliminamos la apuesta al hacer clic en "Sí"
              > 
                <Text style={styles.modalButtonText}> 
                  <MaterialCommunityIcons name="trash-can-outline" size={26} color="white" />
                </Text>
              </TouchableOpacity>
            </View> 
          </View> 
        </View> 
      </Modal> 
      {showDatePicker && (
    <DateTimePicker
        value={new Date(selectedApuestaToUpdate?.Fecha || new Date())}
        mode="date"
        display="spinner" // Cambiamos a spinner para más control sobre el estilo
        onChange={handleDateChange}
        themeVariant="light"
        textColor="#ff9f1c"
        positiveButtonLabel="Aceptar"
        negativeButtonLabel="Cancelar"
        positiveButton={{ label: 'OK', textColor: '#ff9f1c' }}
        negativeButton={{ label: 'Cancelar', textColor: '#ff9f1c' }}
    />
)}
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
  modalContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
  },
  modalContent: {
    backgroundColor: '#303030',
    borderRadius: 15,
    padding: 20,
    width: '80%',
    alignItems: 'center',
    elevation: 5,
    position: 'relative',
  },
  closeButton: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: '#F44336',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 1,
  },
  closeButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: 'bold',
  },
  modalTitle: {
    fontSize: 24,
    fontWeight: 'bold',
    color: 'white',
    marginTop: 20,
    marginBottom: 15,
  },
  modalText: {
    fontSize: 18,
    color: 'white',
    marginBottom: 20,
    textAlign: 'center',
  },
  modalButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    width: '100%',
    marginTop: 10,
  },
  modalButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    minWidth: 100,
    alignItems: 'center',
    marginHorizontal: 10,
  },
  modalButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: 'bold',
  },
  botbarcontainer: {
    height: '100%'
  },
  title: {
    fontSize: 32,
    fontWeight: "bold",
    marginBottom: 30,
    marginTop: 60,
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
    backgroundColor: '#303030',
    borderRadius: 15,
    padding: 15,
    marginVertical: 10,
    width: '100%',
    alignItems: 'center',
  },
  chartScrollContainer: {
    marginTop: 10,
    width: '100%',
  },
  chartScrollContainerSmall: {
    alignSelf: 'center',
  },
  chartContentCentered: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  lineChart: {
    marginVertical: 8,
    borderRadius: 16,
  },
  graphicTitle: {
    color: 'white',
    fontSize: 23,
    marginBottom: 20,
    fontWeight: 'bold'
  },
  filterButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginTop: 10,
    paddingHorizontal: 10,
    flexWrap: 'wrap',
    gap: 10,
  },
  filterButton: {
    backgroundColor: 'rgba(33, 33, 33, 0.5)',
    paddingHorizontal: 15,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#424242',
    minWidth: 70,
    alignItems: 'center',
  },
  filterButtonActive: {
    backgroundColor: '#ff9f1c',
    borderColor: '#ff9f1c',
  },
  filterButtonText: {
    color: 'white',
    fontSize: 12,
    textAlign: 'center',
  },
  fechaContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
  },
  calendarIcon: {
    marginLeft: 5,
  }
});
