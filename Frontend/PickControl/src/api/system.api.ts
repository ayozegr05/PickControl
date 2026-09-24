// Vista de sistema (solo admin): estado de cuotas de los providers de
// resultados/cuotas — quién está sin cuota hoy y misses registrados.
import { apiRequest } from "./client";

export interface ProvidersStatus {
  rate_limited: Record<string, string>;
  missed_by_provider: Record<string, number>;
  calls_today: Record<string, number>;
  calls_by_day: Record<string, Record<string, number>>;
}

export function getProvidersStatus(): Promise<ProvidersStatus> {
  return apiRequest<ProvidersStatus>("/system/providers", { auth: true });
}

// Cola de verificación de picks (solo admin): pendientes desglosadas
// en simples / patas de combinada / padres, y liquidaciones de hoy.
export interface PicksStatus {
  pendientes_simples: number;
  pendientes_patas: number;
  pendientes_combinadas: number;
  resueltas_hoy: number;
  resueltas_hoy_evento_hoy: number;
  resueltas_hoy_evento_previo: number;
  resueltas_total: number;
  resueltas_por_dia: Record<string, number>;
}

export function getPicksStatus(): Promise<PicksStatus> {
  return apiRequest<PicksStatus>("/system/picks", { auth: true });
}
