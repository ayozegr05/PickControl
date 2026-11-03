// Filas del panel admin "Sistema · providers" y "Sistema · cuotas"
// de parsed-picks (extraído de app/(tabs)/parsed-picks.tsx en Fase D).
// Lógica pura: traduce ProvidersStatus (marcas del backend) a las
// filas que renderizan las cards.
import { ProvidersStatus } from "@/src/api/system.api";

export type Bloqueo = {
  cooldownHasta: string | null;
  quotaHasta: string | null;
};

// Clasifica el bloqueo de rate_limited: timestamp con "T" cercano =
// cooldown por ritmo (minutos); fecha desnuda (quota del día) o
// timestamp lejano (>24h) = quota agotada (reset diario/mensual).
export function clasificarBloqueo(raw: string | null): Bloqueo {
  if (!raw) return { cooldownHasta: null, quotaHasta: null };
  if (raw.includes("T")) {
    const until = new Date(raw).getTime();
    if (Number.isFinite(until) && until - Date.now() > 24 * 3600 * 1000) {
      return { cooldownHasta: null, quotaHasta: raw };
    }
    return { cooldownHasta: raw, quotaHasta: null };
  }
  return { cooldownHasta: null, quotaHasta: raw };
}

export interface ProviderRow {
  name: string;
  limitedSince: string | null;
  cooldownHasta: string | null;
  quotaHasta: string | null;
  misses: number;
  callsToday: number;
  dailyLimit: number | null;
  resolved: number;
}

export function buildProviderRows(status: ProvidersStatus): ProviderRow[] {
  const names = new Set<string>([
    ...Object.keys(status.rate_limited),
    ...Object.keys(status.missed_by_provider),
    ...Object.keys(status.calls_today ?? {}),
    ...Object.keys(status.resolved_by_provider ?? {}),
  ]);
  return [...names]
    .sort((a, b) => {
      const aLimited = a in status.rate_limited;
      const bLimited = b in status.rate_limited;
      if (aLimited !== bLimited) return aLimited ? -1 : 1;
      return a.localeCompare(b);
    })
    .map((name) => {
      const limitedSince = status.rate_limited[name] ?? null;
      return {
        name,
        limitedSince,
        ...clasificarBloqueo(limitedSince),
        misses: status.missed_by_provider[name] ?? 0,
        callsToday: status.calls_today?.[name] ?? 0,
        dailyLimit: status.daily_limits?.[name] ?? null,
        resolved: status.resolved_by_provider?.[name] ?? 0,
      };
    });
}

export interface OddsProviderRow {
  name: string;
  limitedSince: string | null;
  cooldownHasta: string | null;
  quotaHasta: string | null;
  callsToday: number;
  dailyLimit: number | null;
  snapshots: number;
  purposeCalls: Record<string, number>;
  extractOk: number | null;
  extractFail: number | null;
}

// Providers de la capa de cuotas (snapshotter + backfill histórico).
// Los comparten nombre con los de resultados (espn...) — la sección
// separa por rol, no por proveedor físico.
export function buildOddsProviderRows(
  status: ProvidersStatus
): OddsProviderRow[] {
  const known = new Set([
    "espn",
    "allsportsapi2",
    "tennisapi1",
    "sportapi7",
    "sofascore6",
    "sofascore_direct",
    "oddspapi",
    "oddsfeed",
  ]);
  // Fuentes de datos puras: snapshots propios o actividad conocida.
  const names = new Set<string>(
    [
      ...Object.keys(status.odds_snapshots_by_provider ?? {}),
      ...[...known].filter(
        (n) => n in status.rate_limited || (status.calls_today?.[n] ?? 0) > 0
      ),
    ].filter((n) => !/^[a-z0-9]+-odds$/.test(n) && !n.startsWith("llm-compat"))
  );
  const generic = [...names].map((name) => {
    const limitedSince = status.rate_limited[name] ?? null;
    return {
      name,
      limitedSince,
      ...clasificarBloqueo(limitedSince),
      callsToday: status.calls_today?.[name] ?? 0,
      dailyLimit: status.daily_limits?.[name] ?? null,
      snapshots: status.odds_snapshots_by_provider?.[name] ?? 0,
      purposeCalls: {} as Record<string, number>,
      extractOk: null as number | null,
      extractFail: null as number | null,
    };
  });
  // Motores del pipeline web+LLM: una fila por extractor
  // (`{motor}-odds`). Las llamadas de groq/cerebras/openrouter se
  // cuentan como `llm-compat-{motor}`; las de Gemini como
  // `gemini-odds` (su calls_as histórico).
  const engines = new Set<string>();
  for (const src of [
    status.calls_today ?? {},
    status.rate_limited ?? {},
    status.calls_by_purpose ?? {},
  ]) {
    for (const n of Object.keys(src)) {
      const m = n.match(/^llm-compat-(.+)$/);
      if (m) engines.add(m[1]);
    }
  }
  for (const n of Object.keys(status.odds_snapshots_by_provider ?? {})) {
    const m = n.match(/^([a-z0-9]+)-odds$/);
    if (m) engines.add(m[1]);
  }
  for (const n of Object.keys(status.extract_stats ?? {})) {
    engines.add(n);
  }
  if (
    "gemini-odds" in status.rate_limited ||
    (status.calls_today?.["gemini-odds"] ?? 0) > 0
  ) {
    engines.add("gemini");
  }
  const engineRows = [...engines].map((engine) => {
    const callMark =
      engine === "gemini" ? "gemini-odds" : `llm-compat-${engine}`;
    const rowProvider = `${engine}-odds`;
    const limitedSince =
      status.rate_limited[callMark] ?? status.rate_limited[rowProvider] ?? null;
    const stats = status.extract_stats?.[engine];
    return {
      name: rowProvider,
      limitedSince,
      ...clasificarBloqueo(limitedSince),
      callsToday: status.calls_today?.[callMark] ?? 0,
      dailyLimit: status.daily_limits?.[callMark] ?? null,
      snapshots: status.odds_snapshots_by_provider?.[rowProvider] ?? 0,
      purposeCalls: status.calls_by_purpose?.[callMark] ?? {},
      extractOk: stats?.ok ?? null,
      extractFail: stats?.fail ?? null,
    };
  });
  return [...generic, ...engineRows].sort((a, b) => {
    const aLimited = a.limitedSince !== null;
    const bLimited = b.limitedSince !== null;
    if (aLimited !== bLimited) return aLimited ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}
