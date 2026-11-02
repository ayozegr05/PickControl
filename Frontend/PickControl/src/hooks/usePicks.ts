// Lista de apuestas del usuario (GET /apuestas) — el fetch
// duplicado de my-picks, analysis y earns. La pantalla decide
// cuándo recargar (sigue llamando a reload() en su useFocusEffect).
import { useCallback } from "react";
import { listPicks } from "@/src/api/picks.api";
import { PickItem } from "@/src/types/pick.types";
import { useApiData } from "./useApiData";

export type PicksData = {
  apuestas: PickItem[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

export function usePicks(enabled = true): PicksData {
  const fetcher = useCallback(() => listPicks(), []);
  const { data, loading, error, reload } = useApiData(fetcher, enabled);
  return { apuestas: data ?? [], loading, error, reload };
}
