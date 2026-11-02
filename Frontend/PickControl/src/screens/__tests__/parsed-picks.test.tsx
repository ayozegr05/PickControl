// Smoke tests de la pantalla parsed-picks (Fase C): fijan el contrato
// visible actual — overview por canal, chips de selección, paginación
// "Cargar más", toggles y panel admin — antes de migrar las listas a
// FlatList. Tras el refactor estos tests deben seguir pasando igual.
import React from "react";
import { render, waitFor, fireEvent } from "@testing-library/react-native";
import ParsedPicksScreen from "@/app/(tabs)/parsed-picks";
import {
  getParsedPicks,
  ParsedPick,
} from "@/src/api/parsed-picks.api";
import { listInformantes } from "@/src/api/informantes.api";
import {
  getProvidersStatus,
  getPicksStatus,
} from "@/src/api/system.api";

const mockPush = jest.fn();
jest.mock("expo-router", () => {
  const React = require("react");
  return {
    useRouter: () => ({
      push: mockPush,
      replace: jest.fn(),
      back: jest.fn(),
    }),
    // En tests no hay foco de navegación: ejecutamos el callback una
    // vez al montar, igual que hace el primer foco real.
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

const mockAuth: { isAdmin: boolean; isAuthenticated: boolean } = {
  isAdmin: false,
  isAuthenticated: true,
};
jest.mock("@/src/context/AuthContext", () => ({ useAuth: () => mockAuth }));

jest.mock("@/src/api/parsed-picks.api", () => ({
  getParsedPicks: jest.fn(),
  updateParsedPickAcierto: jest.fn(),
  anularResiduoPendiente: jest.fn(),
}));
jest.mock("@/src/api/informantes.api", () => ({
  listInformantes: jest.fn(),
  getInformanteStats: jest.fn(),
}));
jest.mock("@/src/api/system.api", () => ({
  getProvidersStatus: jest.fn(),
  getPicksStatus: jest.fn(),
}));
jest.mock("@/src/api/picks.api", () => ({
  listPicks: jest.fn().mockResolvedValue([]),
  updatePick: jest.fn(),
  deletePick: jest.fn(),
}));

const mockGetParsedPicks = getParsedPicks as jest.Mock;
const mockListInformantes = listInformantes as jest.Mock;
const mockProviders = getProvidersStatus as jest.Mock;
const mockPicksStatus = getPicksStatus as jest.Mock;

const makePick = (over: Partial<ParsedPick> = {}): ParsedPick => ({
  id: 1,
  es_apuesta: true,
  apuesta: "Over 2.5 goles",
  deporte: "futbol",
  evento: "Madrid vs Barça",
  mercado: "goles",
  seleccion: "más de 2.5",
  cuota: 1.9,
  stake: 2,
  casa: "bet365",
  informante: "Canal A | FREE",
  explicacion: null,
  metodo: "llm",
  confianza: 0.9,
  fecha_evento: "2025-01-10T20:00:00.000Z",
  linea: 2.5,
  acierto: null,
  anulada: false,
  motivo_anulada: null,
  verificado_por: null,
  informante_id: 7,
  raw_message_id: 1,
  created_at: "2025-01-09T10:00:00.000Z",
  es_reto: false,
  es_combinada: false,
  combinada_id: null,
  orden: null,
  cuota_efectiva: null,
  patas: [],
  fuera_ventana: false,
  ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockAuth.isAdmin = false;
  mockGetParsedPicks.mockResolvedValue([]);
  mockListInformantes.mockResolvedValue([
    { informante: "Canal A | FREE" },
    { informante: "Canal B" },
  ]);
  mockProviders.mockResolvedValue(null);
  mockPicksStatus.mockResolvedValue(null);
});

describe("parsed-picks screen — overview", () => {
  it("renderiza título, toggles, chips de canal y cards del overview", async () => {
    mockGetParsedPicks.mockResolvedValue([
      makePick({ id: 1, apuesta: "Over 2.5 goles" }),
      makePick({ id: 2, apuesta: "Gana Nadal", informante: "Canal B" }),
    ]);

    const screen = await render(<ParsedPicksScreen />);

    expect(screen.getByText("Picks extraídos de Telegram")).toBeTruthy();
    expect(
      screen.getByText(/Ver todos \(incluye mensajes descartados/)
    ).toBeTruthy();
    expect(screen.getByText("Solo pendientes de liquidar")).toBeTruthy();

    // Chips: "Todos" + cada canal con informante_id.
    expect(screen.getByText("Todos")).toBeTruthy();
    // "Canal A FREE" aparece como chip y como título de sección.
    expect(screen.getAllByText("Canal A FREE").length).toBeGreaterThanOrEqual(2);

    // Cards del overview: una por pick.
    expect(screen.getByText("Over 2.5 goles")).toBeTruthy();
    expect(screen.getByText("Gana Nadal")).toBeTruthy();
    // "Ver todos →" por canal con picks.
    expect(screen.getAllByText("Ver todos →")).toHaveLength(2);
  });

  it("un canal sin picks en el filtro muestra su chip pero sin card", async () => {
    mockGetParsedPicks.mockResolvedValue([makePick()]);

    const screen = await render(<ParsedPicksScreen />);

    await waitFor(() =>
      expect(screen.getByText("Over 2.5 goles")).toBeTruthy()
    );
    // "Canal B" existe como título de sección (de listInformantes)…
    expect(screen.getAllByText("Canal B")).toHaveLength(1);
    // …pero su chip NO se renderiza (informanteId null).
    expect(screen.getByText("Sin picks extraídos con este filtro.")).toBeTruthy();
  });
});

describe("parsed-picks screen — vista de canal", () => {
  it("pulsar un chip carga la página 0 del canal y muestra el contador", async () => {
    mockGetParsedPicks.mockResolvedValue([makePick()]);

    const screen = await render(<ParsedPicksScreen />);
    await waitFor(() => expect(screen.getAllByText("Canal A FREE").length).toBeGreaterThan(0));

    const chip = screen.getAllByText("Canal A FREE")[0];
    await fireEvent.press(chip);

    await waitFor(() =>
      expect(mockGetParsedPicks).toHaveBeenCalledWith(
        expect.objectContaining({ informanteId: 7, offset: 0, limit: 20 })
      )
    );
    await waitFor(() =>
      expect(screen.getByText(/picks? de Canal A FREE/)).toBeTruthy()
    );
  });

  it("página llena (20) muestra 'Cargar más' y pide la siguiente página", async () => {
    const page0 = Array.from({ length: 20 }, (_, i) =>
      makePick({ id: 100 + i, apuesta: `Pick ${i}` })
    );
    mockGetParsedPicks.mockImplementation((q: { informanteId?: number }) =>
      Promise.resolve(q.informanteId ? page0 : [makePick()])
    );

    const screen = await render(<ParsedPicksScreen />);
    await waitFor(() =>
      expect(screen.getAllByText("Canal A FREE").length).toBeGreaterThan(0)
    );
    await fireEvent.press(screen.getAllByText("Canal A FREE")[0]);

    await waitFor(() => expect(screen.getByText("Cargar más")).toBeTruthy());
    await fireEvent.press(screen.getByText("Cargar más"));

    await waitFor(() =>
      expect(mockGetParsedPicks).toHaveBeenCalledWith(
        expect.objectContaining({ informanteId: 7, offset: 20 })
      )
    );
  });
});

describe("parsed-picks screen — panel admin", () => {
  it("isAdmin=true muestra 'Sistema · providers' y la card de duplicadas", async () => {
    mockAuth.isAdmin = true;
    mockGetParsedPicks.mockImplementation(
      (q: { soloDuplicados?: boolean }) =>
        Promise.resolve(
          q.soloDuplicados ? [makePick({ id: 9, anulada: true })] : [makePick()]
        )
    );
    mockProviders.mockResolvedValue({
      rate_limited: {},
      missed_by_provider: {},
      calls_today: { espn: 3 },
      daily_limits: {},
      resolved_by_provider: { espn: 2 },
    });

    const screen = await render(<ParsedPicksScreen />);

    await waitFor(() =>
      expect(screen.getByText("Sistema · providers")).toBeTruthy()
    );
    expect(screen.getByText(/Duplicadas detectadas · 1/)).toBeTruthy();
  });

  it("isAdmin=false no pide status ni duplicadas", async () => {
    await render(<ParsedPicksScreen />);

    await waitFor(() => expect(mockGetParsedPicks).toHaveBeenCalled());
    expect(mockProviders).not.toHaveBeenCalled();
    expect(mockPicksStatus).not.toHaveBeenCalled();
    expect(
      mockGetParsedPicks.mock.calls.every(
        (c) => !(c[0] && c[0].soloDuplicados)
      )
    ).toBe(true);
  });
});

describe("parsed-picks screen — toggles", () => {
  it("'Solo pendientes' refetchea el overview con soloPendientes:true", async () => {
    mockGetParsedPicks.mockResolvedValue([makePick()]);
    const screen = await render(<ParsedPicksScreen />);
    await waitFor(() =>
      expect(screen.getByText("Over 2.5 goles")).toBeTruthy()
    );

    await fireEvent.press(screen.getByText("Solo pendientes de liquidar"));

    await waitFor(() =>
      expect(mockGetParsedPicks).toHaveBeenCalledWith(
        expect.objectContaining({ perChannel: 2, soloPendientes: true })
      )
    );
  });
});
