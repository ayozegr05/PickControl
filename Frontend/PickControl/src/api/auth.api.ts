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
