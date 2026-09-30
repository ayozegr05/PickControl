// Registro del token Expo Push en el backend.
//
// Se llama tras autenticarse (AuthContext) y es totalmente best-effort:
// sin permisos, en emulador/web o si el backend no responde, la app
// sigue funcionando — solo no llegarán notificaciones.
import { Platform } from "react-native";

import Constants from "expo-constants";

import { registerDeviceToken } from "@/src/api/devices.api";

type NotificationsModule = typeof import("expo-notifications");

// Import dinámico: `expo-notifications` lanza al evaluarse en entornos
// sin push remoto (Expo Go desde SDK 53, web) — con import estático el
// módulo tumbaba la app entera al cargar AuthContext.
let notificationsPromise: Promise<NotificationsModule | null> | null = null;

function loadNotifications(): Promise<NotificationsModule | null> {
  if (Platform.OS === "web") return Promise.resolve(null);
  if (!notificationsPromise) {
    notificationsPromise = import("expo-notifications").then(
      (m) => {
        // Notificaciones recibidas con la app en primer plano: se
        // muestran igualmente (el aviso de "pick nuevo" interesa aunque
        // estés dentro). En Expo Go esta llamada también lanza.
        try {
          m.setNotificationHandler({
            handleNotification: async () => ({
              shouldPlaySound: false,
              shouldSetBadge: false,
              shouldShowBanner: true,
              shouldShowList: true,
            }),
          });
          return m;
        } catch {
          return null;
        }
      },
      () => null,
    );
  }
  return notificationsPromise;
}

async function configureAndroidChannel(
  Notifications: NotificationsModule,
): Promise<void> {
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
    const Notifications = await loadNotifications();
    if (!Notifications) return; // web / Expo Go / entorno sin push

    await configureAndroidChannel(Notifications);

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
