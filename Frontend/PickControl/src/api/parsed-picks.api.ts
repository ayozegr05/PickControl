import { apiRequest } from "./client";
import { RawPick } from "./picks.api";
import { ParsedPick, PickOdds } from "@/src/types/parsed-pick.types";

export type ParsedPicksQuery = {
  /** Filtra los picks de un único canal (vista detalle paginada). */
  informanteId?: number;
  /** Vista resumen: los N picks más recientes de cada canal. */
  perChannel?: number;
  /** Si es true, el backend filtra es_apuesta antes de paginar. */
  soloApuestas?: boolean;
  /** Vista de revisión manual: solo picks sin liquidar
   * (acierto NULL, no anulados). Implica soloApuestas. */
  soloPendientes?: boolean;
  /** Solo filas anulada·duplicado (reposteos detectados por dedup):
   * alimenta la card "Duplicadas detectadas" de la vista depurativa. */
  soloDuplicados?: boolean;
  /** Incluye las anulada·duplicado mezcladas con el resto (por defecto
   * el backend las oculta: son ruido de ingesta, no resultados). */
  incluirDuplicados?: boolean;
  offset?: number;
  limit?: number;
};

export async function getParsedPicks(
  query: ParsedPicksQuery = {}
): Promise<ParsedPick[]> {
  const params: string[] = [];
  if (query.informanteId !== undefined) {
    params.push(`informante_id=${query.informanteId}`);
  }
  if (query.perChannel !== undefined) {
    params.push(`per_channel=${query.perChannel}`);
  }
  if (query.soloApuestas !== undefined) {
    params.push(`solo_apuestas=${query.soloApuestas}`);
  }
  if (query.soloPendientes !== undefined) {
    params.push(`solo_pendientes=${query.soloPendientes}`);
  }
  if (query.soloDuplicados !== undefined) {
    params.push(`solo_duplicados=${query.soloDuplicados}`);
  }
  if (query.incluirDuplicados !== undefined) {
    params.push(`incluir_duplicados=${query.incluirDuplicados}`);
  }
  if (query.offset !== undefined) {
    params.push(`offset=${query.offset}`);
  }
  if (query.limit !== undefined) {
    params.push(`limit=${query.limit}`);
  }
  const qs = params.length > 0 ? `?${params.join("&")}` : "";
  return apiRequest<ParsedPick[]>(`/telegram/parsed-picks${qs}`, {
    auth: true,
  });
}

export async function updateParsedPickAcierto(
  id: number,
  update: { acierto?: boolean | null; anulada?: boolean }
): Promise<ParsedPick> {
  return apiRequest<ParsedPick>(`/telegram/parsed-picks/${id}`, {
    method: "PATCH",
    body: update,
    auth: true,
  });
}

/** Anula en bloque los picks pendientes fuera de la ventana de
 * verificación (>14 días). Devuelve cuántos se anularon. */
export async function anularResiduoPendiente(): Promise<{ anuladas: number }> {
  return apiRequest<{ anuladas: number }>(
    `/telegram/parsed-picks/anular-residuo`,
    { method: "POST", auth: true }
  );
}

/** Cuota publicada por el tipster vs cuota real de mercado
 * (apertura / publicación / cierre + CLV y detector de cuotas infladas). */
export async function getPickOdds(id: number): Promise<PickOdds> {
  return apiRequest<PickOdds>(`/telegram/parsed-picks/${id}/odds`, {
    auth: true,
  });
}

export type JugarPickPayload = {
  /** Importe real apostado por el usuario (obligatorio). */
  cantidadApostada: number;
  /** Cuota real conseguida; si no se envía se copia la del tipster. */
  cuota?: number;
  /** Casa donde se jugó; si no se envía se copia la del tipster. */
  casa?: string;
};

/** Registra una apuesta del usuario copiada de un pick de Telegram
 * ("Yo también la jugué"). Devuelve la apuesta creada (o la existente
 * si ya se había registrado). */
export async function jugarParsedPick(
  id: number,
  payload: JugarPickPayload
): Promise<RawPick> {
  return apiRequest<RawPick>(`/telegram/parsed-picks/${id}/jugar`, {
    method: "POST",
    body: {
      cantidad_apostada: payload.cantidadApostada,
      cuota: payload.cuota,
      casa: payload.casa,
    },
    auth: true,
  });
}
