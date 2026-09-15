import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Modal,
  TouchableOpacity,
  Alert,
  Platform,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useLocalSearchParams } from "expo-router"; // Obtener los parámetros de búsqueda
import BottomBar from "@/src/components/bottom-bar"; // Importar la BottomBar
import { Dimensions } from "react-native";
import { useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import DateTimePicker from "@react-native-community/datetimepicker";
import { getInformanteStats } from "@/src/api/informantes.api";
import { deletePick, updatePick } from "@/src/api/picks.api";
import { ApiError } from "@/src/api/client";
import { InformanteStats } from "@/src/types/informante.types";
import { PickItem, Acierto } from "@/src/types/pick.types";

export default function InformantDetail() {
  const { informante } = useLocalSearchParams(); // Obtener el parámetro dinámico
  const [data, setData] = useState<InformanteStats | null>(null); // Para almacenar la respuesta del backend
  const [loading, setLoading] = useState(true); // Para manejar el estado de carga
  const [apuestas, setApuestas] = useState<PickItem[]>([]);
  const [modalUpdateVisible, setModalUpdateVisible] = useState(false);
  const [modalDeleteVisible, setModalDeleteVisible] = useState(false);
  const [selectedApuesta, setSelectedApuesta] = useState<PickItem | null>(null);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [selectedApuestaToUpdate, setSelectedApuestaToUpdate] =
    useState<PickItem | null>(null);
  const [periodoSeleccionado, setPeriodoSeleccionado] = useState("semana");

  const router = useRouter();
  const insets = useSafeAreaInsets();
  const screenWidth = Dimensions.get("window").width; // Usado para hacer el gráfico responsivo

  // Hacer fetch a la API con los datos del informante
  const fetchInformanteData = async () => {
    try {
      setLoading(true);
      const result = await getInformanteStats(String(informante));
      console.log("INFORMACION INFORMANTE ACTUALIZADA: ", result);
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
      const {
        totalApuestas,
        totalAciertos,
        porcentajeAciertos,
        apuestas: apuestasData,
      } = data;
      setApuestas(apuestasData);
    }
  }, [data]);

  const eliminarApuesta = async (id: number) => {
    if (!data) return;
    try {
      await deletePick(id);

      // Actualizar las apuestas en el estado
      const updatedApuestas = data.apuestas.filter(
        (apuesta) => apuesta.id !== id
      );

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
              },
            },
          ]
        );
      } else {
        // Si aún quedan apuestas, solo muestra un alerta de éxito normal
        Alert.alert("Éxito", "La apuesta ha sido eliminada exitosamente.");
      }

      setModalDeleteVisible(false);
    } catch (error) {
      console.error("Error al eliminar la apuesta:", error);
      const message =
        error instanceof ApiError
          ? error.message
          : "Hubo un problema al eliminar la apuesta.";
      Alert.alert("Error", message);
    }
  };

  // Función para manejar el modal de eliminación
  const handleEliminarPress = (apuesta: PickItem) => {
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
        <Text style={styles.errorText}>
          No se encontraron datos para este informante.
        </Text>
      </View>
    );
  }

  // Extraemos los datos de la respuesta
  const {
    totalApuestas,
    totalAciertos,
    porcentajeAciertos,
    apuestas: apuestasData,
  } = data;

  // Función para calcular las ganancias de cada apuesta (en euros)
  const calcularGanancia = (
    cantidadApostada: number,
    cuota: number,
    acierto: Acierto
  ) => {
    if (!apuestas || apuestas.length === 0) {
      return 0; // Devolvemos número en lugar de string
    }
    if (acierto === "Pending") {
      return 0;
    }
    if (acierto === "True") {
      return Number((cantidadApostada * (cuota - 1)).toFixed(2)); // Convertimos a número
    }
    return Number((-cantidadApostada).toFixed(2)); // Convertimos a número
  };

  // Función para calcular las ganancias acumuladas
  const calcularGananciaAcumulada = (apuestas: PickItem[]) => {
    if (!apuestas || apuestas.length === 0) {
      return [0];
    }
    let acumulado = 0;
    return apuestas.map((apuesta) => {
      const ganancia = calcularGanancia(
        apuesta.cantidadApostada,
        apuesta.cuota,
        apuesta.acierto
      );
      acumulado += ganancia;
      return Number(acumulado.toFixed(2)); // Aseguramos que devolvemos un número con 2 decimales
    });
  };

  // Función para renderizar los pronósticos con símbolos
  const renderPronostico = (acierto: Acierto, apuesta: PickItem) => {
    if (acierto === "True") {
      return (
        <Text style={[styles.icon, { color: "green", fontSize: 18 }]}>✅</Text>
      );
    } else if (acierto === "False") {
      return <Text style={[styles.icon, { color: "red" }]}>❌</Text>;
    }
    return (
      <TouchableOpacity
        onPress={() => {
          setSelectedApuesta(apuesta);
          setModalUpdateVisible(true);
        }}
      >
        <Text style={[styles.icon, { color: "gray" }]}>❓</Text>
      </TouchableOpacity>
    );
  };

  // Picks de Telegram: solo lectura, sin tocar el modal de edición.
  const renderPronosticoTelegram = (acierto: Acierto) => {
    if (acierto === "True") {
      return (
        <Text style={[styles.icon, { color: "green", fontSize: 18 }]}>✅</Text>
      );
    } else if (acierto === "False") {
      return <Text style={[styles.icon, { color: "red" }]}>❌</Text>;
    }
    return <Text style={[styles.icon, { color: "gray" }]}>❓</Text>;
  };

  // Función para calcular las ganancias totales
  const calcularGananciasTotales = () => {
    if (!apuestas || apuestas.length === 0) {
      return <Text>0€</Text>;
    }
    const total = apuestas.reduce((acc, apuesta) => {
      return (
        acc +
        calcularGanancia(
          apuesta.cantidadApostada,
          apuesta.cuota,
          apuesta.acierto
        )
      );
    }, 0);
    return <Text>{total.toFixed(2)}€</Text>;
  };

  const actualizarApuesta = async (id: number, acierto: Acierto) => {
    if (!id) {
      console.error("El ID de la apuesta es nulo o indefinido.");
      return;
    }
    console.log("Actualizar Apuesta - ID:", id, "Acierto:", acierto);
    try {
      await updatePick(id, { acierto });

      // Actualizamos las apuestas en el estado
      const updatedApuestas = data.apuestas.map((apuesta) =>
        apuesta.id === id ? { ...apuesta, acierto } : apuesta
      );

      // Recalcular estadísticas
      const totalAciertos = updatedApuestas.filter(
        (a) => a.acierto === "True"
      ).length;
      const porcentajeAciertos = (totalAciertos / updatedApuestas.length) * 100;

      // Actualizar el estado completo con las nuevas apuestas y estadísticas
      setData((prevData) =>
        prevData
          ? {
              ...prevData,
              apuestas: updatedApuestas,
              totalAciertos,
              porcentajeAciertos,
            }
          : prevData
      );

      setModalUpdateVisible(false); // Cerramos el modal
    } catch (error) {
      console.error("Error al actualizar la apuesta:", error);
      const message =
        error instanceof ApiError
          ? error.message
          : "No se pudo actualizar la apuesta.";
      Alert.alert("Error", message);
    }
  };

  // Función para formatear la fecha según el período
  const formatearFecha = (fecha: string | Date) => {
    const date = new Date(fecha);
    const dia = date.getDate().toString().padStart(2, "0");
    const mes = (date.getMonth() + 1).toString().padStart(2, "0");
    const año = date.getFullYear().toString().slice(-2);
    return `${dia}/${mes}/${año}`;
  };

  // Función para agrupar datos por mes si es necesario
  const agruparDatos = (apuestas: PickItem[]) => {
    if (periodoSeleccionado === "año") {
      const datosPorMes: Record<number, { ganancias: number[]; fecha: Date }> =
        {};

      // Agrupar datos por mes
      apuestas.forEach((apuesta) => {
        const fecha = new Date(apuesta.fecha);
        const mes = fecha.getMonth();
        if (!datosPorMes[mes]) {
          datosPorMes[mes] = {
            ganancias: [],
            fecha: new Date(fecha.getFullYear(), mes, 1), // Primer día del mes
          };
        }
        datosPorMes[mes].ganancias.push(
          calcularGanancia(
            apuesta.cantidadApostada,
            apuesta.cuota,
            apuesta.acierto
          )
        );
      });

      // Calcular media por mes
      return Object.entries(datosPorMes)
        .map(([mes, datos]) => ({
          Fecha: datos.fecha,
          gananciaMedia:
            datos.ganancias.reduce((a, b) => a + b, 0) / datos.ganancias.length,
        }))
        .sort((a, b) => a.Fecha.getTime() - b.Fecha.getTime());
    }

    return apuestas;
  };

  const apuestasFiltradas = () => {
    if (!apuestas) return [];

    let apuestasFiltradas = [...apuestas];
    const ahora = new Date();
    ahora.setHours(23, 59, 59, 999); // Final del día actual

    switch (periodoSeleccionado) {
      case "semana":
        const unaSemanaMenos = new Date(ahora);
        unaSemanaMenos.setDate(ahora.getDate() - 6); // 7 días incluyendo hoy
        unaSemanaMenos.setHours(0, 0, 0, 0); // Inicio del día
        apuestasFiltradas = apuestas.filter((apuesta) => {
          const fechaApuesta = new Date(apuesta.fecha);
          return fechaApuesta >= unaSemanaMenos && fechaApuesta <= ahora;
        });
        break;
      case "mes":
        const unMesMenos = new Date(ahora);
        unMesMenos.setMonth(ahora.getMonth() - 1);
        unMesMenos.setHours(0, 0, 0, 0);
        apuestasFiltradas = apuestas.filter((apuesta) => {
          const fechaApuesta = new Date(apuesta.fecha);
          return fechaApuesta >= unMesMenos && fechaApuesta <= ahora;
        });
        break;
      case "año":
        const unAñoMenos = new Date(ahora);
        unAñoMenos.setFullYear(ahora.getFullYear() - 1);
        unAñoMenos.setHours(0, 0, 0, 0);
        apuestasFiltradas = apuestas.filter((apuesta) => {
          const fechaApuesta = new Date(apuesta.fecha);
          return fechaApuesta >= unAñoMenos && fechaApuesta <= ahora;
        });
        break;
      default:
        break;
    }

    return apuestasFiltradas.sort(
      (a, b) => new Date(a.fecha).getTime() - new Date(b.fecha).getTime()
    );
  };

  const handleFechaPress = (apuesta: PickItem) => {
    setSelectedApuestaToUpdate(apuesta);
    setShowDatePicker(true);
  };

  const handleDateChange = async (event: any, selectedDate?: Date) => {
    setShowDatePicker(false);

    // Si el evento es 'dismissed' o no hay fecha seleccionada, significa que se canceló
    if (event.type === "dismissed" || !selectedDate) {
      return; // No hacemos nada si se canceló
    }

    if (selectedDate && selectedApuestaToUpdate) {
      try {
        await updatePick(selectedApuestaToUpdate.id, {
          fecha: selectedDate.toISOString(),
        });
        console.log("Fecha actualizada en el servidor");
        // Esperar a que se complete la recarga de datos
        await fetchInformanteData();
        console.log("Datos del informante recargados");
        Alert.alert("Éxito", "Fecha actualizada correctamente");
      } catch (error) {
        console.error("Error en la petición:", error);
        const message =
          error instanceof ApiError
            ? error.message
            : "Ocurrió un error al actualizar la fecha";
        Alert.alert("Error", message);
      }
    }
  };

  return (
    <View style={styles.botbarcontainer}>
      <TopBar />
      <View style={[styles.container, { paddingTop: 20 + insets.top }]}>
        <ScrollView
          contentContainerStyle={[
            styles.scrollView,
            { paddingBottom: 80 + insets.bottom },
          ]}
        >
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
                    <MaterialCommunityIcons
                      name="close"
                      size={26}
                      color="white"
                    />
                  </Text>
                </TouchableOpacity>
                <Text style={styles.modalTitle}>Actualizar Apuesta</Text>
                <Text style={styles.modalText}>¿La apuesta fue correcta?</Text>
                <View style={styles.modalButtons}>
                  <TouchableOpacity
                    style={[styles.modalButton, { backgroundColor: "#4CAF50" }]}
                    onPress={() =>
                      actualizarApuesta(selectedApuesta!.id, "True")
                    }
                  >
                    <Text style={styles.modalButtonText}>
                      <MaterialCommunityIcons
                        name="check"
                        size={26}
                        color="white"
                      />
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.modalButton, { backgroundColor: "#F44336" }]}
                    onPress={() =>
                      actualizarApuesta(selectedApuesta!.id, "False")
                    }
                  >
                    <Text style={styles.modalButtonText}>
                      <MaterialCommunityIcons
                        name="close"
                        size={32}
                        color="white"
                      />
                    </Text>
                  </TouchableOpacity>
                </View>
              </View>
            </View>
          </Modal>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Pronósticos</Text>
            <View style={styles.filterButtons}>
              <TouchableOpacity
                style={[
                  styles.filterButton,
                  periodoSeleccionado === "semana" && styles.filterButtonActive,
                ]}
                onPress={() => setPeriodoSeleccionado("semana")}
              >
                <Text
                  style={[
                    styles.filterButtonText,
                    periodoSeleccionado === "semana" &&
                      styles.filterButtonTextActive,
                  ]}
                >
                  Semana
                </Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[
                  styles.filterButton,
                  periodoSeleccionado === "mes" && styles.filterButtonActive,
                ]}
                onPress={() => setPeriodoSeleccionado("mes")}
              >
                <Text
                  style={[
                    styles.filterButtonText,
                    periodoSeleccionado === "mes" &&
                      styles.filterButtonTextActive,
                  ]}
                >
                  Mes
                </Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[
                  styles.filterButton,
                  periodoSeleccionado === "año" && styles.filterButtonActive,
                ]}
                onPress={() => setPeriodoSeleccionado("año")}
              >
                <Text
                  style={[
                    styles.filterButtonText,
                    periodoSeleccionado === "año" &&
                      styles.filterButtonTextActive,
                  ]}
                >
                  Año
                </Text>
              </TouchableOpacity>
            </View>

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

                {apuestasFiltradas().map((apuesta, index) => (
                  <View
                    key={index}
                    style={[
                      styles.tableRow,
                      index % 2 === 0 ? styles.evenRow : styles.oddRow,
                    ]}
                  >
                    <View style={[styles.tableCell, styles.border]}>
                      <TouchableOpacity
                        onPress={() => handleEliminarPress(apuesta)}
                      >
                        <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                          {apuesta.apuesta}
                        </Text>
                      </TouchableOpacity>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <Text>{renderPronostico(apuesta.acierto, apuesta)}</Text>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <TouchableOpacity
                        onPress={() => handleFechaPress(apuesta)}
                        style={styles.fechaContainer}
                      >
                        <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                          {formatearFecha(apuesta.fecha)}
                        </Text>
                        <MaterialCommunityIcons
                          name="calendar-edit"
                          size={16}
                          color="#ff9f1c"
                          style={styles.calendarIcon}
                        />
                      </TouchableOpacity>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                        {apuesta.tipoDeApuesta}
                      </Text>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                        {Number(apuesta.cuota).toFixed(2)}
                      </Text>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                        {Number(apuesta.cantidadApostada).toFixed(2)}
                        <Text>€</Text>
                      </Text>
                    </View>
                    <View style={[styles.tableCell, styles.border]}>
                      <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                        {Number(
                          calcularGanancia(
                            apuesta.cantidadApostada,
                            apuesta.cuota,
                            apuesta.acierto
                          )
                        ).toFixed(2)}
                        <Text>€</Text>
                      </Text>
                    </View>
                  </View>
                ))}
              </View>
            </ScrollView>
          </View>

          {data && data.parsedPicks.length > 0 && (
            <View style={styles.card}>
              <Text style={[styles.cardTitle, { color: "#ff9f1c" }]}>
                Picks de Telegram
              </Text>
              <View style={styles.row}>
                <Text style={styles.label}>Total Picks:</Text>
                <Text style={styles.value}>{data.parsedTotalApuestas}</Text>
              </View>
              <View style={styles.row}>
                <Text style={styles.label}>Aciertos:</Text>
                <Text style={styles.value}>{data.parsedTotalAciertos}</Text>
              </View>
              <View style={styles.row}>
                <Text style={styles.label}>Porcentaje de Aciertos:</Text>
                <Text style={styles.value}>
                  {data.parsedPorcentajeAciertos.toFixed(2)}%
                </Text>
              </View>
              <View style={styles.row}>
                <Text style={styles.label}>Ganancias (unidades):</Text>
                <Text style={styles.value}>
                  {data.parsedGanancias.toFixed(2)}u
                </Text>
              </View>
              <View style={styles.row}>
                <Text style={styles.label}>Yield:</Text>
                <Text style={styles.value}>
                  {data.parsedYieldPct.toFixed(2)}%
                </Text>
              </View>

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
                      <Text style={styles.tableHeaderText}>Mercado</Text>
                    </View>
                    <View style={[styles.tableHeaderCell, styles.border]}>
                      <Text style={styles.tableHeaderText}>Cuota</Text>
                    </View>
                    <View style={[styles.tableHeaderCell, styles.border]}>
                      <Text style={styles.tableHeaderText}>Stake</Text>
                    </View>
                    <View style={[styles.tableHeaderCell, styles.border]}>
                      <Text style={styles.tableHeaderText}>Ganancia</Text>
                    </View>
                  </View>

                  {[...data.parsedPicks]
                    .sort(
                      (a, b) =>
                        new Date(a.fecha).getTime() -
                        new Date(b.fecha).getTime()
                    )
                    .map((apuesta, index) => (
                      <View
                        key={index}
                        style={[
                          styles.tableRow,
                          index % 2 === 0 ? styles.evenRow : styles.oddRow,
                        ]}
                      >
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {apuesta.apuesta}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text>
                            {renderPronosticoTelegram(apuesta.acierto)}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {formatearFecha(apuesta.fecha)}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {apuesta.tipoDeApuesta}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {Number(apuesta.cuota).toFixed(2)}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {Number(apuesta.cantidadApostada).toFixed(2)}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {Number(
                              apuesta.ganancia ??
                                calcularGanancia(
                                  apuesta.cantidadApostada,
                                  apuesta.cuota,
                                  apuesta.acierto
                                )
                            ).toFixed(2)}
                            <Text>u</Text>
                          </Text>
                        </View>
                      </View>
                    ))}
                </View>
              </ScrollView>
            </View>
          )}
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
                  <MaterialCommunityIcons
                    name="close"
                    size={26}
                    color="white"
                  />
                </Text>
              </TouchableOpacity>

              <Text style={styles.modalTitle}>Eliminar Apuesta</Text>
              <Text style={styles.modalText}>
                ¿Estás seguro de que deseas eliminar esta apuesta?
              </Text>
              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, { backgroundColor: "#F44336" }]}
                  onPress={() => eliminarApuesta(selectedApuesta!.id)} // Eliminamos la apuesta al hacer clic en "Sí"
                >
                  <Text style={styles.modalButtonText}>
                    <MaterialCommunityIcons
                      name="trash-can-outline"
                      size={26}
                      color="white"
                    />
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>
        {showDatePicker && (
          <DateTimePicker
            value={new Date(selectedApuestaToUpdate?.fecha || new Date())}
            mode="date"
            display="spinner" // Cambiamos a spinner para más control sobre el estilo
            onChange={handleDateChange}
            {...(Platform.OS === "ios"
              ? { themeVariant: "light" as const, textColor: "#ff9f1c" }
              : {
                  positiveButton: { label: "OK", textColor: "#ff9f1c" },
                  negativeButton: { label: "Cancelar", textColor: "#ff9f1c" },
                })}
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
    justifyContent: "center",
    alignItems: "center",
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
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "rgba(0, 0, 0, 0.7)",
  },
  modalContent: {
    backgroundColor: "#303030",
    borderRadius: 15,
    padding: 20,
    width: "80%",
    alignItems: "center",
    elevation: 5,
    position: "relative",
  },
  closeButton: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: "#F44336",
    justifyContent: "center",
    alignItems: "center",
    zIndex: 1,
  },
  closeButtonText: {
    color: "white",
    fontSize: 16,
    fontWeight: "bold",
  },
  modalTitle: {
    fontSize: 24,
    fontWeight: "bold",
    color: "white",
    marginTop: 20,
    marginBottom: 15,
  },
  modalText: {
    fontSize: 18,
    color: "white",
    marginBottom: 20,
    textAlign: "center",
  },
  modalButtons: {
    flexDirection: "row",
    justifyContent: "space-around",
    width: "100%",
    marginTop: 10,
  },
  modalButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    minWidth: 100,
    alignItems: "center",
    marginHorizontal: 10,
  },
  modalButtonText: {
    color: "white",
    fontSize: 16,
    fontWeight: "bold",
  },
  botbarcontainer: {
    height: "100%",
  },
  title: {
    fontSize: 32,
    fontWeight: "bold",
    marginBottom: 30,
    marginTop: 60,
    textAlign: "center",
    color: "orange",
  },
  card: {
    backgroundColor: "rgba(33, 33, 33, 0.5)",
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
    color: "#ffba57",
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  label: {
    fontSize: 18,
    color: "white",
  },
  value: {
    fontSize: 16,
    fontWeight: "bold",
    color: "white",
  },
  table: {
    backgroundColor: "#2e2e2e",
    borderRadius: 10,
    borderColor: "white",
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
    paddingHorizontal: 15, // Añadido más espacio horizontal
    justifyContent: "center",
    alignItems: "center",
    textAlign: "center",
    backgroundColor: "#ffdaa4",
  },
  cellText: {
    fontSize: 16,
    fontWeight: "bold",
    textAlign: "center",
    color: "black",
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
    backgroundColor: "#303030",
    borderRadius: 15,
    padding: 15,
    marginVertical: 10,
    width: "100%",
    alignItems: "center",
  },
  chartScrollContainer: {
    marginTop: 10,
    width: "100%",
  },
  chartScrollContainerSmall: {
    alignSelf: "center",
  },
  chartContentCentered: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  lineChart: {
    marginVertical: 8,
    borderRadius: 16,
  },
  graphicTitle: {
    color: "white",
    fontSize: 23,
    marginBottom: 20,
    fontWeight: "bold",
  },
  filterButtons: {
    flexDirection: "row",
    justifyContent: "space-around",
    marginBottom: 30,
    paddingHorizontal: 10,
    flexWrap: "wrap",
    gap: 10,
  },
  filterButton: {
    backgroundColor: "#2e2e2e",
    paddingHorizontal: 15,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#424242",
    minWidth: 70,
    alignItems: "center",
  },
  filterButtonActive: {
    backgroundColor: "#ff9f1c",
    borderColor: "#ff9f1c",
  },
  filterButtonText: {
    color: "white",
    fontSize: 12,
    textAlign: "center",
  },
  filterButtonTextActive: {
    color: "white",
    fontWeight: "bold",
  },
  fechaContainer: {
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
  },
  calendarIcon: {
    marginLeft: 5,
  },
});
