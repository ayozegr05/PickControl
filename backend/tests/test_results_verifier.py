"""Tests unitarios del verificador de resultados (funciones puras).

No llaman a ninguna API externa: cubren la extracción del equipo
predicho y la resolución del ganador a partir de un marcador.
"""

from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import app.services.results.api_football as api_football
import app.services.results.football_data as football_data
import app.services.results.verifier as verifier_mod
from app.core.dates import utc_now
from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User
from app.services.results.api_football import ApiFootballProvider
from app.services.results.api_tennis import ApiTennisProvider
from app.services.results.base import (
    MatchEvents,
    MatchPlayers,
    MatchResult,
    MatchState,
    MatchStats,
    match_score,
)
from app.services.results.football_data import FootballDataProvider
from app.services.results.verifier import (
    _detect_over_under_direction,
    _detect_player_market,
    _extract_handicap_team,
    _extract_predicted_team,
    _providers_for_sport,
    _resolve_asian_handicap,
    _resolve_over_under,
    _resolve_winner,
    _should_attempt_verification,
    _tennis_lookup_hint,
    cascade_user_settlements,
    verify_pick,
)


@pytest.fixture(autouse=True)
def _api_football_sin_limite(monkeypatch):
    """`provider_state.json` real puede tener api-football marcado como
    sin cuota "hoy" (lo escribe el backend en marcha) y las fechas de
    test son fijas mientras la ventana del plan gratis se mueve con la
    fecha real. En tests siempre hay cuota y toda fecha es consultable,
    salvo que el propio test lo pise con otro monkeypatch."""
    monkeypatch.setattr(api_football, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(api_football, "_within_free_window", lambda day: True)


class TestExtractPredictedTeam:
    def test_extrae_equipo_de_frase_gana(self):
        assert _extract_predicted_team("Real Madrid gana") == "Real Madrid"

    def test_extrae_jugador_de_frase_gana_con_guion(self):
        assert _extract_predicted_team("Titouan Droguet - gana") == "Titouan Droguet"

    def test_no_soporta_mercados_distintos_de_ganador(self):
        assert _extract_predicted_team("Real Sociedad B Hándicap Asiático +1.5") is None

    def test_extrae_equipo_cuando_gana_va_primero(self):
        assert _extract_predicted_team("GANA REAL MADRID") == "REAL MADRID"

    def test_limpia_markdown_y_emojis(self):
        assert (
            _extract_predicted_team("**__➡️__**** Titouan Droguet gana**")
            == "Titouan Droguet"
        )

    def test_acepta_solo_nombre_equipo_si_mercado_es_resultado_sin_empate(self):
        assert (
            _extract_predicted_team("Aston Villa", "resultado sin empate")
            == "Aston Villa"
        )

    def test_no_acepta_solo_nombre_equipo_en_mercado_no_reconocido(self):
        assert _extract_predicted_team("Aston Villa", "hándicap asiático") is None


class TestProvidersForSport:
    """El enrutado por deporte evita que un pick de tenis/baloncesto se
    consulte contra APIs de fútbol (falsos positivos por similitud)."""

    def test_futbol_usa_proveedores_de_futbol(self):
        football = FootballDataProvider("k")
        tennis = ApiTennisProvider("k")
        assert _providers_for_sport("fútbol", [football, tennis]) == [football]

    def test_tenis_solo_usa_su_proveedor(self):
        football = FootballDataProvider("k")
        tennis = ApiTennisProvider("k")
        assert _providers_for_sport("tenis", [football, tennis]) == [tennis]

    def test_tenis_sin_proveedor_no_consulta_futbol(self):
        # Sin API_TENNIS_KEY configurada, un pick de tenis queda
        # pendiente: nunca debe caer en las APIs de fútbol.
        providers = [FootballDataProvider("k"), ApiFootballProvider("k", "h")]
        assert _providers_for_sport("tenis", providers) == []

    def test_baloncesto_aun_sin_proveedor(self):
        providers = [FootballDataProvider("k"), ApiTennisProvider("k")]
        assert _providers_for_sport("baloncesto", providers) == []

    def test_deporte_desconocido_no_consulta_nada(self):
        providers = [FootballDataProvider("k"), ApiTennisProvider("k")]
        assert _providers_for_sport("dardos", providers) == []

    def test_sin_deporte_prueba_todos_en_orden(self):
        providers = [FootballDataProvider("k"), ApiTennisProvider("k")]
        assert _providers_for_sport(None, providers) == providers


class _StubTennisProvider:
    """Proveedor de tenis que devuelve sets ganados en home/away_score y,
    opcionalmente, los juegos de cada set en `sets`. Filtra la pista con
    `_pair_similar` como hacen los proveedores reales (dobles incluidos)."""

    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(
        self,
        home_sets: int,
        away_sets: int,
        sets: list[tuple[int, int]] | None = None,
        status: str | None = None,
        home: str = "Carlos Alcaraz",
        away: str = "Jannik Sinner",
    ):
        self._match = MatchResult(
            home_team=home,
            away_team=away,
            home_score=home_sets,
            away_score=away_sets,
            sets=sets,
            status=status,
        )

    async def find_match(self, date, team_hint):
        from app.services.results.api_tennis import _pair_similar

        score = max(
            _pair_similar(team_hint, self._match.home_team),
            _pair_similar(team_hint, self._match.away_team),
        )
        return self._match if score >= 0.6 else None


def _pick_tenis(seleccion: str, mercado: str | None = "ganador") -> ParsedPick:
    return ParsedPick(
        raw_message_id=1,
        deporte="tenis",
        mercado=mercado,
        seleccion=seleccion,
        evento="Alcaraz - Sinner",
        fecha_evento=datetime(2026, 9, 15, 12, 0),
    )


class _StubTennisRearranged(_StubTennisProvider):
    """Tenis: además del partido jugado expone la fila Cancelled del
    fixture original (reordenación de cuadro, caso Szczecin #1715)."""

    def __init__(self, cancelled: "MatchState | None", **kw):
        super().__init__(**kw)
        self._cancelled = cancelled

    async def find_postponed_match(self, date, team_hint):
        from app.services.results.api_tennis import _pair_similar

        if self._cancelled is None:
            return None
        score = max(
            _pair_similar(team_hint, self._cancelled.home_team),
            _pair_similar(team_hint, self._cancelled.away_team),
        )
        return self._cancelled if score >= 0.6 else None


class TestTennisRearrangedFixture:
    """El fixture apostado se canceló y el cuadro se rehízo con otro
    rival: la casa anula. Una doble jornada real tendría ambos Ended —
    un Cancelled + un Ended de la misma pareja es reordenación."""

    def _cancelled(self, home: str, away: str) -> MatchState:
        return MatchState(home_team=home, away_team=away, status="cancelled")

    def _pick(self, seleccion: str) -> ParsedPick:
        # Fecha reciente: ejercita el check del camino de mercado, no la
        # regla de aplazados >72 h (que anula por su cuenta).
        pick = _pick_tenis(seleccion)
        pick.fecha_evento = datetime.now() - timedelta(hours=5)
        return pick

    async def test_fixture_reordenado_es_anulada(self):
        # Kestelboim/Romboli: apostado vs Taverna/Vega (cancelled),
        # jugado vs Barton/Sanchez (ended) -> anulada.
        provider = _StubTennisRearranged(
            cancelled=self._cancelled(
                "Kestelboim M./Romboli F.",
                "Rodriguez Taverna S./Vega Hernandez D.",
            ),
            home_sets=2,
            away_sets=1,
            sets=[(4, 6), (7, 6), (10, 8)],
            home="Kestelboim M./Romboli F.",
            away="Barton H./Sanchez Izquierdo I.",
        )
        pick = self._pick("Kestelboim/Romboli gana")
        pick.evento = "CHALLENGER SZCZECIN DOBLES"
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)

    async def test_mismo_fixture_cancelado_resuelve_normal(self):
        # La fila cancelada es del MISMO cruce que se jugó (feed
        # duplicado) -> no es reordenación, resuelve normal.
        provider = _StubTennisRearranged(
            cancelled=self._cancelled("Carlos Alcaraz", "Jannik Sinner"),
            home_sets=2,
            away_sets=0,
            sets=[(6, 4), (6, 4)],
        )
        pick = self._pick("Alcaraz gana")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_sin_cancelado_resuelve_normal(self):
        provider = _StubTennisRearranged(
            cancelled=None, home_sets=2, away_sets=0, sets=[(6, 4), (6, 4)]
        )
        pick = self._pick("Alcaraz gana")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_cancelado_de_otra_pareja_no_dispara(self):
        # El cancelado no involucra a la pareja apostada.
        provider = _StubTennisRearranged(
            cancelled=self._cancelled("Novak Djokovic", "Daniil Medvedev"),
            home_sets=2,
            away_sets=0,
            sets=[(6, 4), (6, 4)],
        )
        pick = self._pick("Alcaraz gana")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)


