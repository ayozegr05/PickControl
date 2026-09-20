// Canales de Telegram monitorizados (backend: /api/v1/channels).

export type Canal = {
  id: number;
  target: string;
  channelId: number | null;
  name: string | null;
  username: string | null;
  activo: boolean;
  createdAt: string;
};

export type CanalDisponible = {
  channelId: number;
  name: string;
  username: string | null;
  monitorizado: boolean;
};
