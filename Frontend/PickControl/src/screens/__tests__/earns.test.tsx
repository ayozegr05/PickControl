// Contrato de earns: tras el fix de "nunca refresca", la pantalla
// refetchea al volver a la pestaña (pero no en el foco inicial —
// la carga de montaje ya la hace usePicks).
import React from "react";
import { render, act } from "@testing-library/react-native";
import GananciasPage from "@/app/(tabs)/earns";

const mockReload = jest.fn();
jest.mock("@/src/hooks/usePicks", () => ({
  usePicks: () => ({
    apuestas: [],
    loading: false,
    error: null,
    reload: mockReload,
  }),
}));

// Capturamos el callback de foco para simular salir/volver a la tab.
let focusCb: () => void = () => {};
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
  useFocusEffect: (cb: () => void) => {
    focusCb = cb;
  },
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
}));

jest.mock("lottie-react-native", () => () => null);
jest.mock("@/src/components/top-bar", () => () => null);

describe("earns screen", () => {
  beforeEach(() => {
    mockReload.mockClear();
    focusCb = () => {};
  });

  it("no refetchea en el primer foco (montaje) pero sí al volver", async () => {
    await render(<GananciasPage />);
    act(() => focusCb()); // primer foco tras montar: sin refetch
    expect(mockReload).not.toHaveBeenCalled();
    act(() => focusCb()); // volver a la pestaña: refetch
    expect(mockReload).toHaveBeenCalledTimes(1);
    act(() => focusCb());
    expect(mockReload).toHaveBeenCalledTimes(2);
  });
});
