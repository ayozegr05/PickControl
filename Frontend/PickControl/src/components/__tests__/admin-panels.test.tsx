// Contrato de los paneles admin de parsed-picks (Fase D): las 4
// cards "Sistema · providers/cuotas/verificación" y "Duplicadas
// detectadas". El gate isAdmin y el fetch siguen en la pantalla.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import {
  ProvidersCard,
  CuotasCard,
  VerificacionCard,
  DuplicadosCard,
} from "@/src/components/admin-panels";
import { ProviderRow, OddsProviderRow } from "@/src/utils/providers";
import { ProvidersStatus, PicksStatus } from "@/src/types/system.types";
import { ParsedPick } from "@/src/types/parsed-pick.types";

const providerRow = (over: Partial<ProviderRow> = {}): ProviderRow => ({
  name: "espn",
  limitedSince: null,
  cooldownHasta: null,
  quotaHasta: null,
  misses: 0,
  callsToday: 0,
  dailyLimit: null,
  resolved: 0,
  ...over,
});

const oddsRow = (over: Partial<OddsProviderRow> = {}): OddsProviderRow => ({
  name: "oddspapi",
  limitedSince: null,
  cooldownHasta: null,
  quotaHasta: null,
  callsToday: 0,
  dailyLimit: null,
  snapshots: 0,
  purposeCalls: {},
  extractOk: null,
  extractFail: null,
  ...over,
});

const statusBase = (over: Partial<ProvidersStatus> = {}): ProvidersStatus => ({
  rate_limited: {},
  missed_by_provider: {},
  calls_today: {},
  calls_by_day: {},
  daily_limits: {},
  ...over,
});

const picksStatus = (over: Partial<PicksStatus> = {}): PicksStatus => ({
  pendientes_simples: 3,
  pendientes_patas: 2,
  pendientes_combinadas: 1,
  pendientes_jugados_ventana: 4,
  pendientes_futuros: 1,
  pendientes_backlog: 0,
  pendientes_sin_fecha: 0,
  pendientes_por_deporte: {},
  resueltas_hoy: 5,
  resueltas_hoy_evento_hoy: 2,
  resueltas_hoy_evento_previo: 3,
  resueltas_total: 500,
  resueltas_por_dia: { "2025-11-20": 7 },
  resueltas_por_pasada: {},
  rechecks_por_dia: {},
  ...over,
});

const pick = (over: Partial<ParsedPick> = {}): ParsedPick => ({
  id: 7,
  es_apuesta: true,
  apuesta: null,
  deporte: "futbol",
  evento: "A vs B",
  mercado: "1X2",
  seleccion: "Gana A",
  cuota: 1.85,
  stake: 1,
  casa: null,
  informante: "Canal A",
  explicacion: null,
  metodo: "llm",
  confianza: 0.9,
  fecha_evento: "2025-11-20T18:00:00",
  linea: null,
  acierto: null,
  anulada: false,
  motivo_anulada: null,
  verificado_por: null,
  informante_id: 1,
  raw_message_id: 1,
  created_at: "2025-11-20T10:00:00",
  es_reto: false,
  es_combinada: false,
  combinada_id: null,
  orden: null,
  cuota_efectiva: null,
  patas: [],
  fuera_ventana: false,
  ...over,
});

describe("ProvidersCard", () => {
  it("sin filas muestra 'Todos operativos'", async () => {
    const s = await render(<ProvidersCard rows={[]} />);
    expect(s.getByText("Sistema · providers")).toBeTruthy();
    expect(s.getByText("Todos operativos")).toBeTruthy();
  });

  it("fila con cooldown muestra badge y metadatos", async () => {
    const s = await render(
      <ProvidersCard
        rows={[
          providerRow({
            name: "espn",
            limitedSince: "x",
            cooldownHasta: "2025-11-20T18:30:00",
            callsToday: 12,
            dailyLimit: 100,
            misses: 3,
            resolved: 2,
          }),
        ]}
      />
    );
    expect(s.getByText("espn")).toBeTruthy();
    expect(s.getByText("cooldown")).toBeTruthy();
    expect(s.getByText(/12\/100 llamadas hoy · 3 no encontrados/)).toBeTruthy();
  });

  it("fila con quota desnuda muestra 'quota hoy'", async () => {
    const s = await render(
      <ProvidersCard
        rows={[providerRow({ quotaHasta: "2025-12-01", limitedSince: "x" })]}
      />
    );
    expect(s.getByText("quota hoy")).toBeTruthy();
  });
});

