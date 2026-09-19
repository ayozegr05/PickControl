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
import BottomBar from "@/src/components/bottom-bar";
import {
  getAnalisisGlobal,
  AnalisisGlobal,
  AnalisisCanal,
  StatsBloque,
  OddsBloque,
} from "@/src/api/analisis.api";

function yieldColor(y: number): string {
  if (y > 0) return "#4caf50";
  if (y < 0) return "#f44336";
  return "#aaa";
}

function cleanChannel(name: string): string {
  return (
    name
      .split("|")
      .map((part) =>
        part
          .replace(/[^\p{L}\p{N}\s]/gu, " ")
          .replace(/\s+/g, " ")
          .trim()
      )
      .filter(Boolean)
      .join(" ") || "desconocido"
  );
}

function StatsLine({ label, stats }: { label: string; stats: StatsBloque }) {
  return (
    <View style={styles.statsLine}>
      <Text style={styles.statsLineLabel}>{label}</Text>
      <Text style={styles.statsLineValue}>
        {stats.aciertos}/{stats.total - stats.pendientes} aciertos ·{" "}
        {stats.ganancias.toFixed(2)}u ·{" "}
        <Text style={{ color: yieldColor(stats.yield_pct), fontWeight: "bold" }}>
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
          <Text style={{ color: yieldColor(odds.clv_medio), fontWeight: "bold" }}>
            CLV {odds.clv_medio > 0 ? "+" : ""}
            {odds.clv_medio.toFixed(1)}%
          </Text>
        </>
      )}
      {odds.con_clv > 0 && <> · bate cierre {odds.pct_bate_cierre.toFixed(0)}%</>}
      {odds.cuotas_infladas > 0 && (
        <>
          {" · "}
          <Text style={{ color: "#f44336", fontWeight: "bold" }}>
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
    } catch (err: any) {
      setError(err.message);
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
      <View key={canal.informante_id} style={styles.card}>
        <TouchableOpacity
          onPress={() =>
            router.push(`/dynamic-routes/${canal.informante}` as any)
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
                    color: diffCuota >= 0 ? "#4caf50" : "#f44336",
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
      </View>
    );
  };

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 80 + insets.bottom },
        ]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor="#ff9f1c"
          />
        }
      >
        <Text style={styles.title}>Análisis global</Text>
        <Text style={styles.subtitle}>
          Lo que publican los tipsters vs lo que tú jugaste
        </Text>

        {loading && <ActivityIndicator size="large" color="#ff9f1c" />}
        {error && <Text style={styles.error}>Error: {error}</Text>}

        {data && (
          <>
            {/* Resumen global */}
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Resumen</Text>
              <StatsLine label="Tipsters" stats={data.totales_tipster} />
              <StatsLine label="Yo" stats={data.totales_yo} />
              {data.totales_odds && <OddsLine odds={data.totales_odds} />}
            </View>

            {/* Por deporte */}
            {data.deportes.length > 0 && (
              <View style={styles.card}>
                <Text style={styles.sectionTitle}>Por deporte</Text>
                {data.deportes.map((d) => (
                  <View key={d.deporte} style={styles.deporteRow}>
                    <Text style={styles.deporteName}>{d.deporte}</Text>
                    <StatsLine label="Tipster" stats={d.tipster} />
                    <StatsLine label="Yo" stats={d.yo} />
                  </View>
                ))}
              </View>
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
              onPress={() => router.push("/screens/analysis")}
            >
              <Text style={styles.linkButtonText}>
                Proyecciones (calculadora)
              </Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
      <BottomBar />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0d0d0d",
  },
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    color: "#ff9f1c",
    fontSize: 22,
    fontWeight: "bold",
    marginBottom: 4,
  },
  subtitle: {
    color: "#aaa",
    fontSize: 13,
    marginBottom: 16,
  },
  sectionTitle: {
    color: "#ff9f1c",
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    marginBottom: 8,
  },
  sectionTitleOuter: {
    color: "#ff9f1c",
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    marginTop: 6,
    marginBottom: 8,
  },
  card: {
    backgroundColor: "#1a1a1a",
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    borderColor: "#333",
    borderWidth: 1,
  },
  cardTitle: {
    color: "#fff",
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
    color: "#888",
    fontSize: 13,
    width: 70,
  },
  statsLineValue: {
    color: "#ddd",
    fontSize: 13,
    flexShrink: 1,
  },
  jugadasText: {
    color: "#9e9e9e",
    fontSize: 12,
    marginTop: 6,
  },
  deporteRow: {
    borderTopWidth: 1,
    borderTopColor: "#333",
    paddingTop: 8,
    marginTop: 4,
  },
  deporteName: {
    color: "#b388ff",
    fontSize: 14,
    fontWeight: "bold",
    textTransform: "capitalize",
    marginBottom: 4,
  },
  emptyText: {
    color: "#aaa",
    fontSize: 14,
    marginTop: 12,
  },
  error: {
    color: "red",
    fontSize: 14,
    marginBottom: 12,
  },
  linkButton: {
    marginTop: 10,
    alignItems: "center",
    paddingVertical: 12,
  },
  linkButtonText: {
    color: "#ffcf8a",
    fontSize: 14,
    fontWeight: "bold",
  },
});