class TestTennisMarkets:
    """Tenis: sets exactos ("gana 2-0"), over/under y hándicap de juegos
    (con el desglose por sets del proveedor) o de sets."""

    async def test_over_under_juegos_sin_desglose_pendiente(self):
        # Sin `sets` en el resultado no hay total de juegos -> pendiente.
        provider = _StubTennisProvider(2, 0)
        pick = _pick_tenis("Más de 20.5 juegos", "más de")
        pick.linea = 20.5
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_over_under_juegos_total(self):
        # 6-4 3-6 6-2 = 33 juegos > 20.5
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Más de 20.5 juegos", "más de")
        pick.linea = 20.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_under_juegos_total_falla(self):
        # 33 juegos no es menos de 20.5
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Menos de 20.5 juegos", "menos de")
        pick.linea = 20.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_over_under_juegos_sin_sujeto_por_linea(self):
        # Sin la palabra "juegos" la línea alta desambigua a juegos.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Más de 15.5", "over")
        pick.linea = 15.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_over_under_sets_total(self):
        # 2-1 = 3 sets > 2.5
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Más de 2.5 sets", "más de")
        pick.linea = 2.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_over_under_sets_20_es_under(self):
        # 2-0 = 2 sets < 2.5
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Más de 2.5 sets", "más de")
        pick.linea = 2.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_over_under_juegos_jugador(self):
        # Alcaraz: 6+3+6 = 15 juegos > 12.5
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Alcaraz más de 12.5 juegos", "más de")
        pick.linea = 12.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_handicap_sets(self):
        # Alcaraz -1.5 sets con 2-1: 2-1.5 = 0.5 > 1? No: 2-1.5=0.5 vs 1
        # -> pierde. Con 2-0 gana.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Alcaraz -1.5 sets", "hándicap")
        pick.linea = -1.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_over_juegos_set_notacion_mas(self):
        # bet365 "+7,5 juegos en el 1° set" = over 7.5 del set 1:
        # set1 7-6 = 13 juegos -> acierto. La línea viene ya en el pick;
        # lo que falta es la dirección ("over/under" no dice lado).
        provider = _StubTennisProvider(2, 0, sets=[(7, 6), (6, 4)])
        pick = _pick_tenis("+7,5 juegos en el 1° set", "over/under juegos")
        pick.linea = 7.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_over_juegos_set_notacion_mas_falla(self):
        # set1 6-1 = 7 juegos < 7.5 -> fallo.
        provider = _StubTennisProvider(2, 0, sets=[(6, 1), (6, 4)])
        pick = _pick_tenis("+7,5 juegos en el 1° set", "over/under juegos")
        pick.linea = 7.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_mas_juegos_sin_linea_extraida(self):
        # Sin `pick.linea` la notación "+9,5" también da la línea: over 9.5.
        provider = _StubTennisProvider(2, 0, sets=[(7, 6), (6, 4)])
        pick = _pick_tenis("+9,5 juegos en el 1° set", "over/under juegos")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_mas_handicap_no_es_over(self):
        # "Alcaraz +3,5 juegos" con mercado hándicap: el "+" es el lado
        # del hándicap, no un over — no debe resolver como total.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Alcaraz +3,5 juegos", "hándicap juegos")
        pick.linea = 3.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 12 juegos - 8 = +4 > +3.5

    async def test_ganara_el_encuentro_primer_lado(self):
        # Slip bet365: la selección es el nombre del mercado y el lado
        # apostado va primero en `evento` (Alcaraz gana 2-0 -> acierto).
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Ganará el encuentro", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_ganara_el_encuentro_fallo(self):
        # Mismo caso con Alcaraz perdiendo -> fallo.
        provider = _StubTennisProvider(0, 2, sets=[(4, 6), (4, 6)])
        pick = _pick_tenis("Ganará el encuentro", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_handicap_juegos(self):
        # Alcaraz 15 juegos vs Sinner 12: -2.5 -> 12.5 > 12 gana.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Alcaraz -2.5 juegos", "hándicap")
        pick.linea = -2.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_handicap_juegos_push_anulada(self):
        # Alcaraz 15 vs Sinner 12, línea -3 entera -> push -> anulada.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Alcaraz -3 juegos", "hándicap")
        pick.linea = -3.0
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)

    async def test_handicap_sin_sujeto_linea_baja_es_sets(self):
        # Convención de casas: |línea| <= 1.5 sin sujeto -> sets.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Alcaraz -1.5", "hándicap")
        pick.linea = -1.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_handicap_sin_sujeto_linea_alta_es_juegos(self):
        # |línea| >= 3.5 sin sujeto -> juegos (15 vs 12: -4.5 pierde).
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Alcaraz -4.5", "hándicap")
        pick.linea = -4.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_handicap_sin_sujeto_zona_ambigua_pendiente(self):
        # |línea| entre 2 y 3 sin sujeto: ambiguo sets/juegos.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Alcaraz -2.5", "hándicap")
        pick.linea = -2.5
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_gana_un_set(self):
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana un set"), [provider])
        assert acierto is True

    async def test_gana_un_set_pierde_0_2(self):
        provider = _StubTennisProvider(0, 2, sets=[(4, 6), (2, 6)])
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana un set"), [provider])
        assert acierto is False

    async def test_correct_score_juegos_acierta(self):
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 2)])
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana 6-4 6-2"), [provider])
        assert acierto is True

    async def test_correct_score_juegos_falla_otro_marcador(self):
        # 6-3 en el segundo set no es 6-2.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 3)])
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana 6-4 6-2"), [provider])
        assert acierto is False

    async def test_correct_score_juegos_falla_numero_sets(self):
        # El partido fue a 3 sets: el "6-4 6-2" exacto es imposible.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana 6-4 6-2"), [provider])
        assert acierto is False

    async def test_correct_score_juegos_via_mercado(self):
        # Marcador en orden del evento "Alcaraz - Sinner".
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 2)])
        pick = _pick_tenis("Sinner", "resultado exacto 6-4 6-2")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_correct_score_sin_desglose_pendiente(self):
        provider = _StubTennisProvider(2, 0)
        acierto, anulada = await verify_pick(
            _pick_tenis("Alcaraz gana 6-4 6-2"), [provider]
        )
        assert (acierto, anulada) == (None, False)

    async def test_correct_score_retirada_anulada(self):
        provider = _StubTennisProvider(1, 0, sets=[(6, 4)], status="retired")
        acierto, anulada = await verify_pick(
            _pick_tenis("Alcaraz gana 6-4 6-2"), [provider]
        )
        assert (acierto, anulada) == (None, True)

    async def test_over_juegos_linea_plus(self):
        # "21+ juegos" = over 20.5; 6-4 6-4 = 20 juegos -> falla.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        acierto, _ = await verify_pick(
            _pick_tenis("21+ juegos", "total juegos"), [provider]
        )
        assert acierto is False

    async def test_over_juegos_o_mas(self):
        # "20 o más juegos" = over 19.5; 6-4 6-4 = 20 -> acierta.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("20 o más juegos", "total juegos")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_over_under_sin_direccion_pendiente(self):
        # Mercado "over/under juegos" + selección sin lado -> pendiente,
        # no hándicap.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        pick = _pick_tenis("Alcaraz", "over/under juegos")
        pick.linea = 12.5
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_juegos_jugador_desconocido_pendiente(self):
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Fokina más de 12.5 juegos", "más de")
        pick.linea = 12.5
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_retirada_anula_ganador(self):
        provider = _StubTennisProvider(1, 0, sets=[(6, 4)], status="retired")
        acierto, anulada = await verify_pick(_pick_tenis("Alcaraz gana"), [provider])
        assert (acierto, anulada) == (None, True)

    async def test_walkover_anula_ganador(self):
        provider = _StubTennisProvider(0, 0, status="walkover")
        acierto, anulada = await verify_pick(_pick_tenis("Alcaraz gana"), [provider])
        assert (acierto, anulada) == (None, True)

    async def test_retirada_anula_over_juegos(self):
        provider = _StubTennisProvider(1, 0, sets=[(6, 4)], status="retired")
        pick = _pick_tenis("Más de 20.5 juegos", "más de")
        pick.linea = 20.5
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)

    async def test_gana_primer_set_acierta(self):
        # 6-4 3-6 6-2: Alcaraz ganó el set 1.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(
            _pick_tenis("Alcaraz gana el primer set"), [provider]
        )
        assert acierto is True

    async def test_gana_primer_set_falla(self):
        # Sinner perdió el set 1 (4-6).
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(
            _pick_tenis("Sinner gana el primer set"), [provider]
        )
        assert acierto is False

    async def test_gana_segundo_set_visitante(self):
        # 3-6 en el set 2: Sinner (away) lo ganó.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(_pick_tenis("Sinner gana el 2º set"), [provider])
        assert acierto is True

    async def test_set_no_jugado_pendiente(self):
        # El partido acabó 2-0: el 3er set no se jugó.
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 4)])
        acierto, anulada = await verify_pick(
            _pick_tenis("Alcaraz gana el tercer set"), [provider]
        )
        assert (acierto, anulada) == (None, False)

    async def test_set_winner_via_mercado_sin_verbo(self):
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        acierto, _ = await verify_pick(_pick_tenis("Sinner", "1er set"), [provider])
        assert acierto is False  # Sinner perdió el set 1

    async def test_tiebreak_si_acierta(self):
        provider = _StubTennisProvider(2, 0, sets=[(7, 6), (6, 2)])
        acierto, _ = await verify_pick(
            _pick_tenis("Habrá tiebreak", "tiebreak"), [provider]
        )
        assert acierto is True

    async def test_tiebreak_si_falla_sin_7_6(self):
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 2)])
        acierto, _ = await verify_pick(
            _pick_tenis("Habrá tiebreak", "tiebreak"), [provider]
        )
        assert acierto is False

    async def test_no_habra_tiebreak_acierta(self):
        provider = _StubTennisProvider(2, 0, sets=[(6, 4), (6, 2)])
        acierto, _ = await verify_pick(
            _pick_tenis("No habrá tiebreak", "tiebreak"), [provider]
        )
        assert acierto is True

    async def test_tiebreak_sin_desglose_pendiente(self):
        provider = _StubTennisProvider(2, 0)
        acierto, anulada = await verify_pick(
            _pick_tenis("Habrá tiebreak", "tiebreak"), [provider]
        )
        assert (acierto, anulada) == (None, False)

    async def test_over_juegos_primer_set(self):
        # Set 1: 6-4 = 10 juegos > 9.5
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Más de 9.5 juegos en el 1er set", "más de")
        pick.linea = 9.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_over_juegos_jugador_primer_set(self):
        # Sinner solo ganó 4 juegos en el set 1 -> under 4.5 falla el over.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Sinner más de 4.5 juegos en el primer set", "más de")
        pick.linea = 4.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_handicap_juegos_primer_set(self):
        # Set 1 Alcaraz 6-4: -1.5 -> 4.5 > 4 gana.
        provider = _StubTennisProvider(2, 1, sets=[(6, 4), (3, 6), (6, 2)])
        pick = _pick_tenis("Alcaraz -1.5 juegos 1er set", "hándicap")
        pick.linea = -1.5
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_dobles_ganan(self):
        provider = _StubTennisProvider(
            2,
            0,
            sets=[(6, 4), (6, 3)],
            home="Alcaraz / Munar",
            away="Sinner / Berrettini",
        )
        pick = _pick_tenis("Alcaraz / Munar ganan")
        pick.evento = "Alcaraz / Munar - Sinner / Berrettini"
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_dobles_pierden(self):
        provider = _StubTennisProvider(
            0, 2, home="Alcaraz / Munar", away="Sinner / Berrettini"
        )
        pick = _pick_tenis("Alcaraz / Munar ganan")
        pick.evento = "Alcaraz / Munar - Sinner / Berrettini"
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_dobles_orden_invertido_pareja(self):
        # La pareja puede venir en otro orden que en la API.
        provider = _StubTennisProvider(
            2, 0, home="Alcaraz / Munar", away="Sinner / Berrettini"
        )
        pick = _pick_tenis("Munar / Alcaraz ganan")
        pick.evento = "Munar / Alcaraz - Sinner / Berrettini"
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_individual_no_casa_con_dobles(self):
        # Pick individual sobre un dobles del mismo jugador: la pista
        # no casa con la pareja -> el partido no se encuentra.
        provider = _StubTennisProvider(
            2, 0, home="Alcaraz / Munar", away="Sinner / Berrettini"
        )
        acierto, anulada = await verify_pick(_pick_tenis("Alcaraz gana"), [provider])
        assert (acierto, anulada) == (None, False)

    async def test_dobles_no_casa_con_individual(self):
        # Pick de dobles contra un partido individual del jugador.
        provider = _StubTennisProvider(2, 0)
        pick = _pick_tenis("Alcaraz / Munar ganan")
        pick.evento = "Alcaraz / Munar - Sinner / Berrettini"
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_dobles_over_juegos(self):
        provider = _StubTennisProvider(
            2,
            0,
            sets=[(6, 4), (6, 3)],
            home="Alcaraz / Munar",
            away="Sinner / Berrettini",
        )
        pick = _pick_tenis("Alcaraz / Munar más de 11.5 juegos", "más de")
        pick.linea = 11.5
        pick.evento = "Alcaraz / Munar - Sinner / Berrettini"
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 6+6 = 12 > 11.5

    async def test_gana_20_con_20_real_acierta(self):
        provider = _StubTennisProvider(2, 0)
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana 2-0"), [provider])
        assert acierto is True

    async def test_gana_20_con_21_real_es_fallo(self):
        # El bug: "gana 2-0" se verificaba como ganador a secas y un
        # 2-1 real se marcaba acierto cuando la apuesta perdió.
        provider = _StubTennisProvider(2, 1)
        acierto, anulada = await verify_pick(
            _pick_tenis("Alcaraz gana 2-0"), [provider]
        )
        assert (acierto, anulada) == (False, False)

    async def test_gana_20_perdio_es_fallo(self):
        provider = _StubTennisProvider(0, 2)
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana 2-0"), [provider])
        assert acierto is False

    async def test_gana_a_secas_sigue_siendo_ganador(self):
        provider = _StubTennisProvider(2, 1)
        acierto, _ = await verify_pick(_pick_tenis("Alcaraz gana"), [provider])
        assert acierto is True

    async def test_marcador_en_mercado_resultado_exacto(self):
        provider = _StubTennisProvider(1, 2)
        pick = _pick_tenis("Sinner", "resultado exacto 1-2")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_gana_con_marcador_jugador_desconocido_pendiente(self):
        provider = _StubTennisProvider(2, 0)
        acierto, anulada = await verify_pick(_pick_tenis("Fokina gana 2-0"), [provider])
        assert (acierto, anulada) == (None, False)


