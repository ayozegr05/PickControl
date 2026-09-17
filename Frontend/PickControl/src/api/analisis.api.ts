import { apiRequest } from "./client";

export type StatsBloque = {
  total: number;
  aciertos: number;
  ganancias: number;
  porcentaje: number;
  yield_pct: number;
  pendientes: number;
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
};

export async function getAnalisisGlobal(): Promise<AnalisisGlobal> {
  return apiRequest<AnalisisGlobal>("/analisis");
}
