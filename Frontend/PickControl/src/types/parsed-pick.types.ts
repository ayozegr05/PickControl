// Tipos de dominio de picks parseados de Telegram: lo que consumen
// pantallas, componentes y hooks (no los payloads de request, que
// viven junto a su endpoint en src/api/).

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
