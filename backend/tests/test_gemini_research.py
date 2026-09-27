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


def _stub_page(text: str = "contenido de la página"):
    async def fake(url):
        return text

    return fake


@pytest.fixture
def provider():
    return GeminiResearchProvider("tenis", "k")


class TestSearchUrls:
    """Salto 1: Bing parseado por nosotros, filtro allowlist."""

    async def test_urls_no_fiables_se_filtran(self, provider, monkeypatch):
        import base64

        sofa_b64 = base64.urlsafe_b64encode(_SOFA_URL.encode()).decode().rstrip("=")
        html = (
            f'<a href="{_TENNIS_URL}">r</a>'
            f'<a href="https://www.bing.com/ck/a?u=a1{sofa_b64}">r</a>'
            '<a href="https://blog-random.io/pick">r</a>'
            '<a href="https://www.bing.com/search?q=x">self</a>'
            "javascript:void(0)"
        )
        monkeypatch.setattr(provider, "_fetch_html", _stub(html))
        urls = await provider._search_urls(_DATE, "Kestelboim/Romboli")
        assert urls == [_TENNIS_URL, _SOFA_URL]

    async def test_error_fetch_devuelve_vacio(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_fetch_html", _stub(None))
        assert await provider._search_urls(_DATE, "x") == []


class TestFindPostponedMatch:
    @pytest.fixture(autouse=True)
    def _pages(self, provider, monkeypatch):
        # La verificación descarga las páginas: texto cualquiera vale.
        monkeypatch.setattr(provider, "_fetch_page_text", _stub_page())

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


def _stub_seq(payloads: list):
    queue = list(payloads)

    async def fake(*a, **k):
        return queue.pop(0) if queue else None

    return fake


def _played_answer(home: int, away: int, sets=None) -> str:
    return json.dumps(
        {
            "status": "played",
            "home_team": "Kestelboim M./Romboli F.",
            "away_team": "Barton H./Sanchez Izquierdo N.",
            "home_score": home,
            "away_score": away,
            "sets": sets,
            "sources": [_SOFA_URL],
        }
    )


class TestFindMatch:
    @pytest.fixture(autouse=True)
    def _pages(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_fetch_page_text", _stub_page())

    async def test_played_con_doble_lectura_liquida(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_SOFA_URL]))
        payload = _payload(_played_answer(2, 1, sets=[[6, 4], [3, 6], [7, 5]]))
        monkeypatch.setattr(provider, "_ask", _stub(payload))
        result = await provider.find_match(_DATE, "Kestelboim/Romboli")
        assert result is not None
        assert (result.home_score, result.away_score) == (2, 1)
        assert result.sets == [(6, 4), (3, 6), (7, 5)]

    async def test_lecturas_discrepantes_descartan(self, provider, monkeypatch):
        # Anti-alucinación Fase 2: dos lecturas que no coinciden -> None.
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_SOFA_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub_seq(
                [
                    _payload(_played_answer(2, 1)),
                    _payload(_played_answer(1, 2)),
                ]
            ),
        )
        assert await provider.find_match(_DATE, "Kestelboim/Romboli") is None

    async def test_played_sin_marcador_no_liquida(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_SOFA_URL]))
        text = json.dumps(
            {
                "status": "played",
                "home_team": "A",
                "away_team": "B",
                "home_score": None,
                "away_score": None,
                "sources": [_SOFA_URL],
            }
        )
        monkeypatch.setattr(provider, "_ask", _stub(_payload(text)))
        assert await provider.find_match(_DATE, "x") is None

    async def test_played_sin_fuente_leida_no_liquida(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_SOFA_URL]))
        text = json.dumps(
            {
                "status": "played",
                "home_team": "A",
                "away_team": "B",
                "home_score": 3,
                "away_score": 1,
                "sources": ["https://blog-inventado.io/x"],
            }
        )
        monkeypatch.setattr(provider, "_ask", _stub(_payload(text)))
        assert await provider.find_match(_DATE, "x") is None

    async def test_unknown_no_liquida(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_SOFA_URL]))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_answer("unknown", [_SOFA_URL]))),
        )
        assert await provider.find_match(_DATE, "x") is None

    async def test_sin_urls_no_consulta(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([]))

        async def no_debe_llamarse(*a, **k):
            raise AssertionError("_ask no debería llamarse sin URLs")

        monkeypatch.setattr(provider, "_ask", no_debe_llamarse)
        assert await provider.find_match(_DATE, "x") is None


_PAGE = (
    "Invest in Szczecin Open Kestelboim Romboli Rodriguez Taverna "
    "Vega Hernandez Completed Match Statistics Aces 14 12 "
    "Double Faults 3 1 Winners 30 25"
)


def _stats_answer(stats: dict) -> str:
    return json.dumps(
        {
            "team1": "Kestelboim/Romboli",
            "team2": "Taverna/Vega",
            "stats": stats,
        }
    )


class TestFindMatchStats:
    """Fase 3: fetch propio + extracción IA + validación contra el
    texto real (un número alucinado no aparece en el HTML -> fuera)."""

    async def test_stats_verificadas_devuelven_matchstats(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_fetch_page_text", _stub(_PAGE))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_stats_answer({"Aces": [14, 12], "Double Faults": [3, 1]}))),
        )
        stats = await provider.find_match_stats(_DATE, "Kestelboim/Romboli")
        assert stats is not None
        assert stats.values["Aces"] == (14, 12)
        assert stats.values["Double Faults"] == (3, 1)

    async def test_stat_alucinada_se_descarta(self, provider, monkeypatch):
        # El modelo reporta valores que NO están en el texto.
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_fetch_page_text", _stub(_PAGE))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_stats_answer({"Aces": [99, 88]}))),
        )
        assert await provider.find_match_stats(_DATE, "x") is None

    async def test_stat_desconocida_se_ignora(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_fetch_page_text", _stub(_PAGE))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(
                _payload(_stats_answer({"Aces": [14, 12], "Rarezas Varias": [7, 7]}))
            ),
        )
        stats = await provider.find_match_stats(_DATE, "x")
        assert stats is not None
        assert set(stats.values) == {"Aces"}

    async def test_sin_stats_en_texto_devuelve_none(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_fetch_page_text", _stub("página sin tabla"))
        monkeypatch.setattr(provider, "_ask", _stub(_payload(_stats_answer({}))))
        assert await provider.find_match_stats(_DATE, "x") is None

    async def test_fetch_fallido_pasa_a_siguiente_url(self, provider, monkeypatch):
        monkeypatch.setattr(
            provider, "_search_urls", _stub_search([_SOFA_URL, _TENNIS_URL])
        )
        pages = iter([None, _PAGE])
        monkeypatch.setattr(
            provider,
            "_fetch_page_text",
            _stub_seq(list(pages)),
        )
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_stats_answer({"Aces": [14, 12]}))),
        )
        stats = await provider.find_match_stats(_DATE, "x")
        assert stats is not None
        assert stats.values["Aces"] == (14, 12)
