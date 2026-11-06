// Contrato de useInformanteStats (Fase B): fetch por nombre de canal
// — cubre el caso "parámetro dinámico" de [informante].tsx.
import { renderHook, waitFor } from "@testing-library/react-native";
import { getInformanteStats } from "@/src/api/informantes.api";
import { useInformanteStats } from "../useInformanteStats";

jest.mock("@/src/api/informantes.api", () => ({
  getInformanteStats: jest.fn(),
}));

const mockStats = getInformanteStats as jest.Mock;

const fakeStats = {
  informante: "Dm7",
  totalApuestas: 10,
  totalAciertos: 6,
  yieldPct: 8.2,
};

describe("useInformanteStats", () => {
  beforeEach(() => jest.clearAllMocks());

  it("llama a getInformanteStats con el nombre y expone data", async () => {
    mockStats.mockResolvedValue(fakeStats);
    const { result } = await renderHook(() => useInformanteStats("Dm7"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockStats).toHaveBeenCalledWith("Dm7");
    expect(result.current.data).toEqual(fakeStats);
    expect(result.current.error).toBeNull();
  });

  it("error → data null y mensaje", async () => {
    mockStats.mockRejectedValue(new Error("not found"));
    const { result } = await renderHook(() => useInformanteStats("Fantasma"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("not found");
  });

  it("cambiar el nombre refetchea (la ruta dinámica lo necesita)", async () => {
    mockStats.mockResolvedValue(fakeStats);
    const { result, rerender } = await renderHook(
      ({ nombre }: { nombre: string }) => useInformanteStats(nombre),
      { initialProps: { nombre: "Dm7" } }
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    await rerender({ nombre: "LadyBets" });
    await waitFor(() => expect(mockStats).toHaveBeenCalledWith("LadyBets"));
    expect(mockStats).toHaveBeenCalledTimes(2);
  });
});
