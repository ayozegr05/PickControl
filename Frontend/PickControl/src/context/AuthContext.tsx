// Movido desde app/context/AuthContext.tsx (Fase 3: reorganizacion hacia
// src/ junto con la nueva capa de API). La logica es la misma, solo cambia
// la ubicacion del archivo.
import React, { createContext, useContext, useState, useEffect } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { router } from "expo-router";

import { setUnauthorizedHandler } from "@/src/api/client";
import { registerForPushNotifications } from "@/src/notifications/push";
import {
  getSecureItem,
  removeSecureItem,
  setSecureItem,
} from "@/src/storage/secure";

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

  const checkAuth = async () => {
    try {
      const token = await getSecureItem("userToken");
      const storedUserName = await AsyncStorage.getItem("userName");
      const storedRole = await AsyncStorage.getItem("userRole");
      setIsAuthenticated(!!token);
      setUserName(storedUserName);
      setIsAdmin(storedRole === "admin");
      if (token) {
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
      await AsyncStorage.setItem("userRole", role ?? "user");
      setIsAuthenticated(true);
      setUserName(name);
      setIsAdmin(role === "admin");
      void registerForPushNotifications();
    } catch (error) {
      console.error("Error storing token:", error);
    }
  };

  const logout = async () => {
    try {
      await removeSecureItem("userToken");
      await AsyncStorage.removeItem("userName");
      await AsyncStorage.removeItem("userRole");
      setIsAuthenticated(false);
      setUserName(null);
      setIsAdmin(false);
    } catch (error) {
      console.error("Error removing token:", error);
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
