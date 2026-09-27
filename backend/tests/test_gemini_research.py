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

from app.core.config import get_settings
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
    """Salto 1: el modelo lee DDG vía url_context, filtro allowlist."""

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

    async def test_busqueda_usa_url_context(self, provider, monkeypatch):
        calls = []

        async def spy(prompt, use_tool=False):
            calls.append(use_tool)
            return None

        monkeypatch.setattr(provider, "_ask", spy)
        await provider._search_urls(_DATE, "x")
        # Sin resultados prueba la segunda query; todas con tool.
        assert calls and all(c is True for c in calls)


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

    async def test_layout_valor_antes_del_nombre(self, provider, monkeypatch):
        # tennis.com: "4 Aces 16" (v1 nombre v2), no "Aces v1 v2".
        page = "Vacherot Harris Completed Match Statistics 4 Aces 16 2 Double Faults 2"
        monkeypatch.setattr(provider, "_search_urls", _stub_search([_TENNIS_URL]))
        monkeypatch.setattr(provider, "_fetch_page_text", _stub(page))
        monkeypatch.setattr(
            provider,
            "_ask",
            _stub(_payload(_stats_answer({"Aces": [4, 16], "Double Faults": [2, 2]}))),
        )
        stats = await provider.find_match_stats(_DATE, "x")
        assert stats is not None
        assert stats.values["Aces"] == (4, 16)
        assert stats.values["Double Faults"] == (2, 2)

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


class TestFlareSolverrFallback:
    """Cuando el fetch directo choca con Cloudflare (403 o challenge
    HTML) se reintenta vía el Chromium de la VM."""

    async def test_fetch_directo_ok_no_llama_flaresolverr(self, provider, monkeypatch):
        monkeypatch.setattr(
            provider, "_fetch_via_flaresolverr", _stub("<html>flare</html>")
        )

        class _Resp:
            status_code = 200
            text = "<html>contenido real</html>"

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp()

        monkeypatch.setattr(
            "app.services.results.gemini_research.httpx.AsyncClient",
            lambda **k: _Client(),
        )
        assert (
            await provider._fetch_html("https://x.com") == "<html>contenido real</html>"
        )

    async def test_403_cae_a_flaresolverr(self, provider, monkeypatch):
        monkeypatch.setattr(get_settings(), "flaresolverr_url", "http://flare:8191")
        monkeypatch.setattr(
            provider, "_fetch_via_flaresolverr", _stub("<html>flare</html>")
        )

        class _Resp:
            status_code = 403
            text = "challenge"

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp()

        monkeypatch.setattr(
            "app.services.results.gemini_research.httpx.AsyncClient",
            lambda **k: _Client(),
        )
        assert await provider._fetch_html("https://x.com") == "<html>flare</html>"

    async def test_challenge_en_200_cae_a_flaresolverr(self, provider, monkeypatch):
        # Cloudflare a veces devuelve 200 con la página del challenge.
        monkeypatch.setattr(
            provider, "_fetch_via_flaresolverr", _stub("<html>flare</html>")
        )

        class _Resp:
            status_code = 200
            text = 'window._cf_chl_opt={};fa:"__cf_chl_f_tk=abc"'

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp()

        monkeypatch.setattr(
            "app.services.results.gemini_research.httpx.AsyncClient",
            lambda **k: _Client(),
        )
        assert await provider._fetch_html("https://x.com") == "<html>flare</html>"

    async def test_sin_url_devuelve_none(self, provider, monkeypatch):
        monkeypatch.setattr(get_settings(), "flaresolverr_url", None)
        assert await provider._fetch_via_flaresolverr("https://x.com") is None
