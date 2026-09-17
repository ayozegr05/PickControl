"""Tests unitarios del verificador de resultados (funciones puras).

No llaman a ninguna API externa: cubren la extracción del equipo
predicho y la resolución del ganador a partir de un marcador.
"""

from datetime import datetime

import app.services.results.api_football as api_football
import app.services.results.football_data as football_data
from app.models.parsed_pick import ParsedPick
from app.services.results.api_football import ApiFootballProvider
from app.services.results.api_tennis import ApiTennisProvider
from app.services.results.base import MatchResult
from app.services.results.football_data import FootballDataProvider
from app.services.results.verifier import (
    _detect_over_under_direction,
    _extract_handicap_team,
    _extract_predicted_team,
    _providers_for_sport,
    _resolve_asian_handicap,
    _resolve_over_under,
    _resolve_winner,
    verify_pick,
)


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


class TestTennisMarkets:
    """En tenis el proveedor devuelve sets ganados, no juegos: solo el
    mercado "ganador" se puede resolver; hándicaps y totales de juegos
    quedan pendientes para revisión manual."""

    async def test_over_under_tenis_queda_pendiente(self):
        pick = ParsedPick(
            raw_message_id=1,
            deporte="tenis",
            mercado="más de",
            seleccion="Más de 20.5 juegos",
            linea=20.5,
            fecha_evento=datetime(2026, 9, 15, 12, 0),
        )
        # Ni siquiera con el proveedor de tenis configurado.
        acierto, anulada = await verify_pick(pick, [ApiTennisProvider("k")])
        assert acierto is None
        assert anulada is False


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
