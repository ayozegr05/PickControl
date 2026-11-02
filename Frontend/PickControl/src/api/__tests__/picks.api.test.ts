// Caracterización del mapper snake_case→camelCase de /apuestas:
// es la frontera backend↔UI y maneja nulls de picks de Telegram.
import { RawPick, toPickItem } from "../picks.api";

// client.ts importa el storage seguro (expo-secure-store / AsyncStorage);
// el mapper es puro — mockeamos la dependencia nativa.
jest.mock("@/src/storage/secure", () => ({
  getSecureItem: jest.fn().mockResolvedValue(null),
  setSecureItem: jest.fn(),
  removeSecureItem: jest.fn(),
}));

const rawBase: RawPick = {
  id: 42,
  apuesta: "Más de 9.5 córners",
  informante: "Dm7 || GRATUITO || ⚡",
  tipo_de_apuesta: "corners",
  acierto: "Pending",
  casa: "Bet365",
  cantidad_apostada: null,
  cuota: null,
  fecha: "2026-10-28T14:30:00Z",
  source: "telegram",
};

describe("toPickItem", () => {
  it("mapea snake_case → camelCase", () => {
    const item = toPickItem(rawBase);
    expect(item.tipoDeApuesta).toBe("corners");
    expect(item.cantidadApostada).toBeNull();
    expect(item.cuota).toBeNull();
    expect(item.acierto).toBe("Pending");
  });

  it("preserva nulls de picks de Telegram (nunca inventa datos)", () => {
    const item = toPickItem(rawBase);
    expect(item.cantidadApostada).toBeNull();
    expect(item.cuota).toBeNull();
    expect(item.evento).toBeNull();
    expect(item.parsedPickId).toBeNull();
  });

  it("defaults: esReto/esCombinada/anulada false, patas undefined", () => {
    const item = toPickItem(rawBase);
    expect(item.esReto).toBe(false);
    expect(item.esCombinada).toBe(false);
    expect(item.anulada).toBe(false);
    expect(item.patas).toBeUndefined();
  });

  it("mapea campos opcionales cuando vienen", () => {
    const item = toPickItem({
      ...rawBase,
      evento: "Betis - Sevilla",
      es_combinada: true,
      cuota_efectiva: 3.5,
      anulada: true,
      motivo_anulada: "push",
      parsed_pick_id: 7,
      ganancia: 15.5,
    });
    expect(item.evento).toBe("Betis - Sevilla");
    expect(item.esCombinada).toBe(true);
    expect(item.cuotaEfectiva).toBe(3.5);
    expect(item.anulada).toBe(true);
    expect(item.motivoAnulada).toBe("push");
    expect(item.parsedPickId).toBe(7);
    expect(item.ganancia).toBe(15.5);
  });
});
