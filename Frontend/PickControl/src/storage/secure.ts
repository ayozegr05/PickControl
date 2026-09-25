// Almacenamiento seguro para secretos (credenciales biométricas, token
// de sesión). En nativo usa expo-secure-store (Keychain en iOS,
// Keystore en Android — cifrado por el SO); en web no existe SecureStore
// y cae a AsyncStorage, que es lo único disponible ahí.
//
// Reemplaza al AsyncStorage en texto plano que usaban login.tsx,
// AuthContext y client.ts. `getSecureItem` migra en caliente los valores
// heredados: si la clave existe en AsyncStorage pero no en SecureStore,
// la copia y borra la copia insegura — no hace falta pasada manual.

import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

// `available` se evalúa en caliente: el módulo nativo falta en web,
// Expo Go y en dev-clients construidos antes de añadir la dependencia
// — ahí cae a AsyncStorage en vez de romper el login. Tras rebuildar
// el dev-client/APK con el plugin, SecureStore pasa a responder solo.
const isNative = Platform.OS === "ios" || Platform.OS === "android";

async function secureAvailable(): Promise<boolean> {
  if (!isNative) return false;
  try {
    return await SecureStore.isAvailableAsync();
  } catch {
    return false;
  }
}

export async function getSecureItem(key: string): Promise<string | null> {
  if (!(await secureAvailable())) {
    return AsyncStorage.getItem(key);
  }
  try {
    const value = await SecureStore.getItemAsync(key);
    if (value !== null) {
      return value;
    }
    // Migración en caliente desde el AsyncStorage en texto plano.
    const legacy = await AsyncStorage.getItem(key);
    if (legacy !== null) {
      await SecureStore.setItemAsync(key, legacy);
      await AsyncStorage.removeItem(key);
    }
    return legacy;
  } catch {
    return AsyncStorage.getItem(key);
  }
}

export async function setSecureItem(key: string, value: string): Promise<void> {
  if (await secureAvailable()) {
    try {
      return await SecureStore.setItemAsync(key, value);
    } catch {
      // cae al fallback de abajo
    }
  }
  return AsyncStorage.setItem(key, value);
}

export async function removeSecureItem(key: string): Promise<void> {
  if (await secureAvailable()) {
    try {
      await SecureStore.deleteItemAsync(key);
    } catch {
      // no impide limpiar la copia legacy
    }
  }
  await AsyncStorage.removeItem(key);
}