class TestTennisProviderSets:
    """Cada proveedor extrae los juegos por set en `MatchResult.sets`,
    orientados igual que home_team/away_team del resultado."""

    def test_thesportsdb_str_result_juegos_por_set(self):
        from app.services.results.api_tennis import _parse_result

        event = {
            "strResult": "Siniakova  beat Udvardy  2-0\r\n"
            "Siniakova : 6 6\n"
            "Udvardy : 1 2"
        }
        match = _parse_result(event)
        assert match is not None
        assert match.sets == [(6, 1), (6, 2)]

    def test_thesportsdb_sin_lineas_de_juegos(self):
        from app.services.results.api_tennis import _parse_result

        match = _parse_result({"strResult": "Siniakova  beat Udvardy  2-0"})
        assert match is not None
        assert match.sets is None

    def test_rapidapi_result_con_tiebreak(self):
        from app.services.results.rapidapi_tennis import _parse_set_games

        # "6-7(5)": el número entre paréntesis son puntos del tiebreak,
        # no juegos — se ignoran.
        assert _parse_set_games("6-7(5) 6-1 6-3") == [(6, 7), (6, 1), (6, 3)]

    def test_rapidapi_ret_no_parseable(self):
        from app.services.results.rapidapi_tennis import _parse_set_games

        assert _parse_set_games("6-1 2-0 RET") is None
        assert _parse_set_games("W/O") is None

    def test_tennisapi1_period_scores(self):
        from app.services.results.tennisapi1 import _parse_event

        event = {
            "status": {"type": "finished"},
            "homeTeam": {"name": "Carlos Alcaraz"},
            "awayTeam": {"name": "Jannik Sinner"},
            "homeScore": {"current": 2, "period1": 6, "period2": 3, "period3": 7},
            "awayScore": {"current": 1, "period1": 4, "period2": 6, "period3": 6},
        }
        match = _parse_event(event)
        assert match is not None
        assert match.sets == [(6, 4), (3, 6), (7, 6)]

    def test_tennisapi1_sin_periodos(self):
        from app.services.results.tennisapi1 import _parse_event

        event = {
            "status": {"type": "finished"},
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "homeScore": {"current": 2},
            "awayScore": {"current": 0},
        }
        match = _parse_event(event)
        assert match is not None
        assert match.sets is None

    def test_thesportsdb_retirada_con_marcador(self):
        # "1-0 RET" no es un 1-0 real: el partido no terminó.
        from app.services.results.api_tennis import _parse_result

        match = _parse_result({"strResult": "Sinner  beat Alcaraz  1-0 RET"})
        assert match is not None
        assert match.status == "retired"
        assert match.home_team == "Sinner"

    def test_thesportsdb_walkover(self):
        from app.services.results.api_tennis import _parse_result

        match = _parse_result({"strResult": "Sinner  beat Alcaraz  W/O"})
        assert match is not None
        assert match.status == "walkover"

    def test_rapidapi_ret_es_retirada(self):
        from app.services.results.rapidapi_tennis import _RETIREMENT_PATTERN

        assert _RETIREMENT_PATTERN.search("6-1 2-0 RET")
        assert _RETIREMENT_PATTERN.search("W/O")
        assert not _RETIREMENT_PATTERN.search("6-1 6-2")

    def test_pair_similar_dobles(self):
        from app.services.results.api_tennis import _pair_similar

        # Pareja vs pareja: todos los miembros deben casar (orden libre).
        assert _pair_similar("Alcaraz / Munar", "Carlos Alcaraz / Jaume Munar") >= 0.6
        assert _pair_similar("Munar / Alcaraz", "Carlos Alcaraz / Jaume Munar") >= 0.6
        # Individual vs pareja: no casa (falta el segundo miembro).
        assert _pair_similar("Alcaraz", "Carlos Alcaraz / Jaume Munar") < 0.6
        # Pareja vs individual: penalizado.
        assert _pair_similar("Alcaraz / Munar", "Carlos Alcaraz") < 0.6
        # Individual vs individual: sin cambios.
        assert _pair_similar("Alcaraz", "Carlos Alcaraz") >= 0.6

    def test_tennisapi1_retired_status(self):
        from app.services.results.tennisapi1 import _parse_event

        event = {
            "status": {"type": "retired"},
            "homeTeam": {"name": "Carlos Alcaraz"},
            "awayTeam": {"name": "Jannik Sinner"},
            "homeScore": {"current": 1},
            "awayScore": {"current": 0},
        }
        match = _parse_event(event)
        assert match is not None
        assert match.status == "retired"


class TestResolveWinner:
    def test_devuelve_equipo_local_si_gana(self):
        match = MatchResult(
            home_team="Real Madrid",
            away_team="Rayo Vallecano",
            home_score=2,
            away_score=0,
        )
        assert _resolve_winner(match) == "Real Madrid"

    def test_devuelve_equipo_visitante_si_gana(self):
        match = MatchResult(
            home_team="Real Madrid",
            away_team="Rayo Vallecano",
            home_score=0,
            away_score=1,
        )
        assert _resolve_winner(match) == "Rayo Vallecano"

    def test_devuelve_none_en_empate(self):
        match = MatchResult(
            home_team="Real Madrid",
            away_team="Rayo Vallecano",
            home_score=1,
            away_score=1,
        )
        assert _resolve_winner(match) is None


class TestExtractHandicapTeam:
    def test_extrae_equipo_de_seleccion_con_linea(self):
        assert (
            _extract_handicap_team("Real Sociedad B Hándicap Asiático +1.5")
            == "Real Sociedad B"
        )

    def test_extrae_equipo_con_linea_negativa(self):
        assert (
            _extract_handicap_team("Real Madrid Hándicap Asiático -1.5")
            == "Real Madrid"
        )


class TestDetectOverUnderDirection:
    def test_detecta_over(self):
        assert _detect_over_under_direction("Over 2.5 goles") == "over"

    def test_detecta_under(self):
        assert _detect_over_under_direction("Under 2.5 goles") == "under"

    def test_sin_direccion_devuelve_none(self):
        assert _detect_over_under_direction("Real Madrid gana") is None


class TestResolveAsianHandicap:
    def test_acierta_con_linea_positiva(self):
        # Real Sociedad B (visitante) pierde 0-1, pero +1.5 lo compensa.
        match = MatchResult(
            home_team="Rayo Vallecano",
            away_team="Real Sociedad B",
            home_score=1,
            away_score=0,
        )
        acierto, anulada = _resolve_asian_handicap(match, "Real Sociedad B", 1.5)
        assert acierto is True
        assert anulada is False

    def test_falla_con_linea_positiva_insuficiente(self):
        match = MatchResult(
            home_team="Rayo Vallecano",
            away_team="Real Sociedad B",
            home_score=3,
            away_score=0,
        )
        acierto, anulada = _resolve_asian_handicap(match, "Real Sociedad B", 1.5)
        assert acierto is False
        assert anulada is False

    def test_push_con_linea_entera(self):
        match = MatchResult(
            home_team="Rayo Vallecano",
            away_team="Real Sociedad B",
            home_score=2,
            away_score=1,
        )
        acierto, anulada = _resolve_asian_handicap(match, "Real Sociedad B", 1.0)
        assert acierto is None
        assert anulada is True


class TestResolveOverUnder:
    def test_over_acierta(self):
        match = MatchResult(home_team="A", away_team="B", home_score=2, away_score=1)
        acierto, anulada = _resolve_over_under(match, "over", 2.5)
        assert acierto is True
        assert anulada is False

    def test_over_falla(self):
        match = MatchResult(home_team="A", away_team="B", home_score=1, away_score=0)
        acierto, anulada = _resolve_over_under(match, "over", 2.5)
        assert acierto is False
        assert anulada is False

    def test_under_push_con_linea_entera(self):
        match = MatchResult(home_team="A", away_team="B", home_score=1, away_score=2)
        acierto, anulada = _resolve_over_under(match, "under", 3.0)
        assert acierto is None
        assert anulada is True


class TestShouldAttemptVerification:
    """Ventana de verificación: 14 días desde el evento, más un periodo
    de gracia para picks creados hace poco (recuperados tarde por el
    catch-up o un reproceso de raws antiguos)."""

    def test_dentro_de_ventana_siempre_intenta(self):
        now = datetime(2026, 9, 17, 12, 0)
        pick = _pick("Levante gana", "ganador")
        pick.fecha_evento = datetime(2026, 9, 10)  # hace 7 días
        pick.created_at = datetime(2026, 9, 10)
        assert _should_attempt_verification(pick, now) is True

    def test_fuera_de_ventana_pero_creado_ayer_si_intenta(self):
        # El catch-up recuperó un pick de agosto hoy: un solo intento
        # basta para resolverlo si football-data lo cubre.
        now = datetime(2026, 9, 17, 12, 0)
        pick = _pick("Más 1,5 Goles", "over/under goles", linea=1.5)
        pick.fecha_evento = datetime(2026, 8, 20)  # hace ~1 mes
        pick.created_at = now  # creado en esta pasada
        assert _should_attempt_verification(pick, now) is True

    def test_fuera_de_ventana_y_viejo_no_intenta(self):
        now = datetime(2026, 9, 17, 12, 0)
        pick = _pick("Menos 3,5 Goles", "over/under goles", linea=3.5)
        pick.fecha_evento = datetime(2026, 8, 18)
        pick.created_at = datetime(2026, 8, 18)  # no fue recuperado tarde
        assert _should_attempt_verification(pick, now) is False


