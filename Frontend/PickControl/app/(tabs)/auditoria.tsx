import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  RefreshControl,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import TopBar from "@/src/components/top-bar";
import { getAnalisisGlobal } from "@/src/api/analisis.api";
import {
  AnalisisGlobal,
  AnalisisCanal,
  StatsBloque,
  OddsBloque,
} from "@/src/types/analisis.types";
import { metricColor } from "@/src/theme";
import { cleanChannel } from "@/src/utils/format";
import { toErrorMessage } from "@/src/utils/errors";
import { colors } from "@/src/theme";
import { Screen } from "@/src/components/screen";
import { ScreenTitle, ScreenSubtitle } from "@/src/components/typography";
import { Card } from "@/src/components/card";

function StatsLine({ label, stats }: { label: string; stats: StatsBloque }) {
  return (
    <View style={styles.statsLine}>
      <Text style={styles.statsLineLabel}>{label}</Text>
      <Text style={styles.statsLineValue}>
        {stats.aciertos}/{stats.total - stats.pendientes} aciertos ·{" "}
        {stats.ganancias.toFixed(2)}u ·{" "}
        <Text
          style={{ color: metricColor(stats.yield_pct), fontWeight: "bold" }}
        >
          yield {stats.yield_pct.toFixed(1)}%
        </Text>
      </Text>
    </View>
  );
}

// Auditoría de cuotas: veredicto del tipster contra el mercado
// (cuotas infladas, CLV medio y % que bate el cierre).
function OddsLine({ odds }: { odds: OddsBloque }) {
  if (odds.mapeados === 0) return null;
  return (
    <Text style={styles.jugadasText}>
      Mercado: {odds.mapeados}/{odds.con_evento} mapeados
      {odds.clv_medio !== null && (
        <>
          {" · "}
          <Text
            style={{ color: metricColor(odds.clv_medio), fontWeight: "bold" }}
          >
            CLV {odds.clv_medio > 0 ? "+" : ""}
            {odds.clv_medio.toFixed(1)}%
          </Text>
        </>
      )}
      {odds.con_clv > 0 && (
        <> · bate cierre {odds.pct_bate_cierre.toFixed(0)}%</>
      )}
      {odds.cuotas_infladas > 0 && (
        <>
          {" · "}
          <Text style={{ color: colors.danger, fontWeight: "bold" }}>
            {odds.pct_cuota_inflada.toFixed(0)}% cuotas infladas
          </Text>
        </>
      )}
    </Text>
  );
}

export default function AuditoriaScreen() {
  const [data, setData] = useState<AnalisisGlobal | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const load = useCallback(async () => {
    try {
      setData(await getAnalisisGlobal());
      setError(null);
    } catch (err: unknown) {
      setError(toErrorMessage(err, "Error desconocido"));
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load().finally(() => setLoading(false));
    }, [load])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const renderCanal = (canal: AnalisisCanal) => {
    const diffCuota =
      canal.cuota_media_tipster !== null && canal.cuota_media_mia !== null
        ? canal.cuota_media_mia - canal.cuota_media_tipster
        : null;
    return (
      <Card key={canal.informante_id} variant="outlined">
        <TouchableOpacity
          onPress={() =>
            router.push({
              pathname: "/informante/[informante]",
              params: { informante: canal.informante },
            })
          }
        >
          <Text style={styles.cardTitle}>{cleanChannel(canal.informante)}</Text>
        </TouchableOpacity>

        <StatsLine label="Tipster" stats={canal.tipster} />
        <StatsLine label="Yo" stats={canal.yo} />

        {canal.jugadas > 0 && (
          <Text style={styles.jugadasText}>
            La jugué {canal.jugadas} vez{canal.jugadas !== 1 ? "es" : ""}
            {diffCuota !== null && (
              <>
                {" "}
                · cuota media {canal.cuota_media_tipster?.toFixed(2)} →{" "}
                {canal.cuota_media_mia?.toFixed(2)}{" "}
                <Text
                  style={{
                    color: diffCuota >= 0 ? colors.success : colors.danger,
                    fontWeight: "bold",
                  }}
                >
                  ({diffCuota >= 0 ? "+" : ""}
                  {diffCuota.toFixed(2)})
                </Text>
              </>
            )}
          </Text>
        )}

        {canal.odds && <OddsLine odds={canal.odds} />}
      </Card>
    );
  };

  return (
    <Screen tone="black">
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 24 },
        ]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor={colors.primary}
          />
        }
      >
        <ScreenTitle size={22} style={styles.title}>
          Análisis global
        </ScreenTitle>
        <ScreenSubtitle size={13} style={styles.subtitle}>
          Lo que publican los tipsters vs lo que tú jugaste
        </ScreenSubtitle>

        {loading && <ActivityIndicator size="large" color={colors.primary} />}
        {error && <Text style={styles.error}>Error: {error}</Text>}

        {data && (
          <>
            {/* Resumen global */}
            <Card variant="outlined">
              <Text style={styles.sectionTitle}>Resumen</Text>
              <StatsLine label="Tipsters" stats={data.totales_tipster} />
              <StatsLine label="Yo" stats={data.totales_yo} />
              {data.totales_odds && <OddsLine odds={data.totales_odds} />}
            </Card>

            {/* Por deporte */}
            {data.deportes.length > 0 && (
              <Card variant="outlined">
                <Text style={styles.sectionTitle}>Por deporte</Text>
                {data.deportes.map((d) => (
                  <View key={d.deporte} style={styles.deporteRow}>
                    <Text style={styles.deporteName}>{d.deporte}</Text>
                    <StatsLine label="Tipster" stats={d.tipster} />
                    <StatsLine label="Yo" stats={d.yo} />
                  </View>
                ))}
              </Card>
            )}

            {/* Ranking por canal */}
            <Text style={styles.sectionTitleOuter}>Por canal</Text>
            {data.canales.length === 0 ? (
              <Text style={styles.emptyText}>
                Todavía no hay canales con datos.
              </Text>
            ) : (
              data.canales.map(renderCanal)
            )}

            <TouchableOpacity
              style={styles.linkButton}
              onPress={() => router.push("/analysis")}
            >
              <Text style={styles.linkButtonText}>
                Proyecciones (calculadora)
              </Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    marginBottom: 4,
  },
  subtitle: {
    marginBottom: 16,
  },
  sectionTitle: {
    color: colors.primary,
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    marginBottom: 8,
  },
  sectionTitleOuter: {
    color: colors.primary,
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    marginTop: 6,
    marginBottom: 8,
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "bold",
    marginBottom: 8,
  },
  statsLine: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  statsLineLabel: {
    color: colors.textMuted,
    fontSize: 13,
    width: 70,
  },
  statsLineValue: {
    color: colors.textSecondary,
    fontSize: 13,
    flexShrink: 1,
  },
  jugadasText: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 6,
  },
  deporteRow: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 8,
    marginTop: 4,
  },
  deporteName: {
    color: colors.purple,
    fontSize: 14,
    fontWeight: "bold",
    textTransform: "capitalize",
    marginBottom: 4,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: 14,
    marginTop: 12,
  },
  error: {
    color: colors.danger,
    fontSize: 14,
    marginBottom: 12,
  },
  linkButton: {
    marginTop: 10,
    alignItems: "center",
    paddingVertical: 12,
  },
  linkButtonText: {
    color: colors.primarySoft,
    fontSize: 14,
    fontWeight: "bold",
  },
});
