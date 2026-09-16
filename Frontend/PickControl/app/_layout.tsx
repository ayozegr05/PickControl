import { Stack } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider } from "@/src/context/AuthContext";

export default function Layout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="index" />
          <Stack.Screen name="screens/login" />
          <Stack.Screen name="screens/register" />
          <Stack.Screen name="screens/add-pick" />
          <Stack.Screen name="screens/parsed-picks" />
          <Stack.Screen name="screens/my-picks" />
          <Stack.Screen name="dynamic-routes/[informante]" />
        </Stack>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
