// Contrato de useParsedPicks (Fase B): la máquina de estado de la
// pantalla parsed-picks — overview top-N por canal + paginación por
// canal + filtros showAll/soloPendientes que van al backend.
// El hook NO auto-fetchea: la pantalla dispara reload() desde su
// useFocusEffect (como hace hoy), así no hay doble fetch al montar.
import { renderHook, act } from "@testing-library/react-native";
import { getParsedPicks } from "@/src/api/parsed-picks.api";
import { listInformantes } from "@/src/api/informantes.api";
import { useParsedPicks } from "../useParsedPicks";

jest.mock("@/src/api/parsed-picks.api", () => ({ getParsedPicks: jest.fn() }));
jest.mock("@/src/api/informantes.api", () => ({ listInformantes: jest.fn() }));

const mockPicks = getParsedPicks as jest.Mock;
const mockInformantes = listInformantes as jest.Mock;

const pick = (id: number) => ({ id, apuesta: `pick ${id}` });
const canal = { id: 7, name: "Dm7" };
// Página llena (20) → hasMore true; página corta → hasMore false.
const fullPage = () => Array.from({ length: 20 }, (_, i) => pick(i));

const setup = async () => {
  const r = await renderHook(() => useParsedPicks());
  await act(() => r.result.current.reload());
  return r;
};

beforeEach(() => {
  jest.clearAllMocks();
  mockInformantes.mockResolvedValue([{ informante: "Dm7" }]);
  mockPicks.mockResolvedValue([pick(1), pick(2)]);
});

describe("useParsedPicks — overview", () => {
  it("no fetchea solo por montar (lo dispara la pantalla)", async () => {
    await renderHook(() => useParsedPicks());
    expect(mockPicks).not.toHaveBeenCalled();
  });

  it("reload() pide overview top-2/canal (soloApuestas) + canales", async () => {
    const { result } = await setup();

    expect(mockPicks).toHaveBeenCalledWith({
      perChannel: 2,
      soloApuestas: true,
      soloPendientes: false,
    });
    expect(result.current.overviewPicks).toEqual([pick(1), pick(2)]);
    expect(result.current.channelNames).toEqual(["Dm7"]);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("error de overview → error set, loading false", async () => {
    mockPicks.mockRejectedValue(new Error("fallo api"));
    const { result } = await setup();
    expect(result.current.error).toBe("fallo api");
    expect(result.current.loading).toBe(false);
  });
});

describe("useParsedPicks — selección de canal y paginación", () => {
  it("selectChannel pide página 0 del canal y marca hasMore si llena", async () => {
    const { result } = await setup();

    mockPicks.mockResolvedValue(fullPage());
    await act(() => result.current.selectChannel(canal));

    expect(mockPicks).toHaveBeenCalledWith({
      informanteId: 7,
      soloApuestas: true,
      soloPendientes: false,
      offset: 0,
      limit: 20,
    });
    expect(result.current.channelPicks).toHaveLength(20);
    expect(result.current.hasMore).toBe(true);
  });

  it("loadMore añade la página siguiente con offset=items cargados", async () => {
    const { result } = await setup();

    mockPicks.mockResolvedValue(fullPage());
    await act(() => result.current.selectChannel(canal));

    mockPicks.mockResolvedValue([pick(100), pick(101)]);
    await act(() => result.current.loadMore());

    expect(mockPicks).toHaveBeenLastCalledWith(
      expect.objectContaining({ informanteId: 7, offset: 20, limit: 20 })
    );
    expect(result.current.channelPicks).toHaveLength(22);
    expect(result.current.hasMore).toBe(false); // página corta
  });

  it("loadMore sin canal seleccionado no pide nada", async () => {
    const { result } = await setup();
    mockPicks.mockClear();

    await act(() => result.current.loadMore());
    expect(mockPicks).not.toHaveBeenCalled();
  });

  it("selectChannel(null) limpia la lista sin fetch", async () => {
    const { result } = await setup();

    await act(() => result.current.selectChannel(canal));
    mockPicks.mockClear();
    await act(() => result.current.selectChannel(null));

    expect(result.current.channelPicks).toEqual([]);
    expect(result.current.hasMore).toBe(false);
    expect(mockPicks).not.toHaveBeenCalled();
  });
});

describe("useParsedPicks — filtros (van al backend)", () => {
  it("toggleShowAll refetchea con soloApuestas=false", async () => {
    const { result } = await setup();

    await act(() => result.current.toggleShowAll());
    expect(result.current.showAll).toBe(true);
    expect(mockPicks).toHaveBeenLastCalledWith(
      expect.objectContaining({ soloApuestas: false })
    );
  });

  it("toggleShowAll con canal abierto recarga su página 0", async () => {
    const { result } = await setup();

    await act(() => result.current.selectChannel(canal));
    await act(() => result.current.toggleShowAll());

    expect(mockPicks).toHaveBeenLastCalledWith(
      expect.objectContaining({
        informanteId: 7,
        offset: 0,
        soloApuestas: false,
      })
    );
  });

  it("togglePendientes refetchea con soloPendientes=true", async () => {
    const { result } = await setup();

    await act(() => result.current.togglePendientes());
    expect(result.current.soloPendientes).toBe(true);
    expect(mockPicks).toHaveBeenLastCalledWith(
      expect.objectContaining({ soloPendientes: true })
    );
  });
});

describe("useParsedPicks — reload/refresh", () => {
  it("reload recarga overview + canal abierto", async () => {
    const { result } = await setup();

    await act(() => result.current.selectChannel(canal));
    mockPicks.mockClear();
    await act(() => result.current.reload());

    // overview + página 0 del canal
    expect(mockPicks).toHaveBeenCalledWith(
      expect.objectContaining({ perChannel: 2 })
    );
    expect(mockPicks).toHaveBeenCalledWith(
      expect.objectContaining({ informanteId: 7, offset: 0 })
    );
  });

  it("refresh recarga sin dejar refreshing colgado", async () => {
    const { result } = await setup();
    mockPicks.mockClear();

    await act(() => result.current.refresh());
    expect(result.current.refreshing).toBe(false); // terminó
    expect(mockPicks).toHaveBeenCalledTimes(1); // solo overview
  });
});
