// Sección del overview de parsed-picks por canal (extraída de
// app/(tabs)/parsed-picks.tsx en Fase D): cabecera con nombre +
// "Ver todos →", picks simples y subsecciones Combinadas/Retos.
import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { ParsedPick } from "@/src/types/parsed-pick.types";
import ParsedPickCard, {
  CorregirUpdate,
} from "@/src/components/parsed-pick-card";
import { colors } from "@/src/theme";

export interface ChannelGroup {
  key: string;
  informanteId: number | null;
  name: string;
  picks: ParsedPick[];
}

export interface PickCardProps {
  showAll: boolean;
  canCorrect: boolean;
  onCorregir: (pick: ParsedPick, update: CorregirUpdate) => void;
  onCorregirPata: (pata: ParsedPick, update: CorregirUpdate) => void;
  onChannelPress: (informante: string) => void;
}

type Props = {
  group: ChannelGroup;
  cardProps: PickCardProps;
  onVerTodos: () => void;
};

export default function ChannelSection({
  group,
  cardProps,
  onVerTodos,
}: Props) {
  const renderPick = (pick: ParsedPick) => (
    <ParsedPickCard key={pick.id} pick={pick} {...cardProps} />
  );
  return (
    <View style={styles.channelSection}>
      <View style={styles.channelHeader}>
        <Text style={styles.channelTitle}>{group.name}</Text>
        {group.informanteId !== null && (
          <TouchableOpacity onPress={onVerTodos}>
            <Text style={styles.seeAll}>Ver todos →</Text>
          </TouchableOpacity>
        )}
      </View>
      {group.picks.length === 0 ? (
        <Text style={styles.emptyChannel}>
          Sin picks extraídos con este filtro.
        </Text>
      ) : (
        <>
          {group.picks
            .filter((p) => !p.es_reto && !p.es_combinada)
            .map(renderPick)}
          {group.picks.some((p) => p.es_combinada) && (
            <>
              <Text style={styles.retosTitle}>Combinadas</Text>
              {group.picks.filter((p) => p.es_combinada).map(renderPick)}
            </>
          )}
          {group.picks.some((p) => p.es_reto) && (
            <>
              <Text style={styles.retosTitle}>Retos</Text>
              {group.picks.filter((p) => p.es_reto).map(renderPick)}
            </>
          )}
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  channelSection: {
    marginTop: 14,
    marginBottom: 6,
  },
  channelHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#241a0d",
    borderLeftWidth: 4,
    borderLeftColor: colors.primary,
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 12,
  },
  channelTitle: {
    color: colors.primary,
    fontSize: 16,
    fontWeight: "bold",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    flexShrink: 1,
  },
  seeAll: {
    color: "#ffcf8a",
    fontSize: 13,
    fontWeight: "bold",
    marginLeft: 12,
  },
  retosTitle: {
    color: "#b388ff",
    fontSize: 15,
    fontWeight: "bold",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 10,
    marginBottom: 10,
  },
  emptyChannel: {
    color: "#9a9a9a",
    fontSize: 13,
    marginBottom: 12,
    fontStyle: "italic",
  },
});
