// Modal de detalle de un pick extraído de Telegram, extraído de
// app/dynamic-routes/[informante].tsx (Fase D). Muestra las filas de
// detalle, la caja de auditoría de cuotas (PickOdds), el CTA "Yo
// también la jugué" (o el check si ya está registrada) y los botones
// de corrección de resultado.
import React from "react";
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import ModalFrame from "@/src/components/modal-frame";
import { PickOdds } from "@/src/api/parsed-picks.api";
import { PickItem } from "@/src/types/pick.types";
import { formatDateShort } from "@/src/utils/format";
import { colors } from "@/src/theme";

export interface CorrecionPickPayload {
  acierto?: boolean | null;
  anulada?: boolean;
}

interface PickDetailModalProps {
  pick: PickItem | null;
  odds: PickOdds | null;
  oddsLoading: boolean;
  yaRegistrada: boolean;
  /** Solo admin: la corrección liquida picks del tipster. */
  canCorrect?: boolean;
  onClose: () => void;
  onJugar: (pick: PickItem) => void;
  onCorregir: (pick: PickItem, payload: CorrecionPickPayload) => void;
}

export default function PickDetailModal({
  pick,
  odds,
  oddsLoading,
  yaRegistrada,
  canCorrect = false,
  onClose,
  onJugar,
  onCorregir,
}: PickDetailModalProps) {
  return (
    <ModalFrame visible={pick !== null} onClose={onClose}>
      {pick && (
            <>
              <Text style={styles.modalTitle}>
                {pick.apuesta || "(sin nombre)"}
              </Text>
              <View style={styles.detailRows}>
                <Text style={styles.detailRow}>
                  Resultado:{" "}
                  {pick.acierto === "True"
                    ? "Acertó ✅"
                    : pick.acierto === "False"
                      ? "Falló ❌"
                      : "Pendiente ❓"}
                </Text>
                {pick.evento ? (
                  <Text style={styles.detailRow}>Evento: {pick.evento}</Text>
                ) : null}
                <Text style={styles.detailRow}>
                  Mercado: {pick.tipoDeApuesta || "-"}
                </Text>
                <Text style={styles.detailRow}>
                  Cuota:{" "}
                  {pick.cuota != null ? Number(pick.cuota).toFixed(2) : "—"} ·
                  Stake:{" "}
                  {pick.cantidadApostada != null
                    ? Number(pick.cantidadApostada).toFixed(2)
                    : "—"}
                  u
                </Text>
                <Text style={styles.detailRow}>
                  Fecha: {formatDateShort(pick.fecha)}
                </Text>
                {pick.casa ? (
                  <Text style={styles.detailRow}>Casa: {pick.casa}</Text>
                ) : null}
              </View>

              {oddsLoading ? (
                <ActivityIndicator
                  size="small"
                  color={colors.primary}
                  style={{ marginVertical: 8 }}
                />
              ) : odds ? (
                <View style={styles.oddsBox}>
                  <Text style={styles.oddsTitle}>Cuota de mercado</Text>
                  {odds.mapeado ? (
                    <>
                      <Text style={styles.oddsRow}>
                        {odds.mercado_api}
                        {odds.opcion_api ? ` · ${odds.opcion_api}` : ""}
                        {odds.linea_api ? ` (${odds.linea_api})` : ""}
                      </Text>
                      <Text style={styles.oddsRow}>
                        Tipster {odds.cuota_tipster?.toFixed(2) ?? "-"} ·
                        Apertura {odds.cuota_apertura?.toFixed(2) ?? "-"} · Al
                        publicar {odds.cuota_publicacion?.toFixed(2) ?? "-"} ·
                        Cierre {odds.cuota_cierre?.toFixed(2) ?? "-"}
                      </Text>
                      <View style={styles.oddsBadges}>
                        {odds.cuota_disponible === false && (
                          <Text style={styles.oddsBadgeInflada}>
                            ⚠ Cuota inflada
                          </Text>
                        )}
                        {odds.cuota_disponible === true && (
                          <Text style={styles.oddsBadgeOk}>✓ Cuota real</Text>
                        )}
                        {odds.clv_pct !== null && (
                          <Text
                            style={
                              odds.clv_pct >= 0
                                ? styles.oddsBadgeOk
                                : styles.oddsBadgeInflada
                            }
                          >
                            CLV {odds.clv_pct > 0 ? "+" : ""}
                            {odds.clv_pct.toFixed(1)}%
                          </Text>
                        )}
                      </View>
                    </>
                  ) : (
                    <Text style={styles.oddsRowMuted}>
                      Sin mercado comparable en el proveedor
                    </Text>
                  )}
                </View>
              ) : null}

              {yaRegistrada ? (
                <Text style={styles.jugadaText}>
                  ✓ Ya la tienes registrada en tus apuestas
                </Text>
              ) : (
                <TouchableOpacity
                  style={styles.jugarButton}
                  onPress={() => onJugar(pick)}
                >
                  <Text style={styles.jugarButtonText}>Yo también la jugué</Text>
                </TouchableOpacity>
              )}

              {canCorrect && (
                <>
              <Text style={styles.modalText}>Corregir resultado:</Text>
              <View style={styles.detailButtons}>
                <TouchableOpacity
                  style={[styles.detailButton, { backgroundColor: "#1e3a24" }]}
                  onPress={() =>
                    onCorregir(pick, { acierto: true, anulada: false })
                  }
                >
                  <Text style={styles.detailButtonText}>✅ Acertó</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.detailButton, { backgroundColor: "#3a1e1e" }]}
                  onPress={() =>
                    onCorregir(pick, { acierto: false, anulada: false })
                  }
                >
                  <Text style={styles.detailButtonText}>❌ Falló</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.detailButton, { backgroundColor: "#2a2a2a" }]}
                  onPress={() => onCorregir(pick, { anulada: true })}
                >
                  <Text style={styles.detailButtonText}>Anulada</Text>
                </TouchableOpacity>
              </View>
                </>
              )}
            </>
          )}
    </ModalFrame>
  );
}

