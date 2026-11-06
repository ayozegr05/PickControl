// Contrato a11y del TopBar: el botón de cuenta (solo icono) debe
// exponer accessibilityLabel para lectores de pantalla.
import React from "react";
import { render } from "@testing-library/react-native";
import TopBar from "../top-bar";

jest.mock("expo-router", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

const mockAuth = {
  isAuthenticated: true,
  isInitializing: false,
  isAdmin: false,
  userName: "Tester",
  logout: jest.fn(),
};
jest.mock("@/src/context/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

jest.mock("@/src/api/picks.api", () => ({
  listPicks: jest.fn().mockResolvedValue([]),
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

describe("TopBar a11y", () => {
  it("el botón de cuenta tiene accessibilityLabel", async () => {
    const screen = await render(<TopBar />);
    expect(screen.getByLabelText("Menú de cuenta")).toBeTruthy();
  });
});
