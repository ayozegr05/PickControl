// Decoder JWT de solo lectura para el cliente.
//
// Se usa para derivar el rol desde el claim `role` del access token
// en lugar de leer un "userRole" guardado a mano en AsyncStorage,
// que cualquiera podía editarse para ver los paneles de admin. El
// JWT lo firma el backend: el rol de la UI siempre coincide con el
// que autoriza el servidor (los endpoints admin siguen dando 403
// de todas formas — esto solo evita mostrar botones que fallarían).
//
// Sin dependencias: Hermes no trae `atob` ni `Buffer`, así que el
// payload base64url se decodifica a mano y el UTF-8 se resuelve con
// decodeURIComponent (soporta nombres/acentos multibyte).

const BASE64_CHARS =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** base64/base64url → string de bytes (Latin-1). */
function decodeBase64(input: string): string {
  const clean = input.replace(/-/g, "+").replace(/_/g, "/").replace(/=+$/, "");
  let output = "";
  for (let i = 0; i < clean.length; i += 4) {
    let chunk = 0;
    let bits = 0;
    for (let j = 0; j < 4 && i + j < clean.length; j++) {
      chunk = (chunk << 6) | BASE64_CHARS.indexOf(clean[i + j]);
      bits += 6;
    }
    chunk <<= 24 - bits;
    while (bits >= 8) {
      output += String.fromCharCode((chunk >> 16) & 0xff);
      chunk <<= 8;
      bits -= 8;
    }
  }
  return output;
}

/** Devuelve el payload del JWT como objeto, o null si no es válido. */
export function decodeJwtPayload(
  token: string
): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const bytes = decodeBase64(parts[1]);
    // Bytes → UTF-8 real: percent-encodea cada byte y deja que
    // decodeURIComponent recomponga los caracteres multibyte.
    const uriEncoded = bytes
      .split("")
      .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
      .join("");
    const parsed: unknown = JSON.parse(decodeURIComponent(uriEncoded));
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}
