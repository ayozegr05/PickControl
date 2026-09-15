"""Tests unitarios del verificador de resultados (funciones puras).

No llaman a ninguna API externa: cubren la extracción del equipo
predicho y la resolución del ganador a partir de un marcador.
"""

from app.services.results.base import MatchResult
from app.services.results.verifier import (
    _detect_over_under_direction,
    _extract_handicap_team,
    _extract_predicted_team,
    _resolve_asian_handicap,
    _resolve_over_under,
    _resolve_winner,
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
