// Vista de sistema (solo admin): estado de cuotas de los providers de
// resultados/cuotas — quién está sin cuota hoy y misses registrados.
import { apiRequest } from "./client";

/** Embudo del backlog de cuotas históricas (backend lo calcula por
 * pick cruzando marcas de fuentes). `en_cola` = trabajo real;
 * `aplazados` = solo esperan a providers en quota larga. */
/** Resumen de una pasada del backfill (guardado en provider_state). */
export interface BackfillPass {
  at?: string;
  revisados?: number;
  filas?: number;
  con_fixture?: number;
  sin_fixture?: number;
  sin_historial?: number;
  sin_opcion?: number;
  mapeados?: number;
  /** Picks que no se llegaron a intentar (presupuesto diario o error). */
  sin_revisar?: number;
  /** Filas escritas por fuente en la pasada ("tennisdata" -> 87). */
  filas_por_fuente?: Record<string, number>;
}

export interface BackfillStatus {
  total: number;
  resueltos: number;
  /** Picks (distintos) cuya cuota aportó cada fuente de backfill. */
  resueltos_por_fuente?: Record<string, number>;
  en_cola: number;
  aplazados: number;
  agotados: number;
  por_fuente: Record<string, number>;
  gemini_pendientes: number;
  fuentes_en_pausa: Record<string, string>;
  ultima_pasada?: BackfillPass | null;
  /** Últimas ~10 pasadas (más reciente al final) — ritmo del robot. */
  ultimas_pasadas?: BackfillPass[] | null;
}

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
  /** Snapshots de cuotas escritos por provider (capa de cuotas). */
  odds_snapshots_by_provider?: Record<string, number>;
  /** Filas de cuotas escritas por día UTC ("YYYY-MM-DD" -> n). */
  odds_snapshots_por_dia?: Record<string, number>;
  /** Eventos con cuotas registrados en odds_events. */
  odds_events_count?: number;
  /** Picks pendientes de backfill histórico de cuotas. */
  odds_backfill_pending?: number;
  /** Embudo del backlog (en_cola vs aplazados por quota). */
  odds_backfill?: BackfillStatus | null;
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
  // Liquidaciones por hora UTC ("YYYY-MM-DD HH:00"), solo del día
  // actual — aproxima cada pasada del verifier (cada ~3 h).
  resueltas_por_pasada: Record<string, number>;
  // Barrido correctivo diario de anuladas sospechosas (D+1/D+3/D+6):
  // día -> contadores de la pasada.
  rechecks_por_dia: Record<
    string,
    {
      procesadas?: number;
      corregidas?: number;
      confirmadas?: number;
      sin_datos?: number;
    }
  >;
}

export function getPicksStatus(): Promise<PicksStatus> {
  return apiRequest<PicksStatus>("/system/picks", { auth: true });
}
