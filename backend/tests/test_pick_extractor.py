"""Tests unitarios del extractor híbrido de picks (funciones puras).

No llaman a OpenAI: solo cubren el pre-filtro, las reglas y la
extracción de fecha del evento.
"""

from datetime import datetime, timezone

from app.services.telegram.pick_extractor import (
    _extract_event_date,
    _extract_linea,
    _is_settled_ticket,
    _looks_like_bet,
    _rule_extract,
    _sanitize_event_date,
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

    def test_acepta_pick_con_footer_de_afiliado(self):
        # Pick real de Dm7 GRATUITO: el footer "GANA 200€ GRATIS" no debe
        # ganar a las señales fuertes (CUOTA 1.70 + STAKE 4).
        text = (
            "Este es mi pronóstico para hoy\n"
            "Levante 1X ESPAÑA\n"
            "21:30\n"
            "CUOTA 1.70 • STAKE 4\n"
            "GANA 200€ GRATIS DE APUESTA AQUÍ https://bdeal.io/x"
        )
        assert _looks_like_bet(text) is True

    def test_rechaza_promo_gratis_sin_datos(self):
        # Una promo pura (sin cuota/stake) sigue rechazada aunque
        # mencione apuestas.
        text = "GANA 200€ GRATIS DE APUESTA AQUÍ https://bdeal.io/x"
        assert _looks_like_bet(text) is False

    def test_rechaza_anuncio_de_supercuota(self):
        # Caso real: OCR de la promo "EL SUVIDÓN" generaba un pick
        # fantasma "Real Madrid gana" con la fecha del mensaje.
        text = (
            "EL SUVIDÓN\nMULTIPLICA TUS GANANCIAS\nREAL MADRID GANA\n"
            "1.90\n1900\n*Cuotas sujetas a cambios. Se aplican T&C's.\n"
            "SOLO NUEVOS USUARIOS.\n"
            "¡MULTIPLICA TUS GANANCIAS X10 CON EL SUVIDÓN!"
        )
        assert _looks_like_bet(text) is False

    def test_rechaza_celebracion_de_acierto(self):
        # Caso real: mensaje de celebración "acertando otra vez /
        # CLAVAMOS" generaba un pick fantasma del día.
        text = (
            "✅ LA INFORMACIÓN ES PODER ✅\n\n"
            "Sabíamos que este MEGAPACK era 100% seguro y lo hemos "
            "demostrado nuevamente acertando otra vez. CLAVAMOS EL 99% "
            "de estos PACKS\n\n✅ Mas de 1 gol y 2 tarjetas\n\n"
            "Tenemos info, entramos muy fuerte y ganamos dinero."
        )
        assert _looks_like_bet(text) is False


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

    def test_no_usa_linea_de_afiliado_como_seleccion(self):
        # El footer de afiliado contiene "GANA" pero es publi: debe
        # ignorarse. Al quedar sin línea de selección válida, la regla
        # devuelve None y el pick pasa al LLM.
        text = (
            "Este es mi pronóstico para hoy\n"
            "Levante 1X ESPAÑA\n"
            "21:30\n"
            "CUOTA 1.70 • STAKE 4\n"
            "GANA 200€ GRATIS DE APUESTA AQUÍ https://bdeal.io/x"
        )
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is None

    def test_seleccion_real_gana_a_footer_de_afiliado(self):
        # Con una línea de selección real además del footer de afiliado,
        # la regla debe quedarse con la selección, no con la publi.
        text = (
            "Titouan Droguet gana\n"
            "Cuota 1.57 Stake 4\n"
            "GANA 200€ GRATIS DE APUESTA AQUÍ https://bdeal.io/x"
        )
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is not None
        assert "Droguet" in pick.seleccion


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

    def test_fecha_sin_ano_usa_la_del_mensaje(self):
        # El OCR del boleto dice "Mar 15 sep 21:00" sin año: debe salir
        # el año de la fecha del mensaje (2026), no el año actual ni uno
        # inventado — caso real del pick Alavés-Valencia.
        text = "Alavés\nValencia\nMar 15 sep\n21:00"
        ref = datetime(2026, 9, 15, 15, 23)
        result = _extract_event_date(text, fecha_referencia=ref)
        assert result == datetime(2026, 9, 15, 21, 0)

    def test_fecha_sin_ano_sin_referencia_usa_ano_actual(self):
        text = "Sáb 12 sep 14:00"
        result = _extract_event_date(text)
        assert result is not None
        assert result.year == datetime.now().year

    def test_set_de_tenis_no_es_fecha(self):
        # Caso real: "en el 1 set" se interpretaba como 1 de septiembre
        # ("set" era abreviatura de mes). Un pick de tenis con sets no
        # debe producir fecha.
        text = (
            "Tenis - Challenger Francia\n"
            "Daniel Rincón gana y +7.5 juegos en el 1 set\n"
            "Cuota 1.55 Stake 4"
        )
        ref = datetime(2026, 9, 16, 6, 41)
        assert _extract_event_date(text, fecha_referencia=ref) is None


class TestSanitizeEventDate:
    def test_descarta_fecha_muy_antigua(self):
        # Caso real: el LLM devolvió 2023 para un mensaje de 2026.
        ref = datetime(2026, 9, 15, 9, 45)
        assert _sanitize_event_date(datetime(2023, 9, 15, 21, 0), ref) is None

    def test_descarta_fecha_semanas_atras(self):
        # Caso real: combinada con fecha de hace un mes (emisión del slip).
        ref = datetime(2026, 9, 16, 19, 49)
        assert _sanitize_event_date(datetime(2026, 8, 24, 19, 30), ref) is None

    def test_conserva_fecha_del_mismo_dia(self):
        ref = datetime(2026, 9, 15, 15, 23)
        fecha = datetime(2026, 9, 15, 21, 0)
        assert _sanitize_event_date(fecha, ref) == fecha

    def test_conserva_fecha_de_manana(self):
        ref = datetime(2026, 9, 15, 23, 0)
        fecha = datetime(2026, 9, 16, 18, 0)
        assert _sanitize_event_date(fecha, ref) == fecha

    def test_normaliza_tz_aware_a_naive_utc(self):
        ref = datetime(2026, 9, 15, 15, 23)
        aware = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
        assert _sanitize_event_date(aware, ref) == datetime(2026, 9, 15, 23, 0)

    def test_sin_fecha_devuelve_none(self):
        assert _sanitize_event_date(None, datetime(2026, 9, 15)) is None


class TestIsSettledTicket:
    def test_sello_ganador(self):
        assert _is_settled_ticket("APUESTA GANADOR\nCuota 1.50") is True

    def test_premio_pagado_sin_potencial(self):
        # Caso real: slip cobrado reposteado ("verde") — "Imp: <x>€" +
        # "Ganancias <y>€" sin "potenciales" = boleto liquidado, aunque
        # ponga "CREAR APUESTA" (nombre del mercado bet-builder).
        text = (
            "ERROR CUOTA 100%\nCREAR APUESTA 1.70\nMás de 1 goles\n"
            "Más de 2 tarjetas\nGenoa\nComo\n"
            "Imp: 30.000,00€\nGanancias 51000,00€"
        )
        assert _is_settled_ticket(text) is True

    def test_slip_abierto_con_potenciales_no_es_liquidado(self):
        # Caso real: slip abierto de bet365 — lleva "Ganancias
        # potenciales" y "Añadir selección"; no debe rechazarse.
        text = (
            "Alavés - Valencia - Total de goles - Más/menos de 1,5\n"
            "Más 1,5 Goles 1.50\nImporte: €2.000,00\n"
            "Ganancias potenciales: €3.000,00\nAñadir selección"
        )
        assert _is_settled_ticket(text) is False


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

    def test_linea_con_mas_final(self):
        # "21+ juegos" = over 20.5
        assert _extract_linea("21+ juegos") == 20.5

    def test_linea_o_mas(self):
        # "20 o más juegos" = over 19.5
        assert _extract_linea("20 o más juegos") == 19.5

    def test_mas_final_no_pisa_handicap(self):
        # El hándicap con signo tiene prioridad sobre el "+" final.
        assert _extract_linea("Alcaraz -1.5 sets") == -1.5