class TestMatchScore:
    """Matching de fixtures: el hint suele ser el evento completo
    ("Alavés - Valencia") y el proveedor devuelve nombres canónicos
    ("Deportivo Alavés", "Valencia CF"). Antes se comparaba la cadena
    entera contra cada equipo y casi nunca superaba el umbral."""

    def test_evento_completo_casa_con_nombres_canonicos(self):
        score = match_score("Alavés - Valencia", "Deportivo Alavés", "Valencia CF")
        assert score >= 0.6

    def test_dos_partes_exigen_que_ambas_equipos_casen(self):
        # Solo casa "Alavés"; "Valencia" no está en el fixture.
        score = match_score("Alavés - Valencia", "Deportivo Alavés", "Sevilla FC")
        assert score < 0.6

    def test_equipo_suelto_casa_por_subconjunto(self):
        # "Alavés" ⊂ "Deportivo Alavés" aunque la similitud global
        # sea baja (~0.55 < umbral).
        assert match_score("Alavés", "Deportivo Alavés", "Valencia CF") >= 0.6

    def test_texto_sin_equipos_no_casa(self):
        score = match_score("Más 1,5 Goles", "Deportivo Alavés", "Valencia CF")
        assert score < 0.6


class _StubProvider:
    """Proveedor de fútbol que devuelve siempre el mismo marcador.

    `calls` cuenta las consultas: los mercados no resolubles con el
    marcador (córners, tarjetas, combinadas...) no deben ni llamar a
    la API — se quedan pendientes sin gastar cuota.
    """

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, match: MatchResult):
        self._match = match
        self.calls = 0

    async def find_match(self, date, team_hint):
        self.calls += 1
        return self._match


def _pick(
    seleccion: str,
    mercado: str | None,
    linea: float | None = None,
    evento: str | None = "Levante - Real Betis",
    fecha_evento: datetime | None = None,
) -> ParsedPick:
    return ParsedPick(
        raw_message_id=1,
        deporte="fútbol",
        mercado=mercado,
        seleccion=seleccion,
        linea=linea,
        evento=evento,
        fecha_evento=fecha_evento or datetime(2026, 9, 15, 21, 0),
    )


def _match(home: int, away: int) -> MatchResult:
    return MatchResult(
        home_team="Levante", away_team="Real Betis", home_score=home, away_score=away
    )


class _StubStatsProvider:
    """Proveedor con estadísticas de partido (córners, tarjetas...).

    Simula la capacidad extra de API-Football (`find_match_stats`).
    """

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, stats: MatchStats | None):
        self._stats = stats
        self.calls = 0

    async def find_match(self, date, team_hint):
        return None

    async def find_match_stats(self, date, team_hint):
        self.calls += 1
        return self._stats


def _stats(home: dict, away: dict) -> MatchStats:
    return MatchStats(
        home_team="Levante",
        away_team="Real Betis",
        values={k: (home.get(k, 0), away.get(k, 0)) for k in home.keys() | away.keys()},
    )


