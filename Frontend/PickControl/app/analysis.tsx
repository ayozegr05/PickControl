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
import { Screen } from "@/src/components/screen";
import { ScreenTitle } from "@/src/components/typography";

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
    <Screen
      tone="black"
      style={{
        paddingTop: 60 + insets.top,
        paddingBottom: 60 + insets.bottom,
      }}
    >
      <TopBar />
      <ScrollView style={styles.scrollView}>
        <ScreenTitle size={24} style={styles.title}>
          Análisis de Rentabilidad
        </ScreenTitle>

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
              placeholderTextColor={colors.textMuted}
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
              placeholderTextColor={colors.textMuted}
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
                          {
                            color:
                              stats.roi >= 0 ? colors.success : colors.danger,
                          },
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
                            color:
                              stats.tendencia >= 0
                                ? colors.success
                                : colors.danger,
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
                                ? colors.warning
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
    </Screen>
  );
}

const styles = StyleSheet.create({
  scrollView: {
    flex: 1,
    padding: 15,
  },
  title: {
    marginBottom: 20,
    textAlign: "center",
  },
  inputContainer: {
    marginBottom: 20,
    backgroundColor: colors.background,
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
    backgroundColor: colors.surface,
    color: colors.text,
    padding: 10,
    borderRadius: 5,
    width: 100,
    textAlign: "right",
  },
  label: {
    color: colors.text,
    fontSize: 16,
    marginRight: 10,
  },
  informantesList: {
    gap: 15,
  },
  informanteCard: {
    backgroundColor: colors.background,
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
  },
  informanteName: {
    fontSize: 20,
    fontWeight: "bold",
    color: colors.text,
  },
  headerContainer: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "center",
    marginBottom: 20,
  },
  statsButton: {
    backgroundColor: colors.surface,
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
    color: colors.textSecondary,
    fontSize: 14,
    marginBottom: 5,
  },
  statValue: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "bold",
  },
  windowCaption: {
    color: colors.textSecondary,
    fontSize: 11,
    fontStyle: "italic",
    textAlign: "right",
    marginTop: 4,
  },
  statDetail: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: 2,
  },
  projectionContainer: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    padding: 12,
    marginTop: 10,
  },
  projectionTitle: {
    color: colors.text,
    fontSize: 16,
    marginBottom: 8,
  },
  projectionValue: {
    color: colors.success,
    fontSize: 15,
    marginBottom: 4,
  },
  projectionDetail: {
    color: colors.textSecondary,
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
    color: colors.text,
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
    color: colors.text,
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
    backgroundColor: colors.surface,
    padding: 10,
    borderRadius: 5,
  },
  retryButtonText: {
    color: colors.text,
    fontSize: 14,
  },
  emptyText: {
    color: colors.text,
    fontSize: 16,
    marginBottom: 8,
    textAlign: "center",
  },
  emptyHint: {
    color: colors.textMuted,
    fontSize: 13,
    textAlign: "center",
    maxWidth: 300,
  },
});
