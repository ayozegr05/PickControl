import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider } from "@/src/context/AuthContext";
import { AuthGuard } from "@/src/components/auth-guard";

export default function Layout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="light" />
        <AuthGuard>
          <Stack screenOptions={{ headerShown: false }}>
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="screens/login" />
            <Stack.Screen name="screens/register" />
            <Stack.Screen name="screens/analysis" />
            <Stack.Screen name="dynamic-routes/[informante]" />
          </Stack>
        </AuthGuard>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
