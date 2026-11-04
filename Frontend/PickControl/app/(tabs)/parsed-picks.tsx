import React, { useCallback, useMemo, useState } from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  RefreshControl,
  Alert,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import {
  getParsedPicks,
  updateParsedPickAcierto,
  anularResiduoPendiente,
  ParsedPick,
} from "@/src/api/parsed-picks.api";
import { useAuth } from "@/src/context/AuthContext";
import {
  getProvidersStatus,
  getPicksStatus,
  ProvidersStatus,
  PicksStatus,
} from "@/src/api/system.api";
import { cleanChannel } from "@/src/utils/format";
import { useParsedPicks } from "@/src/hooks/useParsedPicks";
import ParsedPickCard from "@/src/components/parsed-pick-card";
import {
  ProvidersCard,
  CuotasCard,
  VerificacionCard,
  DuplicadosCard,
} from "@/src/components/admin-panels";
import ChannelChips from "@/src/components/channel-chips";
import ChannelSection, {
  ChannelGroup,
  PickCardProps,
} from "@/src/components/channel-section";
import {
  buildProviderRows,
  buildOddsProviderRows,
} from "@/src/utils/providers";

export default function ParsedPicksScreen() {
  const {
    overviewPicks,
    channelNames,
    channelPicks,
    selectedChannel,
    hasMore,
    loading,
    loadingMore,
    refreshing,
    error,
    showAll,
    soloPendientes,
    reload,
    refresh,
    selectChannel,
    loadMore,
    toggleShowAll,
    togglePendientes,
    setOverviewPicks,
    setChannelPicks,
    setError,
  } = useParsedPicks();
  const [providersStatus, setProvidersStatus] =
    useState<ProvidersStatus | null>(null);
  const [picksStatus, setPicksStatus] = useState<PicksStatus | null>(null);
  const [dupPicks, setDupPicks] = useState<ParsedPick[]>([]);
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { isAdmin } = useAuth();

  // Panel de sistema: solo admin; un fallo aquí no rompe la lista.
  const loadAdminExtras = useCallback(() => {
    if (!isAdmin) return;
    getProvidersStatus()
      .then(setProvidersStatus)
      .catch(() => setProvidersStatus(null));
    getPicksStatus()
      .then(setPicksStatus)
      .catch(() => setPicksStatus(null));
    // Reposteos anulados por dedup: van a su card, fuera del listado.
    getParsedPicks({ soloDuplicados: true, limit: 200 })
      .then(setDupPicks)
      .catch(() => setDupPicks([]));
  }, [isAdmin]);

  // Recarga automáticamente cada vez que entras a esta pantalla (p. ej.
  // al volver desde otra pestaña), sin tener que reabrir la app.
  useFocusEffect(
    useCallback(() => {
      reload();
      loadAdminExtras();
    }, [reload, loadAdminExtras])
  );

  // Filas de los paneles admin: la forma de cada fila la construyen
  // las funciones puras de src/utils/providers.ts.
  const providerRows = useMemo(
    () => (providersStatus ? buildProviderRows(providersStatus) : []),
    [providersStatus]
  );
  const oddsProviderRows = useMemo(
    () => (providersStatus ? buildOddsProviderRows(providersStatus) : []),
    [providersStatus]
  );

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

  const handleAnularResiduo = useCallback(() => {
    const n = picksStatus?.pendientes_backlog ?? 0;
    Alert.alert(
      "Anular residuo >14d",
      `Se marcarán como anulados los ${n} pendientes que el verifier ya no reintenta. Salen de la cola y cuentan como void. ¿Seguir?`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Anular",
          style: "destructive",
          onPress: async () => {
            try {
              await anularResiduoPendiente();
              await reload();
            } catch (err: any) {
              setError(err.message);
            }
          },
        },
      ]
    );
  }, [picksStatus, reload]);

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

  // Filas aplanadas de la vista de canal para la FlatList: simples
  // primero, luego las secciones "Combinadas" y "Retos" (mismo orden
  // que el map anterior, ahora virtualizado).
  type ChannelRow =
    | { kind: "pick"; pick: ParsedPick }
    | { kind: "sectionTitle"; label: string };

  const channelRows = useMemo<ChannelRow[]>(() => {
    if (!selectedChannel) return [];
    const rows: ChannelRow[] = channelPicks
      .filter((p) => !p.es_reto && !p.es_combinada)
      .map((pick) => ({ kind: "pick", pick }));
    const combinadas = channelPicks.filter((p) => p.es_combinada);
    const retos = channelPicks.filter((p) => p.es_reto);
    if (combinadas.length > 0) {
      rows.push({ kind: "sectionTitle", label: "Combinadas" });
      rows.push(...combinadas.map((pick) => ({ kind: "pick" as const, pick })));
    }
    if (retos.length > 0) {
      rows.push({ kind: "sectionTitle", label: "Retos" });
      rows.push(...retos.map((pick) => ({ kind: "pick" as const, pick })));
    }
    return rows;
  }, [channelPicks, selectedChannel]);

  const pickCardProps: PickCardProps = {
    showAll,
    canCorrect: isAdmin,
    onCorregir: handleCorregirAcierto,
    onCorregirPata: handleCorregirPata,
    onChannelPress: (informante) =>
      router.push({
        pathname: "/informante/[informante]",
        params: { informante },
      }),
  };

  const renderPickCard = (pick: ParsedPick) => (
    <ParsedPickCard key={pick.id} pick={pick} {...pickCardProps} />
  );

  return (
    <View style={styles.container}>
      <TopBar />
      <FlatList
        data={channelRows}
        keyExtractor={(row) =>
          row.kind === "pick" ? `pick-${row.pick.id}` : `title-${row.label}`
        }
        renderItem={({ item }) =>
          item.kind === "sectionTitle" ? (
            <Text style={styles.retosTitle}>{item.label}</Text>
          ) : (
            renderPickCard(item.pick)
          )
        }
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 24 },
        ]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={refresh}
            tintColor="#ff9f1c"
          />
        }
        onEndReached={() => {
          if (selectedChannel && hasMore && !loadingMore) loadMore();
        }}
        onEndReachedThreshold={0.4}
        ListHeaderComponent={
          <>
        <Text style={styles.title}>Picks extraídos de Telegram</Text>

        <TouchableOpacity
          style={styles.toggleRow}
          onPress={toggleShowAll}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: showAll }}
          accessibilityLabel="Ver todos, incluye mensajes descartados por el filtro"
        >
          <View style={[styles.checkbox, showAll && styles.checkboxChecked]} />
          <Text style={styles.toggleLabel}>
            Ver todos (incluye mensajes descartados por el filtro)
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.toggleRow}
          onPress={togglePendientes}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: soloPendientes }}
          accessibilityLabel="Solo pendientes de liquidar"
        >
          <View
            style={[styles.checkbox, soloPendientes && styles.checkboxChecked]}
          />
          <Text style={styles.toggleLabel}>Solo pendientes de liquidar</Text>
        </TouchableOpacity>

        {isAdmin && providersStatus !== null && (
          <ProvidersCard rows={providerRows} />
        )}

        {isAdmin && providersStatus !== null && (
          <CuotasCard rows={oddsProviderRows} status={providersStatus} />
        )}

        {isAdmin && picksStatus !== null && (
          <VerificacionCard
            status={picksStatus}
            onAnularResiduo={handleAnularResiduo}
          />
        )}

        {isAdmin && dupPicks.length > 0 && (
          <DuplicadosCard picks={dupPicks} />
        )}

        {loading && <ActivityIndicator size="large" color="#ff9f1c" />}
        {error && <Text style={styles.error}>Error: {error}</Text>}

        {channelGroups.length > 0 && (
          <ChannelChips
            groups={channelGroups}
            selectedId={selectedChannel?.id ?? null}
            onSelect={selectChannel}
          />
        )}

        {selectedChannel === null ? (
          <>
            {channelGroups.length === 0 && !loading && (
              <Text style={styles.emptyText}>
                Todavía no hay picks extraídos.
              </Text>
            )}
            {channelGroups.map((group) => (
              <ChannelSection
                key={group.key}
                group={group}
                cardProps={pickCardProps}
                onVerTodos={() =>
                  group.informanteId !== null &&
                  selectChannel({
                    id: group.informanteId,
                    name: group.name,
                  })
                }
              />
            ))}
          </>
        ) : (
          <Text style={styles.count}>
            {channelPicks.length} pick{channelPicks.length !== 1 ? "s" : ""}{" "}
            de {selectedChannel.name}
          </Text>
        )}
          </>
        }
        ListFooterComponent={
          selectedChannel !== null && hasMore ? (
            <TouchableOpacity
              style={styles.loadMore}
              onPress={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? (
                <ActivityIndicator size="small" color="#ff9f1c" />
              ) : (
                <Text style={styles.loadMoreText}>Cargar más</Text>
              )}
            </TouchableOpacity>
          ) : null
        }
      />
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
});
