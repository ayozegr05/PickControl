// Contrato del guard centralizado de rutas.
//
// Reglas:
// - Mientras isInitializing → NUNCA redirige (la UI muestra spinner).
// - Sin sesión: solo landing ((tabs)/index), login y register son
//   públicas; cualquier otra ruta → "/" (landing).
// - Con sesión: login/register → "/" (ya estás dentro); el resto pasa.
import { redirectTarget } from "@/src/utils/auth-guard";

const auth = (isAuthenticated: boolean, isInitializing = false) => ({
  isAuthenticated,
  isInitializing,
});

describe("redirectTarget", () => {
  describe("hidratando", () => {
    it("nunca redirige mientras isInitializing (logueado o no)", () => {
      expect(
        redirectTarget(["(tabs)", "my-picks"], auth(false, true))
      ).toBeNull();
      expect(
        redirectTarget(["screens", "analysis"], auth(true, true))
      ).toBeNull();
    });
  });

  describe("sin sesión", () => {
    it("permite la landing y las pantallas públicas", () => {
      expect(redirectTarget(["(tabs)"], auth(false))).toBeNull();
      expect(redirectTarget(["(tabs)", "index"], auth(false))).toBeNull();
      expect(redirectTarget(["login"], auth(false))).toBeNull();
      expect(redirectTarget(["register"], auth(false))).toBeNull();
    });

    it("expulsa a la landing desde cualquier pestaña protegida", () => {
      for (const tab of [
        "earns",
        "auditoria",
        "add-pick",
        "my-picks",
        "parsed-picks",
        "canales",
      ]) {
        expect(redirectTarget(["(tabs)", tab], auth(false))).toBe("/");
      }
    });

    it("expulsa a la landing desde pantallas sueltas protegidas", () => {
      expect(redirectTarget(["analysis"], auth(false))).toBe("/");
      expect(redirectTarget(["informante", "[informante]"], auth(false))).toBe(
        "/"
      );
    });
  });

  describe("con sesión", () => {
    it("rechaza login/register (ya autenticado) hacia la home", () => {
      expect(redirectTarget(["login"], auth(true))).toBe("/");
      expect(redirectTarget(["register"], auth(true))).toBe("/");
    });

    it("deja pasar el resto de rutas", () => {
      expect(redirectTarget(["(tabs)", "earns"], auth(true))).toBeNull();
      expect(redirectTarget(["analysis"], auth(true))).toBeNull();
      expect(
        redirectTarget(["informante", "[informante]"], auth(true))
      ).toBeNull();
      expect(redirectTarget(["(tabs)"], auth(true))).toBeNull();
    });
  });
});
