// Equivalente TS de backend/app/schemas/pick.py
// Nota: la entidad se llama `PickItem` (y no `Pick`) para no chocar con el
// tipo utilitario `Pick<T, K>` de TypeScript.

export type Acierto = "Pending" | "True" | "False";
export type PickSource = "manual" | "telegram";

/** Una pata de una combinada (cada una se verifica por separado). */
export interface CombinadaPata {
  id: number;
  orden?: number | null;
  seleccion?: string | null;
  evento?: string | null;
  mercado?: string | null;
  linea?: number | null;
  cuota?: number | null;
  fecha_evento?: string | null;
  acierto?: boolean | null;
  anulada: boolean;
}

export interface PickItem {
  id: number;
  apuesta: string;
  informante: string;
  tipoDeApuesta: string;
  acierto: Acierto;
  casa: string;
  /** En picks de Telegram pueden ser null (el tipster no siempre
   * publica cuota o stake). La UI muestra "—" en ese caso. */
  cantidadApostada: number | null;
  cuota: number | null;
  fecha: string; // ISO 8601
  source: PickSource;
  ganancia?: number;
  evento?: string | null;
  /** Solo picks de Telegram: apuesta de "reto" del tipster (sección aparte). */
  esReto?: boolean;
  /** Apuestas creadas desde un pick de Telegram ("Yo también la jugué"):
   * id del parsed_pick de origen. Ausente en apuestas dadas de alta a mano. */
  parsedPickId?: number | null;
  /** Solo picks de Telegram: combinada del tipster (sección propia).
   * `patas` lleva cada selección con su resultado; `cuotaEfectiva` es la
   * cuota real tras excluir anuladas (null si no se pudo recalcular). */
  esCombinada?: boolean;
  cuotaEfectiva?: number | null;
  patas?: CombinadaPata[];
}

export interface PickCreatePayload {
  apuesta: string;
  informante: string;
  tipoDeApuesta: string;
  casa: string;
  acierto?: Acierto;
  cantidadApostada?: number;
  cuota?: number;
}

export interface PickUpdatePayload {
  acierto?: Acierto;
  fecha?: string;
}
