// Tests de caracterización de la lógica de apuestas: capturan el
// comportamiento actual de las copias en [informante], my-picks,
// analysis y earns antes de unificarlas (Fase A del refactor).
import { PickItem } from "@/src/types/pick.types";
import {
  gananciaApuesta,
  gananciaNeta,
  calcularROI,
  calcularEstadisticas,
  calcularProyeccion,
  nivelRiesgo,
  iconoResultado,
  iconoAnulada,
  rankingInformantes,
  pendientesDe,
  maxApuestasPorInformante,
  gananciasPor,
  totalGanancias,
} from "../picks";

// Referencia fija para las ventanas por fecha de calcularEstadisticas.
const HOY = new Date("2026-11-03T12:00:00Z");
const daysAgo = (n: number) =>
  new Date(HOY.getTime() - n * 24 * 3600 * 1000).toISOString();

const pick = (over: Partial<PickItem>): PickItem =>
  ({
    id: 1,
    apuesta: "Pick",
    informante: "Canal",
    tipoDeApuesta: "simple",
    acierto: "Pending",
    casa: "Bet365",
    cantidadApostada: 10,
    cuota: 2,
    fecha: "2026-10-28",
    source: "manual",
    esReto: false,
    esCombinada: false,
    cuotaEfectiva: null,
    parsedPickId: null,
    anulada: false,
    motivoAnulada: null,
    ...over,
  }) as PickItem;

describe("gananciaApuesta (variante [informante], Acierto + redondeo)", () => {
  it("acierto → cantidad*(cuota-1)", () => {
    expect(gananciaApuesta(10, 2, "True")).toBe(10);
    expect(gananciaApuesta(5, 1.5, "True")).toBe(2.5);
  });

  it("fallo → -cantidad", () => {
    expect(gananciaApuesta(10, 2, "False")).toBe(-10);
  });

  it("pendiente → 0", () => {
    expect(gananciaApuesta(10, 2, "Pending")).toBe(0);
  });

  it("cantidad o cuota null → 0 (picks de Telegram sin datos)", () => {
    expect(gananciaApuesta(null, 2, "True")).toBe(0);
    expect(gananciaApuesta(10, null, "True")).toBe(0);
  });

  it("redondea a 2 decimales (como [informante])", () => {
    // 3.33 * (1.7-1) = 2.331 → 2.33; 3.33 * 1.5 = 4.995 → 5
    expect(gananciaApuesta(3.33, 1.7, "True")).toBe(2.33);
    expect(gananciaApuesta(3.33, 2.5, "True")).toBe(5);
    // La variante analysis NO redondea: 3.33*1.5 = 4.995 crudo
    expect(gananciaNeta(3.33, 2.5, true)).toBeCloseTo(4.995);
  });
});

describe("gananciaNeta (variante analysis, boolean + sin redondeo)", () => {
  it("acierto → cantidad*(cuota-1) crudo", () => {
    expect(gananciaNeta(10, 2, true)).toBe(10);
  });
  it("fallo → -cantidad", () => {
    expect(gananciaNeta(10, 2, false)).toBe(-10);
  });
});

describe("calcularROI", () => {
  const items = [
    { cantidad: 10, cuota: 2, acierto: true }, // +10
    { cantidad: 10, cuota: 2, acierto: false }, // -10
  ];
  it("ganancias/inversión·100", () => {
    expect(calcularROI(items)).toBe(0);
    expect(calcularROI([{ cantidad: 10, cuota: 2, acierto: true }])).toBe(100);
  });
  it("0 si la inversión es 0 (evita división por cero)", () => {
    expect(calcularROI([])).toBe(0);
    expect(calcularROI([{ cantidad: 0, cuota: 2, acierto: true }])).toBe(0);
  });
});

describe("calcularEstadisticas", () => {
  it("null con lista vacía", () => {
    expect(calcularEstadisticas([])).toBeNull();
  });
  it("filtra a los últimos 30 días por fecha (no las últimas 30 apuestas)", () => {
    const apuestas = [
      // 40 apuestas antiguas que aciertan todas
      ...Array.from({ length: 40 }, () => ({
        cantidad: 10,
        cuota: 2,
        acierto: true,
        fecha: daysAgo(40),
      })),
      // 5 recientes que fallan todas
      ...Array.from({ length: 5 }, () => ({
        cantidad: 10,
        cuota: 2,
        acierto: false,
        fecha: daysAgo(3),
      })),
    ];
    const stats = calcularEstadisticas(apuestas, HOY)!;
    expect(stats.totalApuestas).toBe(5);
    expect(stats.aciertos).toBe(0);
  });
  it("apuestas sin fecha quedan fuera de la ventana", () => {
    const stats = calcularEstadisticas(
      [
        { cantidad: 10, cuota: 2, acierto: true }, // sin fecha
        { cantidad: 10, cuota: 2, acierto: false, fecha: daysAgo(2) },
      ],
      HOY
    )!;
    expect(stats.totalApuestas).toBe(1);
    expect(stats.aciertos).toBe(0);
  });
  it("null si ninguna apuesta cae en la ventana", () => {
    expect(
      calcularEstadisticas(
        [{ cantidad: 10, cuota: 2, acierto: true, fecha: daysAgo(60) }],
        HOY
      )
    ).toBeNull();
  });
  it("tendencia = ROI(0–14d) − ROI(15–28d)", () => {
    // 14 fallos hace 20 días + 14 aciertos hace 5 días:
    // recientes → +100%, previas → −100% → tendencia 200
    const apuestas = [
      ...Array.from({ length: 14 }, () => ({
        cantidad: 10,
        cuota: 2,
        acierto: false,
        fecha: daysAgo(20),
      })),
      ...Array.from({ length: 14 }, () => ({
        cantidad: 10,
        cuota: 2,
        acierto: true,
        fecha: daysAgo(5),
      })),
    ];
    expect(calcularEstadisticas(apuestas, HOY)!.tendencia).toBe(200);
  });
  it("apuestasDiarias = total/30 de la ventana", () => {
    const apuestas = Array.from({ length: 15 }, () => ({
      cantidad: 10,
      cuota: 2,
      acierto: true,
      fecha: daysAgo(10),
    }));
    expect(calcularEstadisticas(apuestas, HOY)!.apuestasDiarias).toBe(0.5);
  });
});

