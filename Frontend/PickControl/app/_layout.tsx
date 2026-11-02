import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider } from "@/src/context/AuthContext";

export default function Layout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="screens/login" />
          <Stack.Screen name="screens/register" />
          <Stack.Screen name="screens/analysis" />
          <Stack.Screen name="dynamic-routes/[informante]" />
        </Stack>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
