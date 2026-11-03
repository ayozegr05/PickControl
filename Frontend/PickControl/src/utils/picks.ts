// Lógica de negocio de apuestas compartida.
//
// Extraída de los duplicados de pantalla ([informante], my-picks,
// analysis, earns, parsed-picks). Cada función conserva la semántica
// del sitio original; las diferencias entre copias quedan
// documentadas en los tests de caracterización.
import { Acierto, PickItem } from "@/src/types/pick.types";

/** Ganancia neta de una apuesta (variante de las pantallas de picks):
 * pendiente o datos incompletos → 0; acierto → cantidad*(cuota-1);
 * fallo → -cantidad. Redondeo a 2 decimales como en [informante].
 *
 * Nota: la versión original también devolvía 0 si `apuestas` de la
 * pantalla estaba vacía (closure sobre el estado del componente) —
 * condición inalcanzable en la práctica, eliminada en la extracción. */
export function gananciaApuesta(
  cantidadApostada: number | null,
  cuota: number | null,
  acierto: Acierto
): number {
  if (acierto === "Pending" || cantidadApostada == null || cuota == null) {
    return 0;
  }
  if (acierto === "True") {
    return Number((cantidadApostada * (cuota - 1)).toFixed(2));
  }
  return Number((-cantidadApostada).toFixed(2));
}

/** Variante de `analysis`: `acierto` booleano y SIN redondeo
 * (sus resultados alimentan ROI/proyección con la precisión cruda). */
export function gananciaNeta(
  cantidad: number,
  cuota: number,
  acierto: boolean
): number {
  return acierto ? cantidad * (cuota - 1) : -cantidad;
}

/** Forma mínima que necesitan las métricas agregadas (analysis mapea
 * su `Apuesta` local a esto en el call site). `fecha` es opcional:
 * las métricas por ventana temporal la necesitan, las globales no. */
export type EstadisticaInput = {
  cantidad: number;
  cuota: number;
  acierto: boolean;
  fecha?: string | null;
};

/** ROI % = ganancias/inversión·100; 0 si no hay inversión. */
export function calcularROI(apuestas: EstadisticaInput[]): number {
  const inversion = apuestas.reduce((sum, a) => sum + a.cantidad, 0);
  const ganancias = apuestas.reduce(
    (sum, a) => sum + gananciaNeta(a.cantidad, a.cuota, a.acierto),
    0
  );
  return inversion > 0 ? (ganancias / inversion) * 100 : 0;
}

export type Estadisticas = {
  porcentajeAciertos: number;
  roi: number;
  tendencia: number;
  apuestasDiarias: number;
  totalApuestas: number;
  aciertos: number;
};

const DIA_MS = 24 * 3600 * 1000;

const dentroDe = (a: EstadisticaInput, desde: number, hasta: number) => {
  if (!a.fecha) return false;
  const t = new Date(a.fecha).getTime();
  return Number.isFinite(t) && t >= desde && t <= hasta;
};

/** Stats del bloque "Análisis": ventana real de los últimos 30 días
 * por `fecha` de la apuesta (no las últimas 30 apuestas — la etiqueta
 * era incorrecta). Apuestas sin fecha no entran: no se les puede
 * asignar un día sin inventarlo. `hoy` inyectable para tests.
 * Tendencia = ROI(0–14d) − ROI(15–28d). null si la ventana está vacía. */
export function calcularEstadisticas(
  apuestas: EstadisticaInput[],
  hoy: Date = new Date()
): Estadisticas | null {
  const fin = hoy.getTime();
  const apuestasRecientes = apuestas.filter((a) =>
    dentroDe(a, fin - 30 * DIA_MS, fin)
  );
  if (apuestasRecientes.length === 0) return null;

  const totalApuestas = apuestasRecientes.length;
  const aciertos = apuestasRecientes.filter((a) => a.acierto).length;
  const porcentajeAciertos = (aciertos / totalApuestas) * 100;
  const roi = calcularROI(apuestasRecientes);

  const roi2Semanas = calcularROI(
    apuestasRecientes.filter((a) => dentroDe(a, fin - 14 * DIA_MS, fin))
  );
  const roiAnterior = calcularROI(
    apuestasRecientes.filter((a) =>
      dentroDe(a, fin - 28 * DIA_MS, fin - 14 * DIA_MS)
    )
  );
  const tendencia = roi2Semanas - roiAnterior;

  return {
    porcentajeAciertos,
    roi,
    tendencia,
    apuestasDiarias: totalApuestas / 30,
    totalApuestas,
    aciertos,
  };
}

export type Proyeccion = {
  inversionMensual: number;
  gananciaProyectada: number;
  porcentajeAciertos: number;
  cuotaMedia: number;
};

/** Proyección mensual de `analysis`: últimas 30 apuestas del
 * informante → cuota media + % aciertos → ganancia esperada con
 * `apuestasPorDia` × `inversionPorApuesta` × 30 días. */
