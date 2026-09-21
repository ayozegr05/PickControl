// Vista de sistema (solo admin): estado de cuotas de los providers de
// resultados/cuotas — quién está sin cuota hoy y misses registrados.
import { apiRequest } from "./client";

export interface ProvidersStatus {
  rate_limited: Record<string, string>;
  missed_by_provider: Record<string, number>;
}

export function getProvidersStatus(): Promise<ProvidersStatus> {
  return apiRequest<ProvidersStatus>("/system/providers");
}
