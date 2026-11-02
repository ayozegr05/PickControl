// Tabla "Mis apuestas siguiéndolo", extraída de
// app/dynamic-routes/[informante].tsx (Fase D). Cabecera fija + filas
// virtualizadas con FlatList (mismo patrón que rowsContainer de
// my-picks y la tabla de picks de esta pantalla). La fila pendiente
// deja pulsar el ❓ para liquidar, la fecha abre el date picker y la
// papelera confirma el borrado — todo vía callbacks de la pantalla.
import React from "react";
import {
  FlatList,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import { Acierto, PickItem } from "@/src/types/pick.types";
import { gananciaApuesta } from "@/src/utils/picks";
import { formatDateShort } from "@/src/utils/format";
import { colors } from "@/src/theme";

const HEADERS = [
  "Apuesta",
  "Acierto",
  "Fecha",
  "Tipo de Apuesta",
  "Cuota",
  "Cant. Apostada",
  "Ganancia",
  "Acciones",
] as const;

interface ApuestasTableProps {
  rows: PickItem[];
  onPronostico: (apuesta: PickItem) => void;
  onFecha: (apuesta: PickItem) => void;
  onEliminar: (apuesta: PickItem) => void;
}

// Icono de resultado de una apuesta manual: pendiente es pulsable
// (abre el modal de liquidación), True/False son solo lectura.
function PronosticoIcon({
  acierto,
  onPress,
}: {
  acierto: Acierto;
  onPress: () => void;
}) {
  if (acierto === "True") {
    return (
      <Text style={[styles.icon, { color: colors.success, fontSize: 18 }]}>
        ✅
      </Text>
    );
  }
  if (acierto === "False") {
    return <Text style={[styles.icon, { color: "red" }]}>❌</Text>;
  }
  return (
    <TouchableOpacity onPress={onPress}>
      <Text style={[styles.icon, { color: "gray" }]}>❓</Text>
    </TouchableOpacity>
  );
}

export default function ApuestasTable({
  rows,
  onPronostico,
  onFecha,
  onEliminar,
}: ApuestasTableProps) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={styles.table}>
        <View style={styles.tableRow}>
          {HEADERS.map((h) => (
            <View key={h} style={[styles.tableHeaderCell, styles.border]}>
              <Text style={styles.tableHeaderText}>{h}</Text>
            </View>
          ))}
        </View>

        <FlatList
          data={rows}
          keyExtractor={(a) => String(a.id)}
          style={styles.tableRows}
          renderItem={({ item: apuesta, index }) => (
            <View
              style={[
                styles.tableRow,
                index % 2 === 0 ? styles.evenRow : styles.oddRow,
              ]}
            >
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                  {apuesta.apuesta || "(sin nombre)"}
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text>
                  <PronosticoIcon
                    acierto={apuesta.acierto}
                    onPress={() => onPronostico(apuesta)}
                  />
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <TouchableOpacity
                  onPress={() => onFecha(apuesta)}
                  style={styles.fechaContainer}
                  accessibilityLabel="Editar fecha"
                >
                  <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                    {formatDateShort(apuesta.fecha)}
                  </Text>
                  <MaterialCommunityIcons
                    name="calendar-edit"
                    size={16}
                    color="#ff9f1c"
                    style={styles.calendarIcon}
                  />
                </TouchableOpacity>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                  {apuesta.tipoDeApuesta}
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                  {apuesta.cuota != null ? Number(apuesta.cuota).toFixed(2) : "—"}
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                  {apuesta.cantidadApostada != null
                    ? Number(apuesta.cantidadApostada).toFixed(2)
                    : "—"}
                  <Text>€</Text>
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <Text style={[styles.cellText, { fontWeight: "bold" }]}>
                  {Number(
                    gananciaApuesta(
                      apuesta.cantidadApostada,
                      apuesta.cuota,
                      apuesta.acierto
                    )
                  ).toFixed(2)}
                  <Text>€</Text>
                </Text>
              </View>
              <View style={[styles.tableCell, styles.border]}>
                <TouchableOpacity
                  onPress={() => onEliminar(apuesta)}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                  accessibilityLabel="Eliminar apuesta"
                >
                  <MaterialCommunityIcons
                    name="trash-can"
                    size={22}
                    color={colors.danger}
                  />
                </TouchableOpacity>
              </View>
            </View>
          )}
        />
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  table: {
    backgroundColor: "#2e2e2e",
    borderRadius: 10,
    borderColor: "white",
    padding: 15,
    marginBottom: 20,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 3,
  },
  // Altura acotada para que la FlatList virtualice (mismo patrón que
  // rowsContainer de my-picks): la tabla scrollea dentro de la página.
  tableRows: {
    maxHeight: 500,
  },
  tableRow: {
    flexDirection: "row",
    borderBottomWidth: 1,
    borderBottomColor: "#444",
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
  icon: {
    fontSize: 17,
    textAlign: "center",
  },
  border: {
    borderWidth: 1,
    borderColor: "#555",
  },
  evenRow: {
    backgroundColor: colors.surfaceAlt,
  },
  oddRow: {
    backgroundColor: colors.surface,
  },
  fechaContainer: {
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
  },
  calendarIcon: {
    marginLeft: 5,
  },
});
