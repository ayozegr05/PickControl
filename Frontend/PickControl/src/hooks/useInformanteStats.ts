// Stats de un informante concreto (GET /informante/{nombre}) — la
// ruta dinámica [informante].tsx. Refetchea si cambia el nombre.
import { useCallback } from "react";
import { getInformanteStats } from "@/src/api/informantes.api";
import { useApiData } from "./useApiData";

export function useInformanteStats(nombre: string) {
  const fetcher = useCallback(() => getInformanteStats(nombre), [nombre]);
  return useApiData(fetcher);
}
