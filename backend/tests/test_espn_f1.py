"""Tests de `espn_f1.py` (Fórmula 1 vía ESPN racing/f1).

La red se corta en `_scoreboard` (listado del mes) y `_get_json`
(detalle de la competition + status por piloto en la core API);
`provider_state` se neutraliza igual que en test_espn.py.
"""

from datetime import datetime

import pytest

import app.services.results.espn as espn
import app.services.results.espn_f1 as espn_f1
from app.models.parsed_pick import ParsedPick
from app.services.results.espn_f1 import EspnF1Provider
from app.services.results.verifier import verify_pick

_EVENT_ID = "600057444"
_RACE_ID = "401839112"
_CORE = espn_f1._CORE_URL

_DRIVERS = [
    ("5503", "George Russell", 1, "STATUS_CLASSIFIED"),
    ("4665", "Max Verstappen", 2, "STATUS_CLASSIFIED"),
    ("5790", "Isack Hadjar", 3, "STATUS_CLASSIFIED"),
    ("5498", "Charles Leclerc", 4, "STATUS_CLASSIFIED"),
    ("5829", "Kimi Antonelli", 5, "STATUS_RETIRED"),
    ("868", "Lewis Hamilton", 6, "STATUS_RETIRED"),
]

_SCOREBOARD = {
    "events": [
        {
            "id": _EVENT_ID,
            "name": "Qatar Airways Azerbaijan Grand Prix",
            "competitions": [
                {
                    "id": "401839108",
                    "date": "2026-09-22T09:00Z",
                    "type": {"id": "1", "abbreviation": "FP1"},
                    "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                    "competitors": [],
                },
                {
                    "id": _RACE_ID,
                    "date": "2026-09-25T11:00Z",
                    "type": {"id": "3", "abbreviation": "Race"},
                    "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                    "competitors": [
                        {
                            "id": did,
                            "type": "athlete",
                            "order": order,
                            "winner": order == 1,
                            "athlete": {"displayName": name},
                            "statistics": [],
                        }
                        for did, name, order, _ in _DRIVERS
                    ],
                },
            ],
        }
    ]
}


def _competition_detail() -> dict:
    """Réplica de `/competitions/{race_id}` de la core API: cada piloto
    con `status.$ref` individual."""
    return {
        "id": _RACE_ID,
        "type": {"id": "3", "abbreviation": "Race"},
        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
        "competitors": [
            {
                "id": did,
                "type": "athlete",
                "order": order,
                "winner": order == 1,
                "status": {
                    "$ref": f"{_CORE}/f1/events/{_EVENT_ID}/competitions/"
                    f"{_RACE_ID}/competitors/{did}/status?lang=en&region=us"
                },
            }
            for did, _, order, _ in _DRIVERS
        ],
    }


