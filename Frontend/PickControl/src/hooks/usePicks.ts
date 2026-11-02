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

// Referencia estable mientras data es null — `[]` inline crearía un
// array nuevo por render y haría buclear los useEffect([apuestas])
// de las pantallas (detectado por el smoke test de my-picks).
const EMPTY_PICKS: PickItem[] = [];

export function usePicks(enabled = true): PicksData {
  const fetcher = useCallback(() => listPicks(), []);
  const { data, loading, error, reload } = useApiData(fetcher, enabled);
  return { apuestas: data ?? EMPTY_PICKS, loading, error, reload };
}
