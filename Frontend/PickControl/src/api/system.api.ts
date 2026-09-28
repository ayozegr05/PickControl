// Vista de sistema (solo admin): estado de cuotas de los providers de
// resultados/cuotas — quién está sin cuota hoy y misses registrados.
import { apiRequest } from "./client";

export interface ProvidersStatus {
  rate_limited: Record<string, string>;
  missed_by_provider: Record<string, number>;
  calls_today: Record<string, number>;
  /** Liquidaciones auto atribuidas por provider (verificado_provider). */
  resolved_by_provider?: Record<string, number>;
  calls_by_day: Record<string, Record<string, number>>;
  // Límite diario observado vía headers x-ratelimit (solo providers
  // que lo reportan).
  daily_limits: Record<string, number>;
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
  // Desglose de pendientes verificables por API (simples + patas):
  // jugadas <14d (el ciclo 3h reintenta), futuras/en juego, backlog
  // >14d (solo verify_backlog) y sin fecha_evento (no verificables).
  pendientes_jugados_ventana: number;
  pendientes_futuros: number;
  pendientes_backlog: number;
  pendientes_sin_fecha: number;
  pendientes_por_deporte: Record<string, number>;
  resueltas_hoy: number;
  resueltas_hoy_evento_hoy: number;
  resueltas_hoy_evento_previo: number;
  resueltas_total: number;
  resueltas_por_dia: Record<string, number>;
  // Liquidaciones por hora UTC ("YYYY-MM-DD HH:00") — aproxima cada
  // pasada del verifier (cada ~3 h).
  resueltas_por_pasada: Record<string, number>;
}

export function getPicksStatus(): Promise<PicksStatus> {
  return apiRequest<PicksStatus>("/system/picks", { auth: true });
}
