import React, { useState, useEffect, useRef } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import { MaterialIcons } from "@expo/vector-icons";
import LottieView from "lottie-react-native";
import { usePicks } from "@/src/hooks/usePicks";
import {
  EstadisticaInput,
  calcularEstadisticas as statsDeApuestas,
  calcularProyeccion as proyeccionDe,
  nivelRiesgo,
} from "@/src/utils/picks";
import { colors } from "@/src/theme";

interface Apuesta {
  Fecha: string;
  CantidadApostada: number;
  Cuota: number;
  Acierto: boolean;
  Informante: string;
}

export default function Analysis() {
  const [apuestas, setApuestas] = useState<Apuesta[]>([]);
  const [informantes, setInformantes] = useState<string[]>([]);
  const [selectedInformante, setSelectedInformante] = useState<string>("");
  const [inversionDiaria, setInversionDiaria] = useState("10");
  const [apuestasPorDia, setApuestasPorDia] = useState("1");
  const [pendientes, setPendientes] = useState(0);
  const loadingRef = useRef(null);

  const router = useRouter();
  const insets = useSafeAreaInsets();

  // Fetch centralizado: loading/error vienen del hook.
  const { apuestas: picks, loading, error, reload } = usePicks();
  const loadError = error !== null;

  // Derivar Apuesta[] (solo liquidadas con cuota/cantidad) + listas
  // auxiliares cuando llega la lista cruda.
  useEffect(() => {
    const apuestasPorInformante: Record<string, Apuesta[]> = {};
    picks.forEach((pick) => {
      // Solo incluir apuestas que no estén pendientes y tengan
      // cuota/cantidad (en picks de Telegram pueden ser null: no se
      // inventan valores para el cálculo de ROI).
      if (
        pick.acierto !== "Pending" &&
        pick.cantidadApostada != null &&
        pick.cuota != null
      ) {
        if (!apuestasPorInformante[pick.informante]) {
          apuestasPorInformante[pick.informante] = [];
        }
        apuestasPorInformante[pick.informante].push({
          Acierto: pick.acierto === "True",
          CantidadApostada: pick.cantidadApostada,
          Cuota: pick.cuota,
          Fecha: pick.fecha,
          Informante: pick.informante,
        });
      }
    });

    setInformantes(Object.keys(apuestasPorInformante));
    setPendientes(picks.filter((pick) => pick.acierto === "Pending").length);
    setApuestas(Object.values(apuestasPorInformante).flat());
  }, [picks]);

  // Adaptador: la pantalla usa `Apuesta` (campos en mayúscula, solo
  // liquidadas); los utils operan con `EstadisticaInput` (camelCase).
  const toInput = (a: Apuesta): EstadisticaInput => ({
    cantidad: a.CantidadApostada,
    cuota: a.Cuota,
    acierto: a.Acierto,
    fecha: a.Fecha,
  });

  const calcularEstadisticas = (informante: string) =>
    statsDeApuestas(
      apuestas.filter((a) => a.Informante === informante).map(toInput)
    );

  const calcularProyeccion = (informante: string) =>
    proyeccionDe(
      apuestas.filter((a) => a.Informante === informante).map(toInput),
      Number(inversionDiaria) || 10,
      Number(apuestasPorDia) || 1
    );

  return (
    <View
      style={[
        styles.container,
        { paddingTop: 60 + insets.top, paddingBottom: 60 + insets.bottom },
      ]}
    >
      <TopBar />
      <ScrollView style={styles.scrollView}>
        <Text style={styles.title}>Análisis de Rentabilidad</Text>

        {/* Selector de inversión diaria */}
        <View style={styles.inputContainer}>
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Inversión por apuesta (€):</Text>
            <TextInput
              style={styles.input}
              value={inversionDiaria}
              onChangeText={setInversionDiaria}
              keyboardType="numeric"
              placeholder="10"
              placeholderTextColor="#666"
            />
          </View>
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Apuestas por día:</Text>
            <TextInput
              style={styles.input}
              value={apuestasPorDia}
              onChangeText={setApuestasPorDia}
              keyboardType="numeric"
              placeholder="1"
              placeholderTextColor="#666"
            />
          </View>
        </View>

        {loading ? (
          <View style={styles.loadingContainer}>
            <Text style={styles.loadingText}>Cargando datos...</Text>
            <LottieView
              ref={loadingRef}
              source={require("../assets/animations/loading.json")}
              style={styles.loadingAnimation}
              autoPlay
              loop
            />
          </View>
        ) : loadError ? (
          <View style={styles.errorContainer}>
            <Text style={styles.errorText}>
              No se pudieron cargar los datos
            </Text>
            <TouchableOpacity style={styles.retryButton} onPress={reload}>
              <Text style={styles.retryButtonText}>Reintentar</Text>
            </TouchableOpacity>
          </View>
        ) : informantes.length === 0 ? (
          <View style={styles.errorContainer}>
            {pendientes > 0 ? (
              <>
                <Text style={styles.emptyText}>
                  Tienes {pendientes} apuesta{pendientes !== 1 ? "s" : ""}{" "}
                  pendiente{pendientes !== 1 ? "s" : ""} de liquidación
                </Text>
                <Text style={styles.emptyHint}>
                  Cuando se resuelvan aparecerán aquí tus estadísticas y
                  proyecciones frente a cada tipster.
                </Text>
              </>
            ) : (
              <>
                <Text style={styles.emptyText}>
                  Aún no has registrado apuestas
                </Text>
                <Text style={styles.emptyHint}>
                  Usa «Yo también la jugué» en un pick de Telegram para comparar
                  tu rentabilidad real con la del tipster.
                </Text>
              </>
            )}
          </View>
        ) : (
          <View style={styles.informantesList}>
            {informantes.map((informante) => {
              const stats = calcularEstadisticas(informante);
              const proyeccion = stats ? calcularProyeccion(informante) : null;

              if (!stats || !proyeccion) return null;

              return (
                <View key={informante} style={styles.informanteCard}>
                  <View style={styles.headerContainer}>
                    <Text style={styles.informanteName}>{informante}</Text>
                    <TouchableOpacity
                      onPress={() =>
                        router.push({
                          pathname: "/informante/[informante]",
                          params: { informante },
                        })
                      }
                      style={styles.statsButton}
                    >
                      <View style={styles.statsButtonContent}>
                        <MaterialIcons
                          name="query-stats"
                          size={20}
                          color={colors.success}
                        />
                        <Text style={styles.statsButtonText}>Ver más</Text>
                      </View>
                    </TouchableOpacity>
                  </View>

                  <View style={styles.statsGrid}>
                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>Aciertos</Text>
                      <Text style={styles.statValue}>
                        {stats.porcentajeAciertos.toFixed(1)}%
                      </Text>
                      <Text style={styles.statDetail}>
                        ({stats.aciertos}/{stats.totalApuestas})
                      </Text>
                    </View>

                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>ROI</Text>
                      <Text
                        style={[
                          styles.statValue,
                          { color: stats.roi >= 0 ? colors.success : colors.danger },
                        ]}
                      >
                        {stats.roi.toFixed(1)}%
                      </Text>
                    </View>

                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>Tendencia</Text>
                      <Text
                        style={[
                          styles.statValue,
                          {
                            color: stats.tendencia >= 0 ? colors.success : colors.danger,
                          },
                        ]}
                      >
                        {stats.tendencia >= 0 ? "↑" : "↓"}
                      </Text>
                    </View>
                  </View>
                  <Text style={styles.windowCaption}>Últimos 30 días</Text>

                  <View style={styles.projectionContainer}>
                    <Text style={styles.projectionTitle}>
                      Proyección Mensual ({inversionDiaria}€/apuesta)
                    </Text>
                    <Text style={styles.projectionValue}>
                      Ganancia est.: {proyeccion.gananciaProyectada.toFixed(2)}€
                    </Text>
                    <Text style={styles.projectionDetail}>
                      Inversión req.: {proyeccion.inversionMensual.toFixed(2)}€
                    </Text>
                    <View
                      style={[
                        styles.riskBadge,
                        {
                          backgroundColor:
                            nivelRiesgo(stats) === "BAJO"
                              ? colors.success
                              : nivelRiesgo(stats) === "MEDIO"
                                ? "#FFC107"
                                : colors.danger,
                        },
                      ]}
                    >
                      <Text style={styles.riskText}>
                        Riesgo: {nivelRiesgo(stats)}
                      </Text>
                    </View>
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "black",
    paddingBottom: 60,
    paddingTop: 60,
  },
  scrollView: {
    flex: 1,
    padding: 15,
  },
  title: {
    fontSize: 24,
    fontWeight: "bold",
    color: colors.primary,
    marginBottom: 20,
    textAlign: "center",
  },
  inputContainer: {
    marginBottom: 20,
    backgroundColor: "#1E1E1E",
    padding: 15,
    borderRadius: 10,
    gap: 15,
  },
  inputGroup: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  input: {
    backgroundColor: "#2C2C2C",
    color: "#FFFFFF",
    padding: 10,
    borderRadius: 5,
    width: 100,
    textAlign: "right",
  },
  label: {
    color: "#FFFFFF",
    fontSize: 16,
    marginRight: 10,
  },
  informantesList: {
    gap: 15,
  },
  informanteCard: {
    backgroundColor: "#1E1E1E",
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
  },
  informanteName: {
    fontSize: 20,
    fontWeight: "bold",
    color: "#FFFFFF",
  },
  headerContainer: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "center",
    marginBottom: 20,
  },
  statsButton: {
    backgroundColor: "#2A2A2A",
    borderRadius: 20,
    padding: 8,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.success,
  },
  statsButtonContent: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  statsButtonText: {
    color: colors.success,
    fontSize: 14,
    fontWeight: "500",
  },
  statsGrid: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 15,
  },
  statItem: {
    alignItems: "center",
    flex: 1,
  },
  statLabel: {
    color: "#BBBBBB",
    fontSize: 14,
    marginBottom: 5,
  },
  statValue: {
    color: "#FFFFFF",
    fontSize: 18,
    fontWeight: "bold",
  },
  windowCaption: {
    color: "#9a9a9a",
    fontSize: 11,
    fontStyle: "italic",
    textAlign: "right",
    marginTop: 4,
  },
  statDetail: {
    color: "#888888",
    fontSize: 12,
    marginTop: 2,
  },
  projectionContainer: {
    backgroundColor: "#2A2A2A",
    borderRadius: 8,
    padding: 12,
    marginTop: 10,
  },
  projectionTitle: {
    color: "#FFFFFF",
    fontSize: 16,
    marginBottom: 8,
  },
  projectionValue: {
    color: colors.success,
    fontSize: 15,
    marginBottom: 4,
  },
  projectionDetail: {
    color: "#BBBBBB",
    fontSize: 14,
    marginBottom: 8,
  },
  riskBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  riskText: {
    color: "#FFFFFF",
    fontSize: 12,
    fontWeight: "bold",
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    height: "100%",
    minHeight: 500,
    gap: 10,
    marginTop: -60,
  },
  loadingText: {
    color: "#FFFFFF",
    fontSize: 18,
    textAlign: "left",
    marginBottom: -70,
    marginStart: 5,
  },
  loadingAnimation: {
    width: 150,
    height: 150,
  },
  errorContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
  },
  errorText: {
    color: colors.danger,
    fontSize: 16,
    marginBottom: 15,
  },
  retryButton: {
    backgroundColor: "#2A2A2A",
    padding: 10,
    borderRadius: 5,
  },
  retryButtonText: {
    color: "#FFFFFF",
    fontSize: 14,
  },
  emptyText: {
    color: "#FFFFFF",
    fontSize: 16,
    marginBottom: 8,
    textAlign: "center",
  },
  emptyHint: {
    color: "#888888",
    fontSize: 13,
    textAlign: "center",
    maxWidth: 300,
  },
});
