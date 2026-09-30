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
from app.models.telegram_raw_message import TelegramRawMessage
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

    async def test_picks_mismo_evento_comparten_busqueda(self, session, monkeypatch):
        """Dos picks del mismo partido: UNA llamada multi resuelve a
        los dos — la búsqueda web es por evento, no por pick."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        calls: list[list[str]] = []

        async def multi(self, date, hint, descs):
            calls.append(descs)
            offers = [
                ClosingOdds(
                    market_name="Match goals",
                    choice_name="Over",
                    choice_group="2.5",
                    cuota=1.85,
                    casa="bet365",
                    source_url="https://oddspedia.com/x",
                ),
                ClosingOdds(
                    market_name="Match goals",
                    choice_name="Under",
                    choice_group="3.5",
                    cuota=2.10,
                    casa="bet365",
                    source_url="https://oddspedia.com/x",
                ),
            ]
            return {i: offers[i] for i in range(len(descs))}

        monkeypatch.setattr(GeminiResearchProvider, "find_closing_odds_multi", multi)
        p1 = self._pick(id=10)
        p2 = self._pick(id=11, seleccion="Menos de 3.5 goles", linea=3.5)
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [p1, p2], set(), set(), session, apply=True, report=report
        )

        assert len(calls) == 1  # una búsqueda para el evento
        assert len(calls[0]) == 2  # con las dos apuestas
        assert report.mapeados == 2

    async def test_eventos_distintos_busquedas_independientes(
        self, session, monkeypatch
    ):
        """Dos eventos distintos siguen gastando una pasada cada uno."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        calls: list[list[str]] = []

        async def multi(self, date, hint, descs):
            calls.append(descs)
            return {}

        monkeypatch.setattr(GeminiResearchProvider, "find_closing_odds_multi", multi)
        p1 = self._pick(id=20)
        p2 = self._pick(id=21, evento="Sevilla vs Betis")
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [p1, p2], set(), set(), session, apply=True, report=report
        )

        assert len(calls) == 2
        assert results_base.is_missed("gemini-odds|done|20")
        assert results_base.is_missed("gemini-odds|done|21")

    async def test_evento_feo_se_rescata_del_raw(self, session, monkeypatch):
        """`evento` con la apuesta en vez del cruce: el rescate lo
        repara in situ y el pick entra en la búsqueda con el evento
        bueno."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        monkeypatch.setattr(
            hb, "rescue_evento", AsyncMock(return_value="Parma vs Inter de Milán")
        )
        multi = AsyncMock(
            return_value={
                0: ClosingOdds(
                    market_name="Match goals",
                    choice_name="Over",
                    choice_group="2.5",
                    cuota=1.85,
                    casa="bet365",
                    source_url="https://oddspedia.com/x",
                )
            }
        )
        monkeypatch.setattr(GeminiResearchProvider, "find_closing_odds_multi", multi)
        pick = self._pick(id=30, evento="9,5 córners en total")
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [pick], set(), set(), session, apply=True, report=report
        )

        assert pick.evento == "Parma vs Inter de Milán"  # reparado in situ
        multi.assert_awaited_once()
        assert report.mapeados == 1

    async def test_evento_no_rescatable_marca_done(self, session, monkeypatch):
        """Ni texto ni OCR ni imagen tienen el partido: se marca done
        sin gastar ninguna búsqueda."""
        monkeypatch.setattr(hb, "get_settings", self._settings)
        monkeypatch.setattr(hb, "rescue_evento", AsyncMock(return_value=None))
        multi = AsyncMock(return_value={})
        monkeypatch.setattr(GeminiResearchProvider, "find_closing_odds_multi", multi)
        pick = self._pick(id=31, evento="premiumpay.net/register")
        report = hb.BackfillReport()
        await hb._run_gemini_pass(
            [pick], set(), set(), session, apply=True, report=report
        )

        multi.assert_not_awaited()
        assert results_base.is_missed("gemini-odds|done|31")


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


class TestQuotaLargaNoBloquea:
    """Opción B: una fuente aparcada por quota larga (reset mensual de
    la suscripción) no bloquea el drenaje ni se le llama — no es un
    miss ("no lo tenía"), es "no hay nada que reintentar hasta que
    renueve". Las marcas caducan solas si la fuente vuelve."""

    def _settings(self, google: str | None = "k"):
        return SimpleNamespace(
            google_api_key=google,
            rapidapi_tennis_key="k",
            rapidapi_oddspapi_host="h",
            rapidapi_oddsfeed_host="h",
        )

    def _pick(self, id_: int = 7) -> ParsedPick:
        return ParsedPick(
            id=id_,
            es_apuesta=True,
            deporte="fútbol",
            fecha_evento=utc_now() - timedelta(days=1),
        )

    def test_quota_larga_no_bloquea_pending(self):
        """oddsfeed ya lo intentó (nofixture) y oddspapi está aparcado
        20 días por quota mensual: el pick sale del pendiente."""
        pick = self._pick()
        results_base.mark_missed("oddsfeed|nofixture|7")
        results_base.mark_cooldown("oddspapi", utc_now() + timedelta(days=20))
        assert not hb._pick_pending(pick, set(), self._settings(google=None))

    def test_sin_quota_sigue_pendiente(self):
        """Con oddspapi viva, su deuda sí bloquea aunque oddsfeed ya
        haya marcado nofixture."""
        pick = self._pick()
        results_base.mark_missed("oddsfeed|nofixture|7")
        assert hb._pick_pending(pick, set(), self._settings(google=None))

    def test_cooldown_corto_si_bloquea(self):
        """Un cooldown de ritmo (minutos) NO es quota: la fuente sigue
        contando como viva y bloquea el drenaje."""
        pick = self._pick()
        results_base.mark_missed("oddsfeed|nofixture|7")
        results_base.mark_cooldown("oddspapi", utc_now() + timedelta(minutes=20))
        assert hb._pick_pending(pick, set(), self._settings(google=None))

    async def test_run_source_salta_fuente_aparcada(self, session):
        """Una fuente en quota larga ni se llama: cero fetch muertos."""
        results_base.mark_cooldown("oddspapi", utc_now() + timedelta(days=20))
        source = hb._OddsPapiSource(api_key="k", api_host="h")
        report = hb.BackfillReport()
        await hb._run_source(
            source, [self._pick()], set(), set(), session, None, True, report
        )
        assert any("quota agotada" in d for d in report.detalles)


