import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import BottomBar from "@/src/components/bottom-bar";
import { listInformantes } from "@/src/api/informantes.api";
import { InformanteSummary } from "@/src/types/informante.types";

function colorForYield(yieldPct: number): string {
  if (yieldPct > 0) return "#4caf50";
  if (yieldPct < 0) return "#f44336";
  return "#aaa";
}

export default function TipstersScreen() {
  const [tipsters, setTipsters] = useState<InformanteSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const load = useCallback(async () => {
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
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handlePress = (nombre: string) => {
    router.push(`/dynamic-routes/${nombre}` as any);
  };

  if (loading) {
    return (
      <View style={styles.container}>
        <TopBar />
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#ff9f1c" />
        </View>
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
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingBottom: insets.bottom }]}>
      <TopBar />
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>Tipsters / Canales</Text>
        {tipsters.length === 0 ? (
          <Text style={styles.empty}>No hay informantes registrados.</Text>
        ) : (
          tipsters.map((t) => (
            <TouchableOpacity
              key={t.informante}
              style={styles.card}
              onPress={() => handlePress(t.informante)}
            >
              <View style={styles.header}>
                <Text style={styles.name}>{t.informante}</Text>
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
                  <Text style={styles.label}>Aciertos</Text>
                  <Text style={styles.value}>{t.aciertos}</Text>
                </View>
                <View style={styles.cell}>
                  <Text style={styles.label}>% acierto</Text>
                  <Text style={styles.value}>{t.porcentaje.toFixed(2)}%</Text>
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

              <View style={styles.row}>
                <View style={styles.cell}>
                  <Text style={styles.subLabel}>Manual</Text>
                  <Text style={styles.subValue}>{t.manualTotal}</Text>
                </View>
                <View style={styles.cell}>
                  <Text style={styles.subLabel}>Telegram</Text>
                  <Text style={styles.subValue}>{t.parsedTotal}</Text>
                </View>
              </View>
            </TouchableOpacity>
          ))
        )}
      </ScrollView>
      <BottomBar />
    </View>
  );
}

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
    padding: 16,
  },
  title: {
    color: "#ff9f1c",
    fontSize: 28,
    fontWeight: "bold",
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
  subLabel: {
    color: "#888",
    fontSize: 10,
    marginBottom: 2,
  },
  subValue: {
    color: "#ccc",
    fontSize: 13,
  },
});
