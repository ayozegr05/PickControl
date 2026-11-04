// Paneles admin de parsed-picks (extraídos de
// app/(tabs)/parsed-picks.tsx en Fase D): las 4 cards de sistema —
// providers de resultados, providers de cuotas (+ embudo de
// backfill), cola de verificación y duplicadas por dedup. El gate
// isAdmin y el fetch del estado siguen en la pantalla; la forma de
// las filas la construye src/utils/providers.ts.
import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { ProvidersStatus, PicksStatus } from "@/src/api/system.api";
import { ParsedPick } from "@/src/api/parsed-picks.api";
import { ProviderRow, OddsProviderRow } from "@/src/utils/providers";
import {
  formatDateTime as formatDate,
  cleanChannel,
} from "@/src/utils/format";
import { colors } from "@/src/theme";

export function ProvidersCard({ rows }: { rows: ProviderRow[] }) {
  return (
    <View style={styles.systemCard}>
      <Text style={styles.systemTitle}>Sistema · providers</Text>
      {rows.length === 0 ? (
        <Text style={styles.providerOk}>Todos operativos</Text>
      ) : (
        rows.map((row) => (
          <View key={row.name} style={styles.providerItem}>
            <View style={styles.providerRow}>
              <Text style={styles.providerName} numberOfLines={1}>
                {row.name}
              </Text>
              <View
                style={[
                  styles.providerBadge,
                  row.quotaHasta
                    ? styles.providerBadgeBad
                    : row.cooldownHasta
                      ? styles.providerBadgeWarn
                      : styles.providerBadgeOk,
                ]}
              >
                <Text
                  style={
                    row.quotaHasta
                      ? styles.providerBad
                      : row.cooldownHasta
                        ? styles.providerWarn
                        : styles.providerOk
                  }
                >
                  {row.quotaHasta
                    ? row.quotaHasta.includes("T")
                      ? `quota → ${row.quotaHasta.slice(8, 10)}/${row.quotaHasta.slice(5, 7)}`
                      : "quota hoy"
                    : row.cooldownHasta
                      ? "cooldown"
                      : "OK"}
                </Text>
              </View>
            </View>
            {(row.callsToday > 0 ||
              row.misses > 0 ||
              row.resolved > 0 ||
              row.cooldownHasta !== null) && (
              <Text style={styles.providerMeta}>
                {row.cooldownHasta
                  ? `hasta ${row.cooldownHasta
                      .slice(5, 16)
                      .replace("T", " ")} UTC`
                  : ""}
                {row.cooldownHasta && row.callsToday > 0 ? " · " : ""}
                {row.callsToday > 0
                  ? `${row.callsToday}${
                      row.dailyLimit ? `/${row.dailyLimit}` : ""
                    } llamada${row.callsToday !== 1 ? "s" : ""} hoy`
                  : ""}
                {(row.cooldownHasta || row.callsToday > 0) && row.misses > 0
                  ? " · "
                  : ""}
                {row.misses > 0
                  ? `${row.misses} no encontrado${
                      row.misses !== 1 ? "s" : ""
                    } (en caché)`
                  : ""}
                {(row.callsToday > 0 || row.misses > 0 || row.cooldownHasta) &&
                row.resolved > 0
                  ? " · "
                  : ""}
                {row.resolved > 0
                  ? `${row.resolved} liquidada${row.resolved !== 1 ? "s" : ""}`
                  : ""}
              </Text>
            )}
          </View>
        ))
      )}
    </View>
  );
}

