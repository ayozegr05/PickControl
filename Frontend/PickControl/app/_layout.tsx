import { Stack } from 'expo-router';
import { AuthProvider } from './context/AuthContext';

export default function Layout() {
  return (
    <AuthProvider>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="screens/login" />
        <Stack.Screen name="screens/register" />
        <Stack.Screen name="dynamic-routes/informante" />
        <Stack.Screen name="screens/add-pick" />
      </Stack>
    </AuthProvider>
  );
}
