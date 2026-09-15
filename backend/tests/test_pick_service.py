"""Tests unitarios de app/services/pick_service.py.

No tocan base de datos: `calcular_ganancia` y `calcular_stats` son
funciones puras sobre objetos Pick en memoria. Cubren en particular la
fórmula de ganancia neta, que en el backend Node original tenía un bug
(ver docstring de `pick_service.py`): calculaba `stake * cuota` (retorno
bruto) en vez de `stake * (cuota - 1)` (beneficio neto).
"""

from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick, PickSource
from app.services.pick_service import (
    calcular_ganancia,
    calcular_stats,
    calcular_stats_parsed,
)


class TestCalcularGanancia:
    def test_acierto_true_devuelve_beneficio_neto_no_retorno_bruto(self):
        # stake=10, cuota=2.5 -> beneficio neto = 10 * (2.5 - 1) = 15.
        # El bug original del Node hubiera dado 25 (10 * 2.5).
        assert calcular_ganancia(10, 2.5, Acierto.TRUE) == 15.0

    def test_acierto_false_devuelve_perdida_del_stake(self):
        assert calcular_ganancia(10, 2.5, Acierto.FALSE) == -10.0

    def test_pending_no_genera_ganancia_ni_perdida(self):
        assert calcular_ganancia(10, 2.5, Acierto.PENDING) == 0.0

    def test_redondea_a_dos_decimales(self):
        assert calcular_ganancia(10, 1.333, Acierto.TRUE) == 3.33


def _make_pick(id_: int, cantidad: float, cuota: float, acierto: Acierto) -> Pick:
    return Pick(
        id=id_,
        apuesta="test",
        tipo_de_apuesta="1x2",
        acierto=acierto,
        casa="Bet365",
        cantidad_apostada=cantidad,
        cuota=cuota,
        source=PickSource.MANUAL,
        usuario_id=1,
        informante_id=1,
    )


class TestCalcularStats:
    def test_lista_vacia(self):
        stats = calcular_stats([])
        assert stats.total_apuestas == 0
        assert stats.total_aciertos == 0
        assert stats.ganancias == 0.0
        assert stats.porcentaje_aciertos == 0.0
        assert stats.yield_pct == 0.0

    def test_solo_pendientes_no_cuentan_para_porcentaje_ni_yield(self):
        picks = [_make_pick(1, 10, 2, Acierto.PENDING)]
        stats = calcular_stats(picks)
        assert stats.total_apuestas == 1
        assert stats.porcentaje_aciertos == 0.0
        assert stats.yield_pct == 0.0

    def test_mezcla_de_aciertos_y_fallos(self):
        picks = [
            _make_pick(1, 10, 2.0, Acierto.TRUE),  # +10
            _make_pick(2, 10, 3.0, Acierto.FALSE),  # -10
            _make_pick(3, 20, 1.5, Acierto.TRUE),  # +10
        ]
        stats = calcular_stats(picks)
        assert stats.total_apuestas == 3
        assert stats.total_aciertos == 2
        assert stats.ganancias == 10.0  # 10 - 10 + 10
        assert stats.porcentaje_aciertos == round(2 / 3 * 100, 2)
        # yield = ganancias / total_apostado * 100 = 10 / 40 * 100
        assert stats.yield_pct == 25.0

    def test_ganancias_por_pick_indexadas_por_id(self):
        picks = [_make_pick(1, 10, 2.0, Acierto.TRUE)]
        stats = calcular_stats(picks)
        assert stats.ganancias_por_pick == {1: 10.0}


def _make_parsed(
    id_: int, stake: float, cuota: float, acierto: bool | None
) -> ParsedPick:
    return ParsedPick(
        id=id_,
        es_apuesta=True,
        seleccion="Real Madrid gana",
        cuota=cuota,
        stake=stake,
        acierto=acierto,
        anulada=False,
        raw_message_id=1,
        informante_id=1,
    )


class TestCalcularStatsParsed:
    def test_parsed_acierto_y_fallo(self):
        parsed = [
            _make_parsed(1, 10.0, 2.0, True),  # +10
            _make_parsed(2, 10.0, 2.0, False),  # -10
        ]
        stats = calcular_stats_parsed(parsed)
        assert stats.total_apuestas == 2
        assert stats.total_aciertos == 1
        assert stats.ganancias == 0.0
        assert stats.porcentaje_aciertos == 50.0
        assert stats.yield_pct == 0.0

    def test_parsed_anulada_o_pendiente_no_cuenta_para_porcentaje(self):
        parsed = [
            _make_parsed(1, 10.0, 2.0, None),  # pendiente
            _make_parsed(2, 10.0, 2.0, True),  # acierto
        ]
        stats = calcular_stats_parsed(parsed)
        assert stats.total_apuestas == 2
        assert stats.total_aciertos == 1
        # Sólo la finalizada entra en % aciertos
        assert stats.porcentaje_aciertos == 100.0
