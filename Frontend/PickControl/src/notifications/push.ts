// Registro del token Expo Push en el backend.
//
// Se llama tras autenticarse (AuthContext) y es totalmente best-effort:
// sin permisos, en emulador/web o si el backend no responde, la app
// sigue funcionando — solo no llegarán notificaciones.
import { Platform } from "react-native";

import Constants from "expo-constants";
import * as Notifications from "expo-notifications";

import { registerDeviceToken } from "@/src/api/devices.api";

// Notificaciones recibidas con la app en primer plano: se muestran
// igualmente (el aviso de "pick nuevo" interesa aunque estés dentro).
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: false,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

async function configureAndroidChannel(): Promise<void> {
  // Android 8+ exige un canal para mostrar notificaciones.
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "Picks",
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }
}

export async function registerForPushNotifications(): Promise<void> {
  try {
    // Web/emulador no tienen tokens push reales.
    if (Platform.OS === "web") return;

    await configureAndroidChannel();

    const { status: current } = await Notifications.getPermissionsAsync();
    const status =
      current === "granted"
        ? current
        : (await Notifications.requestPermissionsAsync()).status;
    if (status !== "granted") return; // permiso denegado: no insistimos

    const projectId = Constants.expoConfig?.extra?.eas?.projectId as
      | string
      | undefined;
    const { data: token } = await Notifications.getExpoPushTokenAsync({
      projectId,
    });
    await registerDeviceToken(token, Platform.OS);
  } catch (error) {
    // Entornos sin soporte (Expo Go, emulador) o backend caído.
    console.warn("Registro de notificaciones omitido:", error);
  }
}
