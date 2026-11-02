// Contrato de los hooks de datos (Fase B): ciclo loading→data/error
// + reload() para el refetch on focus que las pantallas ya hacen con
// useFocusEffect. Escritos antes de extraer los hooks (test-first).
import { renderHook, waitFor, act } from "@testing-library/react-native";
import { listPicks } from "@/src/api/picks.api";
import { listInformantes } from "@/src/api/informantes.api";
import { usePicks } from "../usePicks";
import { useInformantes } from "../useInformantes";

jest.mock("@/src/api/picks.api", () => ({ listPicks: jest.fn() }));
jest.mock("@/src/api/informantes.api", () => ({ listInformantes: jest.fn() }));

const mockListPicks = listPicks as jest.Mock;
const mockListInformantes = listInformantes as jest.Mock;

const fakePick = { id: 1, apuesta: "X", informante: "Canal" };
const fakeInformante = { informante: "Canal", yieldPct: 5 };

describe("usePicks", () => {
  beforeEach(() => jest.clearAllMocks());

  it("empieza cargando y llama a listPicks al montar", async () => {
    // Promesa que resolvemos manualmente para poder observar el
    // estado loading=true (con mockResolvedValue se resuelve antes
    // de que await renderHook devuelva el control).
    let resolveFetch: (picks: unknown[]) => void = () => {};
    mockListPicks.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      })
    );
    const { result } = await renderHook(() => usePicks());

    expect(result.current.loading).toBe(true);
    await act(async () => resolveFetch([fakePick]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockListPicks).toHaveBeenCalledTimes(1);
  });

  it("éxito → apuestas poblado, error null", async () => {
    mockListPicks.mockResolvedValue([fakePick]);
    const { result } = await renderHook(() => usePicks());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.apuestas).toEqual([fakePick]);
    expect(result.current.error).toBeNull();
  });

  it("error → mensaje, loading false, lista vacía", async () => {
    mockListPicks.mockRejectedValue(new Error("red caída"));
    const { result } = await renderHook(() => usePicks());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("red caída");
    expect(result.current.apuestas).toEqual([]);
  });

  it("reload() refetchea (lo que las pantallas hacen en useFocusEffect)", async () => {
    mockListPicks.mockResolvedValue([fakePick]);
    const { result } = await renderHook(() => usePicks());
    await waitFor(() => expect(result.current.loading).toBe(false));

    mockListPicks.mockResolvedValue([fakePick, fakePick]);
    await act(() => result.current.reload());
    expect(mockListPicks).toHaveBeenCalledTimes(2);
    expect(result.current.apuestas).toHaveLength(2);
  });

  it("un error posterior no borra el mensaje tras reload exitoso", async () => {
    mockListPicks.mockRejectedValueOnce(new Error("fallo"));
    const { result } = await renderHook(() => usePicks());
    await waitFor(() => expect(result.current.error).toBe("fallo"));

    mockListPicks.mockResolvedValue([fakePick]);
    await act(() => result.current.reload());
    expect(result.current.error).toBeNull();
  });
});

describe("useInformantes", () => {
  beforeEach(() => jest.clearAllMocks());

  it("enabled=false no fetchea (landing sin sesión → no hay 401)", async () => {
    const { result } = await renderHook(() => useInformantes(false));
    expect(mockListInformantes).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("enabled false→true dispara el fetch (al hacer login)", async () => {
    mockListInformantes.mockResolvedValue([fakeInformante]);
    const { result, rerender } = await renderHook(
      ({ on }: { on: boolean }) => useInformantes(on),
      { initialProps: { on: false } }
    );
    expect(mockListInformantes).not.toHaveBeenCalled();

    await rerender({ on: true });
    await waitFor(() =>
      expect(result.current.informantes).toEqual([fakeInformante])
    );
  });

  it("éxito → informantes poblado", async () => {
    mockListInformantes.mockResolvedValue([fakeInformante]);
    const { result } = await renderHook(() => useInformantes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.informantes).toEqual([fakeInformante]);
    expect(result.current.error).toBeNull();
  });

  it("error → mensaje y lista vacía", async () => {
    mockListInformantes.mockRejectedValue(new Error("401"));
    const { result } = await renderHook(() => useInformantes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("401");
    expect(result.current.informantes).toEqual([]);
  });
});
