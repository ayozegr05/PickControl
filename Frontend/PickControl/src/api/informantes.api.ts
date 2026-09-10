// Migrado del `fetch` inline en app/dynamic-routes/[informante].tsx.
// Antes (Node): GET /informante/:informante -> { totalApuestas, totalAciertos,
// porcentajeAciertos, apuestas } (sin Yield).
// Ahora (FastAPI): GET /api/v1/informante/{nombre} -> snake_case + yield_pct
// (metrica nueva). Se traduce a InformanteStats (camelCase).
import { apiRequest } from "./client";
import { InformanteStats } from "../types/informante.types";
import { RawPick, toPickItem } from "./picks.api";

type RawInformanteStats = {
  informante: string;
  total_apuestas: number;
  total_aciertos: number;
  ganancias: number;
  porcentaje_aciertos: number;
  yield_pct: number;
  apuestas: RawPick[];
};

export async function getInformanteStats(
  nombre: string
): Promise<InformanteStats> {
  const raw = await apiRequest<RawInformanteStats>(
    `/informante/${encodeURIComponent(nombre)}`
  );
  return {
    informante: raw.informante,
    totalApuestas: raw.total_apuestas,
    totalAciertos: raw.total_aciertos,
    ganancias: raw.ganancias,
    porcentajeAciertos: raw.porcentaje_aciertos,
    yieldPct: raw.yield_pct,
    apuestas: raw.apuestas.map(toPickItem),
  };
}
