// API de canales monitorizados (backend: app/api/v1/channels.py).
// snake_case del backend -> camelCase para la app.
import { apiRequest } from "./client";
import { Canal, CanalDisponible } from "../types/channel.types";

type RawChannel = {
  id: number;
  target: string;
  channel_id: number | null;
  name: string | null;
  username: string | null;
  activo: boolean;
  created_at: string;
};

type RawAvailableChannel = {
  channel_id: number;
  name: string;
  username: string | null;
  monitorizado: boolean;
};

function toCanal(raw: RawChannel): Canal {
  return {
    id: raw.id,
    target: raw.target,
    channelId: raw.channel_id,
    name: raw.name,
    username: raw.username,
    activo: raw.activo,
    createdAt: raw.created_at,
  };
}

export async function listCanales(): Promise<Canal[]> {
  const raws = await apiRequest<RawChannel[]>("/channels", { auth: true });
  return raws.map(toCanal);
}

export async function listCanalesDisponibles(): Promise<CanalDisponible[]> {
  const raws = await apiRequest<RawAvailableChannel[]>("/channels/disponibles", {
    auth: true,
  });
  return raws.map((raw) => ({
    channelId: raw.channel_id,
    name: raw.name,
    username: raw.username,
    monitorizado: raw.monitorizado,
  }));
}

export async function addCanal(target: string): Promise<Canal> {
  const raw = await apiRequest<RawChannel>("/channels", {
    method: "POST",
    body: { target },
    auth: true,
  });
  return toCanal(raw);
}

export async function setCanalActivo(
  id: number,
  activo: boolean
): Promise<Canal> {
  const raw = await apiRequest<RawChannel>(`/channels/${id}`, {
    method: "PATCH",
    body: { activo },
    auth: true,
  });
  return toCanal(raw);
}

export async function deleteCanal(id: number): Promise<void> {
  return apiRequest<void>(`/channels/${id}`, {
    method: "DELETE",
    auth: true,
  });
}
