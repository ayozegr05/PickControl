// Cliente HTTP central para hablar con el backend FastAPI (backend/app/main.py).
//
// Sustituye a los `fetch(...)` repetidos en cada pantalla (login.tsx,
// index.tsx, [informante].tsx, etc.) que usaban directamente
// `process.env.EXPO_PUBLIC_API_BASE_URL` + rutas antiguas de Node
// (`/login`, `/apuestas`...). El backend FastAPI expone todo bajo el
// prefijo `/api/v1`.
import AsyncStorage from "@react-native-async-storage/async-storage";

const BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  /** Si es true, adjunta `Authorization: Bearer <token>` leyendo el
   * token guardado por AuthContext en AsyncStorage. */
  auth?: boolean;
};

/** Extrae un mensaje legible de un error de FastAPI.
 *
 * FastAPI usa `{"detail": "mensaje"}` para HTTPException, o
 * `{"detail": [{"msg": "...", ...}, ...]}` para errores 422 de
 * validación de Pydantic. El backend Node usaba `{"message": "..."}`,
 * por eso las pantallas antiguas leían `data.message`.
 */
function extractErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((e) => e?.msg ?? JSON.stringify(e)).join(", ");
    }
  }
  return fallback;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = false } = options;

  const headers: Record<string, string> = { "Content-Type": "application/json" };

  if (auth) {
    const token = await AsyncStorage.getItem("userToken");
    if (!token) {
      throw new ApiError("No has iniciado sesión", 401);
    }
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  // 204 No Content (p. ej. DELETE /apuestas/{id}) no tiene body que parsear.
  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(extractErrorMessage(data, "Error en la solicitud"), response.status);
  }

  return data as T;
}
