// Navegación principal: Tabs nativos de expo-router.
//
// Sustituye a la BottomBar custom que hacía router.push() por icono:
// cada tap apilaba una pantalla nueva en el Stack (el botón atrás se
// convertía en trampa y la memoria crecía). Los Tabs mantienen cada
// pestaña montada y navegan sin apilar.
import React from "react";
import { ColorValue } from "react-native";
import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import FontAwesome6 from "@expo/vector-icons/FontAwesome6";
import { colors } from "@/src/theme";
import { useAuth } from "@/src/context/AuthContext";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];
type Fa6Name = React.ComponentProps<typeof FontAwesome6>["name"];

function tabIcon(name: IoniconName, fa6?: Fa6Name) {
  const Icon = ({ color, size }: { color: ColorValue; size: number }) =>
    fa6 ? (
      <FontAwesome6 name={fa6} size={size} color={color} />
    ) : (
      <Ionicons name={name} size={size} color={color} />
    );
  return Icon;
}

export default function TabsLayout() {
  const { isAuthenticated } = useAuth();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textSecondary,
        tabBarStyle: {
          backgroundColor: colors.bar,
          borderTopColor: colors.border,
          // Sin sesión solo existe la landing: la barra se oculta del
          // todo (antes se veían 7 iconos inútiles en login/register).
          display: isAuthenticated ? "flex" : "none",
        },
        tabBarLabelStyle: { fontSize: 10 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Inicio",
          tabBarIcon: tabIcon("home"),
        }}
      />
      <Tabs.Screen
        name="earns"
        options={{
          title: "Ganancias",
          tabBarIcon: tabIcon("cash-outline", "sack-dollar"),
          href: isAuthenticated ? "/earns" : null,
        }}
      />
      <Tabs.Screen
        name="auditoria"
        options={{
          title: "Análisis",
          tabBarIcon: tabIcon("analytics"),
          href: isAuthenticated ? "/auditoria" : null,
        }}
      />
      <Tabs.Screen
        name="add-pick"
        options={{
          title: "Añadir",
          tabBarIcon: tabIcon("add-circle"),
          href: isAuthenticated ? "/add-pick" : null,
        }}
      />
      <Tabs.Screen
        name="my-picks"
        options={{
          title: "Apuestas",
          tabBarIcon: tabIcon("list"),
          href: isAuthenticated ? "/my-picks" : null,
        }}
      />
      <Tabs.Screen
        name="parsed-picks"
        options={{
          title: "Telegram",
          tabBarIcon: tabIcon("chatbubbles"),
          href: isAuthenticated ? "/parsed-picks" : null,
        }}
      />
      <Tabs.Screen
        name="canales"
        options={{
          title: "Canales",
          tabBarIcon: tabIcon("radio"),
          href: isAuthenticated ? "/canales" : null,
        }}
      />
    </Tabs>
  );
}
