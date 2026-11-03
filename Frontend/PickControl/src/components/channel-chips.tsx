// Barra horizontal de chips de canal del overview de parsed-picks
// (extraída de app/(tabs)/parsed-picks.tsx en Fase D): "Todos" +
// un chip por canal con informanteId.
import React from "react";
import {
  ScrollView,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

export interface ChannelChip {
  key: string;
  informanteId: number | null;
  name: string;
}

export type ChannelSelection = { id: number; name: string } | null;

type Props = {
  groups: ChannelChip[];
  selectedId: number | null;
  onSelect: (selection: ChannelSelection) => void;
};

export default function ChannelChips({ groups, selectedId, onSelect }: Props) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.channelList}
    >
      <TouchableOpacity
        onPress={() => onSelect(null)}
        style={[styles.channelChip, selectedId === null && styles.chipActive]}
      >
        <Text
          style={[
            styles.channelChipText,
            selectedId === null && styles.chipTextActive,
          ]}
        >
          Todos
        </Text>
      </TouchableOpacity>
      {groups
        .filter((group) => group.informanteId !== null)
        .map((group) => (
          <TouchableOpacity
            key={group.key}
            onPress={() =>
              onSelect({ id: group.informanteId as number, name: group.name })
            }
            style={[
              styles.channelChip,
              selectedId === group.informanteId && styles.chipActive,
            ]}
          >
            <Text
              style={[
                styles.channelChipText,
                selectedId === group.informanteId && styles.chipTextActive,
              ]}
            >
              {group.name}
            </Text>
          </TouchableOpacity>
        ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  channelList: {
    paddingVertical: 8,
    gap: 8,
  },
  channelChip: {
    backgroundColor: "#1a1a1a",
    borderColor: "#333",
    borderWidth: 1,
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginRight: 8,
  },
  chipActive: {
    backgroundColor: "#ff9f1c",
    borderColor: "#ff9f1c",
  },
  channelChipText: {
    color: "#aaa",
    fontSize: 13,
  },
  chipTextActive: {
    color: "#0d0d0d",
    fontWeight: "bold",
  },
});
