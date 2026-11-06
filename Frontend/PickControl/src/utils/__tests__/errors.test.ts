// Contrato de toErrorMessage: convierte cualquier valor capturado en
// catch (unknown) a un mensaje de UI seguro, con fallback propio del
// callsite cuando el error no trae mensaje útil.
import { ApiError } from "@/src/api/client";
import { toErrorMessage } from "../errors";

describe("toErrorMessage", () => {
  it("devuelve el mensaje de un Error", () => {
    expect(toErrorMessage(new Error("boom"), "fallback")).toBe("boom");
  });

  it("devuelve el mensaje de un ApiError", () => {
    const err = new ApiError("Server Error", 500);
    expect(toErrorMessage(err, "fallback")).toBeTruthy();
  });

  it("usa el fallback ante un string lanzado", () => {
    expect(toErrorMessage("fallo raro", "fallback")).toBe("fallback");
  });

  it("usa el fallback ante un objeto sin message", () => {
    expect(toErrorMessage({ code: 42 }, "fallback")).toBe("fallback");
  });

  it("usa el fallback ante null/undefined", () => {
    expect(toErrorMessage(null, "fallback")).toBe("fallback");
    expect(toErrorMessage(undefined, "fallback")).toBe("fallback");
  });

  it("usa el fallback si el Error tiene mensaje vacío", () => {
    expect(toErrorMessage(new Error(""), "fallback")).toBe("fallback");
  });
});