export function calcularProyeccion(
  apuestas: EstadisticaInput[],
  inversionPorApuesta: number,
  apuestasPorDia: number
): Proyeccion {
  const apuestasRecientes = apuestas.slice(-30);
  const totalApuestas = apuestasRecientes.length;
  const aciertos = apuestasRecientes.filter((a) => a.acierto).length;
  const porcentajeAciertos =
    totalApuestas > 0 ? (aciertos / totalApuestas) * 100 : 0;
  const cuotaMedia =
    apuestasRecientes.reduce((sum, a) => sum + a.cuota, 0) / totalApuestas;

  const diasMes = 30;
  const inversionMensual = inversionPorApuesta * apuestasPorDia * diasMes;
  const apuestasMensuales = apuestasPorDia * diasMes;
  const apuestasGanadoras = apuestasMensuales * (porcentajeAciertos / 100);

  const gananciaProyectada =
    apuestasGanadoras * inversionPorApuesta * cuotaMedia -
    apuestasMensuales * inversionPorApuesta;

  return {
    inversionMensual,
    gananciaProyectada,
    porcentajeAciertos,
    cuotaMedia,
  };
}

export type NivelRiesgo = "BAJO" | "MEDIO" | "ALTO";

/** Clasificación de riesgo del análisis (límites estrictos `>` —
 * 65 exacto no es BAJO, tendencia negativa tampoco). */
export function nivelRiesgo(stats: {
  porcentajeAciertos: number;
  roi: number;
  tendencia: number;
}): NivelRiesgo {
  if (stats.porcentajeAciertos > 65 && stats.roi > 15 && stats.tendencia >= 0) {
    return "BAJO";
  }
  if (stats.porcentajeAciertos > 50 && stats.roi > 5) {
    return "MEDIO";
  }
  return "ALTO";
}

/** Glifo del estado de una apuesta. Las pantallas difieren en el
 * pendiente: [informante] usa "❓", my-picks usa "-". El caller elige
 * el glifo pendiente que le corresponde. */
export function iconoResultado(acierto: Acierto, pendiente = "❓"): string {
  if (acierto === "True") return "✅";
  if (acierto === "False") return "❌";
  return pendiente;
}

/** Glifo de pick anulada según motivo (↩️ push, 📅 aplazado,
 * 👤 jugador sin minutos, ⏰ expirada, ⊘ genérico). */
export function iconoAnulada(motivo?: string | null): string {
  switch (motivo) {
    case "push":
      return "↩️";
    case "aplazado":
      return "📅";
    case "jugador_fuera":
      return "👤";
    case "expired":
      return "⏰";
    default:
      return "⊘";
  }
}

export type RankingItem = { informante: string; porcentajeAcierto: number };

/** % acierto por informante (solo apuestas liquidadas) ordenado
 * descendente — el "top 5" de my-picks. */
export function rankingInformantes(
  porInformante: Record<string, PickItem[]>,
  top = 5
): RankingItem[] {
  return Object.entries(porInformante)
    .map(([informante, apuestas]) => {
      const finalizadas = apuestas.filter((a) => a.acierto !== "Pending");
      const aciertos = finalizadas.filter((a) => a.acierto === "True").length;
      const porcentajeAcierto =
        finalizadas.length > 0 ? (aciertos / finalizadas.length) * 100 : 0;
      return { informante, porcentajeAcierto };
    })
    .sort((a, b) => b.porcentajeAcierto - a.porcentajeAcierto)
    .slice(0, top);
}

/** Apuestas pendientes de todos los informantes (lista plana) —
 * la sección "Pendientes" de my-picks. */
export function pendientesDe(
  porInformante: Record<string, PickItem[]>
): PickItem[] {
  const pendientes: PickItem[] = [];
  Object.entries(porInformante).forEach(([informante, apuestas]) => {
    apuestas.forEach((apuesta) => {
      if (apuesta.acierto === "Pending") {
        pendientes.push({ ...apuesta, informante });
      }
    });
  });
  return pendientes;
}

/** Máximo de apuestas de un solo informante (dimensiona la tabla). */
export function maxApuestasPorInformante(
  porInformante: Record<string, PickItem[]>
): number {
  const counts = Object.values(porInformante).map((a) => a.length);
  const max = counts.length ? Math.max(...counts) : 0;
  return max > 0 ? max : 0;
}

/** Agregaciones de `earns`: suman `pick.ganancia` (campo del backend)
 * por clave. */
export function gananciasPor(
  picks: PickItem[],
  clave: "informante" | "casa"
): Record<string, number> {
  const ganancias: Record<string, number> = {};
  picks.forEach((pick) => {
    const key = pick[clave];
    ganancias[key] = (ganancias[key] ?? 0) + (pick.ganancia ?? 0);
  });
  return ganancias;
}

export function totalGanancias(picks: PickItem[]): number {
  return picks.reduce((total, pick) => total + (pick.ganancia ?? 0), 0);
}
