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
import { listInformantes } from "@/src/api/informantes.api";

const OVERVIEW_PER_CHANNEL = 2;
const CHANNEL_PAGE_SIZE = 20;

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
  // Los canales se llaman p. ej. "Dm7 || GRATUITO || ⚡️": quedarse solo
  // con el primer segmento haría que "Dm7 || Allsports ||" y
  // "Dm7 || GRATUITO ||" se vieran ambos como "Dm7". Unimos todos los
  // segmentos con texto para que cada canal tenga un nombre distinto.
  return (
    name
      .split("|")
      .map((part) =>
        part
          .replace(/[^\p{L}\p{N}\s]/gu, " ")
          .replace(/\s+/g, " ")
          .trim()
      )
      .filter(Boolean)
      .join(" ") || "desconocido"
  );
}

type ChannelGroup = {
  key: string;
  informanteId: number | null;
  name: string;
  picks: ParsedPick[];
};

export default function ParsedPicksScreen() {
  const [overviewPicks, setOverviewPicks] = useState<ParsedPick[]>([]);
  const [channelNames, setChannelNames] = useState<string[]>([]);
  const [channelPicks, setChannelPicks] = useState<ParsedPick[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<{
    id: number;
    name: string;
  } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const loadOverview = useCallback(async (includeDiscarded: boolean) => {
    const [picksData, informantesData] = await Promise.all([
      getParsedPicks({
        perChannel: OVERVIEW_PER_CHANNEL,
        soloApuestas: !includeDiscarded,
      }),
      // Los canales reales vienen de /informantes (ya filtrado a
      // es_canal_telegram), para que un canal sin picks en el filtro
      // actual siga apareciendo como sección vacía.
      listInformantes(),
    ]);
    setOverviewPicks(picksData);
    setChannelNames(informantesData.map((i) => i.informante));
  }, []);

  const loadChannel = useCallback(
    async (informanteId: number, offset: number, includeDiscarded: boolean) => {
      const data = await getParsedPicks({
        informanteId,
        soloApuestas: !includeDiscarded,
        offset,
        limit: CHANNEL_PAGE_SIZE,
      });
      if (offset === 0) {
        setChannelPicks(data);
      } else {
        setChannelPicks((prev) => [...prev, ...data]);
      }
      setHasMore(data.length === CHANNEL_PAGE_SIZE);
    },
    []
  );

  const reload = useCallback(async () => {
    try {
      await loadOverview(showAll);
      if (selectedChannel) {
        await loadChannel(selectedChannel.id, 0, showAll);
      }
      setError(null);
    } catch (err: any) {
      setError(err.message);
    }
  }, [loadOverview, loadChannel, selectedChannel, showAll]);

  // Recarga automáticamente cada vez que entras a esta pantalla (p. ej.
  // al volver desde otra pestaña), sin tener que reabrir la app.
  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      reload().finally(() => setLoading(false));
    }, [reload])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await reload();
    setRefreshing(false);
  }, [reload]);

  const handleSelectChannel = useCallback(
    async (channel: { id: number; name: string } | null) => {
      setSelectedChannel(channel);
      setChannelPicks([]);
      setHasMore(false);
      if (channel) {
        try {
          await loadChannel(channel.id, 0, showAll);
          setError(null);
        } catch (err: any) {
          setError(err.message);
        }
      }
    },
    [loadChannel, showAll]
  );

  const handleLoadMore = useCallback(async () => {
    if (!selectedChannel || loadingMore) return;
    setLoadingMore(true);
    try {
      await loadChannel(selectedChannel.id, channelPicks.length, showAll);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoadingMore(false);
    }
  }, [selectedChannel, loadingMore, channelPicks.length, loadChannel, showAll]);

  const handleToggleShowAll = useCallback(async () => {
    const next = !showAll;
    setShowAll(next);
    // El filtro es_apuesta se aplica en el backend ANTES del top-N,
    // así que hay que refetchear para que el resumen no se quede con
    // mensajes descartados ocupando los huecos de cada canal.
    setLoading(true);
    try {
      await loadOverview(next);
      if (selectedChannel) {
        await loadChannel(selectedChannel.id, 0, next);
      }
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [showAll, loadOverview, loadChannel, selectedChannel]);

  const applyUpdate = (list: ParsedPick[], updated: ParsedPick) =>
    list.map((p) => (p.id === updated.id ? updated : p));

  const handleCorregirAcierto = async (
    pick: ParsedPick,
    update: { acierto?: boolean | null; anulada?: boolean }
  ) => {
    try {
      const updated = await updateParsedPickAcierto(pick.id, update);
      setOverviewPicks((prev) => applyUpdate(prev, updated));
      setChannelPicks((prev) => applyUpdate(prev, updated));
    } catch (err: any) {
      setError(err.message);
    }
  };

  // Corrección de una PATA de combinada: el PATCH re-liquida el padre
  // en el backend, pero la pata no es fila de primer nivel en la UI —
  // hay que recargar para verla y ver el nuevo estado de la combinada.
  const handleCorregirPata = async (
    pata: ParsedPick,
    update: { acierto?: boolean | null; anulada?: boolean }
  ) => {
    try {
      await updateParsedPickAcierto(pata.id, update);
      await reload();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const estadoPata = (pata: ParsedPick): string => {
    if (pata.anulada) return "Anulada";
    if (pata.acierto === true) return "Acertó";
    if (pata.acierto === false) return "Falló";
    return "Pendiente";
  };

  const renderPataRow = (pata: ParsedPick) => (
    <View key={pata.id} style={styles.pataRow}>
      <View style={{ flex: 1 }}>
        <Text style={styles.pataText}>
          {pata.orden != null ? `${pata.orden + 1}. ` : ""}
          {pata.seleccion || "(pata)"}
        </Text>
        {pata.evento ? (
          <Text style={styles.pataMeta}>{pata.evento}</Text>
        ) : null}
        <Text style={styles.pataMeta}>
          {estadoPata(pata)}
          {pata.verificado_por ? ` (${pata.verificado_por})` : ""}
        </Text>
      </View>
      <View style={styles.pataButtons}>
        <TouchableOpacity onPress={() => handleCorregirPata(pata, { acierto: true })}>
          <Text style={styles.pataButton}>✅</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => handleCorregirPata(pata, { acierto: false })}
        >
          <Text style={styles.pataButton}>❌</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => handleCorregirPata(pata, { anulada: true })}
        >
          <Text style={styles.pataButton}>↩️</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() =>
            handleCorregirPata(pata, { acierto: null, anulada: false })
          }
        >
          <Text style={styles.pataButton}>❓</Text>
        </TouchableOpacity>
      </View>
    </View>
  );

  const channelGroups = useMemo<ChannelGroup[]>(() => {
    const map = new Map<string, ChannelGroup>();
    // Primero todos los canales reales: un canal sin picks en el filtro
    // actual (p. ej. todos descartados) sigue apareciendo, vacío.
    for (const nombre of channelNames) {
      map.set(nombre, {
        key: nombre,
        informanteId: null,
        name: cleanChannel(nombre),
        picks: [],
      });
    }
    for (const p of overviewPicks) {
      const key = p.informante ?? "";
      let group = map.get(key);
      if (!group) {
        group = {
          key,
          informanteId: p.informante_id,
          name: cleanChannel(p.informante),
          picks: [],
        };
        map.set(key, group);
      }
      group.informanteId = group.informanteId ?? p.informante_id;
      group.picks.push(p);
    }
    return Array.from(map.values()).sort((a, b) =>
      a.name.localeCompare(b.name)
    );
  }, [overviewPicks, channelNames]);

  const renderPickCard = (pick: ParsedPick) => (
    <View key={pick.id} style={styles.card}>
      <Text style={styles.cardTitle}>{pick.apuesta || "Sin apuesta"}</Text>
      <Text style={styles.cardDate}>
        {formatDate(pick.fecha_evento ?? pick.created_at)}
      </Text>
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
              onPress={() => handleCorregirAcierto(pick, { acierto: true })}
            >
              <Text style={styles.acertoButtonText}>Acertó</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonFalse]}
              onPress={() => handleCorregirAcierto(pick, { acierto: false })}
            >
              <Text style={styles.acertoButtonText}>Falló</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonAnulada]}
              onPress={() => handleCorregirAcierto(pick, { anulada: true })}
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

      {pick.es_combinada && (pick.patas?.length ?? 0) > 0 && (
        <View style={styles.acertoSection}>
          <Text style={styles.cardMeta}>
            Patas ({pick.patas!.length})
            {pick.cuota_efectiva != null &&
            pick.cuota_efectiva !== pick.cuota
              ? ` · cuota efectiva ${pick.cuota_efectiva}`
              : ""}
          </Text>
          {(pick.patas ?? []).map(renderPataRow)}
        </View>
      )}
    </View>
  );

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
          onPress={handleToggleShowAll}
        >
          <View style={[styles.checkbox, showAll && styles.checkboxChecked]} />
          <Text style={styles.toggleLabel}>
            Ver todos (incluye mensajes descartados por el filtro)
          </Text>
        </TouchableOpacity>

        {loading && <ActivityIndicator size="large" color="#ff9f1c" />}
        {error && <Text style={styles.error}>Error: {error}</Text>}

        {channelGroups.length > 0 && (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.channelList}
          >
            <TouchableOpacity
              onPress={() => handleSelectChannel(null)}
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
            {channelGroups
              .filter((group) => group.informanteId !== null)
              .map((group) => (
                <TouchableOpacity
                  key={group.key}
                  onPress={() =>
                    handleSelectChannel({
                      id: group.informanteId as number,
                      name: group.name,
                    })
                  }
                  style={[
                    styles.channelChip,
                    selectedChannel?.id === group.informanteId &&
                      styles.channelChipActive,
                  ]}
                >
                  <Text
                    style={[
                      styles.channelChipText,
                      selectedChannel?.id === group.informanteId &&
                        styles.channelChipTextActive,
                    ]}
                  >
                    {group.name}
                  </Text>
                </TouchableOpacity>
              ))}
          </ScrollView>
        )}

        {selectedChannel === null ? (
          <>
            {channelGroups.length === 0 && !loading && (
              <Text style={styles.emptyText}>
                Todavía no hay picks extraídos.
              </Text>
            )}
            {channelGroups.map((group) => (
              <View key={group.key} style={styles.channelSection}>
                <View style={styles.channelHeader}>
                  <Text style={styles.channelTitle}>{group.name}</Text>
                  {group.informanteId !== null && (
                    <TouchableOpacity
                      onPress={() =>
                        handleSelectChannel({
                          id: group.informanteId as number,
                          name: group.name,
                        })
                      }
                    >
                      <Text style={styles.seeAll}>Ver todos →</Text>
                    </TouchableOpacity>
                  )}
                </View>
                {group.picks.length === 0 ? (
                  <Text style={styles.emptyChannel}>
                    Sin picks extraídos con este filtro.
                  </Text>
                ) : (
                  <>
                    {group.picks
                      .filter((p) => !p.es_reto && !p.es_combinada)
                      .map(renderPickCard)}
                    {group.picks.some((p) => p.es_combinada) && (
                      <>
                        <Text style={styles.retosTitle}>Combinadas</Text>
                        {group.picks
                          .filter((p) => p.es_combinada)
                          .map(renderPickCard)}
                      </>
                    )}
                    {group.picks.some((p) => p.es_reto) && (
                      <>
                        <Text style={styles.retosTitle}>Retos</Text>
                        {group.picks
                          .filter((p) => p.es_reto)
                          .map(renderPickCard)}
                      </>
                    )}
                  </>
                )}
              </View>
            ))}
          </>
        ) : (
          <>
            <Text style={styles.count}>
              {channelPicks.length} pick{channelPicks.length !== 1 ? "s" : ""}{" "}
              de {selectedChannel.name}
            </Text>
            {channelPicks
              .filter((p) => !p.es_reto && !p.es_combinada)
              .map(renderPickCard)}
            {channelPicks.some((p) => p.es_combinada) && (
              <>
                <Text style={styles.retosTitle}>Combinadas</Text>
                {channelPicks.filter((p) => p.es_combinada).map(renderPickCard)}
              </>
            )}
            {channelPicks.some((p) => p.es_reto) && (
              <>
                <Text style={styles.retosTitle}>Retos</Text>
                {channelPicks.filter((p) => p.es_reto).map(renderPickCard)}
              </>
            )}
            {hasMore && (
              <TouchableOpacity
                style={styles.loadMore}
                onPress={handleLoadMore}
                disabled={loadingMore}
              >
                {loadingMore ? (
                  <ActivityIndicator size="small" color="#ff9f1c" />
                ) : (
                  <Text style={styles.loadMoreText}>Cargar más</Text>
                )}
              </TouchableOpacity>
            )}
          </>
        )}
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
  emptyText: {
    color: "#aaa",
    fontSize: 14,
    marginTop: 12,
  },
  emptyChannel: {
    color: "#666",
    fontSize: 13,
    marginBottom: 12,
    fontStyle: "italic",
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
  channelSection: {
    marginTop: 14,
    marginBottom: 6,
  },
  channelHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#241a0d",
    borderLeftWidth: 4,
    borderLeftColor: "#ff9f1c",
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 12,
  },
  channelTitle: {
    color: "#ff9f1c",
    fontSize: 16,
    fontWeight: "bold",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    flexShrink: 1,
  },
  seeAll: {
    color: "#ffcf8a",
    fontSize: 13,
    fontWeight: "bold",
    marginLeft: 12,
  },
  retosTitle: {
    color: "#b388ff",
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 10,
    marginBottom: 10,
  },
  count: {
    color: "#aaa",
    fontSize: 14,
    marginBottom: 12,
  },
  loadMore: {
    backgroundColor: "#1a1a1a",
    borderColor: "#ff9f1c",
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
    marginBottom: 12,
  },
  loadMoreText: {
    color: "#ff9f1c",
    fontSize: 14,
    fontWeight: "bold",
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
  pataRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderTopWidth: 1,
    borderTopColor: "#2a2a2a",
  },
  pataText: {
    color: "#e5e5e5",
    fontSize: 13,
  },
  pataMeta: {
    color: "#888",
    fontSize: 11,
    marginTop: 2,
  },
  pataButtons: {
    flexDirection: "row",
    marginLeft: 8,
  },
  pataButton: {
    fontSize: 16,
    marginLeft: 6,
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
