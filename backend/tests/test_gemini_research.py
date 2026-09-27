"""GeminiResearchProvider: investigador de último recurso.

Pipeline de dos saltos vía url_context: (1) DDG Lite -> URLs, filtradas
por allowlist; (2) el modelo lee SOLO esas páginas y reporta el estado.
Fase 1: solo estados "no jugado" (cancelled/postponed/walkover) y SOLO
citando una de las URLs fiables leídas — sin esa cita la respuesta se
descarta aunque el modelo jure que está cancelado.
"""

import datetime
import json

import pytest

from app.services.results.base import MatchState
from app.services.results.gemini_research import GeminiResearchProvider

pytestmark = pytest.mark.anyio

_DATE = datetime.datetime(2026, 9, 16)
_TENNIS_URL = "https://www.tennis.com/tournaments/x/matches/abc"
_SOFA_URL = "https://www.sofascore.com/tennis/match/x/y"


def _payload(text: str, read_urls: list[str] | None = None) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "urlContextMetadata": {
                    "urlMetadata": [{"retrievedUrl": u} for u in (read_urls or [])]
                },
            }
        ]
    }


def _answer(status: str, sources: list[str] | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "home_team": "Kestelboim M./Romboli F.",
            "away_team": "Rodriguez Taverna S./Vega Hernandez D.",
            "sources": sources or [],
        }
    )


def _stub(payload):
    async def fake(*a, **k):
        return payload

    return fake


def _stub_search(urls: list[str]):
    async def fake(date, hint):
        return urls

    return fake


@pytest.fixture
def provider():
    return GeminiResearchProvider("tenis", "k")


class TestSearchUrls:
    async def test_urls_no_fiables_se_filtran(self, provider, monkeypatch):
        text = json.dumps(
            {
                "urls": [
                    _TENNIS_URL,
                    "https://blog-random.io/pick",
                    "javascript:alert(1)",
                ]
            }
        )
        monkeypatch.setattr(provider, "_ask", _stub(_payload(text)))
        urls = await provider._search_urls(_DATE, "Kestelboim/Romboli")
        assert urls == [_TENNIS_URL]

    async def test_error_api_devuelve_vacio(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_ask", _stub(None))
        assert await provider._search_urls(_DATE, "x") == []


class TestFindPostponedMatch:
    async def test_cancelado_con_fuente_fiable(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_answer("cancelled", [_TENNIS_URL]))),
        )
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert isinstance(state, MatchState)
        assert state.status == "cancelled"
        assert "Taverna" in state.away_team

    async def test_cancelado_citando_url_no_leida_descartado(
        self, provider, monkeypatch
    ):
        # Anti-alucinación: la fuente debe ser una página que le dimos.
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_answer("cancelled", ["https://otra-web.io/x"]))),
        )
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is None

    async def test_cancelado_sin_fuentes_descartado(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_ask", _stub(_payload(_answer("cancelled"))))
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is None

    async def test_cita_via_url_context_metadata(self, provider, monkeypatch):
        # La fuente puede venir del metadata aunque el JSON la omita
        # de la lista de leídas — si el modelo la cita y estaba leída, vale.
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(
                _payload(
                    _answer("postponed", [_SOFA_URL]),
                    read_urls=[_SOFA_URL],
                )
            ),
        )
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert isinstance(state, MatchState)
        assert state.status == "postponed"

    async def test_sin_urls_de_busqueda_no_verifica(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([]))

        async def no_debe_llamarse(*a, **k):
            raise AssertionError("_ask no debería llamarse sin URLs")

        monkeypatch.setattr(provider, "_ask", no_debe_llamarse)
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is None

    async def test_played_no_devuelve_estado(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_answer("played", [_TENNIS_URL]))),
        )
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is None

    async def test_walkover_normaliza_a_cancelled(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_answer("walkover", [_TENNIS_URL]))),
        )
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is not None
        assert state.status == "cancelled"

    async def test_error_api_devuelve_none(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_ask", _stub(None))
        state = await provider.find_postponed_match(_DATE, "Kestelboim/Romboli")
        assert state is None

    async def test_find_match_nunca_liquida(self, provider):
        # Fase 1: sin resolución de marcadores, siempre None.
        assert await provider.find_match(_DATE, "x") is None
