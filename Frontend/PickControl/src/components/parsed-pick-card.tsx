// Tarjeta de un pick extraído de Telegram (extraída de
// app/(tabs)/parsed-picks.tsx en Fase D): título, canal, cuota/stake,
// estado de liquidación con botones de corrección y patas de
// combinada con corrección por pata. La lógica de negocio (qué PATCH
// enviar y qué recargar) sigue en la pantalla vía callbacks.
import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { ParsedPick } from "@/src/api/parsed-picks.api";
import {
  formatDateTime as formatDate,
  cleanChannel,
} from "@/src/utils/format";

export type CorregirUpdate = {
  acierto?: boolean | null;
  anulada?: boolean;
};

type Props = {
  pick: ParsedPick;
  /** Modo "Ver todos": muestra si el filtro lo marcó como apuesta. */
  showAll?: boolean;
  onCorregir: (pick: ParsedPick, update: CorregirUpdate) => void;
  onCorregirPata: (pata: ParsedPick, update: CorregirUpdate) => void;
  onChannelPress: (informante: string) => void;
};

const estadoPata = (pata: ParsedPick): string => {
  if (pata.anulada) {
    return pata.motivo_anulada ? `Anulada · ${pata.motivo_anulada}` : "Anulada";
  }
  if (pata.acierto === true) return "Acertó";
  if (pata.acierto === false) return "Falló";
  return "Pendiente";
};

