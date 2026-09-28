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
  /** Motivo del void ("push" | "aplazado" | "jugador_fuera" | "expired"),
   * null en anuladas históricas sin motivo registrado. */
  motivo_anulada: string | null;
  verificado_por: string | null;
  informante_id: number | null;
  raw_message_id: number;
  created_at: string;
  es_reto: boolean;
  /** True solo en el padre de una combinada (va en su propia sección). */
  es_combinada: boolean;
  /** En patas: id de la combinada padre. Las patas no llegan como filas
   * sueltas del listado, solo anidadas en `patas`. */
  combinada_id: number | null;
  /** Posición de la pata en el boleto (solo en patas). */
  orden: number | null;
  /** Cuota real de cobro tras excluir patas anuladas (null si no se pudo
   * recalcular — no se inventa la ganancia). */
  cuota_efectiva: number | null;
  /** Patas de la combinada (solo en el padre). */
  patas: ParsedPick[];
  /** Pendiente cuyo evento ya salió de la ventana de verificación
   * (>14 días): el verifier ya no lo reintenta, queda para corrección
   * manual o descarte. */
  fuera_ventana: boolean;
};

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

export type PickOdds = {
  /** False cuando el mercado del pick no tiene equivalente en el
   * proveedor (props de jugador, mercados no cubiertos) o el pick no
   * se enlazó a un evento: las cuotas quedan null, no se inventan. */
  mapeado: boolean;
  mercado_api: string | null;
  opcion_api: string | null;
  linea_api: string | null;
  cuota_tipster: number | null;
  cuota_apertura: number | null;
  /** Cuota de mercado más cercana a la hora de publicación del pick. */
  cuota_publicacion: number | null;
  cuota_cierre: number | null;
  capturas: number;
  /** true = la cuota anunciada existía en mercado al publicar;
   * false = cuota inflada/irreproducible. */
  cuota_disponible: boolean | null;
  /** % sobre el cierre: positivo = el tipster batió al mercado. */
  clv_pct: number | null;
};

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
