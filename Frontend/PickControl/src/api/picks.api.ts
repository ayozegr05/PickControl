// Migrado de los `fetch` inline en app/index.tsx, app/screens/add-pick.tsx,
// app/dynamic-routes/[informante].tsx y app/components/top-bar.tsx.
//
// Antes (Node): GET/POST /apuestas devolvía `{ picks: [...] }` con campos
// en PascalCase (Apuesta, CantidadApostada, _id...).
// Ahora (FastAPI): GET/POST /apuestas devuelve directamente la lista/objeto
// en snake_case. Esta capa traduce esos campos a los tipos camelCase de
// `src/types/pick.types.ts` para que el resto de la app no tenga que
// conocer la forma exacta de la respuesta del backend.
import { apiRequest } from "./client";
import {
  Acierto,
  PickCreatePayload,
  PickItem,
  PickSource,
  PickUpdatePayload,
} from "../types/pick.types";

export type RawPick = {
  id: number;
  apuesta: string;
  informante: string;
  tipo_de_apuesta: string;
  acierto: Acierto;
  casa: string;
  cantidad_apostada: number;
  cuota: number;
  fecha: string;
  source: PickSource;
  ganancia?: number | null;
  evento?: string | null;
  es_reto?: boolean;
  parsed_pick_id?: number | null;
};

export function toPickItem(raw: RawPick): PickItem {
  return {
    id: raw.id,
    apuesta: raw.apuesta,
    informante: raw.informante,
    tipoDeApuesta: raw.tipo_de_apuesta,
    acierto: raw.acierto,
    casa: raw.casa,
    cantidadApostada: raw.cantidad_apostada,
    cuota: raw.cuota,
    fecha: raw.fecha,
    source: raw.source,
    ganancia: raw.ganancia ?? undefined,
    evento: raw.evento ?? null,
    esReto: raw.es_reto ?? false,
    parsedPickId: raw.parsed_pick_id ?? null,
  };
}

export async function listPicks(): Promise<PickItem[]> {
  const raw = await apiRequest<RawPick[]>("/apuestas");
  return raw.map(toPickItem);
}

export async function createPick(
  payload: PickCreatePayload
): Promise<PickItem> {
  const raw = await apiRequest<RawPick>("/apuestas", {
    method: "POST",
    auth: true,
    body: {
      apuesta: payload.apuesta,
      informante: payload.informante,
      tipo_de_apuesta: payload.tipoDeApuesta,
      casa: payload.casa,
      acierto: payload.acierto,
      cantidad_apostada: payload.cantidadApostada,
      cuota: payload.cuota,
    },
  });
  return toPickItem(raw);
}

export async function updatePick(
  id: number,
  payload: PickUpdatePayload
): Promise<PickItem> {
  const raw = await apiRequest<RawPick>(`/apuesta/${id}`, {
    method: "PUT",
    auth: true,
    body: {
      acierto: payload.acierto,
      fecha: payload.fecha,
    },
  });
  return toPickItem(raw);
}

export function deletePick(id: number): Promise<void> {
  return apiRequest<void>(`/apuestas/${id}`, { method: "DELETE", auth: true });
}
