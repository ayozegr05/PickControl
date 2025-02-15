import { Stack } from 'expo-router';
import { AuthProvider } from './context/AuthContext';

export default function Layout() {
  return (
    <AuthProvider>
      <Stack>
        <Stack.Screen 
          name="index" 
          options={{ 
            headerShown: false 
          }} 
        />
        <Stack.Screen 
          name="screens/login" 
          options={{ 
            headerShown: false 
          }} 
        />
        <Stack.Screen 
          name="screens/register" 
          options={{ 
            headerShown: false 
          }} 
        />
      </Stack>
    </AuthProvider>
  );
}
