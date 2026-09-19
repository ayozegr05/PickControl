import { apiRequest } from "./client";

export type StatsBloque = {
  total: number;
  aciertos: number;
  ganancias: number;
  porcentaje: number;
  yield_pct: number;
  pendientes: number;
};

export type OddsBloque = {
  /** Picks simples evaluados. */
  picks: number;
  /** Enlazados a un evento del proveedor de odds. */
  con_evento: number;
  /** Con opción de mercado comparable encontrada. */
  mapeados: number;
  pct_mapeado: number;
  /** Con cuota tipster + cuota al publicar: disponibilidad auditable. */
  auditables: number;
  /** Cuota anunciada que no existía en mercado al publicar. */
  cuotas_infladas: number;
  pct_cuota_inflada: number;
  /** Con cuota de cierre: CLV calculable. */
  con_clv: number;
  /** Media de CLV % (null sin datos): positivo = bate al mercado. */
  clv_medio: number | null;
  pct_bate_cierre: number;
};

export type AnalisisCanal = {
  informante_id: number;
  informante: string;
  tipster: StatsBloque;
  yo: StatsBloque;
  /** Cuántos picks del tipster registró el usuario ("Yo también la jugué"). */
  jugadas: number;
  cuota_media_tipster: number | null;
  cuota_media_mia: number | null;
  /** Combinadas del canal (solo padres), fuera de las stats de simples. */
  combinadas?: StatsBloque | null;
  /** Auditoría de cuotas del canal (tipster vs mercado). */
  odds?: OddsBloque | null;
};

export type AnalisisDeporte = {
  deporte: string;
  tipster: StatsBloque;
  yo: StatsBloque;
};

export type AnalisisGlobal = {
  canales: AnalisisCanal[];
  deportes: AnalisisDeporte[];
  totales_tipster: StatsBloque;
  totales_yo: StatsBloque;
  /** Combinadas agregadas de todos los canales (solo padres). */
  totales_combinadas?: StatsBloque | null;
  /** Auditoría de cuotas agregada de todos los canales. */
  totales_odds?: OddsBloque | null;
};

export async function getAnalisisGlobal(): Promise<AnalisisGlobal> {
  return apiRequest<AnalisisGlobal>("/analisis");
}
