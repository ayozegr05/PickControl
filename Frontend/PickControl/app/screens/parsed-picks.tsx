import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import TopBar from "@/src/components/top-bar";
import BottomBar from "@/src/components/bottom-bar";
import { getParsedPicks, ParsedPick } from "@/src/api/parsed-picks.api";

export default function ParsedPicksScreen() {
  const [picks, setPicks] = useState<ParsedPick[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    getParsedPicks()
      .then(setPicks)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 80 + insets.bottom },
        ]}
      >
        <Text style={styles.title}>Picks extraídos de Telegram</Text>

        {loading && <ActivityIndicator size="large" color="#ff9f1c" />}
        {error && <Text style={styles.error}>Error: {error}</Text>}

        {picks.map((pick) => (
          <View key={pick.id} style={styles.card}>
            <Text style={styles.cardTitle}>
              {pick.apuesta || "Sin apuesta"}
            </Text>
            <Text style={styles.cardMeta}>
              Canal: {pick.informante || "desconocido"}
            </Text>
            <Text style={styles.cardMeta}>
              Cuota: {pick.cuota ?? "-"} | Stake: {pick.stake ?? "-"}
            </Text>
            <Text style={styles.cardMeta}>
              Método: {pick.metodo} | Confianza: {pick.confianza}
            </Text>
            <Text style={styles.cardMeta}>
              Es apuesta: {pick.es_apuesta ? "Sí" : "No"}
            </Text>
            {pick.explicacion && (
              <Text style={styles.explanation}>{pick.explicacion}</Text>
            )}
          </View>
        ))}
      </ScrollView>
      <BottomBar />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0d0d0d",
  },
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    color: "#ff9f1c",
    fontSize: 22,
    fontWeight: "bold",
    marginBottom: 16,
  },
  card: {
    backgroundColor: "#1a1a1a",
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    borderColor: "#333",
    borderWidth: 1,
  },
  cardTitle: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "bold",
    marginBottom: 4,
  },
  cardMeta: {
    color: "#aaa",
    fontSize: 13,
    marginBottom: 2,
  },
  explanation: {
    color: "#ccc",
    fontSize: 12,
    marginTop: 8,
  },
  error: {
    color: "red",
    fontSize: 14,
    marginBottom: 12,
  },
});