export function CuotasCard({
  rows,
  status,
}: {
  rows: OddsProviderRow[];
  status: ProvidersStatus;
}) {
  return (
    <View style={styles.systemCard}>
      <Text style={styles.systemTitle}>Sistema · cuotas</Text>
      {rows.length === 0 ? (
        <Text style={styles.providerOk}>Sin actividad aún</Text>
      ) : (
        rows.map((row) => (
          <View key={row.name} style={styles.providerItem}>
            <View style={styles.providerRow}>
              <Text style={styles.providerName} numberOfLines={1}>
                {row.name}
              </Text>
              <View
                style={[
                  styles.providerBadge,
                  row.quotaHasta
                    ? styles.providerBadgeBad
                    : row.cooldownHasta
                      ? styles.providerBadgeWarn
                      : styles.providerBadgeOk,
                ]}
              >
                <Text
                  style={
                    row.quotaHasta
                      ? styles.providerBad
                      : row.cooldownHasta
                        ? styles.providerWarn
                        : styles.providerOk
                  }
                >
                  {row.quotaHasta
                    ? row.quotaHasta.includes("T")
                      ? `quota → ${row.quotaHasta.slice(8, 10)}/${row.quotaHasta.slice(5, 7)}`
                      : "quota hoy"
                    : row.cooldownHasta
                      ? `cooldown · ${row.cooldownHasta.slice(11, 16)} UTC`
                      : "OK"}
                </Text>
              </View>
            </View>
            {(row.callsToday > 0 || row.snapshots > 0) && (
              <Text style={styles.providerMeta}>
                {row.callsToday > 0
                  ? `${row.callsToday}${
                      row.dailyLimit ? `/${row.dailyLimit}` : ""
                    } llamada${row.callsToday !== 1 ? "s" : ""} hoy`
                  : ""}
                {row.callsToday > 0 && row.snapshots > 0 ? " · " : ""}
                {row.snapshots > 0
                  ? `${row.snapshots} cuota${
                      row.snapshots !== 1 ? "s" : ""
                    } registrada${row.snapshots !== 1 ? "s" : ""}`
                  : ""}
              </Text>
            )}
            {Object.keys(row.purposeCalls).length > 0 && (
              <Text style={styles.providerMeta}>
                {Object.entries(row.purposeCalls)
                  .map(([p, n]) => `${n} ${p}`)
                  .join(" · ")}
              </Text>
            )}
            {row.extractOk !== null || row.extractFail !== null ? (
              <Text style={styles.providerMeta}>
                extracciones: {row.extractOk ?? 0} ok
                {(row.extractFail ?? 0) > 0
                  ? ` · ${row.extractFail} fallos`
                  : ""}
              </Text>
            ) : null}
          </View>
        ))
      )}
      <Text style={styles.statSub}>
        {status.odds_events_count ?? 0} eventos con cuota registrada
      </Text>
      {Object.entries(status.odds_snapshots_por_dia ?? {}).map(([dia, n]) => (
        <Text key={dia} style={styles.statSub}>
          {dia.slice(8, 10)}/{dia.slice(5, 7)} → {n} cuota
          {n !== 1 ? "s" : ""} guardada{n !== 1 ? "s" : ""}
        </Text>
      ))}
      {status.odds_backfill && (
        <View style={styles.funnel}>
          <Text style={styles.funnelHint}>
            Un pick sale de la cola cuando todos los providers vivos lo han
            intentado (o su cuota ya está registrada).
          </Text>
          <Text style={styles.funnelHint}>
            Fase A: listar partidos del día (~3 llamadas por día, más si hay
            muchas páginas) · Fase B: bajar la curva de cuotas (1 llamada por
            partido) · tope 40 llamadas/pasada.
          </Text>
          <View style={styles.providerRow}>
            <Text style={styles.statLabel}>Por auditar</Text>
            <Text style={styles.providerWarn}>
              {status.odds_backfill.total}
            </Text>
          </View>
          <View style={styles.providerRow}>
            <Text style={styles.statLabel}>· En cola</Text>
            <Text style={styles.providerBad}>
              {status.odds_backfill.en_cola}
            </Text>
          </View>
          <View style={styles.providerRow}>
            <Text style={styles.statLabel}>· Aplazados</Text>
            <Text style={styles.providerWarn}>
              {status.odds_backfill.aplazados}
            </Text>
          </View>
          {Object.entries(status.odds_backfill.fuentes_en_pausa).map(
            ([name, until]) => (
              <Text key={name} style={styles.statSub}>
                {"   "}
                {name} en quota →{" "}
                {until.includes("T")
                  ? `${until.slice(8, 10)}/${until.slice(5, 7)}`
                  : until}{" "}
                (vuelven solos)
              </Text>
            )
          )}
          <View style={styles.providerRow}>
            <Text style={styles.statLabel}>· Cuota registrada</Text>
            <Text style={styles.providerOk}>
              {status.odds_backfill.resueltos}
            </Text>
          </View>
          {(() => {
            const porFuente = Object.entries(
              status.odds_backfill?.resueltos_por_fuente ?? {}
            ).filter(([, n]) => n > 0);
            return porFuente.length > 0 ? (
              <Text style={styles.statSub}>
                {"   "}por fuente:{" "}
                {porFuente.map(([n, c]) => `${n} ${c}`).join(" · ")}
              </Text>
            ) : null;
          })()}
          {(status.odds_backfill.agotados ?? 0) > 0 && (
            <View style={styles.providerRow}>
              <Text style={styles.statLabel}>· Nadie los tenía</Text>
              <Text style={styles.statValue}>
                {status.odds_backfill.agotados}
              </Text>
            </View>
          )}
          {(() => {
            const deudas = [
              ...Object.entries(
                status.odds_backfill?.por_fuente ?? {}
              ).filter(([, n]) => n > 0),
              ...((status.odds_backfill?.llm_pendientes ?? 0) > 0
                ? ([
                    [
                      "llm-odds",
                      status.odds_backfill?.llm_pendientes ?? 0,
                    ],
                  ] as [string, number][])
                : []),
            ];
            return deudas.length > 0 ? (
              <Text style={styles.statSub}>
                Providers que aún les deben el intento:{" "}
                {deudas.map(([n, c]) => `${n} ${c}`).join(" · ")}
              </Text>
            ) : null;
          })()}
          {(status.odds_backfill.llm_procesados ?? 0) > 0 && (
            <Text style={styles.statSub}>
              Pipeline web+LLM: {status.odds_backfill.llm_procesados} ya
              procesados (con cuota o sin encontrarla)
            </Text>
          )}
          {(() => {
            // Historial de pasadas (más reciente primero); si aún
            // no existe la lista, al menos la última suelta.
            const pasadas = (
              status.odds_backfill?.ultimas_pasadas ??
              (status.odds_backfill?.ultima_pasada
                ? [status.odds_backfill.ultima_pasada]
                : [])
            )
              .slice(-5)
              .reverse();
            return pasadas.map((p, i) => (
              <Text key={p.at ?? i} style={styles.statSub}>
                {i === 0 ? "Última pasada" : "Pasada anterior"}{" "}
                {(p.at ?? "").slice(5, 16).replace("T", " ")} UTC ·{" "}
                {p.revisados ?? 0} revisados · {p.con_fixture ?? 0} con partido
                · {p.filas ?? 0} cuotas registradas
                {Object.keys(p.filas_por_fuente ?? {}).length > 0
                  ? ` (${Object.entries(p.filas_por_fuente ?? {})
                      .filter(([, n]) => n > 0)
                      .map(([n, c]) => `${n} ${c}`)
                      .join(" · ")})`
                  : ""}
                {(p.sin_revisar ?? 0) > 0
                  ? ` · ${p.sin_revisar} sin revisar (paga diaria agotada)`
                  : ""}
              </Text>
            ));
          })()}
        </View>
      )}
    </View>
  );
}