describe("CuotasCard", () => {
  it("sin actividad muestra 'Sin actividad aún'", async () => {
    const s = await render(
      <CuotasCard rows={[]} status={statusBase({ odds_events_count: 0 })} />
    );
    expect(s.getByText("Sistema · cuotas")).toBeTruthy();
    expect(s.getByText("Sin actividad aún")).toBeTruthy();
    expect(s.getByText("0 eventos con cuota registrada")).toBeTruthy();
  });

  it("fila con extracciones y purpose calls", async () => {
    const s = await render(
      <CuotasCard
        rows={[
          oddsRow({
            name: "groq-odds",
            callsToday: 9,
            snapshots: 5,
            purposeCalls: { cuotas: 9 },
            extractOk: 4,
            extractFail: 1,
          }),
        ]}
        status={statusBase()}
      />
    );
    expect(s.getByText("groq-odds")).toBeTruthy();
    expect(s.getByText("9 llamadas hoy · 5 cuotas registradas")).toBeTruthy();
    expect(s.getByText("9 cuotas")).toBeTruthy();
    expect(s.getByText("extracciones: 4 ok · 1 fallos")).toBeTruthy();
  });

  it("embudo de backfill muestra cola y aplazados", async () => {
    const s = await render(
      <CuotasCard
        rows={[]}
        status={statusBase({
          odds_backfill: {
            total: 40,
            resueltos: 12,
            en_cola: 10,
            aplazados: 8,
            agotados: 0,
            por_fuente: { oddspapi: 10 },
            llm_pendientes: 0,
            fuentes_en_pausa: { oddsfeed: "2025-12-01" },
          },
        })}
      />
    );
    expect(s.getByText("Por auditar")).toBeTruthy();
    expect(s.getByText("40")).toBeTruthy();
    expect(s.getByText("· En cola")).toBeTruthy();
    expect(s.getByText("10")).toBeTruthy();
    expect(s.getByText(/oddsfeed en quota/)).toBeTruthy();
    expect(s.getByText(/oddspapi 10/)).toBeTruthy();
  });
});

describe("VerificacionCard", () => {
  it("muestra resueltas hoy, pendientes y total", async () => {
    const s = await render(
      <VerificacionCard status={picksStatus()} onAnularResiduo={jest.fn()} />
    );
    expect(s.getByText("Sistema · verificación")).toBeTruthy();
    expect(s.getByText("Resueltas hoy")).toBeTruthy();
    expect(s.getByText("5")).toBeTruthy();
    expect(s.getByText("Pendientes")).toBeTruthy();
    // 3 + 2 + 1 = 6
    expect(s.getByText("6")).toBeTruthy();
    expect(s.getByText("Resueltas total")).toBeTruthy();
    expect(s.getByText("500")).toBeTruthy();
  });

  it("con backlog muestra el botón de anular y dispara callback", async () => {
    const onAnular = jest.fn();
    const s = await render(
      <VerificacionCard
        status={picksStatus({ pendientes_backlog: 4 })}
        onAnularResiduo={onAnular}
      />
    );
    await fireEvent.press(s.getByText("Anular residuo >14d (4)"));
    expect(onAnular).toHaveBeenCalled();
  });

  it("sin backlog no muestra el botón", async () => {
    const s = await render(
      <VerificacionCard status={picksStatus()} onAnularResiduo={jest.fn()} />
    );
    expect(s.queryByText(/Anular residuo/)).toBeNull();
  });
});

describe("DuplicadosCard", () => {
  it("colapsada por defecto y expande al pulsar", async () => {
    const s = await render(
      <DuplicadosCard picks={[pick(), pick({ id: 8 })]} />
    );
    expect(s.getByText("Duplicadas detectadas · 2 ▼")).toBeTruthy();
    expect(s.queryByText(/#7/)).toBeNull();
    await fireEvent.press(s.getByText("Duplicadas detectadas · 2 ▼"));
    expect(s.getByText(/#7 Gana A/)).toBeTruthy();
    expect(s.getByText(/#8 Gana A/)).toBeTruthy();
    expect(s.getByText("Duplicadas detectadas · 2 ▲")).toBeTruthy();
  });
});
