import React, { useCallback, useMemo, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  RefreshControl,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import BottomBar from "@/src/components/bottom-bar";
import {
  getParsedPicks,
  updateParsedPickAcierto,
  ParsedPick,
} from "@/src/api/parsed-picks.api";

function formatDate(isoDate: string): string {
  const date = new Date(isoDate);
  if (isNaN(date.getTime())) return isoDate;
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

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
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const loadPicks = useCallback(async () => {
    try {
      const data = await getParsedPicks();
      setPicks(data);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    }
  }, []);

  // Recarga automáticamente cada vez que entras a esta pantalla (p. ej.
  // al volver desde otra pestaña), sin tener que reabrir la app.
  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      loadPicks().finally(() => setLoading(false));
    }, [loadPicks])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadPicks();
    setRefreshing(false);
  }, [loadPicks]);

  const handleCorregirAcierto = async (
    pick: ParsedPick,
    update: { acierto?: boolean | null; anulada?: boolean }
  ) => {
    try {
      const updated = await updateParsedPickAcierto(pick.id, update);
      setPicks((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch (err: any) {
      setError(err.message);
    }
  };

  const realPicks = useMemo(() => picks.filter((p) => p.es_apuesta), [picks]);
  const visiblePicks = showAll ? picks : realPicks;

  const channels = useMemo(
    () =>
      Array.from(
        new Set(
          visiblePicks.map((p) => cleanChannel(p.informante)).filter(Boolean)
        )
      ).sort(),
    [visiblePicks]
  );

  const filteredPicks = useMemo(() => {
    if (!selectedChannel) return visiblePicks;
    return visiblePicks.filter(
      (p) => cleanChannel(p.informante) === selectedChannel
    );
  }, [visiblePicks, selectedChannel]);

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 80 + insets.bottom },
        ]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor="#ff9f1c"
          />
        }
      >
        <Text style={styles.title}>Picks extraídos de Telegram</Text>

        <TouchableOpacity
          style={styles.toggleRow}
          onPress={() => {
            setShowAll((prev) => !prev);
            setSelectedChannel(null);
          }}
        >
          <View style={[styles.checkbox, showAll && styles.checkboxChecked]} />
          <Text style={styles.toggleLabel}>
            Ver todos (incluye mensajes descartados por el filtro)
          </Text>
        </TouchableOpacity>

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
            <Text style={styles.cardDate}>{formatDate(pick.created_at)}</Text>
            <Text style={styles.cardMeta}>
              Canal:{" "}
              <Text
                style={styles.channelLink}
                onPress={() =>
                  router.push(`/dynamic-routes/${pick.informante}` as any)
                }
              >
                {cleanChannel(pick.informante)}
              </Text>
            </Text>
            <Text style={styles.cardMeta}>
              Cuota: {pick.cuota ?? "-"} | Stake: {pick.stake ?? "-"}
            </Text>
            <Text style={styles.cardMeta}>
              Método: {pick.metodo} | Confianza: {pick.confianza}
            </Text>
            {showAll && (
              <Text style={styles.cardMeta}>
                Es apuesta: {pick.es_apuesta ? "Sí" : "No"}
              </Text>
            )}
            {pick.explicacion && (
              <Text style={styles.explanation}>{pick.explicacion}</Text>
            )}

            {pick.es_apuesta && (
              <View style={styles.acertoSection}>
                {pick.linea !== null && (
                  <Text style={styles.cardMeta}>Línea: {pick.linea}</Text>
                )}
                <Text style={styles.cardMeta}>
                  Resultado:{" "}
                  <Text
                    style={
                      pick.anulada
                        ? styles.acertoAnulada
                        : pick.acierto === true
                          ? styles.acertoTrue
                          : pick.acierto === false
                            ? styles.acertoFalse
                            : styles.acertoPending
                    }
                  >
                    {pick.anulada
                      ? "Anulada"
                      : pick.acierto === true
                        ? "Acertó"
                        : pick.acierto === false
                          ? "Falló"
                          : "Pendiente"}
                  </Text>
                  {pick.verificado_por && (
                    <Text style={styles.cardMeta}>
                      {" "}
                      ({pick.verificado_por === "auto" ? "auto" : "manual"})
                    </Text>
                  )}
                </Text>

                <View style={styles.acertoButtons}>
                  <TouchableOpacity
                    style={[styles.acertoButton, styles.acertoButtonTrue]}
                    onPress={() =>
                      handleCorregirAcierto(pick, { acierto: true })
                    }
                  >
                    <Text style={styles.acertoButtonText}>Acertó</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.acertoButton, styles.acertoButtonFalse]}
                    onPress={() =>
                      handleCorregirAcierto(pick, { acierto: false })
                    }
                  >
                    <Text style={styles.acertoButtonText}>Falló</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.acertoButton, styles.acertoButtonAnulada]}
                    onPress={() =>
                      handleCorregirAcierto(pick, { anulada: true })
                    }
                  >
                    <Text style={styles.acertoButtonText}>Anulada</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.acertoButton, styles.acertoButtonPending]}
                    onPress={() =>
                      handleCorregirAcierto(pick, {
                        acierto: null,
                        anulada: false,
                      })
                    }
                  >
                    <Text style={styles.acertoButtonText}>Pendiente</Text>
                  </TouchableOpacity>
                </View>
              </View>
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
  cardDate: {
    color: "#ff9f1c",
    fontSize: 12,
    marginBottom: 6,
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
  toggleRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 12,
  },
  checkbox: {
    width: 18,
    height: 18,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: "#666",
    marginRight: 8,
  },
  checkboxChecked: {
    backgroundColor: "#ff9f1c",
    borderColor: "#ff9f1c",
  },
  toggleLabel: {
    color: "#aaa",
    fontSize: 13,
    flexShrink: 1,
  },
  acertoSection: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#333",
  },
  acertoTrue: {
    color: "#4caf50",
    fontWeight: "bold",
  },
  acertoFalse: {
    color: "#f44336",
    fontWeight: "bold",
  },
  acertoPending: {
    color: "#ff9f1c",
    fontWeight: "bold",
  },
  acertoAnulada: {
    color: "#9e9e9e",
    fontWeight: "bold",
  },
  acertoButtons: {
    flexDirection: "row",
    marginTop: 8,
    gap: 8,
  },
  acertoButton: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    marginRight: 8,
  },
  acertoButtonTrue: {
    backgroundColor: "#1e3a24",
    borderColor: "#4caf50",
    borderWidth: 1,
  },
  acertoButtonFalse: {
    backgroundColor: "#3a1e1e",
    borderColor: "#f44336",
    borderWidth: 1,
  },
  acertoButtonAnulada: {
    backgroundColor: "#2a2a2a",
    borderColor: "#9e9e9e",
    borderWidth: 1,
  },
  acertoButtonPending: {
    backgroundColor: "#332a1a",
    borderColor: "#ff9f1c",
    borderWidth: 1,
  },
  acertoButtonText: {
    color: "#fff",
    fontSize: 12,
  },
  channelLink: {
    color: "#ff9f1c",
    textDecorationLine: "underline",
  },
});