describe("calcularProyeccion", () => {
  it("proyecta a 30 días con cuota media", () => {
    const apuestas = [{ cantidad: 10, cuota: 2, acierto: true }];
    const p = calcularProyeccion(apuestas, 10, 1);
    // 30 apuestas de 10€, 100% aciertos, cuota 2: 30*10*2 − 30*10 = 300
    expect(p.inversionMensual).toBe(300);
    expect(p.gananciaProyectada).toBe(300);
    expect(p.cuotaMedia).toBe(2);
  });
});

describe("nivelRiesgo — límites estrictos", () => {
  it("BAJO solo si aciertos>65 && roi>15 && tendencia>=0", () => {
    expect(nivelRiesgo({ porcentajeAciertos: 70, roi: 20, tendencia: 0 })).toBe(
      "BAJO"
    );
    expect(
      nivelRiesgo({ porcentajeAciertos: 65, roi: 20, tendencia: 1 })
    ).not.toBe("BAJO"); // 65 exacto no
    expect(
      nivelRiesgo({ porcentajeAciertos: 70, roi: 15, tendencia: 1 })
    ).not.toBe("BAJO"); // 15 exacto no
    expect(
      nivelRiesgo({ porcentajeAciertos: 70, roi: 20, tendencia: -0.1 })
    ).not.toBe("BAJO");
  });
  it("MEDIO si aciertos>50 && roi>5 (sin cumplir BAJO)", () => {
    expect(
      nivelRiesgo({ porcentajeAciertos: 60, roi: 10, tendencia: -5 })
    ).toBe("MEDIO");
    expect(nivelRiesgo({ porcentajeAciertos: 50, roi: 10, tendencia: 0 })).toBe(
      "ALTO"
    ); // 50 exacto no
  });
  it("ALTO en el resto", () => {
    expect(
      nivelRiesgo({ porcentajeAciertos: 40, roi: -5, tendencia: -10 })
    ).toBe("ALTO");
  });
});

describe("iconos de estado", () => {
  it("acierto→✅, fallo→❌, pendiente→❓ por defecto", () => {
    expect(iconoResultado("True")).toBe("✅");
    expect(iconoResultado("False")).toBe("❌");
    expect(iconoResultado("Pending")).toBe("❓");
  });
  it("el glifo pendiente es configurable (my-picks usa '-')", () => {
    expect(iconoResultado("Pending", "-")).toBe("-");
  });
  it("iconoAnulada por motivo", () => {
    expect(iconoAnulada("push")).toBe("↩️");
    expect(iconoAnulada("aplazado")).toBe("📅");
    expect(iconoAnulada("jugador_fuera")).toBe("👤");
    expect(iconoAnulada("expired")).toBe("⏰");
    expect(iconoAnulada("otro")).toBe("⊘");
    expect(iconoAnulada(null)).toBe("⊘");
  });
});

describe("agrupaciones de my-picks", () => {
  const porInformante = {
    A: [
      pick({ id: 1, acierto: "True", informante: "A" }),
      pick({ id: 2, acierto: "Pending", informante: "A" }),
      pick({ id: 3, acierto: "False", informante: "A" }),
    ],
    B: [pick({ id: 4, acierto: "True", informante: "B" })],
  };

  it("rankingInformantes: % sobre liquidadas, desc, top N", () => {
    const r = rankingInformantes(porInformante);
    expect(r[0]).toEqual({ informante: "B", porcentajeAcierto: 100 });
    expect(r[1].porcentajeAcierto).toBe(50); // 1/2 liquidadas
  });
  it("pendientesDe: solo las Pending, con informante forzado", () => {
    const p = pendientesDe(porInformante);
    expect(p).toHaveLength(1);
    expect(p[0].id).toBe(2);
    expect(p[0].informante).toBe("A");
  });
  it("maxApuestasPorInformante", () => {
    expect(maxApuestasPorInformante(porInformante)).toBe(3);
    expect(maxApuestasPorInformante({})).toBe(0);
  });
});

describe("agregaciones de earns (campo ganancia del backend)", () => {
  const picks = [
    pick({ informante: "A", casa: "B365", ganancia: 10 }),
    pick({ informante: "A", casa: "Codere", ganancia: -5 }),
    pick({ informante: "B", casa: "B365", ganancia: undefined }),
  ];
  it("gananciasPor agrupa y trata undefined como 0", () => {
    expect(gananciasPor(picks, "informante")).toEqual({ A: 5, B: 0 });
    expect(gananciasPor(picks, "casa")).toEqual({ B365: 10, Codere: -5 });
  });
  it("totalGanancias suma con 0 en null", () => {
    expect(totalGanancias(picks)).toBe(5);
  });
});
