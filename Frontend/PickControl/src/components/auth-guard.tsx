// Guard centralizado: envuelve el Stack en el root _layout y decide
// por useSegments + estado de auth si la ruta actual es alcanzable.
// La decisión vive en src/utils/auth-guard.ts (pura y testeada).
import React from "react";
import { Redirect, useSegments, type Href } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { redirectTarget } from "@/src/utils/auth-guard";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const segments = useSegments();
  const { isAuthenticated, isInitializing } = useAuth();
  const target = redirectTarget(segments, {
    isAuthenticated,
    isInitializing,
  });

  // Montamos Redirect EN LUGAR de los children: la pantalla protegida
  // ni siquiera se renderiza (cero flash de contenido privado).
  if (target) return <Redirect href={target as Href} />;
  return <>{children}</>;
}
