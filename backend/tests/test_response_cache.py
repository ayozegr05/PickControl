"""Tests de `response_cache.py` — caché persistente de providers.

El fixture autouse de conftest aísla el archivo: cada test arranca con
`provider_cache.json` vacío en tmp_path.
"""

from datetime import datetime, timezone

import app.services.results.response_cache as rc


def _ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _event(start: datetime, event_id: int = 1) -> dict:
    return {"id": event_id, "startTimestamp": _ts(start)}


class TestEntityIds:
    def test_roundtrip(self):
        assert rc.get_entity_id("footapi7", "betis") is None
        rc.set_entity_id("footapi7", "Real Betis ", 2816)
        assert rc.get_entity_id("footapi7", "real betis") == 2816

    def test_normaliza_y_separa_providers(self):
        rc.set_entity_id("footapi7", "Elche", 2833)
        assert rc.get_entity_id("footapi7", "elche") == 2833
        assert rc.get_entity_id("tennisapi1", "elche") is None
        assert rc.get_entity_id("footapi7", "betis") is None


class TestEventLists:
    def test_roundtrip(self):
        events = [_event(datetime(2026, 9, 15, 20, 0))]
        rc.set_event_list("footapi7|2816", events)
        entry = rc.get_event_list("footapi7|2816")
        assert entry is not None
        assert entry["events"] == events
        assert "captured_at" in entry

    def test_missing_key(self):
        assert rc.get_event_list("footapi7|999") is None


class TestEventListCovers:
    def test_pick_anterior_al_evento_mas_reciente_cubierto(self):
        # Lista capturada hoy con el partido del pick dentro: cubierto.
        events = [_event(datetime(2026, 9, 20, 20, 0))]
        entry = {
            "captured_at": datetime(2026, 9, 21, 9, 0).isoformat(),
            "events": events,
        }
        assert rc.event_list_covers(entry, datetime(2026, 9, 20, 20, 0))
        assert rc.event_list_covers(entry, datetime(2026, 9, 18, 12, 0))

    def test_pick_posterior_a_captura_no_cubierto(self):
        # El pick es de un partido posterior a la captura: pudo jugarse
        # después -> refetch.
        events = [_event(datetime(2026, 9, 20, 20, 0))]
        entry = {
            "captured_at": datetime(2026, 9, 21, 9, 0).isoformat(),
            "events": events,
        }
        assert not rc.event_list_covers(entry, datetime(2026, 9, 21, 21, 0))

    def test_pick_dentro_del_margen_en_juego_no_cubierto(self):
        # Partido empezado 1h antes de la captura (aún en juego): no
        # cubierto aunque su fecha sea anterior a captured_at.
        events = [_event(datetime(2026, 9, 19, 20, 0))]
        entry = {
            "captured_at": datetime(2026, 9, 21, 9, 0).isoformat(),
            "events": events,
        }
        assert not rc.event_list_covers(entry, datetime(2026, 9, 21, 8, 30))

    def test_pick_viejo_con_captura_reciente_cubierto(self):
        # Jugador inactivo: lista vieja, pick posterior a su último
        # partido pero muy anterior a la captura -> cubierto, miss real.
        events = [_event(datetime(2026, 6, 1, 20, 0))]
        entry = {
            "captured_at": datetime(2026, 9, 21, 9, 0).isoformat(),
            "events": events,
        }
        assert rc.event_list_covers(entry, datetime(2026, 9, 1, 18, 0))

    def test_entrada_corrupta_no_cubre(self):
        assert not rc.event_list_covers({"events": []}, datetime(2026, 9, 20))


class TestMaxEventStart:
    def test_devuelve_el_mas_reciente(self):
        events = [
            _event(datetime(2026, 9, 10, 20, 0), 1),
            _event(datetime(2026, 9, 20, 20, 0), 2),
            _event(datetime(2026, 9, 15, 20, 0), 3),
        ]
        assert rc.max_event_start(events) == datetime(2026, 9, 20, 20, 0)

    def test_sin_timestamps_devuelve_none(self):
        assert rc.max_event_start([{"id": 1}, {"date": "2026-09-20"}]) is None
        assert rc.max_event_start([]) is None
