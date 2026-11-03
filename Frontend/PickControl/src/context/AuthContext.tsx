// Movido desde app/context/AuthContext.tsx (Fase 3: reorganizacion hacia
// src/ junto con la nueva capa de API). La logica es la misma, solo cambia
// la ubicacion del archivo.
import React, { createContext, useContext, useState, useEffect } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { router } from "expo-router";

import { setUnauthorizedHandler } from "@/src/api/client";
import {
  registerForPushNotifications,
  unregisterForPushNotifications,
} from "@/src/notifications/push";
import {
  getSecureItem,
  removeSecureItem,
  setSecureItem,
} from "@/src/storage/secure";
import { decodeJwtPayload } from "@/src/utils/jwt";

type AuthContextType = {
  isAuthenticated: boolean;
  isAdmin: boolean;
  userName: string | null;
  login: (token: string, userName: string, role?: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [userName, setUserName] = useState<string | null>(null);

  useEffect(() => {
    // Verificar si hay un token guardado al iniciar la app
    checkAuth();
  }, []);

  useEffect(() => {
    // 401 del backend (token caducado/inválido): cerrar sesión y llevar
    // al login. Sin esto el usuario quedaba "autenticado" pero con todas
    // las llamadas fallando (sesión zombie).
    setUnauthorizedHandler(() => {
      void logout();
      router.replace("/screens/login");
    });
    return () => setUnauthorizedHandler(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // El rol sale del JWT firmado por el backend, no de un "userRole"
  // en AsyncStorage editable a mano: la UI admin siempre coincide
  // con lo que el servidor autoriza (los endpoints dan 403 igual).
  const roleFromToken = (token: string | null): string | null => {
    if (!token) return null;
    const role = decodeJwtPayload(token)?.role;
    return typeof role === "string" ? role : null;
  };

  const checkAuth = async () => {
    try {
      const token = await getSecureItem("userToken");
      const storedUserName = await AsyncStorage.getItem("userName");
      const claims = token ? decodeJwtPayload(token) : null;
      const expired =
        typeof claims?.exp === "number" && claims.exp * 1000 < Date.now();
      const authenticated = !!token && !expired;
      setIsAuthenticated(authenticated);
      setUserName(authenticated ? storedUserName : null);
      setIsAdmin(roleFromToken(token) === "admin");
      if (authenticated) {
        // Registro push en segundo plano: best-effort, nunca bloquea.
        void registerForPushNotifications();
      }
    } catch (error) {
      console.error("Error checking auth:", error);
    }
  };

  const login = async (token: string, name: string, role?: string) => {
    try {
      await setSecureItem("userToken", token);
      await AsyncStorage.setItem("userName", name);
      setIsAuthenticated(true);
      setUserName(name);
      // Primero el claim firmado; si el token no decodifica (raro),
      // caemos al rol que pasó el caller como antes.
      setIsAdmin((roleFromToken(token) ?? role) === "admin");
      void registerForPushNotifications();
    } catch (error) {
      console.error("Error storing token:", error);
    }
  };

  // Guarda anti-recursión: si el unregister devuelve 401 (token ya
  // caducado), el handler global vuelve a llamar a logout() — sin
  // el flag sería un bucle infinito de peticiones.
  const loggingOut = React.useRef(false);

  const logout = async () => {
    if (loggingOut.current) return;
    loggingOut.current = true;
    try {
      // Best-effort: desactiva el token push en el backend mientras
      // el access token aún es válido; antes seguían llegando notis.
      await unregisterForPushNotifications();
      await removeSecureItem("userToken");
      await AsyncStorage.removeItem("userName");
      setIsAuthenticated(false);
      setUserName(null);
      setIsAdmin(false);
    } catch (error) {
      console.error("Error removing token:", error);
    } finally {
      loggingOut.current = false;
    }
  };

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, isAdmin, userName, login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export default AuthContext;

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
