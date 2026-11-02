import React, { useCallback, useState } from "react";
import {
  Text,
  View,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import BottomBar from "@/src/components/bottom-bar";
import { useFocusEffect, useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import { useAuth } from "@/src/context/AuthContext";
import { useVideoPlayer, VideoView } from "expo-video";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { listInformantes } from "@/src/api/informantes.api";
import { InformanteSummary } from "@/src/types/informante.types";
import DonutChart from "@/src/components/donut-chart";

function colorForYield(yieldPct: number): string {
  if (yieldPct > 0) return "#4caf50";
  if (yieldPct < 0) return "#f44336";
  return "#aaa";
}

const Main = () => {
  const [tipsters, setTipsters] = useState<InformanteSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();
  const { isAuthenticated } = useAuth();
  const insets = useSafeAreaInsets();

  const introVideoPlayer = useVideoPlayer(
    require("../assets/video/intro.mp4"),
    (player) => {
      player.loop = true;
      player.muted = true;
      player.play();
    }
  );

  const loadTipsters = useCallback(async () => {
    if (!isAuthenticated) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listInformantes();
      setTipsters(data);
    } catch (err: any) {
      setError(err.message || "Error cargando informantes");
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useFocusEffect(
    useCallback(() => {
      loadTipsters();
    }, [loadTipsters])
  );

  const handleTipsterPress = (nombre: string) => {
    router.push(`/dynamic-routes/${nombre}` as any);
  };

  if (!isAuthenticated) {
    console.log("Renderizando vista no autenticada");
    try {
      return (
        <View style={[styles.container, { paddingHorizontal: 0 }]}>
          <TopBar />
          <VideoView
            player={introVideoPlayer}
            style={styles.video}
            contentFit="cover"
            nativeControls={false}
          />
          <View style={styles.overlay} />

          {/* Grid de características */}
          <View style={styles.featuresSection}>
            <View style={styles.titleContainer}>
              <MaterialCommunityIcons
                name="crown"
                size={40}
                color="#ff9f1c"
                style={styles.titleIcon}
              />
              <Text style={styles.sectionTitle}>Toma el Control</Text>
            </View>
            <View style={styles.featuresGrid}>
              <View style={styles.featureCard}>
                <MaterialCommunityIcons
                  name="chart-areaspline"
                  size={32}
                  color="#ff9f1c"
                />
                <Text style={styles.featureTitle}>Audita Tipsters</Text>
                <Text style={styles.featureText}>
                  Extrae sus pronósticos y verifica su rentabilidad real
                </Text>
              </View>

              <View style={styles.featureCard}>
                <MaterialCommunityIcons
                  name="account-group"
                  size={32}
                  color="#ff9f1c"
                />
                <Text style={styles.featureTitle}>Ranking Verificado</Text>
                <Text style={styles.featureText}>
                  Yield y aciertos comprobados por canal
                </Text>
              </View>

              <View style={styles.featureCard}>
                <MaterialCommunityIcons
                  name="bell-alert"
                  size={32}
                  color="#ff9f1c"
                />
                <Text style={styles.featureTitle}>Tu Banco Real</Text>
                <Text style={styles.featureText}>
                  Registra tus apuestas y compara contra el tipster
                </Text>
              </View>

              <View style={styles.featureCard}>
                <MaterialCommunityIcons
                  name="wallet"
                  size={32}
                  color="#ff9f1c"
                />
                <Text style={styles.featureTitle}>Gestión de Bankroll</Text>
                <Text style={styles.featureText}>
                  Límites automáticos de riesgo por apuesta
                </Text>
              </View>
            </View>
          </View>

          <TouchableOpacity
            style={styles.joinButton}
            activeOpacity={0.8}
            onPress={() => router.push("/screens/login")}
          >
            <Text style={styles.joinButtonText}>Start</Text>
          </TouchableOpacity>
        </View>
      );
    } catch (error) {
      console.error("Error al cargar el recurso del video:", error);
      return (
        <View style={[styles.container, { paddingHorizontal: 0 }]}>
          <TopBar />
          <View style={styles.videoContainer}>
            <Text style={{ color: "white" }}>Error al cargar el video</Text>
          </View>
        </View>
      );
    }
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <TopBar />
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#ff9f1c" />
        </View>
        <BottomBar />
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.container}>
        <TopBar />
        <View style={styles.center}>
          <Text style={styles.error}>Error: {error}</Text>
        </View>
        <BottomBar />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 80 + insets.bottom },
        ]}
      >
        <Text style={styles.title}>Tipsters / Canales</Text>
        <Text style={styles.subtitle}>
          Rentabilidad verificada de cada tipster. Toca uno para ver su
          historial.
        </Text>
        {tipsters.length === 0 ? (
          <Text style={styles.empty}>No hay informantes registrados.</Text>
        ) : (
          tipsters.map((t, index) => (
            <TouchableOpacity
              key={t.informante}
              style={styles.card}
              onPress={() => handleTipsterPress(t.informante)}
            >
              <View style={styles.header}>
                <View style={styles.nameRow}>
                  <View
                    style={[
                      styles.rankBadge,
                      index === 0 && styles.rankGold,
                      index === 1 && styles.rankSilver,
                      index === 2 && styles.rankBronze,
                    ]}
                  >
                    <Text style={styles.rankText}>{index + 1}</Text>
                  </View>
                  <Text style={styles.name}>{t.informante}</Text>
                </View>
                <Text
                  style={[styles.yield, { color: colorForYield(t.yieldPct) }]}
                >
                  {t.yieldPct.toFixed(2)}% yield
                </Text>
              </View>

              <View style={styles.row}>
                <View style={styles.cell}>
                  <Text style={styles.label}>Total picks</Text>
                  <Text style={styles.value}>{t.total}</Text>
                </View>
                <View style={styles.cell}>
                  <Text style={styles.label}>Pendientes</Text>
                  <Text
                    style={[
                      styles.value,
                      t.pendientes > 0 && { color: "#ff9f1c" },
                    ]}
                  >
                    {t.pendientes}
                  </Text>
                </View>
                <View style={styles.cell}>
                  <Text style={styles.label}>Aciertos</Text>
                  <Text style={styles.value}>{t.aciertos}</Text>
                </View>
                <View style={styles.cell}>
                  <Text style={styles.label}>% acierto</Text>
                  <DonutChart percentage={t.porcentaje} />
                </View>
                <View style={styles.cell}>
                  <Text style={styles.label}>Ganancia</Text>
                  <Text
                    style={[
                      styles.value,
                      { color: colorForYield(t.ganancias) },
                    ]}
                  >
                    {t.ganancias.toFixed(2)}u
                  </Text>
                </View>
              </View>

              <View style={styles.footerRow}>
                <Text style={styles.footerText}>
                  Manual:{" "}
                  <Text style={styles.footerValue}>{t.manualTotal}</Text>
                </Text>
                <Text style={styles.footerText}>
                  Telegram:{" "}
                  <Text style={styles.footerValue}>{t.parsedTotal}</Text>
                </Text>
              </View>
            </TouchableOpacity>
          ))
        )}
      </ScrollView>
      <BottomBar />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#1a1a1a",
  },
  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  error: {
    color: "#f44336",
    fontSize: 16,
    textAlign: "center",
  },
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    color: "#ff9f1c",
    fontSize: 28,
    fontWeight: "bold",
    marginBottom: 8,
  },
  subtitle: {
    color: "#aaa",
    fontSize: 14,
    marginBottom: 16,
  },
  empty: {
    color: "#aaa",
    fontSize: 16,
    textAlign: "center",
    marginTop: 20,
  },
  card: {
    backgroundColor: "#2a2a2a",
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#3a3a3a",
    paddingBottom: 8,
  },
  nameRow: {
    flexDirection: "row",
    alignItems: "center",
    flexShrink: 1,
    marginRight: 8,
  },
  rankBadge: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: "#3a3a3a",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 8,
  },
  rankGold: {
    backgroundColor: "#b8860b",
  },
  rankSilver: {
    backgroundColor: "#7d8590",
  },
  rankBronze: {
    backgroundColor: "#8c5a2b",
  },
  rankText: {
    color: "#fff",
    fontSize: 13,
    fontWeight: "bold",
  },
  name: {
    color: "#fff",
    fontSize: 17,
    fontWeight: "bold",
    flexShrink: 1,
    marginRight: 8,
  },
  yield: {
    fontSize: 16,
    fontWeight: "bold",
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
  },
  cell: {
    flex: 1,
    alignItems: "center",
  },
  label: {
    color: "#aaa",
    fontSize: 11,
    marginBottom: 2,
  },
  value: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "bold",
  },
  footerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    borderTopWidth: 1,
    borderTopColor: "#3a3a3a",
    marginTop: 10,
    paddingTop: 8,
  },
  footerText: {
    color: "#888",
    fontSize: 11,
  },
  footerValue: {
    color: "#ccc",
    fontWeight: "bold",
  },
  videoContainer: {
    flex: 1,
    backgroundColor: "#1a1a1a",
    width: "100%",
    position: "relative",
  },
  video: {
    width: "100%",
    height: "100%",
    backgroundColor: "#1a1a1a",
  },
  overlay: {
    ...StyleSheet.absoluteFill,
    backgroundColor: "rgba(0, 0, 0, 0.7)",
  },
  joinButton: {
    position: "absolute",
    bottom: "10%",
    alignSelf: "center",
    backgroundColor: "#ff9f1c",
    paddingHorizontal: 30,
    paddingVertical: 18,
    borderRadius: 30,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 8,
    borderWidth: 2,
    borderColor: "rgba(255, 255, 255, 0.2)",
  },
  joinButtonText: {
    color: "#fff",
    fontSize: 20,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 1,
    textShadowColor: "rgba(0, 0, 0, 0.3)",
    textShadowOffset: { width: -1, height: 1 },
    textShadowRadius: 5,
  },
  featuresSection: {
    marginTop: -50,
    marginBottom: 655,
    paddingHorizontal: 15,
    position: "absolute",
    top: "20%",
    width: "100%",
  },
  titleContainer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 70,
    backgroundColor: "rgba(45, 45, 45, 0.8)",
    paddingVertical: 20,
    paddingHorizontal: 25,
    borderRadius: 20,
    shadowColor: "darkgray",
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.5,
    shadowRadius: 20,
    elevation: 8,
    borderWidth: 1,
    borderColor: "rgba(255, 159, 28, 0.3)",
  },
  titleIcon: {
    marginRight: 10,
    transform: [{ rotate: "45deg" }],
  },
  sectionTitle: {
    fontSize: 30,
    fontWeight: "900",
    color: "#ffffff",
    textAlign: "center",
    textTransform: "uppercase",
    letterSpacing: 2,
    textShadowColor: "rgba(255, 159, 28, 0.6)",
    textShadowOffset: { width: -2, height: 2 },
    textShadowRadius: 15,
    opacity: 0.95,
  },
  featuresGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    paddingVertical: 10,
  },
  featureCard: {
    width: "48%",
    backgroundColor: "rgba(45, 45, 45, 0.8)",
    borderRadius: 15,
    padding: 15,
    marginBottom: 15,
    alignItems: "center",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    elevation: 5,
    minHeight: 150,
    borderColor: "rgba(255, 159, 28, 0.3)",
    borderWidth: 1,
  },
  featureTitle: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "bold",
    marginVertical: 10,
    textAlign: "center",
  },
  featureText: {
    color: "#ddd",
    fontSize: 13,
    textAlign: "center",
    lineHeight: 18,
  },
});

export default Main;