export default function ParsedPickCard({
  pick,
  showAll = false,
  onCorregir,
  onCorregirPata,
  onChannelPress,
}: Props) {
  const renderPataRow = (pata: ParsedPick) => (
    <View key={pata.id} style={styles.pataRow}>
      <View style={{ flex: 1 }}>
        <Text style={styles.pataText}>
          {pata.orden != null ? `${pata.orden + 1}. ` : ""}
          {pata.seleccion || "(pata)"}
        </Text>
        {pata.evento ? (
          <Text style={styles.pataMeta}>{pata.evento}</Text>
        ) : null}
        <Text style={styles.pataMeta}>
          {estadoPata(pata)}
          {pata.verificado_por ? ` (${pata.verificado_por})` : ""}
          {pata.fuera_ventana ? " · fuera de ventana" : ""}
        </Text>
      </View>
      <View style={styles.pataButtons}>
        <TouchableOpacity
          onPress={() => onCorregirPata(pata, { acierto: true })}
        >
          <Text style={styles.pataButton}>✅</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => onCorregirPata(pata, { acierto: false })}
        >
          <Text style={styles.pataButton}>❌</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => onCorregirPata(pata, { anulada: true })}
        >
          <Text style={styles.pataButton}>↩️</Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => onCorregirPata(pata, { acierto: null, anulada: false })}
        >
          <Text style={styles.pataButton}>❓</Text>
        </TouchableOpacity>
      </View>
    </View>
  );

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{pick.apuesta || "Sin apuesta"}</Text>
      <Text style={styles.cardDate}>
        {formatDate(pick.fecha_evento ?? pick.created_at)}
      </Text>
      <Text style={styles.cardMeta}>
        Canal:{" "}
        <Text
          style={styles.channelLink}
          onPress={() => {
            if (!pick.informante) return;
            onChannelPress(pick.informante);
          }}
        >
          {cleanChannel(pick.informante)}
        </Text>
      </Text>
      <Text style={styles.cardMeta}>
        Cuota: {pick.cuota ?? "-"} | Stake: {pick.stake ?? "-"}
      </Text>
      <Text style={styles.cardMeta}>
        Método: {pick.metodo} | Confianza: {pick.confianza}
      </Text>
      {showAll && (
        <Text style={styles.cardMeta}>
          Es apuesta: {pick.es_apuesta ? "Sí" : "No"}
        </Text>
      )}
      {pick.explicacion && (
        <Text style={styles.explanation}>{pick.explicacion}</Text>
      )}

      {pick.es_apuesta && (
        <View style={styles.acertoSection}>
          {pick.linea !== null && (
            <Text style={styles.cardMeta}>Línea: {pick.linea}</Text>
          )}
          <Text style={styles.cardMeta}>
            Resultado:{" "}
            <Text
              style={
                pick.anulada
                  ? styles.acertoAnulada
                  : pick.acierto === true
                    ? styles.acertoTrue
                    : pick.acierto === false
                      ? styles.acertoFalse
                      : styles.acertoPending
              }
            >
              {pick.anulada
                ? pick.motivo_anulada
                  ? `Anulada · ${pick.motivo_anulada}`
                  : "Anulada"
                : pick.acierto === true
                  ? "Acertó"
                  : pick.acierto === false
                    ? "Falló"
                    : "Pendiente"}
            </Text>
            {pick.verificado_por && (
              <Text style={styles.cardMeta}> ({pick.verificado_por})</Text>
            )}
          </Text>
          {pick.fuera_ventana && (
            <Text style={styles.fueraVentana}>
              fuera de ventana — ya no se reintenta
            </Text>
          )}

          <View style={styles.acertoButtons}>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonTrue]}
              onPress={() => onCorregir(pick, { acierto: true })}
            >
              <Text style={styles.acertoButtonText}>Acertó</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonFalse]}
              onPress={() => onCorregir(pick, { acierto: false })}
            >
              <Text style={styles.acertoButtonText}>Falló</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonAnulada]}
              onPress={() => onCorregir(pick, { anulada: true })}
            >
              <Text style={styles.acertoButtonText}>Anulada</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.acertoButton, styles.acertoButtonPending]}
              onPress={() =>
                onCorregir(pick, {
                  acierto: null,
                  anulada: false,
                })
              }
            >
              <Text style={styles.acertoButtonText}>Pendiente</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {pick.es_combinada && (pick.patas?.length ?? 0) > 0 && (
        <View style={styles.acertoSection}>
          <Text style={styles.cardMeta}>
            Patas ({pick.patas!.length})
            {pick.cuota_efectiva != null && pick.cuota_efectiva !== pick.cuota
              ? ` · cuota efectiva ${pick.cuota_efectiva}`
              : ""}
          </Text>
          {(pick.patas ?? []).map(renderPataRow)}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
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
    marginBottom: 4,
  },
  cardDate: {
    color: "#ff9f1c",
    fontSize: 12,
    marginBottom: 6,
  },
  cardMeta: {
    color: "#aaa",
    fontSize: 13,
    marginBottom: 2,
  },
  explanation: {
    color: "#ccc",
    fontSize: 12,
    marginTop: 8,
  },
  acertoSection: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#333",
  },
  acertoTrue: {
    color: "#4caf50",
    fontWeight: "bold",
  },
  acertoFalse: {
    color: "#f44336",
    fontWeight: "bold",
  },
  acertoPending: {
    color: "#ff9f1c",
    fontWeight: "bold",
  },
  acertoAnulada: {
    color: "#9e9e9e",
    fontWeight: "bold",
  },
  acertoButtons: {
    flexDirection: "row",
    marginTop: 8,
    gap: 8,
  },
  acertoButton: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    marginRight: 8,
  },
  acertoButtonTrue: {
    backgroundColor: "#1e3a24",
    borderColor: "#4caf50",
    borderWidth: 1,
  },
  acertoButtonFalse: {
    backgroundColor: "#3a1e1e",
    borderColor: "#f44336",
    borderWidth: 1,
  },
  acertoButtonAnulada: {
    backgroundColor: "#2a2a2a",
    borderColor: "#9e9e9e",
    borderWidth: 1,
  },
  acertoButtonPending: {
    backgroundColor: "#332a1a",
    borderColor: "#ff9f1c",
    borderWidth: 1,
  },
  acertoButtonText: {
    color: "#fff",
    fontSize: 12,
  },
  fueraVentana: {
    color: "#ff9800",
    fontSize: 11,
    fontStyle: "italic",
    marginTop: 2,
  },
  pataRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderTopWidth: 1,
    borderTopColor: "#2a2a2a",
  },
  pataText: {
    color: "#e5e5e5",
    fontSize: 13,
  },
  pataMeta: {
    color: "#888",
    fontSize: 11,
    marginTop: 2,
  },
  pataButtons: {
    flexDirection: "row",
    marginLeft: 8,
  },
  pataButton: {
    fontSize: 16,
    marginLeft: 6,
  },
  channelLink: {
    color: "#ff9f1c",
    textDecorationLine: "underline",
  },
});
