// Contrato del AuthContext:
// 1. isInitializing=true mientras checkAuth resuelve (evita el frame de
//    landing en cold start de un usuario logueado).
// 2. login() PROPAGA el error de storage — si el token no se persistió,
//    el caller debe enterarse (alerta) en vez de un botón mudo.
// 3. logout() limpia el estado en memoria SIEMPRE, aunque el storage
//    falle (si no, la UI quedaría "logueada" con sesión zombie).
import React from "react";
import { Text } from "react-native";
import { act, render, waitFor } from "@testing-library/react-native";
import { AuthProvider, useAuth } from "@/src/context/AuthContext";
import {
  getSecureItem,
  removeSecureItem,
  setSecureItem,
} from "@/src/storage/secure";

jest.mock("expo-router", () => ({ router: { replace: jest.fn() } }));
jest.mock("@/src/storage/secure", () => ({
  getSecureItem: jest.fn(),
  setSecureItem: jest.fn(),
  removeSecureItem: jest.fn(),
}));
jest.mock("@/src/notifications/push", () => ({
  registerForPushNotifications: jest.fn(),
  unregisterForPushNotifications: jest.fn(),
}));
jest.mock("@/src/api/client", () => ({ setUnauthorizedHandler: jest.fn() }));

const mockGet = getSecureItem as jest.Mock;
const mockSet = setSecureItem as jest.Mock;
const mockRemove = removeSecureItem as jest.Mock;

// Probe: expone el contexto como texto testeable.
function Probe() {
  const { isAuthenticated, isInitializing, login, logout } = useAuth();
  return (
    <>
      <Text testID="init">{String(isInitializing)}</Text>
      <Text testID="auth">{String(isAuthenticated)}</Text>
      <Text testID="login" onPress={() => login("tok", "u")}>
        doLogin
      </Text>
      <Text testID="logout" onPress={() => logout()}>
        doLogout
      </Text>
    </>
  );
}

// JWT válido con role+exp lejano (firma irrelevante: el decoder no la verifica).
const VALID_JWT =
  "eyJhbGciOiJIUzI1NiJ9." +
  "eyJyb2xlIjoidXNlciIsImV4cCI6OTk5OTk5OTk5OX0" +
  ".firma";

describe("AuthContext", () => {
  beforeEach(() => jest.clearAllMocks());

  it("isInitializing=true durante checkAuth y false al resolver", async () => {
    let resolve: (v: string | null) => void = () => {};
    mockGet.mockImplementation(
      () => new Promise((r) => (resolve = r))
    );
    const screen = await render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    expect(screen.getByTestId("init").props.children).toBe("true");
    await act(async () => resolve(VALID_JWT));
    await waitFor(() =>
      expect(screen.getByTestId("init").props.children).toBe("false")
    );
    expect(screen.getByTestId("auth").props.children).toBe("true");
  });

  it("login() propaga el fallo de storage al caller", async () => {
    mockGet.mockResolvedValue(null);
    mockSet.mockRejectedValue(new Error("keystore caído"));
    const screen = await render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() =>
      expect(screen.getByTestId("init").props.children).toBe("false")
    );
    await expect(
      act(async () => {
        await screen.getByTestId("login").props.onPress();
      })
    ).rejects.toThrow("keystore caído");
    // Y sin persistencia no finge sesión autenticada.
    expect(screen.getByTestId("auth").props.children).toBe("false");
  });

  it("logout() limpia el estado en memoria aunque el storage falle", async () => {
    mockGet.mockResolvedValue(VALID_JWT);
    mockRemove.mockRejectedValue(new Error("keystore caído"));
    const screen = await render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() =>
      expect(screen.getByTestId("auth").props.children).toBe("true")
    );
    await act(async () => {
      await screen.getByTestId("logout").props.onPress();
    });
    expect(screen.getByTestId("auth").props.children).toBe("false");
  });
});
