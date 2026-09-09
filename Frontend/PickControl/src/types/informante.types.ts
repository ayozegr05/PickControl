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
  apuestas: PickItem[];
}
