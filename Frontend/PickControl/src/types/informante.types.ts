// Equivalente TS de backend/app/schemas/informante.py
import { PickItem } from "./pick.types";

export interface Informante {
  id: number;
  nombre: string;
}

export interface InformanteStats {
  informante: string;
  totalApuestas: number;
  totalAciertos: number;
  ganancias: number;
  porcentajeAciertos: number;
  yieldPct: number;
  apuestas: PickItem[];

  // Picks extraídos automáticamente de Telegram.
  parsedPicks: PickItem[];
  parsedTotalApuestas: number;
  parsedTotalAciertos: number;
  parsedGanancias: number;
  parsedPorcentajeAciertos: number;
  parsedYieldPct: number;
}

export interface InformanteSummary {
  informante: string;

  // Apuestas manuales.
  manualTotal: number;
  manualAciertos: number;
  manualGanancias: number;
  manualPorcentaje: number;
  manualYield: number;

  // Picks de Telegram.
  parsedTotal: number;
  parsedAciertos: number;
  parsedGanancias: number;
  parsedPorcentaje: number;
  parsedYield: number;

  // Totales combinados.
  total: number;
  aciertos: number;
  ganancias: number;
  porcentaje: number;
  yieldPct: number;
}
