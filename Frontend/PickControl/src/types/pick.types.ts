// Equivalente TS de backend/app/schemas/pick.py
// Nota: la entidad se llama `PickItem` (y no `Pick`) para no chocar con el
// tipo utilitario `Pick<T, K>` de TypeScript.

export type Acierto = "Pending" | "True" | "False";
export type PickSource = "manual" | "telegram";

export interface PickItem {
  id: number;
  apuesta: string;
  informante: string;
  tipoDeApuesta: string;
  acierto: Acierto;
  casa: string;
  cantidadApostada: number;
  cuota: number;
  fecha: string; // ISO 8601
  source: PickSource;
  ganancia?: number;
  evento?: string | null;
  /** Solo picks de Telegram: apuesta de "reto" del tipster (sección aparte). */
  esReto?: boolean;
  /** Apuestas creadas desde un pick de Telegram ("Yo también la jugué"):
   * id del parsed_pick de origen. Ausente en apuestas dadas de alta a mano. */
  parsedPickId?: number | null;
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
