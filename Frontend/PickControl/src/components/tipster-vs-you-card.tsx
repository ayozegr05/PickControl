// Card "Tipster vs. tú", extraída de
// app/dynamic-routes/[informante].tsx (Fase D): compara lo que el
// tipster publica (unidades según su stake) con el resultado real del
// usuario (euros apostados).
import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { metricColor } from "@/src/theme";
import { colors } from "@/src/theme";
import { Card } from "@/src/components/card";

export interface ColumnaComparativa {
  yieldPct: number;
  totalApuestas: number;
  porcentajeAciertos: number;
  ganancias: number;
  pendientes: number;
}

interface TipsterVsYouCardProps {
  tipster: ColumnaComparativa;
  usuario: ColumnaComparativa;
}

export default function TipsterVsYouCard({
  tipster,
  usuario,
}: TipsterVsYouCardProps) {
  return (
    <Card variant="glass">
      <Text style={[styles.cardTitle, { marginBottom: 12 }]}>
        Tipster vs. tú
      </Text>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel} />
        <Text style={styles.compareColTitle}>El tipster</Text>
        <Text style={styles.compareColTitle}>Tú</Text>
      </View>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel}>Yield</Text>
        <Text
          style={[
            styles.compareValue,
            { color: metricColor(tipster.yieldPct) },
          ]}
        >
          {tipster.totalApuestas > 0 ? `${tipster.yieldPct.toFixed(2)}%` : "—"}
        </Text>
        <Text
          style={[
            styles.compareValue,
            { color: metricColor(usuario.yieldPct) },
          ]}
        >
          {usuario.totalApuestas > 0 ? `${usuario.yieldPct.toFixed(2)}%` : "—"}
        </Text>
      </View>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel}>% acierto</Text>
        <Text style={styles.compareValue}>
          {tipster.totalApuestas > 0
            ? `${tipster.porcentajeAciertos.toFixed(1)}%`
            : "—"}
        </Text>
        <Text style={styles.compareValue}>
          {usuario.totalApuestas > 0
            ? `${usuario.porcentajeAciertos.toFixed(1)}%`
            : "—"}
        </Text>
      </View>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel}>Picks</Text>
        <Text style={styles.compareValue}>{tipster.totalApuestas}</Text>
        <Text style={styles.compareValue}>{usuario.totalApuestas}</Text>
      </View>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel}>Pendientes</Text>
        <Text style={styles.compareValue}>{tipster.pendientes}</Text>
        <Text style={styles.compareValue}>{usuario.pendientes}</Text>
      </View>
      <View style={styles.compareRow}>
        <Text style={styles.compareLabel}>Ganancia</Text>
        <Text
          style={[
            styles.compareValue,
            { color: metricColor(tipster.ganancias) },
          ]}
        >
          {tipster.totalApuestas > 0 ? `${tipster.ganancias.toFixed(2)}u` : "—"}
        </Text>
        <Text
          style={[
            styles.compareValue,
            { color: metricColor(usuario.ganancias) },
          ]}
        >
          {usuario.totalApuestas > 0 ? `${usuario.ganancias.toFixed(2)}€` : "—"}
        </Text>
      </View>
      <Text style={styles.compareNote}>
        El tipster: unidades (u) según su stake · Tú: euros que has apostado
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  cardTitle: {
    fontSize: 25,
    fontWeight: "bold",
    marginBottom: 30,
    textAlign: "center",
    color: colors.primarySoft,
  },
  compareRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  compareLabel: {
    flex: 1.2,
    fontSize: 15,
    color: colors.textSecondary,
  },
  compareColTitle: {
    flex: 1,
    fontSize: 14,
    fontWeight: "bold",
    color: colors.primary,
    textAlign: "center",
  },
  compareValue: {
    flex: 1,
    fontSize: 15,
    fontWeight: "bold",
    color: colors.text,
    textAlign: "center",
  },
  compareNote: {
    fontSize: 11,
    color: colors.textSecondary,
    textAlign: "center",
    marginTop: 10,
    fontStyle: "italic",
  },
});
