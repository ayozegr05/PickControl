// Registro del dispositivo para notificaciones push (backend /devices).
// El backend hace upsert por token: re-registrar es idempotente y
// reactiva tokens deshabilitados.
import { apiRequest } from "./client";

export type DeviceTokenResponse = {
  id: number;
  token: string;
  platform: string;
  enabled: boolean;
};

export function registerDeviceToken(
  token: string,
  platform: string
): Promise<DeviceTokenResponse> {
  return apiRequest<DeviceTokenResponse>("/devices", {
    method: "POST",
    body: { token, platform },
    auth: true,
  });
}

export function unregisterDeviceToken(
  token: string,
  platform: string
): Promise<void> {
  return apiRequest<void>("/devices", {
    method: "DELETE",
    body: { token, platform },
    auth: true,
  });
}
