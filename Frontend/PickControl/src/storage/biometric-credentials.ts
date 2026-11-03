// Credencial que se guarda en SecureStore para el login biométrico
// (clave "userCredentials").
//
// Antes se guardaba {email, password} en JSON: aunque SecureStore
// cifra en disco, la contraseña quedaba persistida y el biométrico
// reventaba silenciosamente al cambiarla. Ahora se guarda el
// refresh token del backend (30 días) + el nombre/rol para la UI,
// y se canjea por un access token nuevo en POST /auth/refresh.
//
// El formato legacy {email,password} se sigue reconociendo: al
// usarse una vez con éxito, login.tsx lo sobreescribe con el nuevo
// formato — migración en caliente, sin pasada manual.

export interface RefreshCredential {
  refreshToken: string;
  userName: string;
  role: string;
}

export interface LegacyCredential {
  email: string;
  password: string;
}

export type StoredCredential = RefreshCredential | LegacyCredential;

export function isLegacyCredential(
  credential: StoredCredential
): credential is LegacyCredential {
  return "password" in credential;
}

export function serializeCredential(credential: RefreshCredential): string {
  return JSON.stringify(credential);
}

export function parseStoredCredential(
  raw: string | null
): StoredCredential | null {
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;

    if (
      typeof record.refreshToken === "string" &&
      record.refreshToken.length > 0 &&
      typeof record.userName === "string" &&
      typeof record.role === "string"
    ) {
      return {
        refreshToken: record.refreshToken,
        userName: record.userName,
        role: record.role,
      };
    }

    if (
      typeof record.email === "string" &&
      record.email.length > 0 &&
      typeof record.password === "string" &&
      record.password.length > 0
    ) {
      return { email: record.email, password: record.password };
    }

    return null;
  } catch {
    return null;
  }
}
