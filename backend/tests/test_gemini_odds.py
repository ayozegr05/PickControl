"""Tests del fallback de cuotas de cierre vía Gemini (find_closing_odds
+ la pasada `gemini-odds` del backfill histórico).

Sin HTTP real: `_search_urls`/`_fetch_page_text`/`_ask` se mockean y la
validación clave (literal de la cuota en el HTML + autocheck con
`map_pick_choices`) se ejercita de verdad.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.core.dates import utc_now
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds import historical_backfill as hb
from app.services.results import base as results_base
from app.services.results.gemini_research import (
    ClosingOdds,
    GeminiResearchProvider,
)

_ODDS_PAGE = (
    "Parma vs Inter de Milán · Comparador de cuotas · "
    "Más/Menos 2.5 goles: Más 2.5 a 1.85 en bet365, Menos 2.5 a 2.05 "
    "en Stake. 1X2: Parma 10.00, Empate 5.40, Inter 1.36."
)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """provider_state.json aislado por test (misses/cooldowns)."""
    monkeypatch.setattr(results_base, "_STATE", None)
    monkeypatch.setattr(results_base, "_STATE_FILE", tmp_path / "provider_state.json")


def _provider() -> GeminiResearchProvider:
    return GeminiResearchProvider("futbol", api_key="test-key")


class TestOddsLiteralOk:
    def test_decimal_litera_en_pagina(self):
        assert GeminiResearchProvider._odds_literal_ok(_ODDS_PAGE, "1.85", 1.85)

    def test_decimal_con_coma(self):
        text = "cuota 2,05 en bet365"
        assert GeminiResearchProvider._odds_literal_ok(text, "2,05", 2.05)

    def test_fraccion_uk_coherente(self):
        text = "England boosted from 4/6 to evens"
        # 4/6 -> 1.67 decimal.
        assert GeminiResearchProvider._odds_literal_ok(text, "4/6", 4 / 6 + 1)

    def test_fraccion_incoherente_rechaza(self):
        text = "cuota 4/6 en la casa"
        # 4/6 = 1.67, no 2.50 — el decimal no cuadra con el literal.
        assert not GeminiResearchProvider._odds_literal_ok(text, "4/6", 2.50)

    def test_literal_ausente_rechaza(self):
        assert not GeminiResearchProvider._odds_literal_ok(_ODDS_PAGE, "9.99", 9.99)

    def test_literal_dentro_de_otro_numero_rechaza(self):
        # "1.85" no vale si solo aparece dentro de "21.85".
        text = "la cuota era 21.85"
        assert not GeminiResearchProvider._odds_literal_ok(text, "1.85", 1.85)


class TestClosingFromText:
    def test_happy_path(self):
        data = {
            "market_name": "Match goals",
            "choice_name": "Over",
            "choice_group": "2.5",
            "cuota_decimal": 1.85,
            "cuota_texto": "1.85",
            "casa": "bet365",
        }
        found = _provider()._closing_from_text(_ODDS_PAGE, data, "http://x")
        assert found is not None
        assert found.market_name == "Match goals"
        assert found.cuota == 1.85
        assert found.casa == "bet365"
        assert found.source_url == "http://x"

    def test_mercado_fuera_de_vocabulario(self):
        data = {
            "market_name": "Player props",
            "choice_name": "Over",
            "choice_group": "2.5",
            "cuota_decimal": 1.85,
            "cuota_texto": "1.85",
        }
        assert _provider()._closing_from_text(_ODDS_PAGE, data, "u") is None

    def test_cuota_no_litera(self):
        data = {
            "market_name": "Match goals",
            "choice_name": "Over",
            "choice_group": "2.5",
            "cuota_decimal": 7.77,
            "cuota_texto": "7.77",
        }
        assert _provider()._closing_from_text(_ODDS_PAGE, data, "u") is None

    def test_cuota_fuera_de_rango(self):
        data = {
            "market_name": "Match goals",
            "choice_name": "Over",
            "choice_group": "2.5",
            "cuota_decimal": 0.5,
            "cuota_texto": "1.85",
        }
        assert _provider()._closing_from_text(_ODDS_PAGE, data, "u") is None


class TestFindClosingOdds:
    async def test_flujo_completo(self, monkeypatch):
        provider = _provider()
        monkeypatch.setattr(
            provider,
            "_search_urls",
            AsyncMock(return_value=["https://oddspedia.com/es/futbol/x"]),
        )
        monkeypatch.setattr(
            provider, "_fetch_page_text", AsyncMock(return_value=_ODDS_PAGE)
        )
        import json as _json

        monkeypatch.setattr(
            provider,
            "_ask",
            AsyncMock(
                return_value={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": _json.dumps(
                                            {
                                                "market_name": "Match goals",
                                                "choice_name": "Over",
                                                "choice_group": "2.5",
                                                "cuota_decimal": 1.85,
                                                "cuota_texto": "1.85",
                                                "casa": "bet365",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
        )
        found = await provider.find_closing_odds(
            utc_now() - timedelta(days=1),
            "Parma vs Inter de Milán",
            "más de 2.5 goles",
        )
        assert found is not None
        assert found.choice_name == "Over"
        assert found.source_url.endswith("/x")

    async def test_sin_urls_devuelve_none(self, monkeypatch):
        provider = _provider()
        monkeypatch.setattr(provider, "_search_urls", AsyncMock(return_value=[]))
        found = await provider.find_closing_odds(utc_now(), "A vs B", "gana A")
        assert found is None

    async def test_sin_desc_devuelve_none(self):
        provider = _provider()
        assert await provider.find_closing_odds(utc_now(), "A vs B", "") is None


class TestGeminiBackfillPass:
    def _pick(self, **kwargs) -> ParsedPick:
        kwargs.setdefault("id", 42)
        kwargs.setdefault("raw_message_id", 1)
        kwargs.setdefault("es_apuesta", True)
        kwargs.setdefault("es_combinada", False)
        kwargs.setdefault("deporte", "fútbol")
        kwargs.setdefault("evento", "Parma vs Inter de Milán")
        kwargs.setdefault("mercado", "más/menos goles")
        kwargs.setdefault("seleccion", "Más de 2.5 goles")
        kwargs.setdefault("linea", 2.5)
        kwargs.setdefault("cuota", 1.90)
        kwargs.setdefault("fecha_evento", utc_now() - timedelta(days=2))
        return ParsedPick(**kwargs)

    def _settings(self):
        return SimpleNamespace(
            google_api_key="test-key",
            rapidapi_tennis_key=None,
        )

    async def test_escribe_snapshot_validado(self, session, monkeypatch):
        monkeypatch.setattr(hb, "get_settings", self._settings)
        monkeypatch.setattr(
            GeminiResearchProvider,
            "find_closing_odds",
            AsyncMock(
                return_value=ClosingOdds(
                    market_name="Match goals",
                    choice_name="Over",
                    choice_group="2.5",
                    cuota=1.85,
                    casa="bet365",
                    source_url="https://oddspedia.com/x",
                )
            ),
        )
        pick = self._pick()
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [pick], set(), set(), session, apply=True, report=report
        )
        await session.commit()

        assert report.mapeados == 1
        assert pick.odds_event_id == "gemini-odds:42"
        rows = (
            await session.exec(
                select(OddsSnapshot).where(OddsSnapshot.provider == "gemini-odds")
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].market_name == "Match goals"
        assert rows[0].cuota == 1.85
        assert rows[0].captured_at == pick.fecha_evento

    async def test_propuesta_incoherente_se_descarta(self, session, monkeypatch):
        """El modelo propone Full time/1 para un pick de Over: el
        autocheck con map_pick_choices lo rechaza y no se escribe."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        monkeypatch.setattr(
            GeminiResearchProvider,
            "find_closing_odds",
            AsyncMock(
                return_value=ClosingOdds(
                    market_name="Full time",
                    choice_name="1",
                    choice_group=None,
                    cuota=1.36,
                    casa="Stake",
                    source_url="https://oddspedia.com/x",
                )
            ),
        )
        pick = self._pick()
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [pick], set(), set(), session, apply=True, report=report
        )

        assert report.mapeados == 0
        assert pick.odds_event_id is None
        # Marcada como procesada: no se reintenta en cada pasada.
        assert results_base.is_missed(f"gemini-odds|done|{pick.id}")

    async def test_cubierto_por_filas_existentes_no_llama(self, session, monkeypatch):
        """Si el evento ya tiene la opción casada por otro provider,
        la pasada marca el pick procesado sin llamar al modelo."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        finder = AsyncMock(return_value=None)
        monkeypatch.setattr(GeminiResearchProvider, "find_closing_odds", finder)
        pick = self._pick(odds_event_id="oddspapi:7")
        session.add(
            OddsEvent(
                event_ext_id="oddspapi:7",
                sport="futbol",
                home_team="Parma",
                away_team="Inter de Milán",
                start=pick.fecha_evento,
            )
        )
        session.add(
            OddsSnapshot(
                provider="oddspapi",
                event_ext_id="oddspapi:7",
                market_name="Match goals",
                choice_name="Over",
                choice_group="2.5",
                cuota=1.9,
                captured_at=pick.fecha_evento,
            )
        )
        await session.commit()

        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [pick], {"oddspapi:7"}, set(), session, apply=True, report=report
        )
        assert finder.await_count == 0
        assert results_base.is_missed("gemini-odds|done|42")


class TestPickPendingGemini:
    """La marca `gemini-odds|done` entra en el conteo de pendientes."""

    def _pick(self) -> ParsedPick:
        return ParsedPick(
            id=7,
            es_apuesta=True,
            deporte="fútbol",
            fecha_evento=utc_now() - timedelta(days=1),
        )

    def test_sin_google_key_no_cambia_conteo(self):
        pick = self._pick()
        settings = SimpleNamespace(google_api_key=None, rapidapi_tennis_key=None)
        assert not hb._pick_pending(pick, set(), settings)

    def test_con_google_key_queda_pendiente(self):
        pick = self._pick()
        settings = SimpleNamespace(google_api_key="k", rapidapi_tennis_key=None)
        assert hb._pick_pending(pick, set(), settings)

    def test_marcado_done_no_pendiente(self):
        pick = self._pick()
        settings = SimpleNamespace(google_api_key="k", rapidapi_tennis_key=None)
        results_base.mark_missed("gemini-odds|done|7")
        assert not hb._pick_pending(pick, set(), settings)
