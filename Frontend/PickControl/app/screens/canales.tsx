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
import BottomBar from "@/src/components/bottom-bar";
import { useAuth } from "@/src/context/AuthContext";
import {
  addCanal,
  deleteCanal,
  listCanales,
  listCanalesDisponibles,
  setCanalActivo,
} from "@/src/api/channels.api";
import { Canal, CanalDisponible } from "@/src/types/channel.types";

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
    } catch (err: any) {
      setError(err.message || "Error cargando canales");
    } finally {
      setLoading(false);
    }
    try {
      setDisponibles(await listCanalesDisponibles());
      setDisponiblesError(null);
    } catch (err: any) {
      setDisponibles([]);
      setDisponiblesError(err.message || "No se pudo listar tus canales");
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
      setCanales((prev) =>
        prev.map((c) => (c.id === canal.id ? updated : c))
      );
    } catch (err: any) {
      Alert.alert("Error", err.message || "No se pudo actualizar el canal");
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
              // El backend conserva la fila como inactiva: el canal sigue
              // en la lista y se puede reactivar con el toggle.
              setCanales((prev) =>
                prev.map((c) =>
                  c.id === canal.id ? { ...c, activo: false } : c
                )
              );
            } catch (err: any) {
              Alert.alert(
                "Error",
                err.message || "No se pudo quitar el canal"
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
    } catch (err: any) {
      Alert.alert("Error", err.message || "No se pudo añadir el canal");
    } finally {
      setAdding(false);
    }
  };

  const noMonitorizados = disponibles.filter((d) => !d.monitorizado);

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: 80 + insets.top, paddingBottom: 80 + insets.bottom },
        ]}
      >
        <Text style={styles.title}>Canales</Text>
        <Text style={styles.subtitle}>
          Canales de Telegram que el sistema monitoriza para extraer picks.
          Quitar un canal no borra su historial.
        </Text>

        <Text style={styles.sectionTitle}>Monitorizados</Text>
        {loading ? (
          <ActivityIndicator size="large" color="#ff9f1c" />
        ) : error ? (
          <Text style={styles.error}>{error}</Text>
        ) : canales.length === 0 ? (
          <Text style={styles.empty}>No hay canales configurados.</Text>
        ) : (
          canales.map((canal) => (
            <View key={canal.id} style={styles.card}>
              <View style={styles.cardInfo}>
                <Text style={styles.cardName}>
                  {canal.name || canal.target}
                </Text>
                <Text style={styles.cardMeta}>
                  {canal.username
                    ? `@${canal.username}`
                    : canal.channelId ?? canal.target}
                </Text>
              </View>
              <Switch
                value={canal.activo}
                onValueChange={(v) => handleToggle(canal, v)}
                trackColor={{ false: "#555", true: "#ff9f1c" }}
                thumbColor="#fff"
              />
              <TouchableOpacity
                onPress={() => handleDelete(canal)}
                style={styles.deleteButton}
              >
                <Ionicons name="trash-outline" size={20} color="#f44336" />
              </TouchableOpacity>
            </View>
          ))
        )}

        <Text style={styles.sectionTitle}>Añadir por enlace o id</Text>
        <View style={styles.addRow}>
          <TextInput
            style={styles.input}
            placeholder="t.me/canal, @canal o id"
            placeholderTextColor="#888"
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
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <Ionicons name="add" size={24} color="#fff" />
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
            <View key={d.channelId} style={styles.card}>
              <View style={styles.cardInfo}>
                <Text style={styles.cardName}>{d.name}</Text>
                <Text style={styles.cardMeta}>
                  {d.username ? `@${d.username}` : d.channelId}
                </Text>
              </View>
              <TouchableOpacity
                style={styles.monitorButton}
                onPress={() =>
                  handleAdd(d.username ?? String(d.channelId))
                }
                disabled={adding}
              >
                <Text style={styles.monitorButtonText}>Monitorizar</Text>
              </TouchableOpacity>
            </View>
          ))
        )}
      </ScrollView>
      <BottomBar />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#1a1a1a",
  },
  scroll: {
    paddingHorizontal: 16,
  },
  title: {
    color: "#ff9f1c",
    fontSize: 28,
    fontWeight: "bold",
    marginBottom: 8,
  },
  subtitle: {
    color: "#aaa",
    fontSize: 14,
    marginBottom: 16,
  },
  sectionTitle: {
    color: "#fff",
    fontSize: 18,
    fontWeight: "bold",
    marginTop: 20,
    marginBottom: 10,
  },
  empty: {
    color: "#aaa",
    fontSize: 15,
    textAlign: "center",
    marginTop: 10,
  },
  muted: {
    color: "#888",
    fontSize: 14,
    marginBottom: 8,
  },
  error: {
    color: "#f44336",
    fontSize: 15,
  },
  card: {
    backgroundColor: "#2a2a2a",
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    flexDirection: "row",
    alignItems: "center",
  },
  cardInfo: {
    flex: 1,
    marginRight: 10,
  },
  cardName: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "bold",
  },
  cardMeta: {
    color: "#888",
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
    backgroundColor: "#2a2a2a",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: "#fff",
    fontSize: 15,
  },
  addButton: {
    backgroundColor: "#ff9f1c",
    borderRadius: 10,
    padding: 12,
    marginLeft: 10,
    minWidth: 48,
    alignItems: "center",
  },
  monitorButton: {
    backgroundColor: "#ff9f1c",
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  monitorButtonText: {
    color: "#fff",
    fontWeight: "bold",
    fontSize: 13,
  },
});

export default Canales;