export function VerificacionCard({
  status,
  onAnularResiduo,
}: {
  status: PicksStatus;
  onAnularResiduo: () => void;
}) {
  return (
    <View style={styles.systemCard}>
      <Text style={styles.systemTitle}>Sistema · verificación</Text>
      <View style={styles.providerRow}>
        <Text style={styles.statLabel}>Resueltas hoy</Text>
        <Text style={styles.providerOk}>{status.resueltas_hoy}</Text>
      </View>
      <Text style={styles.statSub}>
        {status.resueltas_hoy_evento_hoy} de eventos de hoy ·{" "}
        {status.resueltas_hoy_evento_previo} backlog
      </Text>
      <View style={styles.providerRow}>
        <Text style={styles.statLabel}>Pendientes</Text>
        <Text style={styles.providerBad}>
          {status.pendientes_simples +
            status.pendientes_patas +
            status.pendientes_combinadas}
        </Text>
      </View>
      <Text style={styles.statSub}>
        {status.pendientes_simples} simples · {status.pendientes_combinadas}{" "}
        combinadas ({status.pendientes_patas} patas)
      </Text>
      <Text style={styles.statSub}>
        {status.pendientes_jugados_ventana ?? 0} jugadas &lt;14d
        {" · "}
        {status.pendientes_futuros ?? 0} futuras / en juego
        {(status.pendientes_sin_fecha ?? 0) > 0
          ? ` · ${status.pendientes_sin_fecha} sin fecha`
          : ""}
      </Text>
      <View style={styles.providerRow}>
        <Text style={styles.statLabel}>· backlog &gt;14d</Text>
        <Text
          style={
            (status.pendientes_backlog ?? 0) > 0
              ? styles.providerBad
              : styles.statValue
          }
        >
          {status.pendientes_backlog ?? 0}
        </Text>
      </View>
      {(status.pendientes_backlog ?? 0) > 0 && (
        <TouchableOpacity
          style={styles.residuoButton}
          onPress={onAnularResiduo}
        >
          <Text style={styles.residuoButtonText}>
            Anular residuo &gt;14d ({status.pendientes_backlog})
          </Text>
        </TouchableOpacity>
      )}
      {Object.keys(status.pendientes_por_deporte ?? {}).length > 0 && (
        <Text style={styles.statSub}>
          {Object.entries(status.pendientes_por_deporte ?? {})
            .map(([dep, n]) => `${n} ${dep}`)
            .join(" · ")}
        </Text>
      )}
      <View style={styles.providerRow}>
        <Text style={styles.statLabel}>Resueltas total</Text>
        <Text style={styles.providerOk}>{status.resueltas_total}</Text>
      </View>
      <Text style={styles.systemSubtitle}>Por día</Text>
      {Object.entries(status.resueltas_por_dia).map(([dia, n]) => (
        <View key={dia} style={styles.providerRow}>
          <Text style={styles.statSubFlex}>{dia}</Text>
          <Text style={styles.statValue}>{n}</Text>
        </View>
      ))}
      {Object.keys(status.resueltas_por_pasada ?? {}).length > 0 && (
        <>
          <Text style={styles.systemSubtitle}>Por pasada (hoy, UTC)</Text>
          {Object.entries(status.resueltas_por_pasada ?? {}).map(
            ([pasada, n]) => (
              <View key={pasada} style={styles.providerRow}>
                <Text style={styles.statSubFlex}>{pasada.slice(5)}</Text>
                <Text style={styles.statValue}>{n}</Text>
              </View>
            )
          )}
        </>
      )}
      {Object.keys(status.rechecks_por_dia ?? {}).length > 0 && (
        <>
          <Text style={styles.systemSubtitle}>Barrido de anuladas (diario)</Text>
          {Object.entries(status.rechecks_por_dia ?? {}).map(([dia, r]) => (
            <View key={dia} style={styles.providerRow}>
              <Text style={styles.statSubFlex}>{dia.slice(5)}</Text>
              <Text style={styles.statValue}>
                {r.procesadas ?? 0} proc.
                {(r.corregidas ?? 0) > 0
                  ? ` · ${r.corregidas} corregida${
                      r.corregidas !== 1 ? "s" : ""
                    }`
                  : ""}
                {(r.confirmadas ?? 0) > 0
                  ? ` · ${r.confirmadas} siguen anuladas`
                  : ""}
              </Text>
            </View>
          ))}
        </>
      )}
    </View>
  );
}

