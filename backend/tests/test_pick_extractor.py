"""Tests unitarios del extractor híbrido de picks (funciones puras).

No llaman a OpenAI: solo cubren el pre-filtro, las reglas y la
extracción de fecha del evento.
"""

from datetime import datetime

from app.services.telegram.pick_extractor import (
    _extract_event_date,
    _extract_linea,
    _looks_like_bet,
    _rule_extract,
)


class TestLooksLikeBet:
    def test_rechaza_saludo_promocional(self):
        text = "Buenos días familia, os dejo el link de acceso gratis"
        assert _looks_like_bet(text) is False

    def test_acepta_mensaje_con_cuota_y_stake(self):
        text = "Titouan Droguet gana\nCuota 1.57 Stake 4"
        assert _looks_like_bet(text) is True

    def test_rechaza_texto_vacio(self):
        assert _looks_like_bet("") is False


class TestRuleExtract:
    def test_extrae_cuota_stake_y_seleccion(self):
        text = "Titouan Droguet gana\nCuota 1.57 Stake 4"
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is not None
        assert pick.es_apuesta is True
        assert pick.cuota == 1.57
        assert pick.stake == 4.0
        assert "Droguet" in pick.seleccion
        assert pick.metodo == "rule"

    def test_no_extrae_si_falta_stake(self):
        text = "Titouan Droguet gana\nCuota 1.57"
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is None

    def test_no_inventa_stake_desde_importe_boleto(self):
        # Un boleto de casa de apuestas con "Imp: 1.000,00€" no debe
        # confundirse con "stake" porque no aparece la palabra literal.
        text = "Titouan Droguet 1.57\nImp: 1.000,00€\nGanancias 1.571,42€"
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is None


class TestExtractEventDate:
    def test_extrae_fecha_formato_punto(self):
        text = "FRANCIA: CASSIS CHALL. MASCULINO\n12.09.2026 14:00"
        result = _extract_event_date(text)
        assert result == datetime(2026, 9, 12, 14, 0)

    def test_extrae_fecha_con_mes_abreviado(self):
        text = "Sáb 12 sep 14:00"
        result = _extract_event_date(text)
        assert result is not None
        assert result.day == 12
        assert result.month == 9
        assert result.hour == 14

    def test_sin_fecha_devuelve_none(self):
        text = "Titouan Droguet gana\nCuota 1.57 Stake 4"
        assert _extract_event_date(text) is None


class TestExtractLinea:
    def test_extrae_handicap_positivo(self):
        assert _extract_linea("Real Sociedad B Hándicap Asiático +1.5") == 1.5

    def test_extrae_handicap_negativo(self):
        assert _extract_linea("Real Madrid Hándicap Asiático -1.5") == -1.5

    def test_extrae_over_sin_signo(self):
        assert _extract_linea("Over 2.5 goles") == 2.5

    def test_extrae_under_sin_signo(self):
        assert _extract_linea("Under 2.5 goles") == 2.5

    def test_sin_linea_devuelve_none(self):
        assert _extract_linea("Real Madrid gana") is None
