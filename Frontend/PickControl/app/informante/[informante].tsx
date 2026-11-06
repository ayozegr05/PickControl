import React, { useEffect, useMemo, useState } from "react";
import { colors } from "@/src/theme";
import { Screen } from "@/src/components/screen";
import { ScreenTitle } from "@/src/components/typography";
import { Card } from "@/src/components/card";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  TouchableOpacity,
  Alert,
  Platform,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useLocalSearchParams } from "expo-router"; // Obtener los parámetros de búsqueda
import { useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import { ErrorState } from "@/src/components/inline-error";
import DateTimePicker, {
  DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { useInformanteStats } from "@/src/hooks/useInformanteStats";
import { deletePick, updatePick } from "@/src/api/picks.api";
import {
  updateParsedPickAcierto,
  jugarParsedPick,
  getPickOdds,
} from "@/src/api/parsed-picks.api";
import { PickOdds } from "@/src/types/parsed-pick.types";
import { ApiError } from "@/src/api/client";
import { PickItem, Acierto } from "@/src/types/pick.types";
import { gananciaApuesta } from "@/src/utils/picks";
import PickDetailModal from "@/src/components/pick-detail-modal";
import JugarModal, { JugarFormValues } from "@/src/components/jugar-modal";
import UpdatePickModal from "@/src/components/update-pick-modal";
import DeletePickModal from "@/src/components/delete-pick-modal";
import ApuestasTable from "@/src/components/apuestas-table";
import TipsterVsYouCard from "@/src/components/tipster-vs-you-card";
import TipsterPicksCard from "@/src/components/tipster-picks-card";
import { useAuth } from "@/src/context/AuthContext";

export default function InformantDetail() {
  const { informante } = useLocalSearchParams(); // Obtener el parámetro dinámico
  const { isAdmin } = useAuth();
  const { data, loading, error, reload, setData } = useInformanteStats(
    String(informante)
  );
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
  const [jugarInitial, setJugarInitial] = useState<Partial<JugarFormValues>>(
    {}
  );
  const [jugando, setJugando] = useState(false);
  const [periodoSeleccionado, setPeriodoSeleccionado] = useState("semana");

  const router = useRouter();
  const insets = useSafeAreaInsets();

  // El hook fetcha al montar y refetchea si cambia `informante`.

  // Filas de la tabla de picks extraídos (simples, por fecha) para la
  // FlatList — referencia estable mientras no cambien los datos.
  const parsedTableRows = useMemo(
    () =>
      [...(data?.parsedPicks ?? [])]
        .filter((p) => !p.esReto && !p.esCombinada)
        .sort(
          (a, b) => new Date(a.fecha).getTime() - new Date(b.fecha).getTime()
        ),
    [data?.parsedPicks]
  );

  // Retos y combinadas van en secciones propias de la card, fuera de
  // las stats de simples, ordenados de más reciente a más antiguo.
  const retosOrdenados = useMemo(
    () =>
      [...(data?.parsedPicks ?? [])]
        .filter((p) => p.esReto)
        .sort(
          (a, b) => new Date(b.fecha).getTime() - new Date(a.fecha).getTime()
        ),
    [data?.parsedPicks]
  );
  const combinadasOrdenadas = useMemo(
    () =>
      [...(data?.parsedPicks ?? [])]
        .filter((p) => p.esCombinada)
        .sort(
          (a, b) => new Date(b.fecha).getTime() - new Date(a.fecha).getTime()
        ),
    [data?.parsedPicks]
  );

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
      setApuestas(data.apuestas);
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
        <ActivityIndicator size="large" color={colors.success} />
        <Text style={styles.loadingText}>Cargando información...</Text>
      </View>
    );
  }
  // Error de red ≠ canal vacío: antes ambos decían "No se
  // encontraron datos" y un fallo era indistinguible de un canal
  // sin stats.
  if (error && !data) {
    return (
      <Screen style={styles.container} tone="black">
        <ErrorState onRetry={reload} />
      </Screen>
    );
  }
  // Si no hay datos, mostramos un mensaje de error
  if (!data) {
    return (
      <Screen style={styles.container} tone="black">
        <Text style={styles.errorText}>
          No se encontraron datos para este informante.
        </Text>
      </Screen>
    );
  }

  // Extraemos los datos de la respuesta
  const { totalApuestas, totalAciertos, porcentajeAciertos } = data;

  // Picks de Telegram: solo lectura, sin tocar el modal de edición.
  // Anulada (void) es un estado cerrado distinto de pendiente: los
  // iconos de motivo viven en `iconoAnulada` y el renderizado en
  // TipsterPicksCard.

  // Función para calcular las ganancias totales
  const calcularGananciasTotales = () => {
    if (!apuestas || apuestas.length === 0) {
      return <Text>0€</Text>;
    }
    const total = apuestas.reduce((acc, apuesta) => {
      return (
        acc +
        gananciaApuesta(
          apuesta.cantidadApostada,
          apuesta.cuota,
          apuesta.acierto
        )
      );
    }, 0);
    return <Text>{total.toFixed(2)}€</Text>;
  };

  const totalGananciasNum = apuestas.reduce(
    (acc, a) => acc + gananciaApuesta(a.cantidadApostada, a.cuota, a.acierto),
    0
  );

  const actualizarApuesta = async (id: number, acierto: Acierto) => {
    if (!id) {
      console.error("El ID de la apuesta es nulo o indefinido.");
      return;
    }
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
      await reload();
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
    setJugarInitial({
      cuota: pick.cuota ? String(Number(pick.cuota)) : "",
      casa: pick.casa || "",
    });
    setJugarModalVisible(true);
  };

  const confirmarJugarPick = async ({
    cantidad: cantidadInput,
    cuota: cuotaInput,
    casa: casaInput,
  }: JugarFormValues) => {
    if (!selectedTelegramPick) return;
    const cantidad = Number(cantidadInput.replace(",", "."));
    if (!cantidad || cantidad <= 0) {
      Alert.alert("Error", "Introduce la cantidad apostada.");
      return;
    }
    const cuota = cuotaInput ? Number(cuotaInput.replace(",", ".")) : 0;
    if (cuotaInput && (!cuota || cuota <= 1)) {
      Alert.alert("Error", "La cuota debe ser mayor que 1.");
      return;
    }
    try {
      setJugando(true);
      await jugarParsedPick(selectedTelegramPick.id, {
        cantidadApostada: cantidad,
        cuota: cuota > 1 ? cuota : undefined,
        casa: casaInput.trim() || undefined,
      });
      setJugarModalVisible(false);
      setSelectedTelegramPick(null);
      await reload();
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

  const handleDateChange = async (
    event: DateTimePickerEvent,
    selectedDate?: Date
  ) => {
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
        // Esperar a que se complete la recarga de datos
        await reload();
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
          contentContainerStyle={[styles.scrollView, { paddingBottom: 24 }]}
        >
          <ScreenTitle size={32} style={styles.title}>
            {informante}
          </ScreenTitle>

          {/* Comparativa: lo que publica el tipster vs tu resultado real */}
          <TipsterVsYouCard
            tipster={{
              yieldPct: data.parsedYieldPct,
              totalApuestas: data.parsedTotalApuestas,
              porcentajeAciertos: data.parsedPorcentajeAciertos,
              ganancias: data.parsedGanancias,
              pendientes: data.parsedPicks.filter(
                (p) => p.acierto === "Pending" && !p.anulada
              ).length,
            }}
            usuario={{
              yieldPct: data.yieldPct,
              totalApuestas,
              porcentajeAciertos,
              ganancias: totalGananciasNum,
              pendientes: apuestas.filter((a) => a.acierto === "Pending")
                .length,
            }}
          />
          <UpdatePickModal
            visible={modalUpdateVisible}
            onClose={() => setModalUpdateVisible(false)}
            onResult={(acierto) => {
              if (selectedApuesta)
                actualizarApuesta(selectedApuesta.id, acierto);
            }}
          />

          {/* Picks publicados por el tipster (extraídos de Telegram) */}
          {/* Picks publicados por el tipster (extraídos de Telegram) */}
          <TipsterPicksCard
            totalApuestas={data.parsedTotalApuestas}
            totalAciertos={data.parsedTotalAciertos}
            porcentajeAciertos={data.parsedPorcentajeAciertos}
            ganancias={data.parsedGanancias}
            yieldPct={data.parsedYieldPct}
            rows={parsedTableRows}
            retos={retosOrdenados}
            combinadas={combinadasOrdenadas}
            combinadasStats={{
              total: data.combinadasTotal,
              aciertos: data.combinadasAciertos,
              ganancias: data.combinadasGanancias,
              yieldPct: data.combinadasYieldPct,
            }}
            onCorregir={() => router.push("/parsed-picks")}
            onPickPress={setSelectedTelegramPick}
          />

          {/* Tus apuestas manuales siguiendo a este tipster */}
          <Card variant="glass">
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
              <ApuestasTable
                rows={apuestasFiltradas()}
                onPronostico={(apuesta) => {
                  setSelectedApuesta(apuesta);
                  setModalUpdateVisible(true);
                }}
                onFecha={handleFechaPress}
                onEliminar={handleEliminarPress}
              />
            )}
          </Card>
        </ScrollView>

        <DeletePickModal
          visible={modalDeleteVisible}
          onClose={() => setModalDeleteVisible(false)}
          onConfirm={() => {
            // Eliminamos la apuesta al hacer clic en "Sí"
            if (selectedApuesta) eliminarApuesta(selectedApuesta.id);
          }}
        />
        {/* Detalle de un pick de Telegram + corrección manual del resultado */}
        <PickDetailModal
          pick={selectedTelegramPick}
          odds={pickOdds}
          oddsLoading={pickOddsLoading}
          yaRegistrada={
            selectedTelegramPick !== null &&
            apuestas.some((a) => a.parsedPickId === selectedTelegramPick.id)
          }
          canCorrect={isAdmin}
          onClose={() => setSelectedTelegramPick(null)}
          onJugar={abrirModalJugar}
          onCorregir={corregirPickTelegram}
        />

        {/* "Yo también la jugué": registra la apuesta real del usuario */}
        <JugarModal
          visible={jugarModalVisible}
          loading={jugando}
          initialValues={jugarInitial}
          onClose={() => setJugarModalVisible(false)}
          onSubmit={confirmarJugarPick}
        />
        {showDatePicker && (
          <DateTimePicker
            value={new Date(selectedApuestaToUpdate?.fecha || new Date())}
            mode="date"
            display="spinner" // Cambiamos a spinner para más control sobre el estilo
            onChange={handleDateChange}
            {...(Platform.OS === "ios"
              ? { themeVariant: "light" as const, textColor: colors.primary }
              : {
                  positiveButton: { label: "OK", textColor: colors.primary },
                  negativeButton: {
                    label: "Cancelar",
                    textColor: colors.primary,
                  },
                })}
          />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: 20,
  },
  scrollView: {
    paddingBottom: 24, // Espacio suficiente para que BottomBar no se superponga
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: colors.black,
  },
  errorText: {
    fontSize: 18,
    color: colors.primarySoft,
    textAlign: "center",
    marginVertical: 20,
  },
  loadingText: {
    fontSize: 18,
    color: colors.success,
    textAlign: "center",
    marginVertical: 20,
  },
  botbarcontainer: {
    height: "100%",
  },
  title: {
    marginBottom: 30,
    marginTop: 60,
    textAlign: "center",
  },
  cardTitle: {
    fontSize: 25,
    fontWeight: "bold",
    marginBottom: 30,
    textAlign: "center",
    color: colors.primarySoft,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  label: {
    fontSize: 18,
    color: colors.text,
  },
  value: {
    fontSize: 16,
    fontWeight: "bold",
    color: colors.text,
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
    backgroundColor: colors.surface,
    paddingHorizontal: 15,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.border,
    minWidth: 70,
    alignItems: "center",
  },
  filterButtonActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  filterButtonText: {
    color: colors.text,
    fontSize: 12,
    textAlign: "center",
  },
  filterButtonTextActive: {
    color: colors.onPrimary,
    fontWeight: "bold",
  },
  cardSubtitle: {
    fontSize: 13,
    color: colors.textSecondary,
    textAlign: "center",
    marginTop: -20,
    marginBottom: 15,
  },
  emptyText: {
    fontSize: 15,
    color: colors.textSecondary,
    textAlign: "center",
    marginVertical: 15,
    fontStyle: "italic",
  },
});
