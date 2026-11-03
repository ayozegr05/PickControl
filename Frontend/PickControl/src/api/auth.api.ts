// Migrado de los `fetch` inline en app/screens/login.tsx y register.tsx.
// Antes: POST /register, POST /login (Node). Ahora: POST /api/v1/auth/register,
// POST /api/v1/auth/login (FastAPI) — mismo payload y forma de respuesta.
import { apiRequest } from "./client";
import {
  AuthResponse,
  LoginPayload,
  RegisterPayload,
} from "../types/user.types";

export function register(payload: RegisterPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse>("/auth/register", {
    method: "POST",
    body: payload,
  });
}

export function login(payload: LoginPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse>("/auth/login", {
    method: "POST",
    body: payload,
  });
}

// Canje de refresh token (login biométrico): el móvil guarda este
// token de larga duración en SecureStore en vez de la contraseña.
export function refresh(refreshToken: string): Promise<AuthResponse> {
  return apiRequest<AuthResponse>("/auth/refresh", {
    method: "POST",
    body: { refresh_token: refreshToken },
  });
}

// Revoca la familia de sesión del refresh token en el servidor —
// la credencial biométrica del dispositivo queda muerta de verdad
// (no solo borrada en local). Pensado para "cerrar sesión en este
// dispositivo" desde ajustes; el logout normal NO lo llama para no
// romper el re-login biométrico.
export function logoutSession(
  refreshToken: string
): Promise<{ message: string; revoked: number }> {
  return apiRequest("/auth/logout", {
    method: "POST",
    body: { refresh_token: refreshToken },
  });
}
