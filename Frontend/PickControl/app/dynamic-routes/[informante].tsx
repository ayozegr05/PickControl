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
  TextInput,
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
import {
  updateParsedPickAcierto,
  jugarParsedPick,
  getPickOdds,
  PickOdds,
} from "@/src/api/parsed-picks.api";
import { ApiError } from "@/src/api/client";
import { InformanteStats } from "@/src/types/informante.types";
import { PickItem, Acierto } from "@/src/types/pick.types";

function colorForYield(value: number): string {
  if (value > 0) return "#4caf50";
  if (value < 0) return "#f44336";
  return "#aaa";
}

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
  const [selectedTelegramPick, setSelectedTelegramPick] =
    useState<PickItem | null>(null);
  // Auditoría de cuotas del pick abierto en el modal (tipster vs mercado).
  const [pickOdds, setPickOdds] = useState<PickOdds | null>(null);
  const [pickOddsLoading, setPickOddsLoading] = useState(false);
  // "Yo también la jugué": mini-formulario para registrar la apuesta real
  // del usuario (cantidad, cuota y casa conseguidas pueden diferir de las
  // publicadas por el tipster).
  const [jugarModalVisible, setJugarModalVisible] = useState(false);
  const [jugarCantidad, setJugarCantidad] = useState("");
  const [jugarCuota, setJugarCuota] = useState("");
  const [jugarCasa, setJugarCasa] = useState("");
  const [jugando, setJugando] = useState(false);
  const [periodoSeleccionado, setPeriodoSeleccionado] = useState("semana");
  // Combinadas expandidas: id del padre -> true si se muestran sus patas.
  const [combinadasAbiertas, setCombinadasAbiertas] = useState<
    Record<number, boolean>
  >({});

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

  // Al abrir el detalle de un pick de Telegram, pide su comparación de
  // cuotas (tipster vs mercado) para la sección "Cuota de mercado".
  useEffect(() => {
    setPickOdds(null);
    if (!selectedTelegramPick) return;
    let cancelled = false;
    setPickOddsLoading(true);
    getPickOdds(selectedTelegramPick.id)
      .then((odds) => {
        if (!cancelled) setPickOdds(odds);
      })
      .catch((err) => console.error("Error al obtener cuotas:", err))
      .finally(() => {
        if (!cancelled) setPickOddsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTelegramPick]);

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
    cantidadApostada: number | null,
    cuota: number | null,
    acierto: Acierto
  ) => {
    if (!apuestas || apuestas.length === 0) {
      return 0; // Devolvemos número en lugar de string
    }
    if (acierto === "Pending" || cantidadApostada == null || cuota == null) {
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
  // Anulada (void) es un estado cerrado distinto de pendiente: sin la
  // flag la pintábamos con "❓" igual que una no verificada.
  const renderPronosticoTelegram = (acierto: Acierto, anulada?: boolean) => {
    if (anulada) {
      return <Text style={[styles.icon, { color: "gray" }]}>↩️</Text>;
    }
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

  const totalGananciasNum = apuestas.reduce(
    (acc, a) => acc + calcularGanancia(a.cantidadApostada, a.cuota, a.acierto),
    0
  );

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

  // Corrección del resultado de un pick de Telegram desde el detalle.
  const corregirPickTelegram = async (
    pick: PickItem,
    update: { acierto?: boolean | null; anulada?: boolean }
  ) => {
    try {
      await updateParsedPickAcierto(pick.id, update);
      setSelectedTelegramPick(null);
      await fetchInformanteData();
    } catch (error) {
      console.error("Error al corregir el pick:", error);
      const message =
        error instanceof ApiError
          ? error.message
          : "No se pudo corregir el pick.";
      Alert.alert("Error", message);
    }
  };

  const handleFechaPress = (apuesta: PickItem) => {
    setSelectedApuestaToUpdate(apuesta);
    setShowDatePicker(true);
  };

  // "Yo también la jugué": abre el formulario pre-rellenado con los
  // datos del pick del tipster (cuota/stake/casa), editables porque la
  // cuota real conseguida puede ser distinta de la publicada.
  const abrirModalJugar = (pick: PickItem) => {
    setJugarCantidad("");
    setJugarCuota(pick.cuota ? String(Number(pick.cuota)) : "");
    setJugarCasa(pick.casa || "");
    setJugarModalVisible(true);
  };

  const confirmarJugarPick = async () => {
    if (!selectedTelegramPick) return;
    const cantidad = Number(jugarCantidad.replace(",", "."));
    if (!cantidad || cantidad <= 0) {
      Alert.alert("Error", "Introduce la cantidad apostada.");
      return;
    }
    const cuota = jugarCuota ? Number(jugarCuota.replace(",", ".")) : 0;
    if (jugarCuota && (!cuota || cuota <= 1)) {
      Alert.alert("Error", "La cuota debe ser mayor que 1.");
      return;
    }
    try {
      setJugando(true);
      await jugarParsedPick(selectedTelegramPick.id, {
        cantidadApostada: cantidad,
        cuota: cuota > 1 ? cuota : undefined,
        casa: jugarCasa.trim() || undefined,
      });
      setJugarModalVisible(false);
      setSelectedTelegramPick(null);
      await fetchInformanteData();
      Alert.alert(
        "Apuesta registrada",
        "Se ha añadido a tus apuestas de este tipster."
      );
    } catch (error) {
      console.error("Error al registrar la apuesta:", error);
      const message =
        error instanceof ApiError
          ? error.message
          : "No se pudo registrar la apuesta.";
      Alert.alert("Error", message);
    } finally {
      setJugando(false);
    }
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

          {/* Comparativa: lo que publica el tipster vs tu resultado real */}
          <View style={styles.card}>
            <Text style={[styles.cardTitle, { marginBottom: 12 }]}>
              Tipster vs. tú
            </Text>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel} />
              <Text style={styles.compareColTitle}>El tipster</Text>
              <Text style={styles.compareColTitle}>Tú</Text>
            </View>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel}>Yield</Text>
              <Text
                style={[
                  styles.compareValue,
                  { color: colorForYield(data.parsedYieldPct) },
                ]}
              >
                {data.parsedTotalApuestas > 0
                  ? `${data.parsedYieldPct.toFixed(2)}%`
                  : "—"}
              </Text>
              <Text
                style={[
                  styles.compareValue,
                  { color: colorForYield(data.yieldPct) },
                ]}
              >
                {totalApuestas > 0 ? `${data.yieldPct.toFixed(2)}%` : "—"}
              </Text>
            </View>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel}>% acierto</Text>
              <Text style={styles.compareValue}>
                {data.parsedTotalApuestas > 0
                  ? `${data.parsedPorcentajeAciertos.toFixed(1)}%`
                  : "—"}
              </Text>
              <Text style={styles.compareValue}>
                {totalApuestas > 0 ? `${porcentajeAciertos.toFixed(1)}%` : "—"}
              </Text>
            </View>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel}>Picks</Text>
              <Text style={styles.compareValue}>
                {data.parsedTotalApuestas}
              </Text>
              <Text style={styles.compareValue}>{totalApuestas}</Text>
            </View>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel}>Pendientes</Text>
              <Text style={styles.compareValue}>
                {
                  data.parsedPicks.filter(
                    (p) => p.acierto === "Pending" && !p.anulada
                  ).length
                }
              </Text>
              <Text style={styles.compareValue}>
                {apuestas.filter((a) => a.acierto === "Pending").length}
              </Text>
            </View>
            <View style={styles.compareRow}>
              <Text style={styles.compareLabel}>Ganancia</Text>
              <Text
                style={[
                  styles.compareValue,
                  { color: colorForYield(data.parsedGanancias) },
                ]}
              >
                {data.parsedTotalApuestas > 0
                  ? `${data.parsedGanancias.toFixed(2)}u`
                  : "—"}
              </Text>
              <Text
                style={[
                  styles.compareValue,
                  { color: colorForYield(totalGananciasNum) },
                ]}
              >
                {totalApuestas > 0 ? `${totalGananciasNum.toFixed(2)}€` : "—"}
              </Text>
            </View>
            <Text style={styles.compareNote}>
              El tipster: unidades (u) según su stake · Tú: euros que has
              apostado
            </Text>
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

          {/* Picks publicados por el tipster (extraídos de Telegram) */}
          <View style={styles.card}>
            <Text style={[styles.cardTitle, { color: "#ff9f1c" }]}>
              Picks del tipster
            </Text>
            <Text style={styles.cardSubtitle}>
              Lo que ha publicado en su canal, verificado automáticamente
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

            <TouchableOpacity
              style={styles.telegramButton}
              onPress={() => router.push("/screens/parsed-picks")}
            >
              <Text style={styles.telegramButtonText}>
                Corregir picks de Telegram
              </Text>
            </TouchableOpacity>

            {data.parsedPicks.length === 0 ? (
              <Text style={styles.emptyText}>
                Todavía no hay picks extraídos de este canal.
              </Text>
            ) : (
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
                    .filter((p) => !p.esReto && !p.esCombinada)
                    .sort(
                      (a, b) =>
                        new Date(a.fecha).getTime() -
                        new Date(b.fecha).getTime()
                    )
                    .map((apuesta, index) => (
                      <TouchableOpacity
                        key={apuesta.id}
                        onPress={() => setSelectedTelegramPick(apuesta)}
                        style={[
                          styles.tableRow,
                          index % 2 === 0 ? styles.evenRow : styles.oddRow,
                        ]}
                      >
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {apuesta.apuesta || "(sin nombre)"}
                          </Text>
                          {apuesta.evento ? (
                            <Text style={styles.eventoText}>
                              {apuesta.evento}
                            </Text>
                          ) : null}
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text>
                            {renderPronosticoTelegram(
                              apuesta.acierto,
                              apuesta.anulada
                            )}
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
                            {apuesta.cuota != null
                              ? Number(apuesta.cuota).toFixed(2)
                              : "—"}
                          </Text>
                        </View>
                        <View style={[styles.tableCell, styles.border]}>
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
                            {apuesta.cantidadApostada != null
                              ? Number(apuesta.cantidadApostada).toFixed(2)
                              : "—"}
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
                      </TouchableOpacity>
                    ))}
                </View>
              </ScrollView>
            )}

            {/* Retos del tipster: van aparte de las apuestas diarias */}
            {data.parsedPicks.some((p) => p.esReto) && (
              <View style={styles.retosSection}>
                <Text style={styles.retosTitle}>Retos</Text>
                {data.parsedPicks
                  .filter((p) => p.esReto)
                  .sort(
                    (a, b) =>
                      new Date(b.fecha).getTime() - new Date(a.fecha).getTime()
                  )
                  .map((reto) => (
                    <TouchableOpacity
                      key={reto.id}
                      onPress={() => setSelectedTelegramPick(reto)}
                      style={styles.retoRow}
                    >
                      <Text style={styles.retoText}>
                        {reto.apuesta || "(sin nombre)"}
                        {reto.evento ? ` · ${reto.evento}` : ""}
                      </Text>
                      <Text style={styles.retoMeta}>
                        {formatearFecha(reto.fecha)} · cuota{" "}
                        {reto.cuota != null
                          ? Number(reto.cuota).toFixed(2)
                          : "—"}{" "}
                        · {renderPronosticoTelegram(reto.acierto, reto.anulada)}
                      </Text>
                    </TouchableOpacity>
                  ))}
              </View>
            )}

            {/* Combinadas del tipster: sección propia, fuera de las
                stats de simples. Cada padre se expande para ver las
                patas con su resultado individual. */}
            {data.parsedPicks.some((p) => p.esCombinada) && (
              <View style={styles.retosSection}>
                <Text style={styles.retosTitle}>Combinadas</Text>
                {data.combinadasTotal > 0 && (
                  <Text style={styles.retoMeta}>
                    {data.combinadasTotal} combinadas ·{" "}
                    {data.combinadasAciertos} aciertos ·{" "}
                    {data.combinadasGanancias.toFixed(2)}u · yield{" "}
                    {data.combinadasYieldPct.toFixed(2)}%
                  </Text>
                )}
                {data.parsedPicks
                  .filter((p) => p.esCombinada)
                  .sort(
                    (a, b) =>
                      new Date(b.fecha).getTime() - new Date(a.fecha).getTime()
                  )
                  .map((comb) => {
                    const abierta = !!combinadasAbiertas[comb.id];
                    const numPatas = comb.patas?.length ?? 0;
                    return (
                      <View key={comb.id} style={styles.retoRow}>
                        <View style={styles.combinadaHeader}>
                          <TouchableOpacity
                            style={{ flex: 1 }}
                            onPress={() => setSelectedTelegramPick(comb)}
                          >
                            <Text style={styles.retoText}>
                              {comb.apuesta || "(combinada)"}
                              {comb.evento ? ` · ${comb.evento}` : ""}
                            </Text>
                            <Text style={styles.retoMeta}>
                              {formatearFecha(comb.fecha)} · cuota{" "}
                              {Number(
                                comb.cuotaEfectiva ?? comb.cuota
                              ).toFixed(2)}
                              {comb.cuotaEfectiva != null &&
                              comb.cuotaEfectiva !== comb.cuota
                                ? ` (declarada ${Number(comb.cuota).toFixed(2)})`
                                : ""}
                              {" · "}
                              {numPatas} patas ·{" "}
                              {renderPronosticoTelegram(
                                comb.acierto,
                                comb.anulada
                              )}
                            </Text>
                          </TouchableOpacity>
                          {numPatas > 0 && (
                            <TouchableOpacity
                              style={styles.combinadaChevron}
                              onPress={() =>
                                setCombinadasAbiertas((prev) => ({
                                  ...prev,
                                  [comb.id]: !abierta,
                                }))
                              }
                            >
                              <MaterialCommunityIcons
                                name={abierta ? "chevron-up" : "chevron-down"}
                                size={22}
                                color="#b388ff"
                              />
                            </TouchableOpacity>
                          )}
                        </View>
                        {abierta &&
                          (comb.patas ?? []).map((pata) => (
                            <View key={pata.id} style={styles.pataRow}>
                              <Text style={styles.pataIcono}>
                                {pata.anulada
                                  ? "↩️"
                                  : pata.acierto === true
                                    ? "✅"
                                    : pata.acierto === false
                                      ? "❌"
                                      : "❓"}
                              </Text>
                              <View style={{ flex: 1 }}>
                                <Text style={styles.pataText}>
                                  {pata.seleccion || "(pata)"}
                                </Text>
                                <Text style={styles.pataMeta}>
                                  {[
                                    pata.evento,
                                    pata.mercado,
                                    pata.anulada ? "anulada" : null,
                                  ]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </Text>
                              </View>
                            </View>
                          ))}
                      </View>
                    );
                  })}
              </View>
            )}
          </View>

          {/* Tus apuestas manuales siguiendo a este tipster */}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Mis apuestas siguiéndolo</Text>
            <Text style={styles.cardSubtitle}>
              Las que has registrado manualmente
            </Text>
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

            {apuestas.length === 0 ? (
              <Text style={styles.emptyText}>
                Aún no has registrado apuestas siguiendo a este tipster.
              </Text>
            ) : apuestasFiltradas().length === 0 ? (
              <Text style={styles.emptyText}>
                Sin apuestas en el período seleccionado.
              </Text>
            ) : (
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
                      <Text style={styles.tableHeaderText}>
                        Tipo de Apuesta
                      </Text>
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
                    <View style={[styles.tableHeaderCell, styles.border]}>
                      <Text style={styles.tableHeaderText}>Acciones</Text>
                    </View>
                  </View>

                  {apuestasFiltradas().map((apuesta, index) => (
                    <View
                      key={apuesta.id}
                      style={[
                        styles.tableRow,
                        index % 2 === 0 ? styles.evenRow : styles.oddRow,
                      ]}
                    >
                      <View style={[styles.tableCell, styles.border]}>
                        <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                          {apuesta.apuesta || "(sin nombre)"}
                        </Text>
                      </View>
                      <View style={[styles.tableCell, styles.border]}>
                        <Text>
                          {renderPronostico(apuesta.acierto, apuesta)}
                        </Text>
                      </View>
                      <View style={[styles.tableCell, styles.border]}>
                        <TouchableOpacity
                          onPress={() => handleFechaPress(apuesta)}
                          style={styles.fechaContainer}
                        >
                          <Text
                            style={[styles.cellText, { fontWeight: "bold" }]}
                          >
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
                          {apuesta.cuota != null
                            ? Number(apuesta.cuota).toFixed(2)
                            : "—"}
                        </Text>
                      </View>
                      <View style={[styles.tableCell, styles.border]}>
                        <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                          {apuesta.cantidadApostada != null
                            ? Number(apuesta.cantidadApostada).toFixed(2)
                            : "—"}
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
                      <View style={[styles.tableCell, styles.border]}>
                        <TouchableOpacity
                          onPress={() => handleEliminarPress(apuesta)}
                          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                        >
                          <MaterialCommunityIcons
                            name="trash-can"
                            size={22}
                            color="#f44336"
                          />
                        </TouchableOpacity>
                      </View>
                    </View>
                  ))}
                </View>
              </ScrollView>
            )}
          </View>
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
        {/* Detalle de un pick de Telegram + corrección manual del resultado */}
        <Modal
          animationType="slide"
          transparent={true}
          visible={selectedTelegramPick !== null}
          onRequestClose={() => setSelectedTelegramPick(null)}
        >
          <View style={styles.modalContainer}>
            <View style={styles.modalContent}>
              <TouchableOpacity
                style={styles.closeButton}
                onPress={() => setSelectedTelegramPick(null)}
              >
                <Text style={styles.closeButtonText}>
                  <MaterialCommunityIcons
                    name="close"
                    size={26}
                    color="white"
                  />
                </Text>
              </TouchableOpacity>

              {selectedTelegramPick && (
                <>
                  <Text style={styles.modalTitle}>
                    {selectedTelegramPick.apuesta || "(sin nombre)"}
                  </Text>
                  <View style={styles.detailRows}>
                    <Text style={styles.detailRow}>
                      Resultado:{" "}
                      {selectedTelegramPick.acierto === "True"
                        ? "Acertó ✅"
                        : selectedTelegramPick.acierto === "False"
                          ? "Falló ❌"
                          : "Pendiente ❓"}
                    </Text>
                    {selectedTelegramPick.evento ? (
                      <Text style={styles.detailRow}>
                        Evento: {selectedTelegramPick.evento}
                      </Text>
                    ) : null}
                    <Text style={styles.detailRow}>
                      Mercado: {selectedTelegramPick.tipoDeApuesta || "-"}
                    </Text>
                    <Text style={styles.detailRow}>
                      Cuota:{" "}
                      {selectedTelegramPick.cuota != null
                        ? Number(selectedTelegramPick.cuota).toFixed(2)
                        : "—"}{" "}
                      · Stake:{" "}
                      {selectedTelegramPick.cantidadApostada != null
                        ? Number(selectedTelegramPick.cantidadApostada).toFixed(
                            2
                          )
                        : "—"}
                      u
                    </Text>
                    <Text style={styles.detailRow}>
                      Fecha: {formatearFecha(selectedTelegramPick.fecha)}
                    </Text>
                    {selectedTelegramPick.casa ? (
                      <Text style={styles.detailRow}>
                        Casa: {selectedTelegramPick.casa}
                      </Text>
                    ) : null}
                  </View>

                  {pickOddsLoading ? (
                    <ActivityIndicator
                      size="small"
                      color="#ff9f1c"
                      style={{ marginVertical: 8 }}
                    />
                  ) : pickOdds ? (
                    <View style={styles.oddsBox}>
                      <Text style={styles.oddsTitle}>Cuota de mercado</Text>
                      {pickOdds.mapeado ? (
                        <>
                          <Text style={styles.oddsRow}>
                            {pickOdds.mercado_api}
                            {pickOdds.opcion_api
                              ? ` · ${pickOdds.opcion_api}`
                              : ""}
                            {pickOdds.linea_api
                              ? ` (${pickOdds.linea_api})`
                              : ""}
                          </Text>
                          <Text style={styles.oddsRow}>
                            Tipster{" "}
                            {pickOdds.cuota_tipster?.toFixed(2) ?? "-"} ·
                            Apertura{" "}
                            {pickOdds.cuota_apertura?.toFixed(2) ?? "-"} · Al
                            publicar{" "}
                            {pickOdds.cuota_publicacion?.toFixed(2) ?? "-"} ·
                            Cierre {pickOdds.cuota_cierre?.toFixed(2) ?? "-"}
                          </Text>
                          <View style={styles.oddsBadges}>
                            {pickOdds.cuota_disponible === false && (
                              <Text style={styles.oddsBadgeInflada}>
                                ⚠ Cuota inflada
                              </Text>
                            )}
                            {pickOdds.cuota_disponible === true && (
                              <Text style={styles.oddsBadgeOk}>
                                ✓ Cuota real
                              </Text>
                            )}
                            {pickOdds.clv_pct !== null && (
                              <Text
                                style={
                                  pickOdds.clv_pct >= 0
                                    ? styles.oddsBadgeOk
                                    : styles.oddsBadgeInflada
                                }
                              >
                                CLV {pickOdds.clv_pct > 0 ? "+" : ""}
                                {pickOdds.clv_pct.toFixed(1)}%
                              </Text>
                            )}
                          </View>
                        </>
                      ) : (
                        <Text style={styles.oddsRowMuted}>
                          Sin mercado comparable en el proveedor
                        </Text>
                      )}
                    </View>
                  ) : null}

                  {apuestas.some(
                    (a) => a.parsedPickId === selectedTelegramPick.id
                  ) ? (
                    <Text style={styles.jugadaText}>
                      ✓ Ya la tienes registrada en tus apuestas
                    </Text>
                  ) : (
                    <TouchableOpacity
                      style={styles.jugarButton}
                      onPress={() => abrirModalJugar(selectedTelegramPick)}
                    >
                      <Text style={styles.jugarButtonText}>
                        Yo también la jugué
                      </Text>
                    </TouchableOpacity>
                  )}

                  <Text style={styles.modalText}>Corregir resultado:</Text>
                  <View style={styles.detailButtons}>
                    <TouchableOpacity
                      style={[
                        styles.detailButton,
                        { backgroundColor: "#1e3a24" },
                      ]}
                      onPress={() =>
                        corregirPickTelegram(selectedTelegramPick, {
                          acierto: true,
                          anulada: false,
                        })
                      }
                    >
                      <Text style={styles.detailButtonText}>✅ Acertó</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[
                        styles.detailButton,
                        { backgroundColor: "#3a1e1e" },
                      ]}
                      onPress={() =>
                        corregirPickTelegram(selectedTelegramPick, {
                          acierto: false,
                          anulada: false,
                        })
                      }
                    >
                      <Text style={styles.detailButtonText}>❌ Falló</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[
                        styles.detailButton,
                        { backgroundColor: "#2a2a2a" },
                      ]}
                      onPress={() =>
                        corregirPickTelegram(selectedTelegramPick, {
                          anulada: true,
                        })
                      }
                    >
                      <Text style={styles.detailButtonText}>Anulada</Text>
                    </TouchableOpacity>
                  </View>
                </>
              )}
            </View>
          </View>
        </Modal>

        {/* "Yo también la jugué": registra la apuesta real del usuario */}
        <Modal
          animationType="slide"
          transparent={true}
          visible={jugarModalVisible}
          onRequestClose={() => setJugarModalVisible(false)}
        >
          <View style={styles.modalContainer}>
            <View style={styles.modalContent}>
              <TouchableOpacity
                style={styles.closeButton}
                onPress={() => setJugarModalVisible(false)}
              >
                <Text style={styles.closeButtonText}>
                  <MaterialCommunityIcons
                    name="close"
                    size={26}
                    color="white"
                  />
                </Text>
              </TouchableOpacity>

              <Text style={styles.modalTitle}>Yo también la jugué</Text>
              <Text style={styles.jugarHint}>
                Cantidad, cuota y casa reales — pueden diferir de las del
                tipster y así se compara el yield publicado con el tuyo.
              </Text>

              <TextInput
                style={styles.jugarInput}
                placeholder="Cantidad apostada (obligatorio)"
                placeholderTextColor="#888"
                keyboardType="numeric"
                value={jugarCantidad}
                onChangeText={setJugarCantidad}
              />
              <TextInput
                style={styles.jugarInput}
                placeholder="Cuota conseguida (opcional)"
                placeholderTextColor="#888"
                keyboardType="numeric"
                value={jugarCuota}
                onChangeText={setJugarCuota}
              />
              <TextInput
                style={styles.jugarInput}
                placeholder="Casa (opcional)"
                placeholderTextColor="#888"
                value={jugarCasa}
                onChangeText={setJugarCasa}
              />

              <TouchableOpacity
                style={[styles.jugarButton, jugando && { opacity: 0.6 }]}
                onPress={confirmarJugarPick}
                disabled={jugando}
              >
                {jugando ? (
                  <ActivityIndicator size="small" color="white" />
                ) : (
                  <Text style={styles.jugarButtonText}>
                    Registrar apuesta
                  </Text>
                )}
              </TouchableOpacity>
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
  detailRows: {
    alignSelf: "stretch",
    marginBottom: 10,
  },
  eventoText: {
    fontSize: 11,
    color: "#999",
    marginTop: 2,
  },
  detailRow: {
    fontSize: 15,
    color: "#ddd",
    marginBottom: 6,
  },
  oddsBox: {
    alignSelf: "stretch",
    backgroundColor: "#141414",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#333",
    padding: 10,
    marginBottom: 10,
  },
  oddsTitle: {
    fontSize: 12,
    color: "#ff9f1c",
    fontWeight: "bold",
    textTransform: "uppercase",
    marginBottom: 6,
  },
  oddsRow: {
    fontSize: 13,
    color: "#ddd",
    marginBottom: 3,
  },
  oddsRowMuted: {
    fontSize: 13,
    color: "#888",
  },
  oddsBadges: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4,
  },
  oddsBadgeOk: {
    fontSize: 12,
    color: "#4caf50",
    fontWeight: "bold",
  },
  oddsBadgeInflada: {
    fontSize: 12,
    color: "#f44336",
    fontWeight: "bold",
  },
  detailButtons: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
  },
  detailButton: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: "#555",
  },
  detailButtonText: {
    color: "white",
    fontSize: 13,
    fontWeight: "bold",
  },
  jugarButton: {
    backgroundColor: "#ff9f1c",
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 8,
    marginTop: 6,
    marginBottom: 4,
    alignSelf: "center",
  },
  jugarButtonText: {
    color: "#0d0d0d",
    fontSize: 15,
    fontWeight: "bold",
  },
  jugadaText: {
    color: "#4caf50",
    fontSize: 14,
    fontWeight: "bold",
    marginTop: 6,
    marginBottom: 4,
    alignSelf: "center",
  },
  jugarHint: {
    color: "#aaa",
    fontSize: 12,
    textAlign: "center",
    marginBottom: 12,
  },
  jugarInput: {
    alignSelf: "stretch",
    height: 44,
    backgroundColor: "white",
    borderRadius: 6,
    paddingHorizontal: 10,
    marginVertical: 6,
    fontSize: 15,
    color: "#000",
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
  telegramButton: {
    backgroundColor: "#ff9f1c",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    alignSelf: "center",
    marginVertical: 12,
    alignItems: "center",
  },
  telegramButtonText: {
    color: "#1a1a1a",
    fontWeight: "bold",
    fontSize: 14,
  },
  cardSubtitle: {
    fontSize: 13,
    color: "#999",
    textAlign: "center",
    marginTop: -20,
    marginBottom: 15,
  },
  emptyText: {
    fontSize: 15,
    color: "#aaa",
    textAlign: "center",
    marginVertical: 15,
    fontStyle: "italic",
  },
  compareRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: "#3a3a3a",
  },
  compareLabel: {
    flex: 1.2,
    fontSize: 15,
    color: "#ccc",
  },
  compareColTitle: {
    flex: 1,
    fontSize: 14,
    fontWeight: "bold",
    color: "#ff9f1c",
    textAlign: "center",
  },
  compareValue: {
    flex: 1,
    fontSize: 15,
    fontWeight: "bold",
    color: "white",
    textAlign: "center",
  },
  compareNote: {
    fontSize: 11,
    color: "#777",
    textAlign: "center",
    marginTop: 10,
    fontStyle: "italic",
  },
  retosSection: {
    marginTop: 16,
    borderTopWidth: 1,
    borderTopColor: "#333",
    paddingTop: 12,
  },
  retosTitle: {
    color: "#b388ff",
    fontSize: 16,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  retoRow: {
    backgroundColor: "#1a1425",
    borderColor: "#3a2d55",
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
  },
  retoText: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "bold",
  },
  retoMeta: {
    color: "#aaa",
    fontSize: 12,
    marginTop: 4,
  },
  combinadaHeader: {
    flexDirection: "row",
    alignItems: "center",
  },
  combinadaChevron: {
    padding: 4,
    marginLeft: 8,
  },
  pataRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: "#2a2140",
    paddingLeft: 8,
  },
  pataIcono: {
    fontSize: 14,
    marginRight: 8,
  },
  pataText: {
    color: "#e8e0f5",
    fontSize: 13,
  },
  pataMeta: {
    color: "#888",
    fontSize: 11,
    marginTop: 2,
  },
});
