"""Tests unitarios del extractor híbrido de picks (funciones puras).

No llaman a OpenAI: solo cubren el pre-filtro, las reglas y la
extracción de fecha del evento.
"""

from datetime import datetime, timezone

from app.services.telegram.pick_extractor import (
    ExtractedPick,
    _clean_text_field,
    _detect_deporte,
    _extract_event_date,
    _extract_linea,
    _is_settled_ticket,
    _looks_like_bet,
    _normalize_pick,
    _rule_extract,
    _sanitize_event_date,
    extract_pick,
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


class TestCleanTextField:
    """Markdown/emojis de Telegram no deben sobrevivir a los campos
    extraídos (caso real: "**Chidek gana ****🇫🇷**")."""

    def test_quita_markdown_y_emojis(self):
        assert _clean_text_field("**Chidek gana ****🇫🇷**") == "Chidek gana"

    def test_quita_bullets_y_flechas(self):
        assert _clean_text_field("➡️ Cecchinato gana") == "Cecchinato gana"

    def test_quita_ruido_mixto(self):
        assert (
            _clean_text_field("**__➡️__**** Marco Cecchinato gana **")
            == "Marco Cecchinato gana"
        )

    def test_none_y_vacio(self):
        assert _clean_text_field(None) is None
        assert _clean_text_field("***") is None

    def test_conserva_texto_limpio(self):
        assert _clean_text_field("Alcaraz -1.5 sets") == "Alcaraz -1.5 sets"


class TestDetectDeporte:
    def test_detecta_tenis_por_torneo(self):
        assert _detect_deporte("**CHALL RENNES **🇫🇷 **🎾** Chidek gana") == "tenis"

    def test_detecta_tenis_por_palabra(self):
        assert _detect_deporte("🎾 TENIS - Chall. Szczecin Cecchinato gana") == "tenis"

    def test_detecta_futbol(self):
        assert _detect_deporte("⚽ La Liga - Real Madrid gana") == "fútbol"

    def test_sin_senal_devuelve_none(self):
        assert _detect_deporte("Cuota 1.50 Stake 3") is None


class TestNormalizePick:
    def test_limpia_seleccion_e_infiere_deporte(self):
        # Caso real Bet Fran: reglas sacaron la selección con ruido y
        # sin deporte; la señal de tenis está en la cabecera.
        text = (
            "**CHALL RENNES **🇫🇷 **🎾**\n\n**Chidek gana ****🇫🇷**\n\n"
            "**Cuota 1.53📈**\n\n**Stake 3💰**"
        )
        pick = _rule_extract(text)
        assert pick is not None
        pick = _normalize_pick(pick, text)
        assert pick.seleccion == "Chidek gana"
        assert pick.deporte == "tenis"

    def test_no_pisa_deporte_del_llm(self):
        pick = _rule_extract("Cecchinato gana\nCuota 1.50\nStake 4")
        pick.deporte = "tenis"
        pick = _normalize_pick(pick, "Cecchinato gana\nCuota 1.50\nStake 4")
        assert pick.deporte == "tenis"


class TestRuleExtractEvento:
    def test_rellena_evento_si_hay_enfrentamiento_en_el_mensaje(self):
        text = (
            "Marco Cecchinato vs Marvin Moeller\n\n"
            "Cecchinato gana\nCuota 1.50\nStake 4"
        )
        pick = _rule_extract(text)
        assert pick is not None
        assert pick.evento == "Marco Cecchinato vs Marvin Moeller"

    def test_evento_none_si_el_rival_solo_esta_en_prosa(self):
        # Formato Bet Fran: el rival solo aparece en el análisis.
        text = (
            "**CHALL RENNES **🇫🇷 **🎾**\n\n**Chidek gana ****🇫🇷**\n\n"
            "**Cuota 1.53📈**\n\n**Stake 3💰**\n\n"
            "__el H2H global favorece a Mayot 2-1__"
        )
        pick = _rule_extract(text)
        assert pick is not None
        assert pick.evento is None


class TestRuleLlmEnrichment:
    """Segunda pasada LLM: solo se gasta cuando las reglas dejan el
    pick sin `evento` (el rival solo aparece en la prosa)."""

    async def test_llm_rellena_evento_cuando_reglas_no_lo_ven(self, monkeypatch):
        text = (
            "**CHALL RENNES **🇫🇷 **🎾**\n\n**Chidek gana ****🇫🇷**\n\n"
            "**Cuota 1.53📈**\n\n**Stake 3💰**\n\n"
            "__el H2H global favorece a Mayot 2-1__"
        )
        llamadas = []

        async def fake_llm(*args, **kwargs):
            llamadas.append(args)
            return ExtractedPick(
                es_apuesta=True,
                deporte="tenis",
                evento="Clement Chidekh vs Harold Mayot",
                mercado="ganador",
                seleccion="Chidekh gana",
            )

        monkeypatch.setattr(
            "app.services.telegram.pick_extractor._llm_extract", fake_llm
        )
        pick = await extract_pick(text, api_key="k")
        assert llamadas  # sí se llamó al LLM
        assert pick is not None
        # Merge: evento/deporte/mercado del LLM, seleccion de reglas.
        assert pick.evento == "Clement Chidekh vs Harold Mayot"
        assert pick.deporte == "tenis"
        assert pick.mercado == "ganador"
        assert pick.seleccion == "Chidek gana"
        assert pick.metodo == "rule+llm"

    async def test_no_llama_llm_si_reglas_ya_tienen_evento(self, monkeypatch):
        text = (
            "Marco Cecchinato vs Marvin Moeller\n\n"
            "Cecchinato gana\nCuota 1.50\nStake 4"
        )

        async def fake_llm(*args, **kwargs):
            raise AssertionError("no debería llamar al LLM")

        monkeypatch.setattr(
            "app.services.telegram.pick_extractor._llm_extract", fake_llm
        )
        pick = await extract_pick(text, api_key="k")
        assert pick is not None
        assert pick.evento == "Marco Cecchinato vs Marvin Moeller"
        assert pick.metodo == "rule"

    async def test_llm_sin_evento_conserva_pick_de_reglas(self, monkeypatch):
        text = (
            "**CHALL RENNES **🇫🇷 **🎾**\n\n**Chidek gana ****🇫🇷**\n\n"
            "**Cuota 1.53📈**\n\n**Stake 3💰**"
        )

        async def fake_llm(*args, **kwargs):
            return ExtractedPick(es_apuesta=True, seleccion="Chidek gana")

        monkeypatch.setattr(
            "app.services.telegram.pick_extractor._llm_extract", fake_llm
        )
        pick = await extract_pick(text, api_key="k")
        assert pick is not None
        assert pick.seleccion == "Chidek gana"
        assert pick.metodo == "rule"  # sin evento aportado no cambia


class TestDeporteNoSoportado:
    """Deportes sin proveedor de resultados (automovilismo, F1...): el
    pick se guarda rechazado en vez de quedar pendiente para siempre."""

    def test_motor_detectado_por_senal(self):
        assert _detect_deporte("GP ESPAÑA - Menos de 18,5 coches") == ("automovilismo")

    def test_pick_motor_queda_rejected(self):
        pick = _normalize_pick(
            ExtractedPick(
                es_apuesta=True,
                deporte="automovilismo",
                seleccion="Menos de 19,5 coches",
                evento="GP Países Bajos",
                metodo="rule",
            ),
            "GP PAÍSES BAJOS\nMenos de 19,5 coches",
        )
        assert pick.es_apuesta is False
        assert pick.metodo == "rejected"

    def test_pick_motor_sin_deporte_lo_detecta_y_rechaza(self):
        pick = _normalize_pick(
            ExtractedPick(
                es_apuesta=True,
                seleccion="Menos de 18,5 coches",
                metodo="llm",
            ),
            "GP ESPAÑA 🏎️\nMenos de 18,5 coches",
        )
        assert pick.deporte == "automovilismo"
        assert pick.es_apuesta is False

    def test_variante_f1_del_llm(self):
        pick = _normalize_pick(
            ExtractedPick(
                es_apuesta=True,
                deporte="F1",
                seleccion="Verstappen gana",
                metodo="llm",
            ),
            "Verstappen gana",
        )
        assert pick.es_apuesta is False
