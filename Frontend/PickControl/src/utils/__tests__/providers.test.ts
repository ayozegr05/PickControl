// Contrato de los constructores de filas del panel admin de
// parsed-picks (Fase D): clasificarBloqueo, buildProviderRows y
// buildOddsProviderRows — la lógica que estaba inline en la pantalla.
import {
  clasificarBloqueo,
  buildProviderRows,
  buildOddsProviderRows,
} from "@/src/utils/providers";
import { ProvidersStatus } from "@/src/types/system.types";

const baseStatus = (): ProvidersStatus => ({
  rate_limited: {},
  missed_by_provider: {},
  calls_today: {},
  calls_by_day: {},
  daily_limits: {},
});

const inHours = (h: number) =>
  new Date(Date.now() + h * 3600 * 1000).toISOString();

describe("clasificarBloqueo", () => {
  it("sin marca devuelve nulls", () => {
    expect(clasificarBloqueo(null)).toEqual({
      cooldownHasta: null,
      quotaHasta: null,
    });
  });

  it("timestamp cercano con T es cooldown", () => {
    const raw = inHours(2);
    expect(clasificarBloqueo(raw)).toEqual({
      cooldownHasta: raw,
      quotaHasta: null,
    });
  });

  it("timestamp lejano (>24h) es quota", () => {
    const raw = inHours(48);
    expect(clasificarBloqueo(raw)).toEqual({
      cooldownHasta: null,
      quotaHasta: raw,
    });
  });

  it("fecha desnuda sin T es quota", () => {
    expect(clasificarBloqueo("2025-12-31")).toEqual({
      cooldownHasta: null,
      quotaHasta: "2025-12-31",
    });
  });
});

describe("buildProviderRows", () => {
  it("une nombres de todas las fuentes y pone limitados primero", () => {
    const rows = buildProviderRows({
      ...baseStatus(),
      rate_limited: { zebra: inHours(2) },
      missed_by_provider: { alpha: 3 },
      calls_today: { beta: 5 },
      resolved_by_provider: { alpha: 7 },
    });
    expect(rows.map((r) => r.name)).toEqual(["zebra", "alpha", "beta"]);
    const alpha = rows.find((r) => r.name === "alpha")!;
    expect(alpha.misses).toBe(3);
    expect(alpha.resolved).toBe(7);
    expect(alpha.callsToday).toBe(0);
    const zebra = rows[0];
    expect(zebra.limitedSince).not.toBeNull();
    expect(zebra.cooldownHasta).not.toBeNull();
  });

  it("dailyLimit se propaga cuando existe", () => {
    const rows = buildProviderRows({
      ...baseStatus(),
      calls_today: { espn: 10 },
      daily_limits: { espn: 100 },
    });
    expect(rows[0]).toMatchObject({
      name: "espn",
      callsToday: 10,
      dailyLimit: 100,
    });
  });

  it("devuelve vacío sin actividad", () => {
    expect(buildProviderRows(baseStatus())).toEqual([]);
  });
});

describe("buildOddsProviderRows", () => {
  it("fila genérica por snapshots y actividad conocida", () => {
    const rows = buildOddsProviderRows({
      ...baseStatus(),
      odds_snapshots_by_provider: { oddspapi: 42 },
      calls_today: { oddspapi: 3 },
      daily_limits: { oddspapi: 250 },
    });
    expect(rows).toEqual([
      expect.objectContaining({
        name: "oddspapi",
        snapshots: 42,
        callsToday: 3,
        dailyLimit: 250,
        extractOk: null,
      }),
    ]);
  });

  it("los motores llm-compat/X-odds no salen como fila genérica", () => {
    const rows = buildOddsProviderRows({
      ...baseStatus(),
      calls_today: { "llm-compat-groq": 9, sportapi7: 2 },
      odds_snapshots_by_provider: { "groq-odds": 5 },
      calls_by_purpose: { "llm-compat-groq": { cuotas: 9 } },
      extract_stats: { groq: { ok: 4, fail: 1 } },
    });
    const names = rows.map((r) => r.name);
    expect(names).not.toContain("llm-compat-groq");
    // sportapi7 es fuente conocida con llamadas → fila genérica propia
    expect(names).toContain("sportapi7");
    expect(rows.find((r) => r.name === "sportapi7")!.snapshots).toBe(0);
    expect(names).toContain("groq-odds");
    const groq = rows.find((r) => r.name === "groq-odds")!;
    expect(groq.callsToday).toBe(9);
    expect(groq.purposeCalls).toEqual({ cuotas: 9 });
    expect(groq.extractOk).toBe(4);
    expect(groq.extractFail).toBe(1);
    expect(groq.snapshots).toBe(5);
  });

  it("gemini-odds activo genera fila gemini-odds", () => {
    const rows = buildOddsProviderRows({
      ...baseStatus(),
      calls_today: { "gemini-odds": 7 },
      daily_limits: { "gemini-odds": 50 },
    });
    const gemini = rows.find((r) => r.name === "gemini-odds")!;
    expect(gemini.callsToday).toBe(7);
    expect(gemini.dailyLimit).toBe(50);
  });

  it("provider conocido con rate limit aparece aunque sin llamadas", () => {
    const rows = buildOddsProviderRows({
      ...baseStatus(),
      rate_limited: { oddspapi: "2025-12-31" },
    });
    const p = rows.find((r) => r.name === "oddspapi")!;
    expect(p.quotaHasta).toBe("2025-12-31");
    expect(rows[0].name).toBe("oddspapi"); // limitado primero
  });

  it("rate limit del engine puede venir del mark llm-compat", () => {
    const rows = buildOddsProviderRows({
      ...baseStatus(),
      rate_limited: { "llm-compat-cerebras": inHours(2) },
      calls_today: { "llm-compat-cerebras": 4 },
    });
    const eng = rows.find((r) => r.name === "cerebras-odds")!;
    expect(eng.cooldownHasta).not.toBeNull();
  });
});
