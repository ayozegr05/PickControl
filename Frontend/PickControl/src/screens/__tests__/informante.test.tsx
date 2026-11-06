// Smoke tests de la pantalla [informante] (Fase C): fijan el contrato
// visible actual — comparativa tipster/tú, tabla de picks extraídos,
// modal de detalle y "Yo también la jugué" — antes de virtualizar y
// partir el componente.
import React from "react";
import { render, waitFor, fireEvent } from "@testing-library/react-native";
import InformantDetail from "@/app/informante/[informante]";
import { getInformanteStats } from "@/src/api/informantes.api";
import { getPickOdds } from "@/src/api/parsed-picks.api";
import { InformanteStats } from "@/src/types/informante.types";
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
    useLocalSearchParams: () => ({ informante: "Canal A" }),
    Redirect: () => null,
  };
});

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
  SafeAreaView: ({ children }: { children: React.ReactNode }) => children,
}));

jest.mock("@/src/context/AuthContext", () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isAdmin: false,
    userName: "t",
    logout: jest.fn(),
  }),
}));

jest.mock("@/src/api/informantes.api", () => ({
  listInformantes: jest.fn(),
  getInformanteStats: jest.fn(),
}));
jest.mock("@/src/api/parsed-picks.api", () => ({
  updateParsedPickAcierto: jest.fn(),
  jugarParsedPick: jest.fn(),
  getPickOdds: jest.fn(),
}));
jest.mock("@/src/api/picks.api", () => ({
  listPicks: jest.fn().mockResolvedValue([]),
  updatePick: jest.fn(),
  deletePick: jest.fn(),
}));
jest.mock("@react-native-community/datetimepicker", () => {
  const { View } = require("react-native");
  return { __esModule: true, default: View };
});

const mockGetStats = getInformanteStats as jest.Mock;
const mockGetPickOdds = getPickOdds as jest.Mock;

const makePick = (over: Partial<PickItem> = {}): PickItem => ({
  id: 1,
  apuesta: "Over 2.5 goles",
  informante: "Canal A",
  tipoDeApuesta: "simple",
  acierto: "Pending",
  casa: "bet365",
  cantidadApostada: null,
  cuota: 1.9,
  fecha: "2025-01-10T20:00:00.000Z",
  source: "telegram",
  evento: "Madrid vs Barça",
  ...over,
});

const makeStats = (over: Partial<InformanteStats> = {}): InformanteStats => ({
  informante: "Canal A",
  totalApuestas: 3,
  totalAciertos: 2,
  ganancias: 5.5,
  porcentajeAciertos: 66.67,
  yieldPct: 12.5,
  apuestas: [],
  parsedPicks: [
    makePick({ id: 1 }),
    makePick({ id: 2, apuesta: "Gana Nadal" }),
  ],
  parsedTotalApuestas: 10,
  parsedTotalAciertos: 7,
  parsedGanancias: 8.2,
  parsedPorcentajeAciertos: 70,
  parsedYieldPct: 15.3,
  combinadasTotal: 0,
  combinadasAciertos: 0,
  combinadasGanancias: 0,
  combinadasPorcentaje: 0,
  combinadasYieldPct: 0,
  combinadasPendientes: 0,
  ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockGetStats.mockResolvedValue(makeStats());
  mockGetPickOdds.mockResolvedValue(null);
});

describe("[informante] screen", () => {
  it("pide las stats del canal de la ruta y muestra la comparativa", async () => {
    const screen = await render(<InformantDetail />);

    await waitFor(() => expect(mockGetStats).toHaveBeenCalledWith("Canal A"));
    expect(screen.getByText("Canal A")).toBeTruthy();
    expect(screen.getByText("Tipster vs. tú")).toBeTruthy();
    expect(screen.getByText("El tipster")).toBeTruthy();
    expect(screen.getByText("Tú")).toBeTruthy();
    expect(screen.getByText("Yield")).toBeTruthy();
    expect(screen.getAllByText("15.30%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Pendientes")).toBeTruthy();
    // "Ganancia" aparece en la comparativa y en la cabecera de tabla.
    expect(screen.getAllByText("Ganancia").length).toBeGreaterThanOrEqual(1);
  });

  it("renderiza la tabla de picks extraídos con cabeceras y filas", async () => {
    const screen = await render(<InformantDetail />);

    await waitFor(() =>
      expect(screen.getByText("Over 2.5 goles")).toBeTruthy()
    );
    // Cabeceras de la tabla ("Ganancia" también es label de la
    // comparativa, por eso getAllByText).
    for (const h of [
      "Apuesta",
      "Acierto",
      "Fecha",
      "Mercado",
      "Cuota",
      "Stake",
      "Ganancia",
    ]) {
      expect(screen.getAllByText(h).length).toBeGreaterThanOrEqual(1);
    }
    expect(screen.getByText("Gana Nadal")).toBeTruthy();
    expect(screen.getAllByText("Madrid vs Barça")).toHaveLength(2);
  });

  it("pulsar una fila abre el modal de detalle con 'Yo también la jugué'", async () => {
    const screen = await render(<InformantDetail />);
    await waitFor(() =>
      expect(screen.getByText("Over 2.5 goles")).toBeTruthy()
    );

    await fireEvent.press(screen.getAllByText("Over 2.5 goles")[0]);

    await waitFor(() =>
      expect(screen.getByText("Yo también la jugué")).toBeTruthy()
    );
    // Al abrir el detalle pide la auditoría de cuotas del pick.
    await waitFor(() => expect(mockGetPickOdds).toHaveBeenCalledWith(1));
  });

  it("'Yo también la jugué' abre el formulario de registro", async () => {
    const screen = await render(<InformantDetail />);
    await waitFor(() =>
      expect(screen.getByText("Over 2.5 goles")).toBeTruthy()
    );
    await fireEvent.press(screen.getAllByText("Over 2.5 goles")[0]);
    await waitFor(() =>
      expect(screen.getByText("Yo también la jugué")).toBeTruthy()
    );

    await fireEvent.press(screen.getByText("Yo también la jugué"));

    // El formulario pide cantidad (y quedan el botón del detalle + el
    // título del modal como dos "Yo también la jugué").
    await waitFor(() =>
      expect(
        screen.getByPlaceholderText("Cantidad apostada (obligatorio)")
      ).toBeTruthy()
    );
    await waitFor(() =>
      expect(
        screen.getByPlaceholderText("Cuota conseguida (opcional)")
      ).toBeTruthy()
    );
  });

  it("canal sin picks muestra el mensaje de vacío", async () => {
    mockGetStats.mockResolvedValue(makeStats({ parsedPicks: [] }));

    const screen = await render(<InformantDetail />);

    await waitFor(() =>
      expect(
        screen.getByText("Todavía no hay picks extraídos de este canal.")
      ).toBeTruthy()
    );
  });

  it("error de red muestra ErrorState, no 'No se encontraron datos'", async () => {
    mockGetStats.mockRejectedValue(new Error("boom"));

    const screen = await render(<InformantDetail />);

    await waitFor(() =>
      expect(screen.getByText("Error al cargar los datos")).toBeTruthy()
    );
    // El mensaje de "sin datos" solo es para el canal realmente vacío.
    expect(
      screen.queryByText("No se encontraron datos para este informante.")
    ).toBeNull();

    mockGetStats.mockResolvedValue(makeStats());
    await fireEvent.press(screen.getByLabelText("Reintentar"));
    await waitFor(() =>
      expect(screen.getByText("Tipster vs. tú")).toBeTruthy()
    );
  });
});
