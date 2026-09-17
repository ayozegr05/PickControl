import { apiRequest } from "./client";
import { RawPick } from "./picks.api";

export type ParsedPick = {
  id: number;
  es_apuesta: boolean;
  apuesta: string | null;
  deporte: string | null;
  evento: string | null;
  mercado: string | null;
  seleccion: string | null;
  cuota: number | null;
  stake: number | null;
  casa: string | null;
  informante: string | null;
  explicacion: string | null;
  metodo: string;
  confianza: number;
  fecha_evento: string | null;
  linea: number | null;
  acierto: boolean | null;
  anulada: boolean;
  verificado_por: string | null;
  informante_id: number | null;
  raw_message_id: number;
  created_at: string;
  es_reto: boolean;
};

export type ParsedPicksQuery = {
  /** Filtra los picks de un único canal (vista detalle paginada). */
  informanteId?: number;
  /** Vista resumen: los N picks más recientes de cada canal. */
  perChannel?: number;
  /** Si es true, el backend filtra es_apuesta antes de paginar. */
  soloApuestas?: boolean;
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