def _status_payload(name: str) -> dict:
    return {
        "type": {"id": "8", "name": name, "state": "post"},
        "displayValue": name.replace("STATUS_", "").title(),
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

    prov = EspnF1Provider()

    async def fake_scoreboard(league, period):
        return _SCOREBOARD if league == "f1" else None

    status_by_id = {did: st for did, _, _, st in _DRIVERS}

    async def fake_get_json(url, league, params):
        if url.endswith(f"/competitions/{_RACE_ID}"):
            return _competition_detail()
        for did, status_name in status_by_id.items():
            if f"/competitors/{did}/status" in url:
                return _status_payload(status_name)
        return None

    monkeypatch.setattr(prov, "_scoreboard", fake_scoreboard)
    monkeypatch.setattr(prov, "_get_json", fake_get_json)
    return prov


class TestEventScore:
    def test_gp_espanol_casa_con_nombre_espn(self):
        score = espn_f1._event_score(
            "GP AZERBAIYÁN", "Qatar Airways Azerbaijan Grand Prix"
        )
        assert score >= espn_f1._MIN_EVENT_SCORE

    def test_gp_italia_casa_italian(self):
        assert espn_f1._event_score("GP Italia", "Pirelli Italian Grand Prix") >= 0.6

    def test_gp_sin_localizacion_no_casa(self):
        assert espn_f1._event_score("GP", "Qatar Airways Azerbaijan Grand Prix") < 0.6

    def test_otro_gp_no_casa(self):
        assert (
            espn_f1._event_score("GP de México", "Qatar Airways Azerbaijan Grand Prix")
            < 0.6
        )


class TestFindRaceAndStats:
    async def test_clasificados_cuentan_solo_status_classified(self, provider):
        stats = await provider.find_match_stats(datetime(2026, 9, 25), "GP AZERBAIYÁN")
        assert stats is not None
        # 4 clasificados + 2 retirados en el fixture.
        assert stats.values["Classified Cars"] == (4, 0)

    async def test_carrera_sin_terminar_devuelve_none(self, provider, monkeypatch):
        board = {"events": [dict(_SCOREBOARD["events"][0])]}
        board["events"][0]["competitions"] = [
            dict(c) for c in _SCOREBOARD["events"][0]["competitions"]
        ]
        board["events"][0]["competitions"][1]["status"] = {
            "type": {"name": "STATUS_IN_PROGRESS", "completed": False}
        }

        async def fake_scoreboard(league, period):
            return board

        monkeypatch.setattr(provider, "_scoreboard", fake_scoreboard)
        assert (
            await provider.find_match_stats(datetime(2026, 9, 25), "GP AZERBAIYÁN")
            is None
        )

    async def test_status_fallido_devuelve_none(self, provider, monkeypatch):
        """Un status que falla invalida el conteo — nunca se devuelve
        un número parcial."""
        detail = _competition_detail()

        async def fake_get_json(url, league, params):
            if url.endswith(f"/competitions/{_RACE_ID}"):
                return detail
            if "/competitors/868/status" in url:
                return None  # un piloto sin respuesta
            return _status_payload("STATUS_CLASSIFIED")

        monkeypatch.setattr(provider, "_get_json", fake_get_json)
        assert (
            await provider.find_match_stats(datetime(2026, 9, 25), "GP AZERBAIYÁN")
            is None
        )

    async def test_gp_distinto_devuelve_none(self, provider):
        assert (
            await provider.find_match_stats(datetime(2026, 9, 25), "GP de México")
            is None
        )


class TestFindMatch:
    async def test_ganador_modelado_como_1_0(self, provider):
        match = await provider.find_match(datetime(2026, 9, 25), "GP AZERBAIYÁN")
        assert match is not None
        assert match.home_team == "George Russell"
        assert match.away_team == "Max Verstappen"
        assert (match.home_score, match.away_score) == (1, 0)


class TestPostponed:
    async def test_gp_aplazado(self, provider, monkeypatch):
        board = {"events": [dict(_SCOREBOARD["events"][0])]}
        board["events"][0]["competitions"] = [
            dict(c) for c in _SCOREBOARD["events"][0]["competitions"]
        ]
        board["events"][0]["competitions"][1]["status"] = {
            "type": {"name": "STATUS_POSTPONED", "completed": False}
        }

        async def fake_scoreboard(league, period):
            return board

        monkeypatch.setattr(provider, "_scoreboard", fake_scoreboard)
        state = await provider.find_postponed_match(
            datetime(2026, 9, 25), "GP AZERBAIYÁN"
        )
        assert state is not None
        assert state.status == "postponed"


class TestVerifyPickIntegration:
    async def test_pick_menos_coches_acierto(self, provider):
        """El caso real #3550: menos de 17.5 coches, 4 clasificados."""
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Menos de 5.5 coches",
            mercado="over/under",
            evento="GP AZERBAIYÁN",
            linea=5.5,
            deporte="automovilismo",
            fecha_evento=datetime(2026, 9, 25),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 4 clasificados < 5.5 -> acierto.
        assert (acierto, anulada) == (True, False)

    async def test_pick_menos_coches_fallo(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Menos de 3.5 coches",
            mercado="over/under",
            evento="GP AZERBAIYÁN",
            linea=3.5,
            deporte="automovilismo",
            fecha_evento=datetime(2026, 9, 25),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 4 clasificados > 3.5 -> fallo.
        assert (acierto, anulada) == (False, False)
