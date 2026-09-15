import { apiRequest } from "./client";

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
  raw_message_id: number;
  created_at: string;
};

export async function getParsedPicks(): Promise<ParsedPick[]> {
  return apiRequest<ParsedPick[]>("/telegram/parsed-picks", { auth: true });
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
