import React, { useCallback, useState } from "react";
import {
  Alert,
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import TopBar from "@/src/components/top-bar";
import { useAuth } from "@/src/context/AuthContext";
import {
  addCanal,
  deleteCanal,
  listCanales,
  listCanalesDisponibles,
  setCanalActivo,
} from "@/src/api/channels.api";
import { Canal, CanalDisponible } from "@/src/types/channel.types";
import { toErrorMessage } from "@/src/utils/errors";
import { colors } from "@/src/theme";
import { Screen } from "@/src/components/screen";
import { ScreenTitle, ScreenSubtitle } from "@/src/components/typography";
import { Card } from "@/src/components/card";

const Canales = () => {
  const [canales, setCanales] = useState<Canal[]>([]);
  const [disponibles, setDisponibles] = useState<CanalDisponible[]>([]);
  const [nuevoTarget, setNuevoTarget] = useState("");
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disponiblesError, setDisponiblesError] = useState<string | null>(null);

  const { isAuthenticated } = useAuth();
  const insets = useSafeAreaInsets();

  const load = useCallback(async () => {
    if (!isAuthenticated) return;
    setLoading(true);
    setError(null);
    try {
      setCanales(await listCanales());
    } catch (err: unknown) {
      setError(toErrorMessage(err, "Error cargando canales"));
    } finally {
      setLoading(false);
    }
    try {
      setDisponibles(await listCanalesDisponibles());
      setDisponiblesError(null);
    } catch (err: unknown) {
      setDisponibles([]);
      setDisponiblesError(toErrorMessage(err, "No se pudo listar tus canales"));
    }
  }, [isAuthenticated]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleToggle = async (canal: Canal, activo: boolean) => {
    try {
      const updated = await setCanalActivo(canal.id, activo);
      setCanales((prev) => prev.map((c) => (c.id === canal.id ? updated : c)));
    } catch (err: unknown) {
      Alert.alert(
        "Error",
        toErrorMessage(err, "No se pudo actualizar el canal")
      );
    }
  };

  const handleDelete = (canal: Canal) => {
    Alert.alert(
      "Quitar canal",
      `Se dejará de monitorizar "${canal.name || canal.target}". El historial de mensajes y picks se conserva.`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Quitar",
          style: "destructive",
          onPress: async () => {
            try {
              await deleteCanal(canal.id);
              // Borrado lógico: sale de "Monitorizados" y vuelve a la
              // lista de disponibles de abajo (la fila se conserva).
              setCanales((prev) => prev.filter((c) => c.id !== canal.id));
              try {
                setDisponibles(await listCanalesDisponibles());
              } catch {
                // Si el listener no responde, se refresca al re-entrar.
              }
            } catch (err: unknown) {
              Alert.alert(
                "Error",
                toErrorMessage(err, "No se pudo quitar el canal")
              );
            }
          },
        },
      ]
    );
  };

  const handleAdd = async (target: string) => {
    if (!target.trim()) return;
    setAdding(true);
    try {
      await addCanal(target.trim());
      setNuevoTarget("");
      await load();
    } catch (err: unknown) {
      Alert.alert("Error", toErrorMessage(err, "No se pudo añadir el canal"));
    } finally {
      setAdding(false);
    }
  };

  const noMonitorizados = disponibles.filter((d) => !d.monitorizado);

  return (
    <Screen>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 24 },
        ]}
      >
        <ScreenTitle size={28} style={styles.title}>
          Canales
        </ScreenTitle>
        <ScreenSubtitle size={14} style={styles.subtitle}>
          Canales de Telegram que el sistema monitoriza para extraer picks.
          Quitar un canal no borra su historial.
        </ScreenSubtitle>

        <Text style={styles.sectionTitle}>Monitorizados</Text>
        {loading ? (
          <ActivityIndicator size="large" color={colors.primary} />
        ) : error ? (
          <Text style={styles.error}>{error}</Text>
        ) : canales.length === 0 ? (
          <Text style={styles.empty}>No hay canales configurados.</Text>
        ) : (
          canales.map((canal) => (
            <Card key={canal.id} style={styles.card}>
              <View style={styles.cardInfo}>
                <Text style={styles.cardName}>
                  {canal.name || canal.target}
                </Text>
                <Text style={styles.cardMeta}>
                  {canal.username
                    ? `@${canal.username}`
                    : (canal.channelId ?? canal.target)}
                </Text>
              </View>
              <Switch
                value={canal.activo}
                onValueChange={(v) => handleToggle(canal, v)}
                trackColor={{ false: colors.border, true: colors.primary }}
                thumbColor={colors.text}
                accessibilityLabel={`Activar canal ${canal.name || canal.target}`}
                accessibilityState={{ checked: canal.activo }}
              />
              <TouchableOpacity
                onPress={() => handleDelete(canal)}
                style={styles.deleteButton}
                accessibilityRole="button"
                accessibilityLabel={`Eliminar canal ${canal.name || canal.target}`}
              >
                <Ionicons
                  name="trash-outline"
                  size={20}
                  color={colors.danger}
                />
              </TouchableOpacity>
            </Card>
          ))
        )}

        <Text style={styles.sectionTitle}>Añadir por enlace o id</Text>
        <View style={styles.addRow}>
          <TextInput
            style={styles.input}
            placeholder="t.me/canal, @canal o id"
            placeholderTextColor={colors.textMuted}
            value={nuevoTarget}
            onChangeText={setNuevoTarget}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <TouchableOpacity
            style={styles.addButton}
            onPress={() => handleAdd(nuevoTarget)}
            disabled={adding || !nuevoTarget.trim()}
          >
            {adding ? (
              <ActivityIndicator size="small" color={colors.text} />
            ) : (
              <Ionicons name="add" size={24} color={colors.text} />
            )}
          </TouchableOpacity>
        </View>

        <Text style={styles.sectionTitle}>Tus canales de Telegram</Text>
        {disponiblesError ? (
          <Text style={styles.muted}>{disponiblesError}</Text>
        ) : noMonitorizados.length === 0 ? (
          <Text style={styles.muted}>
            No hay más canales disponibles en tu cuenta.
          </Text>
        ) : (
          noMonitorizados.map((d) => (
            <Card key={d.channelId} style={styles.card}>
              <View style={styles.cardInfo}>
                <Text style={styles.cardName}>{d.name}</Text>
                <Text style={styles.cardMeta}>
                  {d.username ? `@${d.username}` : d.channelId}
                </Text>
              </View>
              <TouchableOpacity
                style={styles.monitorButton}
                onPress={() => handleAdd(d.username ?? String(d.channelId))}
                disabled={adding}
              >
                <Text style={styles.monitorButtonText}>Monitorizar</Text>
              </TouchableOpacity>
            </Card>
          ))
        )}
      </ScrollView>
    </Screen>
  );
};

const styles = StyleSheet.create({
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    marginBottom: 8,
  },
  subtitle: {
    marginBottom: 16,
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "bold",
    marginTop: 20,
    marginBottom: 10,
  },
  empty: {
    color: colors.textSecondary,
    fontSize: 15,
    textAlign: "center",
    marginTop: 10,
  },
  muted: {
    color: colors.textMuted,
    fontSize: 14,
    marginBottom: 8,
  },
  error: {
    color: colors.danger,
    fontSize: 15,
  },
  card: {
    marginBottom: 10,
    flexDirection: "row",
    alignItems: "center",
  },
  cardInfo: {
    flex: 1,
    marginRight: 10,
  },
  cardName: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "bold",
  },
  cardMeta: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: 2,
  },
  deleteButton: {
    padding: 8,
    marginLeft: 6,
  },
  addRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  input: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 15,
  },
  addButton: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    padding: 12,
    marginLeft: 10,
    minWidth: 48,
    alignItems: "center",
  },
  monitorButton: {
    backgroundColor: colors.primary,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  monitorButtonText: {
    color: colors.onPrimary,
    fontWeight: "bold",
    fontSize: 13,
  },
});

export default Canales;
