// Contrato de earns ante fallo de red: ErrorState + Reintentar, y
// NUNCA un 0.00€ presentado como ganancia real.
//
// Vive en su propio archivo: en el mismo suite que earns.test.tsx,
// el render quedaba contaminado por el timer de la animación de
// ganancias del test anterior (el segundo render montaba un árbol
// vacío en este entorno Jest/RN — ver nota en earns.test.tsx).
import React from "react";
import { render, waitFor } from "@testing-library/react-native";
import GananciasPage from "@/app/(tabs)/earns";

const mockReload = jest.fn();
jest.mock("@/src/hooks/usePicks", () => ({
  usePicks: () => ({
    apuestas: [],
    loading: false,
    error: "Sin conexión",
    reload: mockReload,
  }),
}));

jest.mock("expo-router", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
  useFocusEffect: jest.fn(),
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
}));

jest.mock("lottie-react-native", () => () => null);
jest.mock("@/src/components/top-bar", () => () => null);

describe("earns screen — estado de error", () => {
  it("muestra ErrorState en vez del 0.00€ falso cuando la API falla", async () => {
    const screen = await render(<GananciasPage />);

    await waitFor(() =>
      expect(screen.getByText("Error al cargar los datos")).toBeTruthy()
    );
    expect(screen.getByLabelText("Reintentar")).toBeTruthy();
    // El contrato del fix: nunca 0.00€ presentado como ganancia real.
    expect(screen.queryByText(/0\.00/)).toBeNull();
  });
});