export function DuplicadosCard({ picks }: { picks: ParsedPick[] }) {
  const [open, setOpen] = useState(false);
  return (
    <View style={styles.systemCard}>
      <TouchableOpacity onPress={() => setOpen((v) => !v)}>
        <Text style={styles.systemTitle}>
          Duplicadas detectadas · {picks.length} {open ? "▲" : "▼"}
        </Text>
      </TouchableOpacity>
      {open ? (
        picks.map((p) => (
          <View key={p.id} style={styles.providerItem}>
            <Text style={styles.providerName} numberOfLines={2}>
              #{p.id} {p.seleccion || p.apuesta || "(sin selección)"}
            </Text>
            <Text style={styles.providerMeta}>
              {cleanChannel(p.informante)} ·{" "}
              {formatDate(p.fecha_evento ?? p.created_at)}
              {p.cuota != null ? ` · cuota ${p.cuota}` : ""}
              {p.es_combinada
                ? ` · combinada (${p.patas?.length ?? 0} patas)`
                : ""}
            </Text>
          </View>
        ))
      ) : (
        <Text style={styles.statSub}>
          Reposteos del mismo pick/boleto anulados por dedup: fuera de stats y
          del listado. Pulsa para verlas.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  systemCard: {
    backgroundColor: "#141821",
    borderColor: "#2d3a4f",
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 14,
  },
  systemTitle: {
    color: "#7ab3ff",
    fontSize: 13,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  systemSubtitle: {
    color: "#6f8299",
    fontSize: 11,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 12,
    marginBottom: 2,
  },
  providerItem: {
    paddingVertical: 4,
  },
  providerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 3,
    gap: 8,
  },
  providerName: {
    color: "#ddd",
    fontSize: 13,
    flex: 1,
  },
  providerBadge: {
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  providerBadgeOk: {
    backgroundColor: "rgba(76, 175, 80, 0.15)",
  },
  providerBadgeBad: {
    backgroundColor: "rgba(244, 67, 54, 0.15)",
  },
  providerBadgeWarn: {
    backgroundColor: "rgba(255, 152, 0, 0.15)",
  },
  providerOk: {
    color: colors.success,
    fontSize: 12,
    fontWeight: "bold",
  },
  providerBad: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "bold",
  },
  providerWarn: {
    color: "#ff9800",
    fontSize: 12,
    fontWeight: "bold",
  },
  providerMeta: {
    color: "#9a9a9a",
    fontSize: 11,
    marginTop: 1,
  },
  statLabel: {
    color: "#9aa4b2",
    fontSize: 13,
    flex: 1,
  },
  statSub: {
    color: "#9a9a9a",
    fontSize: 11,
    marginTop: 1,
    marginBottom: 4,
  },
  statSubFlex: {
    color: "#9a9a9a",
    fontSize: 11,
    flex: 1,
  },
  statValue: {
    color: "#ddd",
    fontSize: 12,
    fontWeight: "600",
  },
  residuoButton: {
    marginTop: 6,
    backgroundColor: "rgba(244, 67, 54, 0.15)",
    borderRadius: 8,
    paddingVertical: 8,
    alignItems: "center",
  },
  residuoButtonText: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "bold",
  },
  funnel: {
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: "#333",
  },
  funnelHint: {
    color: "#9a9a9a",
    fontSize: 10,
    fontStyle: "italic",
    marginBottom: 6,
  },
});
