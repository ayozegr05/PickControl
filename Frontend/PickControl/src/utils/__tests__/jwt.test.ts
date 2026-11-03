// Contrato del decoder JWT del cliente: se usa para derivar `isAdmin`
// del claim `role` firmado por el backend en vez de leer "userRole"
// de AsyncStorage, que cualquiera podía editar.
import { decodeJwtPayload } from "../jwt";

// Token real con el payload {"sub":"7","email":"ayo@example.com",
// "role":"admin","exp":1893456000,"type":"access"} (firma inventada).
const TOKEN_ADMIN =
  "eyJhbGciOiJIUzI1NiJ9." +
  "eyJzdWIiOiI3IiwiZW1haWwiOiJheW9AZXhhbXBsZS5jb20iLCJyb2xlIjoiYWRtaW4iLCJleHAiOjE4OTM0NTYwMDAsInR5cGUiOiJhY2Nlc3MifQ." +
  "firma";

// Payload con UTF-8 multibyte ("Gómez") y caracteres base64url (-_).
// base64url de {"name":"Gómez","role":"user","extra":"a-b_c"}.
const TOKEN_UTF8 =
  "eyJhbGciOiJIUzI1NiJ9." +
  "eyJuYW1lIjoiR8OzbWV6Iiwicm9sZSI6InVzZXIiLCJleHRyYSI6ImEtYl9jIn0" +
  ".firma";

describe("decodeJwtPayload", () => {
  it("decodifica el payload de un JWT bien formado", () => {
    expect(decodeJwtPayload(TOKEN_ADMIN)).toMatchObject({
      sub: "7",
      email: "ayo@example.com",
      role: "admin",
      type: "access",
    });
  });

  it("acepta base64url (-_) y UTF-8 multibyte", () => {
    const payload = decodeJwtPayload(TOKEN_UTF8);
    expect(payload?.name).toBe("Gómez");
    expect(payload?.extra).toBe("a-b_c");
  });

  it("devuelve null ante tokens malformados", () => {
    expect(decodeJwtPayload("")).toBeNull();
    expect(decodeJwtPayload("sin-puntos")).toBeNull();
    expect(decodeJwtPayload("a.b")).toBeNull();
    // Payload que no es JSON (base64 de "no-json").
    expect(decodeJwtPayload("aaa.bm8tanNvbg.c")).toBeNull();
    // Payload JSON pero no objeto (base64 de "[1,2]").
    expect(decodeJwtPayload("aaa.WzEsMl0.c")).toBeNull();
  });
});
