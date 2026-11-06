// Vista de sistema (solo admin): estado de cuotas de los providers de
// resultados/cuotas — quién está sin cuota hoy y misses registrados.
// Los tipos de dominio viven en src/types/system.types.ts.
import { apiRequest } from "./client";
import { PicksStatus, ProvidersStatus } from "@/src/types/system.types";

export function getProvidersStatus(): Promise<ProvidersStatus> {
  return apiRequest<ProvidersStatus>("/system/providers", { auth: true });
}

export function getPicksStatus(): Promise<PicksStatus> {
  return apiRequest<PicksStatus>("/system/picks", { auth: true });
}
