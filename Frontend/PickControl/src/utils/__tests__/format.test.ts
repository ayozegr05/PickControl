// Tests de caracterización de las utilidades de formato: capturan el
// comportamiento actual de las copias duplicadas en pantallas antes
// de la unificación (Fase A del refactor).
import {
  formatDateTime,
  formatDateShort,
  formatDateES,
  cleanChannel,
} from "../format";

describe("formatDateTime", () => {
  it("formatea fecha+hora en español (dd/mm/yyyy hh:mm)", () => {
    const out = formatDateTime("2026-10-28T14:30:00");
    expect(out).toMatch(/28\/10\/2026/);
    expect(out).toMatch(/14:30/);
  });

  it("devuelve el input tal cual si la fecha es inválida", () => {
    expect(formatDateTime("no-es-fecha")).toBe("no-es-fecha");
    expect(formatDateTime("")).toBe("");
  });
});

describe("formatDateShort", () => {
  it("usa año de 2 dígitos (variante de [informante])", () => {
    expect(formatDateShort("2026-10-28T14:30:00")).toBe("28/10/26");
    expect(formatDateShort(new Date(2026, 0, 5))).toBe("05/01/26");
  });
});

describe("formatDateES", () => {
  it("usa año completo en formato español", () => {
    expect(formatDateES("2026-10-28")).toBe("28/10/2026");
  });
});

describe("cleanChannel", () => {
  it("devuelve 'desconocido' para null/undefined/vacío", () => {
    expect(cleanChannel(null)).toBe("desconocido");
    expect(cleanChannel(undefined)).toBe("desconocido");
    expect(cleanChannel("")).toBe("desconocido");
  });

  it("quita pipes, emojis y caracteres no alfanuméricos", () => {
    expect(cleanChannel("Dm7 || GRATUITO || ⚡🔞")).toBe("Dm7 GRATUITO");
    expect(cleanChannel("Lady Bets 💰🔞")).toBe("Lady Bets");
  });

  it("UNE todos los segmentos con texto (no solo el primero)", () => {
    // Regresión documentada en parsed-picks: quedarse con el primer
    // segmento haría que dos canales "Dm7" distintos colisionaran.
    expect(cleanChannel("Dm7 || Allsports || 🔥")).toBe("Dm7 Allsports");
    expect(cleanChannel("Dm7 || GRATUITO || 🔥")).toBe("Dm7 GRATUITO");
  });

  it("normaliza espacios múltiples y trim por segmento", () => {
    expect(cleanChannel("  Canal   Con   Espacios  ")).toBe(
      "Canal Con Espacios"
    );
  });

  it("'desconocido' si solo quedan separadores tras limpiar", () => {
    expect(cleanChannel("|||")).toBe("desconocido");
    expect(cleanChannel("⚡🔞💰")).toBe("desconocido");
  });
});