@pytest.mark.asyncio
class TestBackfillBreakdown:
    """El desglose del embudo clasifica cada pick del backlog."""

    def _settings(self):
        return SimpleNamespace(
            google_api_key="k",
            rapidapi_tennis_key="k",
            rapidapi_oddspapi_host="h",
            rapidapi_oddsfeed_host="h",
        )

    async def _pick(self, session, message_id: int) -> ParsedPick:
        raw = TelegramRawMessage(
            channel_id=1,
            message_id=message_id,
            channel_name="Test",
            text="pick",
            processed=True,
        )
        session.add(raw)
        await session.flush()
        pick = ParsedPick(
            raw_message_id=raw.id,
            es_apuesta=True,
            deporte="fútbol",
            fecha_evento=utc_now() - timedelta(days=2),
        )
        session.add(pick)
        await session.flush()
        return pick

    async def test_clasifica_en_cola_aplazado_agotado_resuelto(
        self, session, monkeypatch
    ):
        monkeypatch.setattr(hb, "get_settings", self._settings)
        results_base.mark_cooldown("oddspapi", utc_now() + timedelta(days=20))

        await self._pick(session, 1)  # nadie lo intentó aún -> en cola
        aplazado = await self._pick(session, 2)  # solo le debe oddspapi
        results_base.mark_missed(f"oddsfeed|nofixture|{aplazado.id}")
        results_base.mark_missed(f"gemini-odds|done|{aplazado.id}")
        agotado = await self._pick(session, 3)  # todos intentaron
        for src in ("oddsfeed", "oddspapi"):
            results_base.mark_missed(f"{src}|nofixture|{agotado.id}")
        results_base.mark_missed(f"gemini-odds|done|{agotado.id}")
        resuelto = await self._pick(session, 4)  # ya tiene cuotas
        resuelto.odds_event_id = "oddsfeed:99"
        session.add(
            OddsSnapshot(
                provider="oddsfeed",
                event_ext_id="oddsfeed:99",
                market_name="Match goals",
                choice_name="Over",
                choice_group="2.5",
                cuota=1.9,
                captured_at=utc_now(),
            )
        )
        await session.commit()

        bd = await hb.backfill_breakdown(session)
        assert bd["total"] == 4
        assert bd["resueltos"] == 1
        assert bd["en_cola"] == 1
        assert bd["aplazados"] == 1
        assert bd["agotados"] == 1
        # En cola solo le debe oddsfeed (oddspapi está aparcada).
        assert bd["por_fuente"] == {"oddspapi": 0, "oddsfeed": 1}
        assert bd["gemini_pendientes"] == 1
        assert "oddspapi" in bd["fuentes_en_pausa"]
        # El contador del job diario solo mide trabajo real (en_cola).
        assert await hb.count_pending_backfill(session) == 1
