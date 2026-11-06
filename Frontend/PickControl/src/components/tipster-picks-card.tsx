// Card "Picks del tipster", extraída de
// app/dynamic-routes/[informante].tsx (Fase D): stats del canal,
// tabla de picks simples (FlatList virtualizada), sección de retos y
// combinadas expandibles con sus patas. El estado de qué combinadas
// están abiertas es interno — es puramente visual.
import React, { useState } from "react";
import {
  FlatList,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import { Acierto, MotivoAnulada, PickItem } from "@/src/types/pick.types";
import { gananciaApuesta, iconoAnulada } from "@/src/utils/picks";
import { formatDateShort } from "@/src/utils/format";
import { colors } from "@/src/theme";

export interface CombinadasStats {
  total: number;
  aciertos: number;
  ganancias: number;
  yieldPct: number;
}

interface TipsterPicksCardProps {
  totalApuestas: number;
  totalAciertos: number;
  porcentajeAciertos: number;
  ganancias: number;
  yieldPct: number;
  /** Picks simples (sin retos ni combinadas), ordenados por fecha. */
  rows: PickItem[];
  retos: PickItem[];
  combinadas: PickItem[];
  combinadasStats: CombinadasStats;
  onCorregir: () => void;
  onPickPress: (pick: PickItem) => void;
}

const HEADERS = [
  "Apuesta",
  "Acierto",
  "Fecha",
  "Mercado",
  "Cuota",
  "Stake",
  "Ganancia",
] as const;

// Solo lectura: pendiente/anulada no abre el modal de edición (es de
// apuestas manuales). La anulada distingue el motivo con su icono.
function PronosticoTelegram({
  acierto,
  anulada,
  motivoAnulada,
}: {
  acierto: Acierto;
  anulada?: boolean;
  motivoAnulada?: MotivoAnulada | null;
}) {
  if (anulada) {
    return (
      <Text style={[styles.icon, { color: colors.textMuted }]}>
        {iconoAnulada(motivoAnulada)}
      </Text>
    );
  }
  if (acierto === "True") {
    return (
      <Text style={[styles.icon, { color: colors.success, fontSize: 18 }]}>
        ✅
      </Text>
    );
  }
  if (acierto === "False") {
    return <Text style={[styles.icon, { color: colors.danger }]}>❌</Text>;
  }
  return <Text style={[styles.icon, { color: colors.textMuted }]}>❓</Text>;
}

export default function TipsterPicksCard({
  totalApuestas,
  totalAciertos,
  porcentajeAciertos,
  ganancias,
  yieldPct,
  rows,
  retos,
  combinadas,
  combinadasStats,
  onCorregir,
  onPickPress,
}: TipsterPicksCardProps) {
  // Combinadas expandidas: id del padre -> true si se muestran sus patas.
  const [combinadasAbiertas, setCombinadasAbiertas] = useState<
    Record<number, boolean>
  >({});

  return (
    <View style={styles.card}>
      <Text style={[styles.cardTitle, { color: colors.primary }]}>
        Picks del tipster
      </Text>
      <Text style={styles.cardSubtitle}>
        Lo que ha publicado en su canal, verificado automáticamente
      </Text>
      <View style={styles.row}>
        <Text style={styles.label}>Total Picks:</Text>
        <Text style={styles.value}>{totalApuestas}</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Aciertos:</Text>
        <Text style={styles.value}>{totalAciertos}</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Porcentaje de Aciertos:</Text>
        <Text style={styles.value}>{porcentajeAciertos.toFixed(2)}%</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Ganancias (unidades):</Text>
        <Text style={styles.value}>{ganancias.toFixed(2)}u</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Yield:</Text>
        <Text style={styles.value}>{yieldPct.toFixed(2)}%</Text>
      </View>

      <TouchableOpacity style={styles.telegramButton} onPress={onCorregir}>
        <Text style={styles.telegramButtonText}>
          Corregir picks de Telegram
        </Text>
      </TouchableOpacity>

      {rows.length === 0 ? (
        <Text style={styles.emptyText}>
          Todavía no hay picks extraídos de este canal.
        </Text>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={true}>
          <View style={styles.table}>
            <View style={styles.tableRow}>
              {HEADERS.map((h) => (
                <View key={h} style={[styles.tableHeaderCell, styles.border]}>
                  <Text style={styles.tableHeaderText}>{h}</Text>
                </View>
              ))}
            </View>

            <FlatList
              style={styles.tableRows}
              nestedScrollEnabled={true}
              data={rows}
              keyExtractor={(apuesta) => String(apuesta.id)}
              renderItem={({ item: apuesta, index }) => (
                <TouchableOpacity
                  onPress={() => onPickPress(apuesta)}
                  style={[
                    styles.tableRow,
                    index % 2 === 0 ? styles.evenRow : styles.oddRow,
                  ]}
                >
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {apuesta.apuesta || "(sin nombre)"}
                    </Text>
                    {apuesta.evento ? (
                      <Text style={styles.eventoText}>{apuesta.evento}</Text>
                    ) : null}
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text>
                      <PronosticoTelegram
                        acierto={apuesta.acierto}
                        anulada={apuesta.anulada}
                        motivoAnulada={apuesta.motivoAnulada}
                      />
                    </Text>
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {formatDateShort(apuesta.fecha)}
                    </Text>
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {apuesta.tipoDeApuesta}
                    </Text>
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {apuesta.cuota != null
                        ? Number(apuesta.cuota).toFixed(2)
                        : "—"}
                    </Text>
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {apuesta.cantidadApostada != null
                        ? Number(apuesta.cantidadApostada).toFixed(2)
                        : "—"}
                    </Text>
                  </View>
                  <View style={[styles.tableCell, styles.border]}>
                    <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                      {Number(
                        apuesta.ganancia ??
                          gananciaApuesta(
                            apuesta.cantidadApostada,
                            apuesta.cuota,
                            apuesta.acierto
                          )
                      ).toFixed(2)}
                      <Text>u</Text>
                    </Text>
                  </View>
                </TouchableOpacity>
              )}
            />
          </View>
        </ScrollView>
      )}

      {/* Retos del tipster: van aparte de las apuestas diarias */}
      {retos.length > 0 && (
        <View style={styles.retosSection}>
          <Text style={styles.retosTitle}>Retos</Text>
          {retos.map((reto) => (
            <TouchableOpacity
              key={reto.id}
              onPress={() => onPickPress(reto)}
              style={styles.retoRow}
            >
              <Text style={styles.retoText}>
                {reto.apuesta || "(sin nombre)"}
                {reto.evento ? ` · ${reto.evento}` : ""}
              </Text>
              <Text style={styles.retoMeta}>
                {formatDateShort(reto.fecha)} · cuota{" "}
                {reto.cuota != null ? Number(reto.cuota).toFixed(2) : "—"} ·{" "}
                <PronosticoTelegram
                  acierto={reto.acierto}
                  anulada={reto.anulada}
                  motivoAnulada={reto.motivoAnulada}
                />
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      )}

      {/* Combinadas del tipster: sección propia, fuera de las stats de
          simples. Cada padre se expande para ver las patas con su
          resultado individual. */}
      {combinadas.length > 0 && (
        <View style={styles.retosSection}>
          <Text style={styles.retosTitle}>Combinadas</Text>
          {combinadasStats.total > 0 && (
            <Text style={styles.retoMeta}>
              {combinadasStats.total} combinadas · {combinadasStats.aciertos}{" "}
              aciertos · {combinadasStats.ganancias.toFixed(2)}u · yield{" "}
              {combinadasStats.yieldPct.toFixed(2)}%
            </Text>
          )}
          {combinadas.map((comb) => {
            const abierta = !!combinadasAbiertas[comb.id];
            const numPatas = comb.patas?.length ?? 0;
            return (
              <View key={comb.id} style={styles.retoRow}>
                <View style={styles.combinadaHeader}>
                  <TouchableOpacity
                    style={{ flex: 1 }}
                    onPress={() => onPickPress(comb)}
                  >
                    <Text style={styles.retoText}>
                      {comb.apuesta || "(combinada)"}
                      {comb.evento ? ` · ${comb.evento}` : ""}
                    </Text>
                    <Text style={styles.retoMeta}>
                      {formatDateShort(comb.fecha)} · cuota{" "}
                      {Number(comb.cuotaEfectiva ?? comb.cuota).toFixed(2)}
                      {comb.cuotaEfectiva != null &&
                      comb.cuotaEfectiva !== comb.cuota
                        ? ` (declarada ${Number(comb.cuota).toFixed(2)})`
                        : ""}
                      {" · "}
                      {numPatas} patas ·{" "}
                      <PronosticoTelegram
                        acierto={comb.acierto}
                        anulada={comb.anulada}
                        motivoAnulada={comb.motivoAnulada}
                      />
                    </Text>
                  </TouchableOpacity>
                  {numPatas > 0 && (
                    <TouchableOpacity
                      style={styles.combinadaChevron}
                      onPress={() =>
                        setCombinadasAbiertas((prev) => ({
                          ...prev,
                          [comb.id]: !abierta,
                        }))
                      }
                      accessibilityLabel="Expandir combinada"
                    >
                      <MaterialCommunityIcons
                        name={abierta ? "chevron-up" : "chevron-down"}
                        size={22}
                        color={colors.purple}
                      />
                    </TouchableOpacity>
                  )}
                </View>
                {abierta &&
                  (comb.patas ?? []).map((pata) => (
                    <View key={pata.id} style={styles.pataRow}>
                      <Text style={styles.pataIcono}>
                        {pata.anulada
                          ? iconoAnulada(pata.motivo_anulada)
                          : pata.acierto === true
                            ? "✅"
                            : pata.acierto === false
                              ? "❌"
                              : "❓"}
                      </Text>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.pataText}>
                          {pata.seleccion || "(pata)"}
                        </Text>
                        <Text style={styles.pataMeta}>
                          {[
                            pata.evento,
                            pata.mercado,
                            pata.anulada ? "anulada" : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </Text>
                      </View>
                    </View>
                  ))}
              </View>
            );
          })}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "rgba(33, 33, 33, 0.5)",
    padding: 20,
    borderRadius: 10,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
    marginBottom: 20,
  },
  cardTitle: {
    fontSize: 25,
    fontWeight: "bold",
    marginBottom: 30,
    textAlign: "center",
    color: colors.primarySoft,
  },
  cardSubtitle: {
    fontSize: 13,
    color: colors.textSecondary,
    textAlign: "center",
    marginTop: -20,
    marginBottom: 15,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  label: {
    fontSize: 18,
    color: colors.text,
  },
  value: {
    fontSize: 16,
    fontWeight: "bold",
    color: colors.text,
  },
  telegramButton: {
    backgroundColor: colors.primary,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    alignSelf: "center",
    marginVertical: 12,
    alignItems: "center",
  },
  telegramButtonText: {
    color: colors.background,
    fontWeight: "bold",
    fontSize: 14,
  },
  emptyText: {
    fontSize: 15,
    color: colors.textSecondary,
    textAlign: "center",
    marginVertical: 15,
    fontStyle: "italic",
  },
  table: {
    backgroundColor: colors.surface,
    borderRadius: 10,
    borderColor: colors.text,
    padding: 15,
    marginBottom: 20,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
  },
  // Altura acotada para que la FlatList virtualice.
  tableRows: {
    maxHeight: 500,
  },
  tableRow: {
    flexDirection: "row",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tableHeaderCell: {
    width: 150, // Ajusta el tamaño de cada celda
    paddingVertical: 12,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: colors.primary,
    borderBottomWidth: 2,
    borderBottomColor: colors.background,
  },
  tableHeaderText: {
    fontSize: 16,
    fontWeight: "bold",
    textAlign: "center",
    color: colors.onPrimary,
  },
  tableCell: {
    width: 150, // Ajusta el tamaño de cada celda
    paddingVertical: 10,
    paddingHorizontal: 15, // Añadido más espacio horizontal
    justifyContent: "center",
    alignItems: "center",
    textAlign: "center",
    backgroundColor: colors.surface,
  },
  cellText: {
    fontSize: 16,
    fontWeight: "bold",
    textAlign: "center",
    color: colors.text,
  },
  eventoText: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  icon: {
    fontSize: 17,
    textAlign: "center",
  },
  border: {
    borderWidth: 1,
    borderColor: colors.border,
  },
  evenRow: {
    backgroundColor: colors.surfaceAlt,
  },
  oddRow: {
    backgroundColor: colors.surface,
  },
  retosSection: {
    marginTop: 16,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
  },
  retosTitle: {
    color: colors.purple,
    fontSize: 16,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  retoRow: {
    backgroundColor: colors.purpleBg,
    borderColor: colors.purpleBg,
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
  },
  retoText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "bold",
  },
  retoMeta: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 4,
  },
  combinadaHeader: {
    flexDirection: "row",
    alignItems: "center",
  },
  combinadaChevron: {
    padding: 4,
    marginLeft: 8,
  },
  pataRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: colors.purpleBg,
    paddingLeft: 8,
  },
  pataIcono: {
    fontSize: 14,
    marginRight: 8,
  },
  pataText: {
    color: colors.purpleText,
    fontSize: 13,
  },
  pataMeta: {
    color: colors.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
});
