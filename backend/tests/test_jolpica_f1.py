"""Tests de `jolpica_f1.py` (Fórmula 1 vía Jolpica/Ergast).

La red se corta en `_get_json` (calendario del año + resultados de la
ronda); `provider_state` se neutraliza igual que en test_espn_f1.py.

Los fixtures replican la forma del GP de Italia 2025 real (simplificado):
clasificados con positionText numérico, un "R" (retirado) y un "W" (DNS).
"""

from datetime import datetime

import pytest

import app.services.results.espn as espn
import app.services.results.espn_f1 as espn_f1
from app.models.parsed_pick import ParsedPick
from app.services.results.jolpica_f1 import JolpicaF1Provider
from app.services.results.verifier import verify_pick

_RESULTS = [
    ("1", "1", "Max", "Verstappen", "Finished"),
    ("2", "2", "Lando", "Norris", "Finished"),
    ("3", "3", "Oscar", "Piastri", "Finished"),
    ("4", "4", "Charles", "Leclerc", "Finished"),
    ("5", "5", "George", "Russell", "Finished"),
    ("15", "15", "Esteban", "Ocon", "Lapped"),
    ("19", "R", "Fernando", "Alonso", "Retired"),
    ("20", "W", "Nico", "Hülkenberg", "Did not start"),
]

_RACES = [
    {
        "season": "2026",
        "round": "15",
        "raceName": "Azerbaijan Grand Prix",
        "Circuit": {
            "circuitId": "baku",
            "circuitName": "Baku City Circuit",
            "Location": {"locality": "Baku", "country": "Azerbaijan"},
        },
        "date": "2026-09-13",
        "time": "11:00:00Z",
    },
    {
        "season": "2026",
        "round": "16",
        "raceName": "Italian Grand Prix",
        "Circuit": {
            "circuitId": "monza",
            "circuitName": "Autodromo Nazionale di Monza",
            "Location": {"locality": "Monza", "country": "Italy"},
        },
        "date": "2026-09-06",
        "time": "13:00:00Z",
    },
]


def _season_payload() -> dict:
    return {"MRData": {"RaceTable": {"Races": _RACES}}}


def _results_payload() -> dict:
    return {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "15",
                        "raceName": "Azerbaijan Grand Prix",
                        "date": "2026-09-13",
                        "Results": [
                            {
                                "position": pos,
                                "positionText": pos_text,
                                "Driver": {"givenName": first, "familyName": last},
                                "status": status,
                            }
                            for pos, pos_text, first, last, status in _RESULTS
                        ],
                    }
                ]
            }
        }
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(espn, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(espn, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(espn, "mark_missed", lambda key: None)
    monkeypatch.setattr(espn, "mark_rate_limited", lambda name: None)
    monkeypatch.setattr(espn, "count_provider_call", lambda name: None)
    monkeypatch.setattr(espn_f1, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(espn_f1, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(espn_f1, "mark_missed", lambda key: None)

    prov = JolpicaF1Provider()

    async def fake_get_json(url, league, params):
        if url.endswith("/races/"):
            return _season_payload()
        if url.endswith("/15/results/"):
            return _results_payload()
        return None

    monkeypatch.setattr(prov, "_get_json", fake_get_json)
    return prov


class TestFindRace:
    async def test_gp_espanol_casa_con_calendario(self, provider):
        race = await provider._find_race(datetime(2026, 9, 13), "GP AZERBAIYÁN")
        assert race is not None
        assert race["raceName"] == "Azerbaijan Grand Prix"

    async def test_gp_italia_casa_italian_monza(self, provider):
        race = await provider._find_race(datetime(2026, 9, 6), "GP ITALIA")
        assert race is not None
        assert race["raceName"] == "Italian Grand Prix"

    async def test_gp_inexistente_devuelve_none(self, provider):
        assert await provider._find_race(datetime(2026, 9, 6), "GP de México") is None


class TestClassifiedCount:
    async def test_clasificados_solo_position_numerica(self, provider):
        """18 clasificados = 15 Finished + 3 Lapped; R y W no cuentan."""
        stats = await provider.find_match_stats(datetime(2026, 9, 13), "GP AZERBAIYÁN")
        assert stats is not None
        assert stats.values["Classified Cars"] == (6, 0)
        assert stats.home_team == "Azerbaijan Grand Prix Baku"

    async def test_carrera_sin_resultados_devuelve_none(self, provider, monkeypatch):
        """Sin resultados oficiales -> pendiente, nunca conteo inventado."""

        async def fake_get_json(url, league, params):
            if url.endswith("/races/"):
                return _season_payload()
            if url.endswith("/results/"):
                return {"MRData": {"RaceTable": {"Races": []}}}
            return None

        monkeypatch.setattr(provider, "_get_json", fake_get_json)
        assert (
            await provider.find_match_stats(datetime(2026, 9, 13), "GP AZERBAIYÁN")
            is None
        )


class TestFindMatch:
    async def test_ganador_modelado_como_1_0(self, provider):
        match = await provider.find_match(datetime(2026, 9, 13), "GP AZERBAIYÁN")
        assert match is not None
        assert match.home_team == "Max Verstappen"
        assert match.away_team == "Lando Norris"
        assert (match.home_score, match.away_score) == (1, 0)


class TestVerifyPickIntegration:
    async def test_pick_menos_coches_acierto(self, provider):
        """Caso real #1803 vía Jolpica: menos de 7.5, hay 6."""
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Menos de 7.5 coches",
            mercado="over/under",
            evento="GP AZERBAIYÁN",
            linea=7.5,
            deporte="automovilismo",
            fecha_evento=datetime(2026, 9, 13),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_pick_mas_coches_fallo(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Más de 7.5 coches",
            mercado="over/under",
            evento="GP AZERBAIYÁN",
            linea=7.5,
            deporte="automovilismo",
            fecha_evento=datetime(2026, 9, 13),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (False, False)

    async def test_name_es_jolpica_para_atribucion(self, provider):
        """`verificado_provider` debe distinguirlo del provider ESPN."""
        assert provider.NAME == "jolpica"
