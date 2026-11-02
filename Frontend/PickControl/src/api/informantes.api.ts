// Migrado del `fetch` inline en app/dynamic-routes/[informante].tsx.
// Antes (Node): GET /informante/:informante -> { totalApuestas, totalAciertos,
// porcentajeAciertos, apuestas } (sin Yield).
// Ahora (FastAPI): GET /api/v1/informante/{nombre} -> snake_case + yield_pct
// (metrica nueva) y picks de Telegram (parsed_*). Se traduce a InformanteStats
// (camelCase).
import { apiRequest } from "./client";
import { InformanteStats, InformanteSummary } from "../types/informante.types";
import { RawPick, toPickItem } from "./picks.api";

type RawInformanteStats = {
  informante: string;
  total_apuestas: number;
  total_aciertos: number;
  ganancias: number;
  porcentaje_aciertos: number;
  yield_pct: number;
  apuestas: RawPick[];

  parsed_picks: RawPick[];
  parsed_total_apuestas: number;
  parsed_total_aciertos: number;
  parsed_ganancias: number;
  parsed_porcentaje_aciertos: number;
  parsed_yield_pct: number;

  combinadas_total: number;
  combinadas_aciertos: number;
  combinadas_ganancias: number;
  combinadas_porcentaje: number;
  combinadas_yield_pct: number;
  combinadas_pendientes: number;
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
    parsedPicks: raw.parsed_picks.map(toPickItem),
    parsedTotalApuestas: raw.parsed_total_apuestas,
    parsedTotalAciertos: raw.parsed_total_aciertos,
    parsedGanancias: raw.parsed_ganancias,
    parsedPorcentajeAciertos: raw.parsed_porcentaje_aciertos,
    parsedYieldPct: raw.parsed_yield_pct,
    combinadasTotal: raw.combinadas_total,
    combinadasAciertos: raw.combinadas_aciertos,
    combinadasGanancias: raw.combinadas_ganancias,
    combinadasPorcentaje: raw.combinadas_porcentaje,
    combinadasYieldPct: raw.combinadas_yield_pct,
    combinadasPendientes: raw.combinadas_pendientes,
  };
}

type RawInformanteSummary = {
  informante: string;
  manual_total: number;
  manual_aciertos: number;
  manual_ganancias: number;
  manual_porcentaje: number;
  manual_yield: number;
  manual_pendientes: number;
  parsed_total: number;
  parsed_aciertos: number;
  parsed_ganancias: number;
  parsed_porcentaje: number;
  parsed_yield: number;
  parsed_pendientes: number;
  total: number;
  aciertos: number;
  ganancias: number;
  porcentaje: number;
  yield_pct: number;
  pendientes: number;
};

export async function listInformantes(): Promise<InformanteSummary[]> {
  const raws = await apiRequest<RawInformanteSummary[]>("/informantes");
  return raws.map((raw) => ({
    informante: raw.informante,
    manualTotal: raw.manual_total,
    manualAciertos: raw.manual_aciertos,
    manualGanancias: raw.manual_ganancias,
    manualPorcentaje: raw.manual_porcentaje,
    manualYield: raw.manual_yield,
    parsedTotal: raw.parsed_total,
    parsedAciertos: raw.parsed_aciertos,
    parsedGanancias: raw.parsed_ganancias,
    parsedPorcentaje: raw.parsed_porcentaje,
    parsedYield: raw.parsed_yield,
    manualPendientes: raw.manual_pendientes,
    parsedPendientes: raw.parsed_pendientes,
    total: raw.total,
    aciertos: raw.aciertos,
    ganancias: raw.ganancias,
    porcentaje: raw.porcentaje,
    yieldPct: raw.yield_pct,
    pendientes: raw.pendientes,
  }));
}
