import React, { useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import TopBar from "@/src/components/top-bar";
import BottomBar from "@/src/components/bottom-bar";
import { getParsedPicks, ParsedPick } from "@/src/api/parsed-picks.api";

function cleanChannel(name: string | null): string {
  if (!name) return "desconocido";
  return name
    .replace(/[^\p{L}\p{N}\s|]/gu, " ")
    .replace(/\s+/g, " ")
    .split("|")[0]
    .trim();
}

export default function ParsedPicksScreen() {
  const [picks, setPicks] = useState<ParsedPick[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    getParsedPicks()
      .then(setPicks)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const channels = useMemo(
    () =>
      Array.from(
        new Set(picks.map((p) => cleanChannel(p.informante)).filter(Boolean))
      ).sort(),
    [picks]
  );

  const filteredPicks = useMemo(() => {
    if (!selectedChannel) return picks;
    return picks.filter((p) => cleanChannel(p.informante) === selectedChannel);
  }, [picks, selectedChannel]);

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

        {channels.length > 0 && (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.channelList}
          >
            <TouchableOpacity
              onPress={() => setSelectedChannel(null)}
              style={[
                styles.channelChip,
                selectedChannel === null && styles.channelChipActive,
              ]}
            >
              <Text
                style={[
                  styles.channelChipText,
                  selectedChannel === null && styles.channelChipTextActive,
                ]}
              >
                Todos
              </Text>
            </TouchableOpacity>
            {channels.map((channel) => (
              <TouchableOpacity
                key={channel}
                onPress={() => setSelectedChannel(channel)}
                style={[
                  styles.channelChip,
                  selectedChannel === channel && styles.channelChipActive,
                ]}
              >
                <Text
                  style={[
                    styles.channelChipText,
                    selectedChannel === channel && styles.channelChipTextActive,
                  ]}
                >
                  {channel}
                </Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        )}

        <Text style={styles.count}>
          {filteredPicks.length} pick{filteredPicks.length !== 1 ? "s" : ""}
        </Text>

        {filteredPicks.map((pick) => (
          <View key={pick.id} style={styles.card}>
            <Text style={styles.cardTitle}>
              {pick.apuesta || "Sin apuesta"}
            </Text>
            <Text style={styles.cardMeta}>
              Canal: {cleanChannel(pick.informante)}
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
  channelList: {
    paddingVertical: 8,
    gap: 8,
  },
  channelChip: {
    backgroundColor: "#1a1a1a",
    borderColor: "#333",
    borderWidth: 1,
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginRight: 8,
  },
  channelChipActive: {
    backgroundColor: "#ff9f1c",
    borderColor: "#ff9f1c",
  },
  channelChipText: {
    color: "#aaa",
    fontSize: 13,
  },
  channelChipTextActive: {
    color: "#0d0d0d",
    fontWeight: "bold",
  },
  count: {
    color: "#aaa",
    fontSize: 14,
    marginBottom: 12,
  },
});
