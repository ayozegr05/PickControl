import React from "react";
import { View, TouchableOpacity, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter, usePathname } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import FontAwesome6 from "@expo/vector-icons/FontAwesome6";

const BAR_HEIGHT = 60;

const BottomBar = () => {
  const router = useRouter();
  const pathname = usePathname(); // Obtiene la ruta actual
  const insets = useSafeAreaInsets();

  return (
    <View
      style={[
        styles.container,
        { height: BAR_HEIGHT + insets.bottom, paddingBottom: insets.bottom },
      ]}
      pointerEvents="box-none"
    >
      {/* Botón de Inicio (se oculta en la pantalla de inicio) */}
      {pathname !== "/" && (
        <TouchableOpacity onPress={() => router.push("/")}>
          <Ionicons name="home" size={32} color="orange" />
        </TouchableOpacity>
      )}

      {/* Botón de Ganancias */}
      <TouchableOpacity onPress={() => router.push("/screens/earns")}>
        <FontAwesome6 name="sack-dollar" size={24} color="orange" />
      </TouchableOpacity>

      {/* Botón de Análisis global (auditoría tipster vs tú) */}
      <TouchableOpacity onPress={() => router.push("/screens/auditoria")}>
        <Ionicons name="analytics" size={38} color="orange" />
      </TouchableOpacity>

      {/* Botón de Añadir */}
      <TouchableOpacity onPress={() => router.push("/screens/add-pick")}>
        <Ionicons name="add-circle" size={32} color="orange" />
      </TouchableOpacity>

      {/* Botón de Mis Apuestas */}
      <TouchableOpacity onPress={() => router.push("/screens/my-picks")}>
        <Ionicons name="list" size={30} color="orange" />
      </TouchableOpacity>

      {/* Botón de Picks de Telegram */}
      <TouchableOpacity onPress={() => router.push("/screens/parsed-picks")}>
        <Ionicons name="chatbubbles" size={30} color="orange" />
      </TouchableOpacity>

      {/* Botón de Canales monitorizados */}
      <TouchableOpacity onPress={() => router.push("/screens/canales")}>
        <Ionicons name="radio" size={28} color="orange" />
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "center",
    height: 60,
    width: "100%",
    backgroundColor: "#131313",
    position: "absolute",
    bottom: 0,
  },
});

export default BottomBar;
