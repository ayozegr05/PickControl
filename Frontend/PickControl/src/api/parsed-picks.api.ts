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
  raw_message_id: number;
  created_at: string;
};

export async function getParsedPicks(): Promise<ParsedPick[]> {
  return apiRequest<ParsedPick[]>("/telegram/parsed-picks", { auth: true });
}
