// Smoke tests de la pantalla my-picks (Fase C): fijan el contrato
// visible actual — tabla agrupada por informante, lista de pendientes
// con modal de resultado, ranking y filtros de periodo — antes de
// migrar la tabla a FlatList/virtualización.
import React from "react";
import { render, waitFor, fireEvent } from "@testing-library/react-native";
import MyPicksScreen from "@/app/(tabs)/my-picks";
import { listPicks, updatePick } from "@/src/api/picks.api";
import { PickItem } from "@/src/types/pick.types";

const mockPush = jest.fn();
jest.mock("expo-router", () => {
  const React = require("react");
  return {
    useRouter: () => ({
      push: mockPush,
      replace: jest.fn(),
      back: jest.fn(),
    }),
    useFocusEffect: (cb: () => void) => {
      const ran = React.useRef(false);
      React.useEffect(() => {
        if (!ran.current) {
          ran.current = true;
          cb();
        }
      }, [cb]);
    },
    useLocalSearchParams: () => ({}),
    Redirect: () => null,
  };
});

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
  SafeAreaView: ({ children }: { children: React.ReactNode }) => children,
}));

const mockAuth = { isAuthenticated: true, isAdmin: false, userName: "t" };
jest.mock("@/src/context/AuthContext", () => ({ useAuth: () => mockAuth }));

jest.mock("@/src/api/picks.api", () => ({
  listPicks: jest.fn(),
  updatePick: jest.fn(),
  deletePick: jest.fn(),
}));

const mockListPicks = listPicks as jest.Mock;
const mockUpdatePick = updatePick as jest.Mock;

const makePick = (over: Partial<PickItem> = {}): PickItem => ({
  id: 1,
  apuesta: "Over 2.5",
  informante: "Tipster A",
  tipoDeApuesta: "simple",
  acierto: "Pending",
  casa: "bet365",
  cantidadApostada: 10,
  cuota: 2.0,
  fecha: "2025-01-10T20:00:00.000Z",
  source: "manual",
  ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockAuth.isAuthenticated = true;
  mockListPicks.mockResolvedValue([]);
});

describe("my-picks screen", () => {
  it("renderiza la tabla por informante con sus filas de resultados", async () => {
    mockListPicks.mockResolvedValue([
      makePick({ id: 1, acierto: "True" }),
      makePick({ id: 2, acierto: "False", informante: "Tipster B" }),
      makePick({ id: 3, acierto: "True", informante: "Tipster B" }),
    ]);

    const screen = await render(<MyPicksScreen />);

    expect(screen.getByText("Mis Apuestas")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Tipster A")).toBeTruthy());
    // Columnas de informantes en la cabecera de la tabla.
    expect(screen.getByText("Tipster B")).toBeTruthy();
    // Cabecera de fecha + iconos de resultado por fila.
    expect(screen.getByText("Fecha")).toBeTruthy();
    // 2 aciertos ✅ (Tipster A y el True de B) y 1 fallo ❌.
    expect(screen.getAllByText(/✅/)).toHaveLength(2);
    expect(screen.getAllByText(/❌/)).toHaveLength(1);
  });

  it("muestra 'Apuestas Pendientes' con todas las Pending (cuota o no)", async () => {
    mockListPicks.mockResolvedValue([
      makePick({ id: 1, apuesta: "Pendiente con datos" }),
      makePick({ id: 2, acierto: "True" }),
      makePick({ id: 3, apuesta: "Sin cuota", cuota: null }),
    ]);

    const screen = await render(<MyPicksScreen />);

    await waitFor(() =>
      expect(screen.getByText("Apuestas Pendientes")).toBeTruthy()
    );
    // pendientesDe cuenta TODA Pending — sin exigir cuota/stake.
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("Pendiente con datos")).toBeTruthy();
    expect(screen.getByText("Sin cuota")).toBeTruthy();
    // Y cada pendiente lleva su ❓ para liquidarla.
    expect(screen.getAllByText("❓")).toHaveLength(2);
  });

  it("el ❓ abre el modal y ✔️ liquida la apuesta vía updatePick", async () => {
    mockListPicks.mockResolvedValue([
      makePick({ id: 42, apuesta: "Mi pick" }),
    ]);
    mockUpdatePick.mockResolvedValue(makePick({ id: 42, acierto: "True" }));

    const screen = await render(<MyPicksScreen />);
    await waitFor(() => expect(screen.getByText("Mi pick")).toBeTruthy());

    await fireEvent.press(screen.getByText("❓"));
    await waitFor(() =>
      expect(
        screen.getByText("¿Cuál es el resultado de la apuesta?")
      ).toBeTruthy()
    );

    await fireEvent.press(screen.getByText("✔️"));
    await waitFor(() =>
      expect(mockUpdatePick).toHaveBeenCalledWith(42, { acierto: "True" })
    );
    // Tras liquidar refetchea la lista.
    await waitFor(() =>
      expect(mockListPicks.mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it("muestra el ranking top-5 por % de aciertos", async () => {
    mockListPicks.mockResolvedValue([
      makePick({ id: 1, acierto: "True" }),
      makePick({ id: 2, acierto: "True" }),
      makePick({ id: 3, acierto: "False", informante: "Tipster B" }),
    ]);

    const screen = await render(<MyPicksScreen />);

    await waitFor(() =>
      expect(screen.getByText("Ranking (mis apuestas)")).toBeTruthy()
    );
    expect(screen.getByText("1. Tipster A")).toBeTruthy();
    expect(screen.getByText("2. Tipster B")).toBeTruthy();
  });

  it("sin sesión no pide apuestas (el Redirect lo hace el AuthGuard)", async () => {
    mockAuth.isAuthenticated = false;

    await render(<MyPicksScreen />);

    // La redirección la hace el guard centralizado del root layout;
    // el contrato de ESTA pantalla es no llamar a la API sin sesión.
    expect(mockListPicks).not.toHaveBeenCalled();
  });

  it("muestra ErrorState con Reintentar cuando la API falla", async () => {
    mockListPicks.mockRejectedValue(new Error("boom"));

    const screen = await render(<MyPicksScreen />);

    await waitFor(() =>
      expect(screen.getByText("Error al cargar los datos")).toBeTruthy()
    );
    // No finge una tabla vacía: el error es explícito.
    expect(screen.queryByText("Mis Apuestas")).toBeNull();

    mockListPicks.mockResolvedValue([]);
    await fireEvent.press(screen.getByLabelText("Reintentar"));
    await waitFor(() =>
      expect(screen.getByText("Mis Apuestas")).toBeTruthy()
    );
  });
});
