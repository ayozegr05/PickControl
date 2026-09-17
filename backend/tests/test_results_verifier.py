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
from app.services.results.base import MatchResult, MatchStats, match_score
from app.services.results.football_data import FootballDataProvider
from app.services.results.verifier import (
    _detect_over_under_direction,
    _extract_handicap_team,
    _extract_predicted_team,
    _providers_for_sport,
    _resolve_asian_handicap,
    _resolve_over_under,
    _resolve_winner,
    _should_attempt_verification,
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
) -> ParsedPick:
    return ParsedPick(
        raw_message_id=1,
        deporte="fútbol",
        mercado=mercado,
        seleccion=seleccion,
        linea=linea,
        evento=evento,
        fecha_evento=datetime(2026, 9, 15, 21, 0),
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

    async def test_primera_parte_no_se_resuelve(self):
        provider = _StubProvider(_match(3, 0))
        pick = _pick("Más de 0.5 goles 1ª parte", "over/under goles", linea=0.5)
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, False)
        assert provider.calls == 0

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
    (fixtures vs fixtures/statistics)."""

    def __init__(self, calls, fixtures_payload, stats_payload):
        self._calls = calls
        self._fixtures = fixtures_payload
        self._stats = stats_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(url)
        if "statistics" in url:
            return _FakeResponse(self._stats)
        return _FakeResponse(self._fixtures)


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
