// Máquina de estado de la pantalla parsed-picks (Fase B):
// overview top-N por canal + lista paginada por canal seleccionado
// + filtros showAll/soloPendientes que viajan al backend.
//
// No auto-fetchea al montar: la pantalla llama a reload() dentro de
// su useFocusEffect (igual que hoy) para evitar doble fetch en el
// primer foco.
import { useCallback, useState } from "react";
import { getParsedPicks } from "@/src/api/parsed-picks.api";
import { ParsedPick } from "@/src/types/parsed-pick.types";
import { listInformantes } from "@/src/api/informantes.api";

export const OVERVIEW_PER_CHANNEL = 2;
export const CHANNEL_PAGE_SIZE = 20;

export type CanalSeleccionado = { id: number; name: string };

export function useParsedPicks() {
  const [overviewPicks, setOverviewPicks] = useState<ParsedPick[]>([]);
  const [channelNames, setChannelNames] = useState<string[]>([]);
  const [channelPicks, setChannelPicks] = useState<ParsedPick[]>([]);
  const [selectedChannel, setSelectedChannel] =
    useState<CanalSeleccionado | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [soloPendientes, setSoloPendientes] = useState(false);

  const loadOverview = useCallback(
    async (includeDiscarded: boolean, pending: boolean) => {
      const [picksData, informantesData] = await Promise.all([
        getParsedPicks({
          perChannel: OVERVIEW_PER_CHANNEL,
          soloApuestas: !includeDiscarded,
          soloPendientes: pending,
        }),
        // Los canales reales vienen de /informantes, para que un canal
        // sin picks en el filtro actual siga apareciendo vacío.
        listInformantes(),
      ]);
      setOverviewPicks(picksData);
      setChannelNames(informantesData.map((i) => i.informante));
    },
    []
  );

  const loadChannel = useCallback(
    async (
      informanteId: number,
      offset: number,
      includeDiscarded: boolean,
      pending: boolean
    ) => {
      const data = await getParsedPicks({
        informanteId,
        soloApuestas: !includeDiscarded,
        soloPendientes: pending,
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
    setLoading(true);
    try {
      await loadOverview(showAll, soloPendientes);
      if (selectedChannel) {
        await loadChannel(selectedChannel.id, 0, showAll, soloPendientes);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }, [loadOverview, loadChannel, selectedChannel, showAll, soloPendientes]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await reload();
    setRefreshing(false);
  }, [reload]);

  const selectChannel = useCallback(
    async (channel: CanalSeleccionado | null) => {
      setSelectedChannel(channel);
      setChannelPicks([]);
      setHasMore(false);
      if (channel) {
        try {
          await loadChannel(channel.id, 0, showAll, soloPendientes);
          setError(null);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Error desconocido");
        }
      }
    },
    [loadChannel, showAll, soloPendientes]
  );

  const loadMore = useCallback(async () => {
    if (!selectedChannel || loadingMore) return;
    setLoadingMore(true);
    try {
      await loadChannel(
        selectedChannel.id,
        channelPicks.length,
        showAll,
        soloPendientes
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setLoadingMore(false);
    }
  }, [
    selectedChannel,
    loadingMore,
    channelPicks.length,
    loadChannel,
    showAll,
    soloPendientes,
  ]);

  // Los filtros se aplican en el backend ANTES del top-N: hay que
  // refetchear overview y canal abierto al cambiarlos.
  const toggleShowAll = useCallback(async () => {
    const next = !showAll;
    setShowAll(next);
    setLoading(true);
    try {
      await loadOverview(next, soloPendientes);
      if (selectedChannel) {
        await loadChannel(selectedChannel.id, 0, next, soloPendientes);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }, [showAll, loadOverview, loadChannel, selectedChannel, soloPendientes]);

  const togglePendientes = useCallback(async () => {
    const next = !soloPendientes;
    setSoloPendientes(next);
    setLoading(true);
    try {
      await loadOverview(showAll, next);
      if (selectedChannel) {
        await loadChannel(selectedChannel.id, 0, showAll, next);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }, [soloPendientes, loadOverview, loadChannel, selectedChannel, showAll]);

  return {
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
    // Setters para las mutaciones optimistas de la pantalla (corregir
    // acierto actualiza la fila sin refetch).
    setOverviewPicks,
    setChannelPicks,
    setError,
  };
}