const styles = StyleSheet.create({
  modalTitle: {
    fontSize: 24,
    fontWeight: "bold",
    color: "white",
    marginTop: 20,
    marginBottom: 15,
  },
  modalText: {
    fontSize: 18,
    color: "white",
    marginBottom: 20,
    textAlign: "center",
  },
  detailRows: {
    alignSelf: "stretch",
    marginBottom: 10,
  },
  detailRow: {
    fontSize: 15,
    color: "#ddd",
    marginBottom: 6,
  },
  oddsBox: {
    alignSelf: "stretch",
    backgroundColor: "#141414",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#333",
    padding: 10,
    marginBottom: 10,
  },
  oddsTitle: {
    fontSize: 12,
    color: colors.primary,
    fontWeight: "bold",
    textTransform: "uppercase",
    marginBottom: 6,
  },
  oddsRow: {
    fontSize: 13,
    color: "#ddd",
    marginBottom: 3,
  },
  oddsRowMuted: {
    fontSize: 13,
    color: "#888",
  },
  oddsBadges: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4,
  },
  oddsBadgeOk: {
    fontSize: 12,
    color: colors.success,
    fontWeight: "bold",
  },
  oddsBadgeInflada: {
    fontSize: 12,
    color: colors.danger,
    fontWeight: "bold",
  },
  detailButtons: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
  },
  detailButton: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: "#555",
  },
  detailButtonText: {
    color: "white",
    fontSize: 13,
    fontWeight: "bold",
  },
  jugarButton: {
    backgroundColor: colors.primary,
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 8,
    marginTop: 6,
    marginBottom: 4,
    alignSelf: "center",
  },
  jugarButtonText: {
    color: "#0d0d0d",
    fontSize: 15,
    fontWeight: "bold",
  },
  jugadaText: {
    color: colors.success,
    fontSize: 14,
    fontWeight: "bold",
    marginTop: 6,
    marginBottom: 4,
    alignSelf: "center",
  },
});
