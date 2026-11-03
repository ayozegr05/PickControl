// Contrato del componente AuthGuard: cuando redirectTarget devuelve
// una ruta, monta <Redirect> EN LUGAR de los children (la pantalla
// protegida ni se renderiza).
import React from "react";
import { Text } from "react-native";
import { render } from "@testing-library/react-native";
import { AuthGuard } from "@/src/components/auth-guard";

let mockSegments: string[] = ["(tabs)", "my-picks"];
let mockAuth = { isAuthenticated: false, isInitializing: false };

jest.mock("expo-router", () => ({
  useSegments: () => mockSegments,
  Redirect: ({ href }: { href: string }) => {
    const { createElement } = require("react");
    const { Text: T } = require("react-native");
    return createElement(T, { testID: "redirect" }, `redirect:${href}`);
  },
}));

jest.mock("@/src/context/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

describe("AuthGuard", () => {
  it("renderiza los children cuando la ruta es válida", async () => {
    mockAuth = { isAuthenticated: true, isInitializing: false };
    mockSegments = ["(tabs)", "my-picks"];
    const screen = await render(
      <AuthGuard>
        <Text>Contenido privado</Text>
      </AuthGuard>
    );
    expect(screen.getByText("Contenido privado")).toBeTruthy();
    expect(screen.queryByTestId("redirect")).toBeNull();
  });

  it("monta Redirect y NO los children en ruta prohibida", async () => {
    mockAuth = { isAuthenticated: false, isInitializing: false };
    mockSegments = ["(tabs)", "my-picks"];
    const screen = await render(
      <AuthGuard>
        <Text>Contenido privado</Text>
      </AuthGuard>
    );
    expect(screen.getByTestId("redirect").props.children).toBe(
      "redirect:/"
    );
    expect(screen.queryByText("Contenido privado")).toBeNull();
  });
});