class TestStatOverUnder:
    """Mercados de estadísticas (córners, tarjetas, tiros...): se
    resuelven con /fixtures/statistics de API-Football, no con el
    marcador."""

    async def test_corners_total_over_acierta(self):
        stats = _stats({"Corner Kicks": 6}, {"Corner Kicks": 4})
        provider = _StubStatsProvider(stats)
        pick = _pick("Más de 8.0 corners", "over/under", linea=8.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)  # 10 > 8
        assert provider.calls == 1

    async def test_corners_total_under_falla(self):
        stats = _stats({"Corner Kicks": 6}, {"Corner Kicks": 4})
        provider = _StubStatsProvider(stats)
        pick = _pick("Menos de 8.0 corners", "over/under", linea=8.0)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False  # 10 > 8

    async def test_corners_por_equipo(self):
        stats = _stats({"Corner Kicks": 6}, {"Corner Kicks": 2})
        provider = _StubStatsProvider(stats)
        pick = _pick("Levante más de 4.5 córners", "over/under", linea=4.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # Levante sacó 6, no 8 del total

    async def test_tarjetas_suman_amarillas_y_rojas(self):
        stats = _stats(
            {"Yellow Cards": 2},
            {"Yellow Cards": 1, "Red Cards": 1},
        )
        provider = _StubStatsProvider(stats)
        pick = _pick("Más de 3 tarjetas", "over/under tarjetas", linea=3.0)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 2 + (1+1) = 4

    async def test_sujeto_sin_estadistica_queda_pendiente(self):
        provider = _StubStatsProvider(_stats({"Corner Kicks": 6}, {}))
        pick = _pick("Menos de 19.5 coches", "over/under", linea=19.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

    async def test_sin_stats_del_proveedor_queda_pendiente(self):
        # El proveedor no devolvió estadísticas (partido fuera de la
        # ventana del plan gratis, liga sin datos...).
        provider = _StubStatsProvider(None)
        pick = _pick("Más de 8.0 corners", "over/under", linea=8.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 1

    async def test_primera_parte_ni_siquiera_consulta_stats(self):
        provider = _StubStatsProvider(_stats({"Corner Kicks": 6}, {}))
        pick = _pick("Más de 4.5 corners 1ª parte", "over/under", linea=4.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0


class _StubEventsProvider:
    """Proveedor con eventos de partido (goles, tarjetas, cambios).

    `players` simula `/fixtures/players` (quién disputó minutos): solo
    se consulta cuando el jugador no aparece en ningún evento.
    """

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, events: MatchEvents | None, players: MatchPlayers | None = None):
        self._events = events
        self._players = players
        self.calls = 0
        self.players_calls = 0

    async def find_match(self, date, team_hint):
        return None

    async def find_match_events(self, date, team_hint):
        self.calls += 1
        return self._events

    async def find_match_players(self, date, team_hint):
        self.players_calls += 1
        return self._players


def _events(
    scorers: list[str],
    assisters: list[str] | None = None,
    booked: list[str] | None = None,
    participants: list[str] | None = None,
) -> MatchEvents:
    assisters = assisters or []
    booked = booked or []
    if participants is None:
        participants = scorers + assisters + booked
    return MatchEvents(
        home_team="Barcelona",
        away_team="Al Ahly Cairo",
        scorers=scorers,
        assisters=assisters,
        booked=booked,
        participants=participants,
    )


def _players(
    played: list[str], stats: dict[str, dict[str, int]] | None = None
) -> MatchPlayers:
    return MatchPlayers(
        home_team="Barcelona",
        away_team="Al Ahly Cairo",
        played=played,
        stats=stats or {},
    )


class _StubPropProvider:
    """Proveedor con estadísticas de equipo Y de jugador.

    Simula API-Football completo: `find_match_stats` para props de
    equipo (córners...) y `find_match_players` para props de jugador
    ("X más de 1.5 tiros").
    """

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(
        self,
        stats: MatchStats | None = None,
        players: MatchPlayers | None = None,
    ):
        self._stats = stats
        self._players = players
        self.players_calls = 0

    async def find_match(self, date, team_hint):
        return None

    async def find_match_stats(self, date, team_hint):
        return self._stats

    async def find_match_players(self, date, team_hint):
        self.players_calls += 1
        return self._players


class TestPlayerMarkets:
    """Mercados de jugador ("X marca", "X marca o asiste", "X recibe
    tarjeta") resueltos con los eventos del partido."""

    def test_detecta_marca_o_asiste(self):
        result = _detect_player_market("Raphinha: Jugador que Marca o Asiste", None)
        assert result == ("scorer_or_assist", "Raphinha")

    def test_detecta_marca_gol(self):
        assert _detect_player_market("Isak: Marca gol", "goleador") == (
            "scorer",
            "Isak",
        )

    def test_no_detecta_mercado_de_equipo(self):
        assert _detect_player_market("Levante gana", "ganador") is None

    async def test_jugador_marca_acierta(self):
        provider = _StubEventsProvider(_events(scorers=["Raphinha"]))
        pick = _pick("Raphinha: Jugador que Marca o Asiste", None)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_marca_o_asiste_cuenta_la_asistencia(self):
        provider = _StubEventsProvider(
            _events(scorers=["Lamine Yamal"], assisters=["Raphinha"])
        )
        pick = _pick("Raphinha marca o asiste", None)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_jugo_y_no_marco_es_fallo(self):
        # Aparece en eventos (cambio) pero no marcó ni asistió.
        provider = _StubEventsProvider(
            _events(
                scorers=["Lamine Yamal"],
                participants=["Lamine Yamal", "Raphinha"],
            )
        )
        pick = _pick("Raphinha marca gol", None)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_jugador_sin_constancia_queda_pendiente(self):
        # No aparece en ningún evento: no sabemos si jugó (la casa
        # anularía si no participó) -> pendiente, no fallo.
        provider = _StubEventsProvider(_events(scorers=["Lamine Yamal"]))
        pick = _pick("Raphinha marca gol", None)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_jugador_recibe_tarjeta(self):
        provider = _StubEventsProvider(_events(scorers=[], booked=["Iñigo Martínez"]))
        pick = _pick("Iñigo Martínez recibe tarjeta", "tarjetas jugador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_jugador_que_no_jugo_es_anulada(self):
        # No aparece en eventos y la lista de jugadores con minutos
        # confirma que no disputó el partido: la casa devuelve.
        provider = _StubEventsProvider(
            _events(scorers=["Lamine Yamal"]),
            players=_players(["Lamine Yamal", "Lewandowski", "Pedri"]),
        )
        pick = _pick("Raphinha marca gol", None)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)
        assert provider.players_calls == 1

    async def test_jugo_sin_eventos_es_fallo(self):
        # Jugó 90' sin generar ningún evento: no aparece en events pero
        # sí en la lista de jugadores con minutos -> fallo, no anulada.
        provider = _StubEventsProvider(
            _events(scorers=["Lamine Yamal"]),
            players=_players(["Lamine Yamal", "Raphinha Dias"]),
        )
        pick = _pick("Raphinha marca gol", None)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (False, False)

    async def test_sin_datos_de_jugadores_sigue_pendiente(self):
        # El endpoint de players no devolvió datos fiables: no se
        # asume que no jugó.
        provider = _StubEventsProvider(_events(scorers=["Lamine Yamal"]), players=None)
        pick = _pick("Raphinha marca gol", None)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.players_calls == 1

    async def test_no_consulta_players_si_eventos_ya_resuelven(self):
        # El jugador marcó: no hace falta la llamada extra a players.
        provider = _StubEventsProvider(
            _events(scorers=["Raphinha"]), players=_players(["Raphinha"])
        )
        pick = _pick("Raphinha marca gol", None)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True
        assert provider.players_calls == 0


class _StubPostponedProvider:
    """Proveedor que localiza partidos aplazados/cancelados."""

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, state: MatchState | None):
        self._state = state
        self.calls = 0
        self.postponed_calls = 0

    async def find_match(self, date, team_hint):
        self.calls += 1
        return None

    async def find_postponed_match(self, date, team_hint):
        self.postponed_calls += 1
        return self._state


class TestPostponedVoid:
    """Partido aplazado/cancelado fuera de la ventana de la casa
    (~72 h) -> la apuesta se devuelve (anulada), sea cual sea el
    mercado. Los parados a mitad (SUSP/ABD) no entran aquí."""

    def _postponed(self) -> MatchState:
        return MatchState(
            home_team="Levante", away_team="Real Betis", status="postponed"
        )

    async def test_aplazado_viejo_es_anulada(self):
        provider = _StubPostponedProvider(self._postponed())
        pick = _pick(
            "Levante gana",
            "ganador",
            fecha_evento=datetime(2020, 1, 1, 21, 0),
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)
        assert provider.calls == 0  # ni siquiera llega al marcador

    async def test_aplazado_reciente_sigue_pendiente(self):
        # Dentro de la ventana de reprogramación de la casa: aún puede
        # jugarse -> pendiente, ni se consulta el estado de aplazado.
        provider = _StubPostponedProvider(self._postponed())
        pick = _pick(
            "Levante gana",
            "ganador",
            fecha_evento=datetime.now() - timedelta(hours=1),
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.postponed_calls == 0

    async def test_partido_jugado_sigue_su_curso(self):
        # find_postponed no encuentra nada (el partido se disputó):
        # la verificación continúa por el camino normal.
        provider = _StubPostponedProvider(None)

        async def find_match(date, team_hint):
            return _match(2, 0)

        provider.find_match = find_match
        pick = _pick(
            "Levante gana",
            "ganador",
            fecha_evento=datetime(2020, 1, 1, 21, 0),
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_sin_evento_no_consulta_aplazados(self):
        provider = _StubPostponedProvider(self._postponed())
        pick = _pick(
            "Levante gana",
            "ganador",
            evento=None,
            fecha_evento=datetime(2020, 1, 1, 21, 0),
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.postponed_calls == 0

    async def test_tambien_anula_mercados_de_stats(self):
        # El aplazamiento aplica a cualquier mercado (córners, jugador...).
        provider = _StubPostponedProvider(self._postponed())
        pick = _pick(
            "Más de 8.0 córners",
            "over/under",
            linea=8.0,
            fecha_evento=datetime(2020, 1, 1, 21, 0),
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)


class TestQuarterHandicap:
    """Cuartos de línea asiáticos (±0.25, ±0.75): la casa parte la
    apuesta en dos medias (linea-0.25 y linea+0.5)."""

    async def test_mas_025_con_empate_es_medio_acierto(self):
        # +0.25 -> mitad a 0 (push con 1-1) + mitad a +0.5 (gana).
        provider = _StubProvider(_match(1, 1))
        pick = _pick("Levante hándicap asiático +0.25", "hándicap asiático", 0.25)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_mas_025_con_derrota_falla(self):
        provider = _StubProvider(_match(0, 1))
        pick = _pick("Levante hándicap asiático +0.25", "hándicap asiático", 0.25)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_menos_075_con_victoria_por_uno_es_medio_acierto(self):
        # -0.75 -> mitad a -0.5 (gana con 1-0) + mitad a -1.0 (push).
        provider = _StubProvider(_match(1, 0))
        pick = _pick("Levante hándicap asiático -0.75", "hándicap asiático", -0.75)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_menos_025_con_empate_es_medio_fallo(self):
        # -0.25 -> mitad a -0.5 (pierde con 1-1) + mitad a 0 (push).
        provider = _StubProvider(_match(1, 1))
        pick = _pick("Levante hándicap asiático -0.25", "hándicap asiático", -0.25)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (False, False)

    async def test_linea_entera_sigue_con_push(self):
        provider = _StubProvider(_match(1, 0))
        pick = _pick("Levante hándicap asiático -1", "hándicap asiático", -1.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)


class TestExactScore:
    """Resultado exacto: la selección es el marcador ("2-1")."""

    async def test_resultado_exacto_acierta(self):
        provider = _StubProvider(_match(2, 1))
        pick = _pick("2-1", "resultado exacto")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_resultado_exacto_falla(self):
        provider = _StubProvider(_match(1, 1))
        pick = _pick("2-1", "resultado exacto")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_scoreline_pelado_sin_mercado(self):
        provider = _StubProvider(_match(0, 0))
        pick = _pick("0-0", None)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_evento_al_reves_invierte_el_marcador(self):
        # El tipster escribió "Betis - Levante" pero el fixture es
        # Levante-Betis: su "1-2" significa Levante 2, Betis 1.
        provider = _StubProvider(_match(2, 1))
        pick = _pick("1-2", "resultado exacto", evento="Real Betis - Levante")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestPlayerProps:
    """Props de jugador con número ("Budimir más de 1.5 tiros a
    puerta"): /fixtures/players por jugador."""

    def test_detecta_prop_formato_prefijo(self):
        from app.services.results.verifier import _detect_player_prop

        result = _detect_player_prop(
            "Ante Budimir más de 1.5 tiros a puerta", None, 1.5, "Osasuna - Levante"
        )
        assert result == ("Ante Budimir", ("shots.on",), "over", 1.5)

    def test_detecta_prop_formato_sufijo(self):
        from app.services.results.verifier import _detect_player_prop

        result = _detect_player_prop(
            "2 o más tiros a portería - Ante Budimir", None, None, "Osasuna - Levante"
        )
        assert result == ("Ante Budimir", ("shots.on",), "over", 1.5)

    def test_equipo_en_lugar_de_jugador_no_es_prop(self):
        from app.services.results.verifier import _detect_player_prop

        result = _detect_player_prop(
            "Levante más de 1.5 tiros", None, 1.5, "Levante - Real Betis"
        )
        assert result is None

    async def test_prop_tiro_a_puerta_acierta(self):
        players = _players(
            played=["Ante Budimir", "Rubén García"],
            stats={
                "Ante Budimir": {"shots.total": 3, "shots.on": 2},
                "Rubén García": {"shots.total": 1, "shots.on": 0},
            },
        )
        provider = _StubPropProvider(players=players)
        pick = _pick(
            "Ante Budimir más de 1.5 tiros a puerta",
            "tiros a puerta",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_prop_falla_si_no_llega(self):
        players = _players(
            played=["Ante Budimir"],
            stats={"Ante Budimir": {"shots.total": 1, "shots.on": 0}},
        )
        provider = _StubPropProvider(players=players)
        pick = _pick(
            "Ante Budimir más de 1.5 tiros a puerta",
            "tiros a puerta",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (False, False)

    async def test_prop_jugador_no_jugo_es_anulada(self):
        players = _players(
            played=["Rubén García"], stats={"Rubén García": {"shots.total": 2}}
        )
        provider = _StubPropProvider(players=players)
        pick = _pick(
            "Ante Budimir más de 1.5 tiros",
            "tiros",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)

    def test_detecta_prop_notacion_slip(self):
        # "2+ faltas" (notación bet365) = "2 o más" = over 1.5, aunque
        # la línea del pick se parseara mal ("Romero - 2+" -> -2.0).
        from app.services.results.verifier import _detect_player_prop

        result = _detect_player_prop(
            "Ivan Romero - 2+ faltas cometidas",
            "over/under",
            -2.0,
            "Osasuna - Levante",
        )
        assert result == ("Ivan Romero", ("fouls.committed",), "over", 1.5)

    async def test_prop_faltas_notacion_slip_resuelve(self):
        players = _players(
            played=["Ivan Romero"], stats={"Ivan Romero": {"fouls.committed": 3}}
        )
        provider = _StubPropProvider(players=players)
        pick = _pick(
            "Ivan Romero - 2+ faltas cometidas",
            "over/under",
            linea=-2.0,
            evento="Osasuna - Levante",
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_prop_remates_a_puerta_vocabulario_bet365(self):
        # "remates a puerta" es el "tiros a puerta" de bet365.
        players = _players(
            played=["Ante Budimir"], stats={"Ante Budimir": {"shots.on": 2}}
        )
        provider = _StubPropProvider(players=players)
        pick = _pick(
            "Ante Budimir: 2+ remates a puerta",
            "over/under",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_prop_se_resuelve_antes_que_stat_de_equipo(self):
        # "Ivan Romero - 2+ faltas" con stats de equipo disponibles:
        # el prop gana — el total del partido no es el del jugador.
        players = _players(
            played=["Ivan Romero"], stats={"Ivan Romero": {"fouls.committed": 1}}
        )
        provider = _StubPropProvider(
            stats=_stats({"Fouls": 12}, {"Fouls": 15}),
            players=players,
        )
        pick = _pick(
            "Ivan Romero - 2+ faltas cometidas",
            "over/under",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 1 falta suya < 1.5 -> fallo; como stat de equipo sería acierto.
        assert (acierto, anulada) == (False, False)

    async def test_stat_de_equipo_sin_jugador_sigue_por_stats(self):
        # "Más de 2.5 tarjetas" sin nombre: el prop no detecta jugador
        # y se resuelve con las stats del partido como antes.
        provider = _StubPropProvider(
            stats=_stats({"Yellow Cards": 3}, {"Yellow Cards": 1}),
        )
        pick = _pick(
            "Más de 2.5 tarjetas",
            "over/under tarjetas",
            linea=2.5,
            evento="Osasuna - Levante",
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 4 > 2.5

    async def test_prop_via_fallback_over_under(self):
        # Mercado "over/under" + nombre que no casa con ningún equipo:
        # cae al fallback de prop de jugador.
        players = _players(
            played=["Ante Budimir"],
            stats={"Ante Budimir": {"shots.on": 3}},
        )
        provider = _StubPropProvider(
            stats=_stats({"Shots on Goal": 4}, {"Shots on Goal": 2}),
            players=players,
        )
        pick = _pick(
            "Ante Budimir más de 1.5",
            "over/under tiros a puerta",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 3 > 1.5 (suyos, no los 6 del partido)

    async def test_prop_sin_datos_players_queda_pendiente(self):
        provider = _StubPropProvider(players=None)
        pick = _pick(
            "Ante Budimir más de 1.5 tiros",
            "tiros",
            linea=1.5,
            evento="Osasuna - Levante",
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)


class TestDoubleChance:
    async def test_equipo_y_empate_acierta_con_empate(self):
        provider = _StubProvider(_match(1, 1))
        pick = _pick("Levante y empate", "doble oportunidad")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_equipo_y_empate_acierta_si_gana_el_equipo(self):
        provider = _StubProvider(_match(2, 0))
        pick = _pick("Levante y empate", "doble oportunidad")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_equipo_y_empate_falla_si_pierde(self):
        provider = _StubProvider(_match(0, 1))
        pick = _pick("Levante y empate", "doble oportunidad")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_1x_literal(self):
        provider = _StubProvider(_match(0, 2))
        pick = _pick("1X", "doble oportunidad")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False  # ganó el visitante

    async def test_x2_literal(self):
        provider = _StubProvider(_match(0, 2))
        pick = _pick("X2", "doble oportunidad")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # empate o visitante

    async def test_12_falla_con_empate(self):
        provider = _StubProvider(_match(2, 2))
        pick = _pick("12", "doble oportunidad")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False


class TestBtts:
    async def test_ambos_marcan_acierta(self):
        provider = _StubProvider(_match(2, 1))
        pick = _pick("Ambos equipos marcan", "ambos marcan")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_ambos_marcan_falla_con_porteria_a_cero(self):
        provider = _StubProvider(_match(2, 0))
        pick = _pick("Ambos equipos marcan", "ambos marcan")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_no_anotan_ambos_acierta_con_porteria_a_cero(self):
        provider = _StubProvider(_match(1, 0))
        pick = _pick("NO ANOTAN AMBOS EQUIPOS EN EL PARTIDO", "no anotan ambos equipos")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_no_anotan_ambos_falla_si_marcan_los_dos(self):
        provider = _StubProvider(_match(1, 1))
        pick = _pick("NO ANOTAN AMBOS EQUIPOS EN EL PARTIDO", "no anotan ambos equipos")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_formato_slip_no(self):
        provider = _StubProvider(_match(0, 0))
        pick = _pick("Ambos equipos marcan - No", "ambos marcan")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestEmpateNoValido:
    async def test_empate_anula(self):
        provider = _StubProvider(_match(1, 1))
        pick = _pick("Levante", "empate no válido")
        acierto, anulada = await verify_pick(pick, [provider])
        assert acierto is None
        assert anulada is True

    async def test_victoria_acierta(self):
        provider = _StubProvider(_match(2, 0))
        pick = _pick("Levante", "draw no bet")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestOverUnderSubjectGuards:
    """Las líneas de córners/tarjetas/etc. no se pueden resolver con el
    marcador: quedan pendientes SIN llamar a la API (antes "Menos de
    11.0 córners" se comparaba contra los goles y salía falso acierto)."""

    async def test_corners_no_se_resuelve_con_goles(self):
        provider = _StubProvider(_match(1, 0))  # 1 gol < 11 -> sería "acierto"
        pick = _pick("Menos de 11.0 corners", "over/under", linea=11.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

    async def test_tarjetas_no_se_resuelven(self):
        provider = _StubProvider(_match(0, 0))
        pick = _pick("Más de 3 tarjetas", "over/under tarjetas", linea=3.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

    async def test_linea_alta_sin_gol_es_ambigua(self):
        # "Más de 8.0" sin sujeto: casi seguro córners, no goles.
        provider = _StubProvider(_match(5, 4))
        pick = _pick("Más de 8.0", "over/under", linea=8.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

    async def test_primera_parte_sin_marcador_descanso_pendiente(self):
        # Ya se consulta al proveedor (los 1H se resuelven desde que
        # MatchResult lleva ht_*), pero sin marcador al descanso el pick
        # sigue pendiente — nunca se verifica con el marcador final.
        provider = _StubProvider(_match(3, 0))
        pick = _pick("Más de 0.5 goles 1ª parte", "over/under goles", linea=0.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 1

    async def test_goles_si_se_resuelve(self):
        provider = _StubProvider(_match(2, 1))
        pick = _pick("Más de 2.5 goles", "over/under goles", linea=2.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestTeamTotalOverUnder:
    async def test_equipo_mas_de_acierta(self):
        provider = _StubProvider(_match(2, 1))
        pick = _pick("Levante más de 1.5 goles", "over/under", linea=1.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # Levante marcó 2

    async def test_equipo_mas_de_falla(self):
        provider = _StubProvider(_match(1, 2))
        pick = _pick("Levante más de 1.5 goles", "over/under", linea=1.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False  # Levante marcó solo 1 aunque hubo 3 goles

    async def test_equipo_menos_de_acierta(self):
        provider = _StubProvider(_match(0, 3))
        pick = _pick("Levante menos de 2.5 goles", "over/under", linea=2.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestCombinadaGuard:
    """Una combinada no se puede resolver con un solo marcador: la rama
    de "ganador" solo comprobaría la primera selección."""

    async def test_combinada_queda_pendiente_sin_consultar(self):
        provider = _StubProvider(_match(2, 0))
        pick = _pick("Levante gana + Real Betis gana", "combinada")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

    async def test_acumulador_queda_pendiente(self):
        provider = _StubProvider(_match(2, 0))
        pick = _pick("Isak: Marca gol + Menos de 9.5 córners", "acumulador")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0


class _FakeResponse:
    """Respuesta httpx vacía (sin fixtures) para los tests de caché."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _CountingClient:
    """Sustituto de httpx.AsyncClient que cuenta las peticiones reales."""

    def __init__(self, calls, payload):
        self._calls = calls
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(params or {})
        return _FakeResponse(self._payload)


class TestProviderFixtureCache:
    """La caché por fecha evita repetir llamadas idénticas dentro
    de una pasada de verificación (clave para no agotar la cuota
    gratuita de API-Football, ~100 requests/día)."""

    async def test_api_football_reusa_fixtures_de_la_misma_fecha(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": []}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        await provider.find_match(datetime(2026, 9, 15), "Real Madrid")
        assert len(calls) == 3  # fecha y ±1 día

        await provider.find_match(datetime(2026, 9, 15), "Barcelona")
        assert len(calls) == 3  # segunda consulta: todo servido desde caché

    async def test_api_football_no_cachea_fechas_distintas(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": []}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        await provider.find_match(datetime(2026, 9, 15), "Real Madrid")
        await provider.find_match(datetime(2026, 9, 20), "Barcelona")
        assert len(calls) == 6  # 3 offsets por cada fecha distinta

    async def test_football_data_reusa_ventana_de_la_misma_fecha(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            football_data.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"matches": []}),
        )
        provider = FootballDataProvider("key")

        await provider.find_match(datetime(2026, 9, 15), "Real Madrid")
        await provider.find_match(datetime(2026, 9, 15), "Barcelona")
        assert len(calls) == 1  # una sola ventana ±1 día, reutilizada


def _api_fixture(
    home: str, away: str, home_goals: int, away_goals: int, status: str = "FT"
):
    return {
        "fixture": {"status": {"short": status}},
        "teams": {"home": {"name": home}, "away": {"name": away}},
        "goals": {"home": home_goals, "away": away_goals},
    }


class TestApiFootballMatchFinding:
    """Regresión del pick Alavés - Valencia: el evento del pick no
    casaba con los nombres canónicos del proveedor y quedaba pendiente
    para siempre."""

    async def test_alaves_valencia_se_resuelve_como_derrota_over15(self, monkeypatch):
        calls = []
        fixture = _api_fixture("Deportivo Alavés", "Valencia CF", 0, 1)
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": [fixture]}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        match = await provider.find_match(datetime(2026, 9, 15), "Alavés - Valencia")
        assert match is not None
        assert (match.home_score, match.away_score) == (0, 1)

        pick = _pick(
            "Más 1,5 Goles", "over/under goles", linea=1.5, evento="Alavés - Valencia"
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 0-1 = 1 gol total < 1.5 -> la apuesta pierde.
        assert (acierto, anulada) == (False, False)

    async def test_empate_entre_dos_fixtures_no_adivina(self, monkeypatch):
        # Hint ambiguo: "Madrid" casa igual con ambos equipos que
        # juegan ese día -> pendiente, no se arriesga.
        calls = []
        fixtures = [
            _api_fixture("Real Madrid", "Getafe CF", 2, 0),
            _api_fixture("Atlético de Madrid", "Villarreal CF", 1, 0),
        ]
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": fixtures}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        match = await provider.find_match(datetime(2026, 9, 15), "Madrid")
        assert match is None

    async def test_partido_no_finalizado_no_casa(self, monkeypatch):
        # Un aplazado/suspendido no es FT: queda pendiente aunque el
        # emparejamiento de nombres sea perfecto.
        calls = []
        fixture = _api_fixture("Levante UD", "Athletic Club", 0, 0, status="SUSP")
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": [fixture]}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        match = await provider.find_match(
            datetime(2026, 9, 16), "Levante - Athletic de Bilbao"
        )
        assert match is None


class _RoutingClient:
    """Como _CountingClient pero devuelve distinto payload según la URL
    (fixtures vs fixtures/statistics vs fixtures/events)."""

    def __init__(
        self,
        calls,
        fixtures_payload,
        stats_payload,
        events_payload=None,
        players_payload=None,
    ):
        self._calls = calls
        self._fixtures = fixtures_payload
        self._stats = stats_payload
        self._events = events_payload or {"response": []}
        self._players = players_payload or {"response": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(url)
        if "statistics" in url:
            return _FakeResponse(self._stats)
        if "events" in url:
            return _FakeResponse(self._events)
        if "players" in url:
            return _FakeResponse(self._players)
        return _FakeResponse(self._fixtures)


class TestApiFootballRateLimit:
    """API-Football devuelve el límite diario como HTTP 200 con
    `errors` en el JSON — hay que detectarlo y dejar de llamar."""

    async def test_request_limit_se_detecta_y_para(self, monkeypatch):
        calls = []
        limited: list[str] = []
        monkeypatch.setattr(api_football, "is_rate_limited", lambda name: bool(limited))
        monkeypatch.setattr(
            api_football,
            "mark_rate_limited",
            lambda name: limited.append(name),
        )
        payload = {
            "response": [],
            "errors": {"requests": "You have reached the request limit for the day"},
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        assert await provider.find_match(datetime(2026, 9, 16), "Levante") is None
        assert limited == ["api-football"]
        # Tras detectar la cuota agotada, los otros offsets ni se piden.
        assert len(calls) == 1

    async def test_error_de_plan_no_marca_cuota(self, monkeypatch):
        calls = []
        limited: list[str] = []
        monkeypatch.setattr(api_football, "is_rate_limited", lambda name: bool(limited))
        monkeypatch.setattr(
            api_football,
            "mark_rate_limited",
            lambda name: limited.append(name),
        )
        payload = {
            "response": [],
            "errors": {"plan": "Free plans do not have access to this date"},
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        assert await provider.find_match(datetime(2026, 9, 15), "Levante") is None
        assert limited == []  # fecha fuera de ventana no es falta de cuota


class TestApiFootballFreeWindow:
    """El plan gratis solo consulta [ayer, mañana]: fechas más viejas
    se saltan sin llamar para no quemar la cuota de ~100 req/día."""

    async def test_fecha_vieja_no_gasta_llamadas(self, monkeypatch):
        calls = []
        # Ventana real con "hoy" fijado en 2026-09-17.
        monkeypatch.setattr(
            api_football,
            "_within_free_window",
            lambda day: abs((day - date_type(2026, 9, 17)).days) <= 1,
        )
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": []}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        # 10/09: ni el día ni sus offsets ±1 entran en la ventana.
        match = await provider.find_match(datetime(2026, 9, 10), "Levante")
        assert match is None
        assert calls == []

    async def test_fecha_ayer_si_consulta(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            api_football,
            "_within_free_window",
            lambda day: abs((day - date_type(2026, 9, 17)).days) <= 1,
        )
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"response": []}),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        await provider.find_match(datetime(2026, 9, 16), "Levante")
        assert len(calls) == 2  # solo 16 y 17 están en ventana (16-18)


class TestApiFootballStats:
    """/fixtures/statistics: parsing por equipo y caché por fixture."""

    async def test_devuelve_estadisticas_por_equipo(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 123, "status": {"short": "FT"}},
            "teams": {
                "home": {"id": 10, "name": "Levante UD"},
                "away": {"id": 20, "name": "Real Betis"},
            },
            "goals": {"home": 2, "away": 1},
        }
        stats_payload = {
            "response": [
                {
                    "team": {"id": 20},
                    "statistics": [
                        {"type": "Corner Kicks", "value": 4},
                        {"type": "Yellow Cards", "value": 1},
                        {"type": "Ball Possession", "value": "55%"},
                    ],
                },
                {
                    "team": {"id": 10},
                    "statistics": [
                        {"type": "Corner Kicks", "value": 6},
                        {"type": "Yellow Cards", "value": 2},
                    ],
                },
            ]
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(calls, {"response": [fixture]}, stats_payload),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        stats = await provider.find_match_stats(
            datetime(2026, 9, 15), "Levante - Betis"
        )
        assert stats is not None
        assert stats.home_team == "Levante UD"
        # (local, visitante) aunque la API devuelva al visitante primero.
        assert stats.values["Corner Kicks"] == (6, 4)
        assert stats.values["Yellow Cards"] == (2, 1)
        # Los valores no numéricos ("55%") se descartan.
        assert "Ball Possession" not in stats.values

    async def test_stats_cacheadas_por_fixture(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 123, "status": {"short": "FT"}},
            "teams": {
                "home": {"id": 10, "name": "Levante UD"},
                "away": {"id": 20, "name": "Real Betis"},
            },
            "goals": {"home": 2, "away": 1},
        }
        stats_payload = {
            "response": [
                {
                    "team": {"id": 10},
                    "statistics": [{"type": "Corner Kicks", "value": 6}],
                }
            ]
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(calls, {"response": [fixture]}, stats_payload),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        await provider.find_match_stats(datetime(2026, 9, 15), "Levante")
        await provider.find_match_stats(datetime(2026, 9, 15), "Levante")
        stats_calls = [u for u in calls if "statistics" in u]
        assert len(stats_calls) == 1  # segunda vez servida desde caché


class TestApiFootballEvents:
    """/fixtures/events: goleadores, asistentes, tarjetas y
    participantes (para mercados de jugador)."""

    async def test_parsea_goles_asistencias_y_tarjetas(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 55, "status": {"short": "FT"}},
            "teams": {
                "home": {"id": 10, "name": "Barcelona"},
                "away": {"id": 20, "name": "Al Ahly Cairo"},
            },
            "goals": {"home": 3, "away": 0},
        }
        events_payload = {
            "response": [
                {
                    "type": "Goal",
                    "detail": "Normal Goal",
                    "player": {"name": "Raphinha"},
                    "assist": {"name": "Lamine Yamal"},
                },
                {
                    "type": "Goal",
                    "detail": "Own Goal",
                    "player": {"name": "Pau Cubarsí"},
                    "assist": {"name": None},
                },
                {
                    "type": "Goal",
                    "detail": "Missed Penalty",
                    "player": {"name": "Lewandowski"},
                    "assist": {"name": None},
                },
                {
                    "type": "Card",
                    "detail": "Yellow Card",
                    "player": {"name": "Iñigo Martínez"},
                },
                {
                    "type": "subst",
                    "detail": "Substitution 1",
                    "player": {"name": "Ferran Torres"},
                    "assist": {"name": "Ansu Fati"},
                },
            ]
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, {"response": [fixture]}, {"response": []}, events_payload
            ),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        events = await provider.find_match_events(
            datetime(2026, 9, 15), "Barcelona - Al Ahly"
        )
        assert events is not None
        # Gol normal cuenta; propia puerta y penalti fallado no.
        assert events.scorers == ["Raphinha"]
        assert events.assisters == ["Lamine Yamal"]
        assert events.booked == ["Iñigo Martínez"]
        # Todos los que aparecen en algún evento son "participantes".
        assert "Pau Cubarsí" in events.participants
        assert "Ansu Fati" in events.participants


class TestApiFootballPostponed:
    """Fixtures aplazados/cancelados: sirven para anular el pick."""

    async def test_fixture_aplazado_devuelve_match_state(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 77, "status": {"short": "PST"}},
            "teams": {
                "home": {"id": 10, "name": "Levante UD"},
                "away": {"id": 20, "name": "Athletic Club"},
            },
            "goals": {"home": None, "away": None},
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, {"response": [fixture]}, {"response": []}
            ),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        state = await provider.find_postponed_match(
            datetime(2026, 9, 16), "Levante - Athletic"
        )
        assert state is not None
        assert state.status == "postponed"
        assert state.home_team == "Levante UD"
        # Y ese mismo fixture no se ofrece como resultado finalizado.
        assert (
            await provider.find_match(datetime(2026, 9, 16), "Levante - Athletic")
            is None
        )

    async def test_fixture_suspendido_no_es_aplazado(self, monkeypatch):
        # SUSP (parado a mitad) no anula: con marcador parcial algunos
        # mercados ya están decididos y la casa los paga.
        calls = []
        fixture = {
            "fixture": {"id": 78, "status": {"short": "SUSP"}},
            "teams": {
                "home": {"id": 10, "name": "Levante UD"},
                "away": {"id": 20, "name": "Athletic Club"},
            },
            "goals": {"home": 1, "away": 0},
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, {"response": [fixture]}, {"response": []}
            ),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")
        state = await provider.find_postponed_match(
            datetime(2026, 9, 16), "Levante - Athletic"
        )
        assert state is None


class TestApiFootballPlayers:
    """/fixtures/players: quién disputó minutos (anulada si no jugó)."""

    async def test_parsea_jugadores_con_minutos(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 55, "status": {"short": "FT"}},
            "teams": {
                "home": {"id": 10, "name": "Barcelona"},
                "away": {"id": 20, "name": "Al Ahly Cairo"},
            },
            "goals": {"home": 3, "away": 0},
        }
        players_payload = {
            "response": [
                {
                    "team": {"id": 10, "name": "Barcelona"},
                    "players": [
                        {
                            "player": {"name": "Lamine Yamal"},
                            "statistics": [
                                {
                                    "games": {"minutes": 90},
                                    "shots": {"total": 4, "on": 2},
                                }
                            ],
                        },
                        {
                            # Convocado pero no entró: 0 minutos.
                            "player": {"name": "Marc Bernal"},
                            "statistics": [{"games": {"minutes": 0}}],
                        },
                        {
                            "player": {"name": "Ferran Torres"},
                            "statistics": [{"games": {"minutes": 25}}],
                        },
                    ],
                }
            ]
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls,
                {"response": [fixture]},
                {"response": []},
                players_payload=players_payload,
            ),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")

        players = await provider.find_match_players(
            datetime(2026, 9, 15), "Barcelona - Al Ahly"
        )
        assert players is not None
        assert players.played == ["Lamine Yamal", "Ferran Torres"]
        # Stats aplanadas para props de jugador.
        assert players.stats["Lamine Yamal"]["shots.on"] == 2
        assert players.stats["Lamine Yamal"]["games.minutes"] == 90

    async def test_sin_datos_devuelve_none(self, monkeypatch):
        calls = []
        fixture = {
            "fixture": {"id": 55, "status": {"short": "FT"}},
            "teams": {
                "home": {"id": 10, "name": "Barcelona"},
                "away": {"id": 20, "name": "Al Ahly Cairo"},
            },
            "goals": {"home": 3, "away": 0},
        }
        monkeypatch.setattr(
            api_football.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, {"response": [fixture]}, {"response": []}
            ),
        )
        provider = ApiFootballProvider("key", "v3.football.api-sports.io")
        players = await provider.find_match_players(
            datetime(2026, 9, 15), "Barcelona - Al Ahly"
        )
        assert players is None


class TestFootballDataPostponed:
    """football-data: POSTPONED/CANCELLED -> MatchState (con histórico,
    cubre también partidos viejos fuera de la ventana de API-Football)."""

    async def test_partido_aplazado_devuelve_match_state(self, monkeypatch):
        calls = []
        payload = {
            "matches": [
                {
                    "status": "POSTPONED",
                    "homeTeam": {"name": "Levante UD"},
                    "awayTeam": {"name": "Athletic Club"},
                    "score": {"fullTime": {"home": None, "away": None}},
                },
                {
                    "status": "FINISHED",
                    "homeTeam": {"name": "Real Madrid"},
                    "awayTeam": {"name": "Getafe CF"},
                    "score": {"fullTime": {"home": 2, "away": 0}},
                },
            ]
        }
        monkeypatch.setattr(
            football_data.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = FootballDataProvider("key")

        state = await provider.find_postponed_match(
            datetime(2026, 9, 16), "Levante - Athletic"
        )
        assert state is not None
        assert state.status == "postponed"
        assert state.home_team == "Levante UD"

    async def test_partido_jugado_no_es_aplazado(self, monkeypatch):
        calls = []
        payload = {
            "matches": [
                {
                    "status": "FINISHED",
                    "homeTeam": {"name": "Levante UD"},
                    "awayTeam": {"name": "Athletic Club"},
                    "score": {"fullTime": {"home": 1, "away": 0}},
                }
            ]
        }
        monkeypatch.setattr(
            football_data.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = FootballDataProvider("key")
        state = await provider.find_postponed_match(
            datetime(2026, 9, 16), "Levante - Athletic"
        )
        assert state is None


class TestTennisLookupHint:
    """Un `evento` que solo trae el torneo ("Tenis - Chall. Szczecin")
    es contexto para la UI pero un hint inútil para los proveedores
    (casan por nombre de jugador): se prefiere el jugador."""

    def _pick(self, evento):
        return ParsedPick(
            raw_message_id=1,
            deporte="tenis",
            mercado="ganador",
            seleccion="Cecchinato gana",
            evento=evento,
            fecha_evento=datetime(2026, 9, 17, 9, 0),
        )

    def test_torneo_usa_el_jugador(self):
        pick = self._pick("Tenis - Chall. Szczecin")
        assert _tennis_lookup_hint(pick, "Cecchinato") == "Cecchinato"

    def test_evento_vs_se_usa_entero(self):
        pick = self._pick("Marco Cecchinato vs Marvin Moeller")
        assert _tennis_lookup_hint(pick, "Cecchinato") == (
            "Marco Cecchinato vs Marvin Moeller"
        )

    def test_evento_dos_nombres_con_guion_se_usa(self):
        pick = self._pick("Chidekh - Mayot")
        assert _tennis_lookup_hint(pick, "Chidek") == "Chidekh - Mayot"

    def test_sin_evento_usa_fallback(self):
        pick = self._pick(None)
        assert _tennis_lookup_hint(pick, "Cecchinato") == "Cecchinato"

    def test_torneo_sin_fallback_devuelve_evento(self):
        pick = self._pick("ATP Cincinnati")
        assert _tennis_lookup_hint(pick, None) == "ATP Cincinnati"


def _match_ht(
    home: int,
    away: int,
    ht_home: int | None,
    ht_away: int | None,
) -> MatchResult:
    return MatchResult(
        home_team="Levante",
        away_team="Real Betis",
        home_score=home,
        away_score=away,
        ht_home_score=ht_home,
        ht_away_score=ht_away,
    )


class _StubStats1HProvider:
    """Proveedor con estadísticas de la PRIMERA parte (periodo "1ST").

    Simula `find_match_stats_1h` de footapi7: córners/tarjetas al
    descanso, independientes de las del partido completo.
    """

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, stats: MatchStats | None):
        self._stats = stats
        self.calls = 0

    async def find_match(self, date, team_hint):
        return None

    async def find_match_stats_1h(self, date, team_hint):
        self.calls += 1
        return self._stats


class TestFirstHalf:
    """Mercados de primera parte / descanso: se resuelven con el
    marcador al descanso (ht_*), nunca con el final. Sin dato HT el
    pick queda pendiente — mejor que verificarlo mal."""

    async def test_gana_primera_parte_aunque_pierda_el_partido(self):
        # FT 1-2 pero al descanso iba 1-0.
        provider = _StubProvider(_match_ht(1, 2, 1, 0))
        pick = _pick("Levante gana la 1ª parte", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_gana_primera_parte_falla_aunque_gane_el_partido(self):
        # FT 3-0 pero al descanso iba 0-1: el pick pierde.
        provider = _StubProvider(_match_ht(3, 0, 0, 1))
        pick = _pick("Levante gana la 1ª parte", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_gana_visitante_primera_parte(self):
        provider = _StubProvider(_match_ht(0, 3, 0, 1))
        pick = _pick(
            "Real Betis gana la 1ª parte",
            "ganador",
            evento="Real Betis - Levante",
        )
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_empate_al_descanso(self):
        provider = _StubProvider(_match_ht(1, 3, 1, 1))
        pick = _pick("Empate al descanso", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_empate_al_descanso_falla(self):
        provider = _StubProvider(_match_ht(1, 3, 1, 0))
        pick = _pick("Empate al descanso", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_empate_a_tiempo_completo(self):
        # Bonus: "Empate" sin marca de 1ª parte se resuelve con el
        # marcador final (antes quedaba pendiente).
        provider = _StubProvider(_match(1, 1))
        pick = _pick("Empate", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_empate_o_equipo_no_es_pick_de_empate(self):
        # "Empate o Betis" es doble oportunidad, no apuesta al empate:
        # el resolutor de empate no debe interceptarlo. Iba 1-0 al
        # descanso -> ni empate ni Betis -> la doble oportunidad falla.
        provider = _StubProvider(_match_ht(1, 0, 1, 0))
        pick = _pick("Empate o Betis", "doble oportunidad 1ª parte")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_mas_goles_primera_parte(self):
        provider = _StubProvider(_match_ht(4, 1, 2, 0))
        pick = _pick("Más de 1.5 goles 1ª parte", "over/under", linea=1.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 2 goles al descanso (no 5 del total)

    async def test_mas_goles_primera_parte_falla_con_total_mayor(self):
        provider = _StubProvider(_match_ht(4, 1, 2, 0))
        pick = _pick("Más de 3.5 goles primera parte", "over/under", linea=3.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False  # 2 al descanso aunque el partido tuvo 5

    async def test_push_goles_primera_parte(self):
        provider = _StubProvider(_match_ht(2, 2, 1, 1))
        pick = _pick("Más de 2 goles 1ª parte", "over/under", linea=2.0)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)  # 2 goles = push

    async def test_menos_goles_primera_parte(self):
        provider = _StubProvider(_match_ht(4, 2, 1, 0))
        pick = _pick("Menos de 1.5 goles 1ª parte", "over/under", linea=1.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 1 gol al descanso

    async def test_sin_marcador_descanso_queda_pendiente(self):
        # El proveedor no trajo el resultado al descanso: pendiente.
        provider = _StubProvider(_match(2, 1))
        pick = _pick("Levante gana la 1ª parte", "ganador")
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_sin_marcador_descanso_over_under_pendiente(self):
        provider = _StubProvider(_match(2, 1))
        pick = _pick("Más de 1.5 goles 1ª parte", "over/under", linea=1.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_gana_primera_parte_y_el_partido(self):
        provider = _StubProvider(_match_ht(2, 1, 1, 0))
        pick = _pick("Levante gana la 1ª parte y el partido", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_gana_primera_parte_y_el_partido_falla_si_empata(self):
        # Ganó la 1ª parte pero el partido acabó 1-1 -> falla.
        provider = _StubProvider(_match_ht(1, 1, 1, 0))
        pick = _pick("Levante gana la 1ª parte y el partido", "ganador")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is False

    async def test_corners_primera_parte_usa_stats_1h(self):
        stats = _stats({"Corner Kicks": 4}, {"Corner Kicks": 2})
        provider = _StubStats1HProvider(stats)
        pick = _pick("Más de 4.5 corners 1ª parte", "over/under", linea=4.5)
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 6 córners en la 1ª parte
        assert provider.calls == 1

    async def test_corners_primera_parte_sin_stats_1h_pendiente(self):
        provider = _StubStats1HProvider(None)
        pick = _pick("Más de 4.5 corners 1ª parte", "over/under", linea=4.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)

    async def test_doble_oportunidad_primera_parte(self):
        provider = _StubProvider(_match_ht(0, 2, 1, 1))
        pick = _pick("Levante y empate", "doble oportunidad 1ª parte")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True  # 1-1 al descanso cubre "Levante y empate"

    async def test_btts_primera_parte(self):
        provider = _StubProvider(_match_ht(3, 0, 1, 1))
        pick = _pick("Ambos equipos marcan 1ª parte", "over/under")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True

    async def test_marcador_exacto_al_descanso(self):
        provider = _StubProvider(_match_ht(2, 1, 0, 0))
        pick = _pick("0-0 al descanso", "resultado exacto")
        acierto, _ = await verify_pick(pick, [provider])
        assert acierto is True


class TestCascadeUserSettlements:
    """La cascada propaga el resultado del pick del canal a las apuestas
    de usuario enlazadas ("Yo también la jugué") que siguen pendientes."""

    async def _mk_user(self, session, email: str) -> User:
        user = User(name="u", email=email, password_hash="x")
        session.add(user)
        await session.flush()
        return user

    async def _mk_parsed(self, session, informante_id, acierto, raw_mid: int):
        raw = TelegramRawMessage(
            channel_id=1, message_id=raw_mid, channel_name="c", text="x"
        )
        session.add(raw)
        await session.flush()
        pp = ParsedPick(
            raw_message_id=raw.id,
            informante_id=informante_id,
            es_apuesta=True,
            apuesta="A gana",
            acierto=acierto,
        )
        session.add(pp)
        await session.flush()
        return pp

    def _mk_user_pick(self, user, informante_id, parsed_pick_id):
        return Pick(
            apuesta="A gana",
            tipo_de_apuesta="ganador",
            casa="Bet365",
            usuario_id=user.id,
            informante_id=informante_id,
            parsed_pick_id=parsed_pick_id,
        )

    async def test_propaga_acierto_a_enlazadas_pendientes(self, session, crear_canal):
        canal = await crear_canal("ElTipster")
        user = await self._mk_user(session, "u1@x.com")
        resuelto = await self._mk_parsed(session, canal.id, True, 1)
        apuesta = self._mk_user_pick(user, canal.id, resuelto.id)
        session.add(apuesta)
        await session.commit()

        n = await cascade_user_settlements(session)
        await session.commit()

        assert n == 1
        await session.refresh(apuesta)
        assert apuesta.acierto == Acierto.TRUE

    async def test_propaga_fallo(self, session, crear_canal):
        canal = await crear_canal("ElTipster")
        user = await self._mk_user(session, "u2@x.com")
        resuelto = await self._mk_parsed(session, canal.id, False, 2)
        apuesta = self._mk_user_pick(user, canal.id, resuelto.id)
        session.add(apuesta)
        await session.commit()

        n = await cascade_user_settlements(session)
        await session.commit()

        assert n == 1
        await session.refresh(apuesta)
        assert apuesta.acierto == Acierto.FALSE

    async def test_no_toca_pendiente_de_pick_sin_resolver(self, session, crear_canal):
        canal = await crear_canal("ElTipster")
        user = await self._mk_user(session, "u3@x.com")
        sin_resolver = await self._mk_parsed(session, canal.id, None, 3)
        apuesta = self._mk_user_pick(user, canal.id, sin_resolver.id)
        session.add(apuesta)
        await session.commit()

        n = await cascade_user_settlements(session)
        await session.commit()

        assert n == 0
        await session.refresh(apuesta)
        assert apuesta.acierto == Acierto.PENDING

    async def test_no_pisa_correccion_manual_del_usuario(self, session, crear_canal):
        canal = await crear_canal("ElTipster")
        user = await self._mk_user(session, "u4@x.com")
        resuelto = await self._mk_parsed(session, canal.id, True, 4)
        apuesta = self._mk_user_pick(user, canal.id, resuelto.id)
        apuesta.acierto = Acierto.FALSE  # el usuario la liquidó a mano
        session.add(apuesta)
        await session.commit()

        n = await cascade_user_settlements(session)
        await session.commit()

        assert n == 0
        await session.refresh(apuesta)
        assert apuesta.acierto == Acierto.FALSE

    async def test_no_toca_apuestas_sin_enlace(self, session, crear_canal):
        canal = await crear_canal("ElTipster")
        user = await self._mk_user(session, "u5@x.com")
        manual = self._mk_user_pick(user, canal.id, None)
        session.add(manual)
        await session.commit()

        n = await cascade_user_settlements(session)
        await session.commit()

        assert n == 0
        await session.refresh(manual)
        assert manual.acierto == Acierto.PENDING


def _session_cm(session):
    """`AsyncSessionLocal` falso que devuelve la sesión del test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


class TestPataFueraDeVentana:
    """Las patas de combinadas pendientes se intentan aunque superen la
    ventana de 14 días: sin ellas el padre no puede liquidar nunca."""

    async def test_pata_caducada_con_padre_pendiente_se_verifica(
        self, session, crear_canal, monkeypatch
    ):
        canal = await crear_canal("ElTipster")
        vieja = utc_now() - timedelta(days=30)
        raw = TelegramRawMessage(channel_id=1, message_id=1, channel_name="c", text="x")
        session.add(raw)
        await session.flush()
        parent = ParsedPick(
            raw_message_id=raw.id,
            informante_id=canal.id,
            es_apuesta=True,
            apuesta="combo",
            es_combinada=True,
            fecha_evento=vieja,
            created_at=vieja,
        )
        session.add(parent)
        await session.flush()
        leg = ParsedPick(
            raw_message_id=raw.id,
            informante_id=canal.id,
            es_apuesta=True,
            apuesta="A gana",
            combinada_id=parent.id,
            fecha_evento=vieja,
            created_at=vieja,
        )
        simple_viejo = ParsedPick(
            raw_message_id=raw.id,
            informante_id=canal.id,
            es_apuesta=True,
            apuesta="B gana",
            fecha_evento=vieja,
            created_at=vieja,
        )
        session.add_all([leg, simple_viejo])
        await session.commit()

        monkeypatch.setattr(verifier_mod, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            verifier_mod, "_get_providers", AsyncMock(return_value=[object()])
        )
        monkeypatch.setattr(
            verifier_mod, "verify_pick", AsyncMock(return_value=(True, False))
        )
        monkeypatch.setattr(verifier_mod, "notify_settled_picks", AsyncMock())

        await verifier_mod.verify_pending_picks()

        await session.refresh(leg)
        await session.refresh(simple_viejo)
        await session.refresh(parent)
        # la pata se intentó y resolvió pese a estar caducada…
        assert leg.acierto is True
        # …y el padre se liquidó en conjunto
        assert parent.acierto is True
        # el pick simple viejo, sin padre pendiente, ni se intentó
        assert simple_viejo.acierto is None
