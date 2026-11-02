// Ranking/resumen de informantes (GET /informantes) — usado por el
// home y cualquier pantalla que liste canales.
import { useCallback } from "react";
import { listInformantes } from "@/src/api/informantes.api";
import { InformanteSummary } from "@/src/types/informante.types";
import { useApiData } from "./useApiData";

export type InformantesData = {
  informantes: InformanteSummary[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

export function useInformantes(enabled = true): InformantesData {
  const fetcher = useCallback(() => listInformantes(), []);
  const { data, loading, error, reload } = useApiData(fetcher, enabled);
  return { informantes: data ?? [], loading, error, reload };
}
