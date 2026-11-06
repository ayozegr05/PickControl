// Contrato de la credencial guardada en SecureStore para el login
// biométrico. La clave `userCredentials` históricamente almacenaba
// {email, password} en claro — el nuevo formato guarda el refresh
// token de larga duración y NUNCA la contraseña.
import {
  isLegacyCredential,
  parseStoredCredential,
  serializeCredential,
} from "../biometric-credentials";

describe("parseStoredCredential", () => {
  it("parsea el formato nuevo (refresh token) con todos los campos", () => {
    const raw = JSON.stringify({
      refreshToken: "rt.abc.def",
      userName: "Ayoze",
      role: "admin",
    });

    expect(parseStoredCredential(raw)).toEqual({
      refreshToken: "rt.abc.def",
      userName: "Ayoze",
      role: "admin",
    });
  });

  it("devuelve el formato legacy {email,password} para migrarlo en caliente", () => {
    const raw = JSON.stringify({
      email: "ana@example.com",
      password: "supersecreta",
    });

    const parsed = parseStoredCredential(raw);
    expect(isLegacyCredential(parsed!)).toBe(true);
    expect(parsed).toEqual({
      email: "ana@example.com",
      password: "supersecreta",
    });
  });

  it("rechaza JSON inválido, null y formas sin los campos mínimos", () => {
    expect(parseStoredCredential(null)).toBeNull();
    expect(parseStoredCredential("no es json")).toBeNull();
    expect(parseStoredCredential("{}")).toBeNull();
    // Forma nueva sin token
    expect(parseStoredCredential(JSON.stringify({ userName: "X" }))).toBeNull();
    // Forma legacy sin password (ni email)
    expect(
      parseStoredCredential(JSON.stringify({ email: "a@b.c" }))
    ).toBeNull();
    // Password vacía no vale como credencial legacy
    expect(
      parseStoredCredential(JSON.stringify({ email: "a@b.c", password: "" }))
    ).toBeNull();
  });
});

describe("serializeCredential", () => {
  it("serializa el refresh token, nombre y rol — sin contraseña", () => {
    const raw = serializeCredential({
      refreshToken: "rt.abc.def",
      userName: "Ayoze",
      role: "admin",
    });

    const parsed = JSON.parse(raw);
    expect(parsed).toEqual({
      refreshToken: "rt.abc.def",
      userName: "Ayoze",
      role: "admin",
    });
    // La regla de negocio entera: la password nunca se persistió.
    expect(JSON.stringify(parsed)).not.toContain("password");
  });
});
