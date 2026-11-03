// Contrato del componente PickDetailModal (Fase D): modal de detalle
// de un pick de Telegram en la pantalla [informante] — filas de
// detalle, caja de cuota de mercado, "Yo también la jugué" y botones
// de corrección. Test-first, antes de extraerlo.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import PickDetailModal from "@/src/components/pick-detail-modal";
import { PickOdds } from "@/src/api/parsed-picks.api";
import { PickItem } from "@/src/types/pick.types";

const makePick = (over: Partial<PickItem> = {}): PickItem => ({
  id: 5,
  apuesta: "Over 2.5 goles",
  informante: "Canal A",
  tipoDeApuesta: "goles",
  acierto: "Pending",
  casa: "bet365",
  cantidadApostada: 2,
  cuota: 1.9,
  fecha: "2025-01-10T20:00:00.000Z",
  source: "telegram",
  evento: "Madrid vs Barça",
  ...over,
});

const makeOdds = (over: Partial<PickOdds> = {}): PickOdds => ({
  mapeado: true,
  mercado_api: "Total de goles",
  opcion_api: "Más de",
  linea_api: "2.5",
  cuota_tipster: 1.9,
  cuota_apertura: 1.85,
  cuota_publicacion: 1.88,
  cuota_cierre: 1.75,
  capturas: 4,
  cuota_disponible: true,
  clv_pct: 8.5,
  ...over,
});

const baseProps = () => ({
  odds: null,
  oddsLoading: false,
  yaRegistrada: false,
  canCorrect: true,
  onClose: jest.fn(),
  onJugar: jest.fn(),
  onCorregir: jest.fn(),
});

describe("PickDetailModal", () => {
  it("pick null no renderiza contenido", async () => {
    const props = baseProps();
    const screen = await render(<PickDetailModal pick={null} {...props} />);

    expect(screen.queryByText(/Resultado:/)).toBeNull();
  });

  it("renderiza las filas de detalle del pick", async () => {
    const props = baseProps();
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(screen.getByText("Over 2.5 goles")).toBeTruthy();
    expect(screen.getByText(/Resultado:.*Pendiente/)).toBeTruthy();
    expect(screen.getByText(/Evento: Madrid vs Barça/)).toBeTruthy();
    expect(screen.getByText(/Mercado: goles/)).toBeTruthy();
    expect(screen.getByText(/Cuota: 1.90 · Stake: 2.00u/)).toBeTruthy();
    expect(screen.getByText(/Casa: bet365/)).toBeTruthy();
  });

  it("oddsLoading muestra spinner y no la caja de cuotas", async () => {
    const props = { ...baseProps(), oddsLoading: true };
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(screen.queryByText("Cuota de mercado")).toBeNull();
  });

  it("odds mapeadas muestran mercado, cuotas y badges", async () => {
    const props = { ...baseProps(), odds: makeOdds() };
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(screen.getByText("Cuota de mercado")).toBeTruthy();
    expect(screen.getByText(/Total de goles · Más de \(2.5\)/)).toBeTruthy();
    expect(
      screen.getByText(/Tipster 1.90 · Apertura 1.85 · Al publicar 1.88 · Cierre 1.75/)
    ).toBeTruthy();
    expect(screen.getByText(/✓ Cuota real/)).toBeTruthy();
    expect(screen.getByText(/CLV \+8.5%/)).toBeTruthy();
  });

  it("cuota no disponible muestra el aviso de cuota inflada", async () => {
    const props = {
      ...baseProps(),
      odds: makeOdds({ cuota_disponible: false, clv_pct: -3 }),
    };
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(screen.getByText(/⚠ Cuota inflada/)).toBeTruthy();
    expect(screen.getByText(/CLV -3.0%/)).toBeTruthy();
  });

  it("odds sin mercado comparable muestra el mensaje", async () => {
    const props = { ...baseProps(), odds: makeOdds({ mapeado: false }) };
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(
      screen.getByText("Sin mercado comparable en el proveedor")
    ).toBeTruthy();
  });

  it("sin registrar muestra el botón 'Yo también la jugué' → onJugar", async () => {
    const props = baseProps();
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    await fireEvent.press(screen.getByText("Yo también la jugué"));
    expect(props.onJugar).toHaveBeenCalledWith(
      expect.objectContaining({ id: 5 })
    );
  });

  it("ya registrada muestra el check en vez del botón", async () => {
    const props = { ...baseProps(), yaRegistrada: true };
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    expect(
      screen.getByText("✓ Ya la tienes registrada en tus apuestas")
    ).toBeTruthy();
    expect(screen.queryByText("Yo también la jugué")).toBeNull();
  });

  it("los botones de corrección llaman a onCorregir con su payload", async () => {
    const props = baseProps();
    const screen = await render(
      <PickDetailModal pick={makePick()} {...props} />
    );

    await fireEvent.press(screen.getByText("✅ Acertó"));
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      acierto: true,
      anulada: false,
    });

    await fireEvent.press(screen.getByText("❌ Falló"));
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      acierto: false,
      anulada: false,
    });

    await fireEvent.press(screen.getByText("Anulada"));
    expect(props.onCorregir).toHaveBeenCalledWith(expect.anything(), {
      anulada: true,
    });
  });

  it("sin canCorrect no muestra la sección 'Corregir resultado'", async () => {
    const screen = await render(
      <PickDetailModal pick={makePick()} {...baseProps()} canCorrect={false} />
    );
    expect(screen.queryByText("Corregir resultado:")).toBeNull();
    expect(screen.queryByText("✅ Acertó")).toBeNull();
    // El resto del modal sigue visible.
    expect(screen.getByText("Yo también la jugué")).toBeTruthy();
  });
});
