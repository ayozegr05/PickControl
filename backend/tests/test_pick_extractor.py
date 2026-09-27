"""Tests unitarios del extractor híbrido de picks (funciones puras).

No llaman a OpenAI: solo cubren el pre-filtro, las reglas y la
extracción de fecha del evento.
"""

from datetime import datetime, timezone

from app.services.telegram.pick_extractor import (
    _LEG_NOISE_PATTERN,
    ExtractedPick,
    _clean_text_field,
    _detect_deporte,
    _extract_event_date,
    _extract_eventos,
    _extract_linea,
    _is_settled_ticket,
    _looks_like_bet,
    _normalize_pick,
    _rule_extract,
    _sanitize_event_date,
    _strip_trailing_competition,
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


class TestLineaNuevosPatrones:
    def test_mas_sin_de(self):
        # "Más 8.0 corners" (sin "de") — caso real de Dm7.
        assert _extract_linea("Más 8.0 corners") == 8.0

    def test_mas_de(self):
        assert _extract_linea("Más de 2,5 goles") == 2.5

    def test_menos_de(self):
        assert _extract_linea("Menos de 11.0 córners") == 11.0

    def test_sin_linea(self):
        assert _extract_linea("Alcaraz gana") is None


class TestSeleccionAmbigua:
    """Normalizaciones de `_normalize_pick` sobre selecciones que el
    LLM deja inverificables."""

    def _pick(self, seleccion, evento=None, deporte="tenis", patas=None):
        return ExtractedPick(
            es_apuesta=True,
            deporte=deporte,
            evento=evento,
            mercado="ganador",
            seleccion=seleccion,
            cuota=1.5,
            stake=4,
            patas=patas or [],
        )

    def test_ganador_generico_recupera_el_sujeto(self):
        # Caso real (id=2468): "Ganará el encuentro" + el apostado
        # destacado en su propia línea del mensaje.
        text = (
            "🏆 Tenis - Copa Davis 🌎\n\n"
            "**➡️** Leyre Romero Gormaz\n\n"
            "📈 Cuota 1.53     💰 Stake 4\n\n"
            "Leyre Romero tiene un cruce para morder a Hercog..."
        )
        pick = self._pick(
            "Ganará el encuentro",
            evento="Leyre Romero Gormaz vs Polona Hercog",
        )
        out = _normalize_pick(pick, text)
        assert out.seleccion == "Leyre Romero Gormaz gana"

    def test_ganador_generico_ambiguo_no_inventa(self):
        # Los dos lados aparecen como línea propia: no se puede saber.
        text = "Mérida\nExtremadura\nCuota 1.80 Stake 3"
        pick = self._pick("Ganará el encuentro", evento="Mérida - Extremadura")
        out = _normalize_pick(pick, text)
        assert out.seleccion == "Ganará el encuentro"

    def test_ganador_con_sujeto_no_se_toca(self):
        pick = self._pick("Alcaraz gana", evento="Alcaraz - Sinner")
        out = _normalize_pick(pick, "Cuota 1.5")
        assert out.seleccion == "Alcaraz gana"

    def test_crear_apuesta_sin_patas_se_rechaza(self):
        # Caso real (id=2825): la selección es el botón del boleto.
        pick = self._pick(
            "Crear apuesta", evento="Barcelona - Paris FC", deporte="fútbol"
        )
        out = _normalize_pick(pick, "CREAR APUESTA CUOTA 91")
        assert out.es_apuesta is False
        assert out.metodo == "rejected"

    def test_crear_apuesta_con_patas_se_conserva(self):
        pata = ExtractedPick(
            es_apuesta=True, seleccion="Barcelona gana", mercado="ganador"
        )
        pick = self._pick(
            "Crear apuesta",
            evento="Barcelona - Paris FC",
            deporte="fútbol",
            patas=[pata, pata.model_copy()],
        )
        out = _normalize_pick(pick, "CREAR APUESTA CUOTA 91")
        assert out.es_apuesta is True


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

    def test_sello_won_ingles(self):
        # Caso real: boleto ya cobrado de casa en inglés — "WON" a nivel
        # de boleto + "Return <importe>". Las patas usan "Won" (capital)
        # y no deben disparar el sello.
        text = (
            "Accumulator\n+215\n3 choices\nBet MX$50.00\n"
            "Return MX$157.58\nWON\n"
            "Hugo Gaston -1.5\nHandicap Games\nWon"
        )
        assert _is_settled_ticket(text) is True

    def test_returned_ingles(self):
        assert _is_settled_ticket("100,00€ Crear apuesta\n€230.00 Returned") is True

    def test_pata_won_minuscula_no_es_sello(self):
        # "Won" con minúsculas es el estado de una pata dentro de un slip
        # abierto, no el sello del boleto.
        assert _is_settled_ticket("Hugo Gaston -1.5\nHandicap Games\nWon") is False

    def test_sello_ganad_arroba(self):
        # Caso real: OCR del slip ganado lleva el sello "GANAD@S".
        text = "CREA TU APUESTA 5 pronósticos\nGANAD@S\nCuota 61.00"
        assert _is_settled_ticket(text) is True

    def test_checkmarks_con_premio_es_liquidado(self):
        # Caso real msg 8884: slip "verde" con ✓ por selección y premio
        # pagado — dice "Crear apuesta" porque así se llama el mercado.
        text = (
            "CREAR APUESTA  61.00\n"
            "✓ Ante Budimir: 2+ remates a puerta\n"
            "✓ Ruben Garcia: 2+ remates a puerta\n"
            "✓ Ivan Romero será Amonestado\n"
            "Osasuna\nLevante\nImp: 100,00€\n6100,00€  Ganancias\n"
        )
        assert _is_settled_ticket(text) is True

    def test_checkmarks_sin_premio_no_es_liquidado(self):
        # Checkmarks sueltos sin línea de premio: no basta para sellar.
        text = "✓ Madrid gana\n✓ Over 2.5\nCrear apuesta @3.00"
        assert _is_settled_ticket(text) is False

    def test_marcador_final_en_slip_es_liquidado(self):
        # Caso real msg 84305: bet builder multi-partido reposteado — el
        # OCR transcribe "Marcador 0-2" junto a los equipos, algo que un
        # slip abierto nunca lleva.
        text = (
            "Bet Builder 1.78\nFC Porto\nManchester City  Marcador 0-2\n"
            "Más 1.5 Goles totales\nReal Madrid\nInter de Milán  Marcador 2-1\n"
            "Apuesta S/200.00\nGanancias S/496.69"
        )
        assert _is_settled_ticket(text) is True

    def test_payout_footer_con_compartir_es_liquidado(self):
        # Caso real msg 83914: slip cobrado de bet365 — la vista liquidada
        # muestra el botón "Compartir" y el mercado "CREAR APUESTA", que
        # no deben vetar el pie de cobro "<importe>€ Ganancias".
        text = (
            "Compartir\nCREAR APUESTA 1.80\nMenos de 5 goles\n"
            "Más de 2 tarjetas\nBorussia Dortmund\nBayern de Múnich\n"
            "Imp:\n30.000,00€\n54000,00€ Ganancias\n"
        )
        assert _is_settled_ticket(text) is True

    def test_slip_abierto_con_cerrar_apuesta_no_es_liquidado(self):
        # Caso real msg 84039: slip vivo — botón "Cerrar apuesta"
        # (cash-out) presente: nunca se marca como liquidado.
        text = (
            "CREAR APUESTA 1.50\nResultado final: Barcelona\n"
            "Más de 2 goles\nBarcelona\nRayo Vallecano\n"
            "Imp: 2.000,00€\nGanancias 3.000,00€\nCerrar apuesta 2.000,00€"
        )
        assert _is_settled_ticket(text) is False

    def test_sello_alucinado_con_cerrar_apuesta_no_es_liquidado(self):
        # El modelo de OCR a veces estampa 'SELLO: GANADOR' en slips
        # vivos por el pie "Ganancias <importe>". Si el texto transcrito
        # muestra el botón "Cerrar apuesta" (cash-out = apuesta viva),
        # la UI manda sobre el sello.
        text = (
            "SELLO: GANADOR\nCREAR APUESTA 1.50\nResultado final: Barcelona\n"
            "2 GOLES DE VENTAJA\nMás de 2 goles\nBarcelona\nRayo Vallecano\n"
            "Imp: 2.000,00€\nGanancias 3.000,00€\nCerrar apuesta 2.000,00€"
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

    def test_recorta_prosa_del_llm(self):
        # El LLM volcó la frase del análisis en `seleccion`: se queda
        # con la etiqueta del pick, cortada en el borde de frase.
        pick = _normalize_pick(
            ExtractedPick(
                es_apuesta=True,
                seleccion=(
                    "Isak marca gol. El Liverpool llega en racha tras "
                    "ganar sus últimos cinco partidos y el Newcastle "
                    "encaja demasiado en casa para fiarse del rival"
                ),
                metodo="llm",
            ),
            "Isak marca gol",
        )
        assert pick.seleccion == "Isak marca gol"

    def test_prosa_sin_borde_corta_en_palabra(self):
        # Sin borde de frase: corta en la última palabra completa.
        prosa = "Isak marca porque el Liverpool llega en racha " * 4
        pick = _normalize_pick(
            ExtractedPick(es_apuesta=True, seleccion=prosa, metodo="llm"),
            prosa,
        )
        assert pick.seleccion is not None
        assert len(pick.seleccion) <= 120
        assert not pick.seleccion.endswith(" ")

    def test_combinada_larga_no_se_recorta(self):
        # El "A + B + C" de una combinada es largo legítimo: las patas
        # pobladas impiden el recorte de prosa.
        patas = [
            ExtractedPick(es_apuesta=True, seleccion=f"Pata número {i} gana su partido")
            for i in range(5)
        ]
        joined = " + ".join(p.seleccion for p in patas)
        assert len(joined) > 120
        pick = _normalize_pick(
            ExtractedPick(es_apuesta=True, seleccion=joined, patas=patas, metodo="llm"),
            joined,
        )
        assert pick.seleccion == joined


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


class TestSeleccionKeyword:
    """La regla que elige la línea del pick: límites de palabra,
    keywords en español y guarda de longitud contra la prosa."""

    def test_menos_de_es_seleccion_y_no_la_prosa(self):
        # Caso real Dm7 84473: la línea "Menos de 3,5 goles" no casaba
        # con ninguna keyword inglesa y el párrafo de análisis se colaba
        # por el substring "gana" de "necesita ganar".
        text = (
            "Este es mi pronóstico para hoy\n\n"
            "Menos de 3,5 goles ESPAÑA\n\n"
            "18:30 CUOTA 1.50 STAKE 4\n\n"
            "El Celta–Racing presenta un escenario favorable para buscar "
            "menos de 3,5 goles. El conjunto vigués solamente suma cuatro "
            "puntos y un Celta que necesita ganar, pero que difícilmente "
            "asumirá riesgos excesivos mientras el marcador permanezca "
            "igualado en los primeros minutos del encuentro."
        )
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is not None
        assert "Menos de 3,5" in pick.seleccion
        assert "necesita ganar" not in pick.seleccion
        assert pick.linea == 3.5

    def test_mover_no_es_over(self):
        # "mover el balón" contiene el substring "over" pero no debe
        # casar: los límites de palabra lo evitan (caso real msg 62978).
        text = (
            "Menos de 11.0 corners ESPAÑA\n\n"
            "CUOTA 1.50\nSTAKE 4\n\n"
            "El partido entre Sevilla y Barcelona puede desarrollarse "
            "con un ritmo controlado si el Barcelona consigue dominar "
            "la posesión y mover el balón con paciencia durante todo "
            "el encuentro sin apenas sobresaltos en defensa."
        )
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is not None
        assert "corners" in pick.seleccion.lower()
        assert "mover el balón" not in pick.seleccion

    def test_prosa_larga_nunca_es_seleccion(self):
        # Solo hay prosa larga con keyword: sin línea de pick válida la
        # regla devuelve None y el mensaje pasa al LLM.
        prosa = (
            "El equipo llega necesitando ganar tras varias jornadas sin "
            "conocer la victoria y el técnico ha insistido en que la "
            "clave será mover el balón con paciencia en campo rival "
            "durante los noventa minutos de juego."
        )
        text = f"CUOTA 1.50\nSTAKE 4\n\n{prosa}"
        pick = _rule_extract(text, informante="TestChannel")
        assert pick is None


class TestExtractEventos:
    def test_cabecera_competicion_no_es_evento(self):
        # "TENIS - Copa davis" casa con el patrón "A - B" pero es la
        # cabecera del torneo, no el cruce (caso real Lady Bets).
        lines = ["🎾 TENIS - Copa davis 🏆", "Bergs gana", "STAKE 4"]
        assert _extract_eventos(lines) == []

    def test_enfrentamiento_normal_sigue_funcionando(self):
        lines = ["Osasuna - Levante", "Más de 9,5 córners", "STAKE 3"]
        assert _extract_eventos(lines) == ["Osasuna - Levante"]

    def test_formato_v_de_boleto(self):
        # Los slips usan "A v B": "Dominko/Sesko v Cukierman/Shimanov".
        lines = ["Copa Davis", "Dominko/Sesko v Cukierman/Shimanov"]
        assert _extract_eventos(lines) == ["Dominko/Sesko v Cukierman/Shimanov"]


class TestCelebracionVerde:
    def test_caption_verde_sin_datos_no_es_pick(self):
        # Caption bajo el boleto ganador reposteado: sin cuota/stake
        # propios es marketing, no un pick abierto.
        text = "✅ OTRO VERDE PARA ADENTRO ✅ seguimos sumando mis niños"
        assert _looks_like_bet(text) is False

    def test_total_ganado_no_es_pick(self):
        text = "BUENOS DÍAS\nTOTAL GANADO= +1.000€\nEn un ratito otro pick"
        assert _looks_like_bet(text) is False

    def test_pick_con_senales_fuertes_sobrevive(self):
        # Un pick real que diga "a por otro verde" pero traiga cuota y
        # stake propios sigue entrando: las señales fuertes ganan.
        text = "A por otro verde\nBergs gana\nCuota 1.50 Stake 4"
        assert _looks_like_bet(text) is True


class TestExtractLineaMarcador:
    def test_marcador_0_0_no_es_linea(self):
        # "0-0" es un marcador, no el hándicap "-0" (caso real: la
        # prosa del análisis producía linea=-0.0).
        assert _extract_linea("incluidos el 0-0 ante la Real Sociedad") is None


class TestTeamNamesConConectores:
    def test_nombre_con_de_y_del_completo(self):
        # "Celta de Vigo - Racing de Santander": los conectores en
        # minúscula forman parte del nombre; sin ellos se capturaba
        # truncado como "Vigo - Racing" (caso real Dm7 Gratuito).
        lines = ["Celta de Vigo - Racing de Santander", "Menos 3,5 Goles"]
        assert _extract_eventos(lines) == ["Celta de Vigo - Racing de Santander"]


class TestStripTrailingCompetition:
    def test_cola_competicion_en_mayusculas(self):
        # "pick + cabecera de competición" en la misma línea del tipster.
        assert _strip_trailing_competition("Menos de 3,5 goles ESPAÑA") == (
            "Menos de 3,5 goles"
        )

    def test_seleccion_toda_en_mayusculas_se_conserva(self):
        # Si no queda texto en minúscula tras el recorte, la selección
        # era íntegramente en mayúsculas y no se toca.
        assert _strip_trailing_competition("MALLORCA RESULTADO SIN EMPATE") == (
            "MALLORCA RESULTADO SIN EMPATE"
        )

    def test_sin_cola_no_cambia(self):
        assert _strip_trailing_competition("Bergs gana") == "Bergs gana"


class TestPromosYRetos:
    """P.2/P.3: anuncios de afiliado y escaleras de reto no son picks."""

    def test_escalera_reto_pasos_no_es_pick(self):
        # Caso real AllSportsPicks: "ESPECIAL 5 CREAR APUESTA" con la
        # escalera del reto (30€→96€→307€...) se parseaba como
        # combinada de 5 patas. Dinero→dinero nunca es una selección.
        text = (
            "✅ ESPECIAL 5 CREAR APUESTA\n"
            "1⃣PASO 30€ - 96€\n2⃣PASO 96€ - 307€\n"
            "3⃣PASO 307€ - 983€\n🛍 https://premiumpay.pro/2032/A"
        )
        assert _looks_like_bet(text) is False

    def test_anuncio_reto_del_ano_no_es_pick(self):
        text = (
            "🚨 HOY: RETO 30€ ➜ 10.000€ 🚨\nESPECIAL CREAR APUESTA\n"
            "✅ [Ya lo pegamos hace 1 semana](https://t.me/+capy)"
        )
        assert _looks_like_bet(text) is False

    def test_cta_plaza_empieza_a_ganar_no_es_pick(self):
        text = (
            "PRIMER PASO DEL RETO DEL AÑO 🍾 YA DISPONIBLE\n"
            "Desde HOY EMPIEZA A GANAR 🫵\n"
            "ACCEDE AQUÍ TU PLAZA DEL RETO CON DESCUENTO"
        )
        assert _looks_like_bet(text) is False

    def test_pick_con_enlace_de_afiliado_sobrevive(self):
        # Un pick real que cuelga un link al final sigue entrando:
        # las señales fuertes tienen precedencia sobre los negativos.
        text = "Bergs gana\nCuota 1.50\nStake 4\nhttps://premiumpay.pro/x"
        assert _looks_like_bet(text) is True

    def test_supercuota_marca_no_es_pick(self):
        # Anuncio de cuota mejorada de la casa: "SUPERCUOTA ELCHE-REAL
        # MADRID GANA REAL MADRID 1.30 → 5.00 REGÍSTRATE".
        text = (
            "SUPERCUOTA\nELCHE-REAL MADRID\nGANA REAL MADRID\n1.30 → 5.00\nREGÍSTRATE"
        )
        assert _looks_like_bet(text) is False

    def test_boleto_cobrado_celebrado_no_es_pick(self):
        # Repost del boleto ya pagado con celebración ("ha ganado").
        text = (
            "€150.00 Single\nPetr Bar Biryukov 1.53\nTo Win Match\n"
            "€230.00 Returned\n\nQue barbaridad, con que facilidad ha ganado"
        )
        assert _looks_like_bet(text) is False


class TestMarkdownEnFiltro:
    def test_cuota_entre_marcas_markdown_cuenta(self):
        # "__Cuota 1.50__": "_" es carácter de palabra y anulaba el \b
        # del patrón — un pick real quedaba rechazado (pick 435 Cretu).
        text = "**Cretu** __gana__\n__Cuota 1.50__\n__Stake 3__"
        assert _looks_like_bet(text) is True


class TestLegNoiseLinks:
    def test_link_markdown_no_es_pata(self):
        assert _LEG_NOISE_PATTERN.search("[Ya lo pegamos](https://t.me/x)")

    def test_url_pelada_no_es_pata(self):
        assert _LEG_NOISE_PATTERN.search("https://premiumpay.pro/2032/A")

    def test_escalera_euros_no_es_pata(self):
        assert _LEG_NOISE_PATTERN.search("1⃣PASO 30€ - 96€")

    def test_pata_normal_no_es_ruido(self):
        assert not _LEG_NOISE_PATTERN.search("Zizou Bergs gana el partido")


class TestFixtureFromSlipOcr:
    """Rescate del cruce "A v B" / "A 0-0 B" del OCR de boletos en vivo
    (bet365): el LLM a veces devuelve la cabecera de liga."""

    def test_linea_v_devuelve_cruce(self):
        from app.services.telegram.pick_extractor import _fixture_from_slip_ocr

        ocr = (
            "España - La Liga\nCelta de Vigo 0 0 Racing Santander\n"
            "Estadísticas\nMás de 7.0\nTotal - Córners\n"
            "Celta de Vigo v Racing Santander\n1.50"
        )
        assert _fixture_from_slip_ocr(ocr) == "Celta de Vigo - Racing Santander"

    def test_linea_marcador_con_guion(self):
        from app.services.telegram.pick_extractor import _fixture_from_slip_ocr

        ocr = (
            "Andorra - Primera División\n"
            "Sporting Club d'Escaldes 0 - 2 FC Santa Coloma\n"
            "Estadísticas\nMenos de 4.5\n1.61"
        )
        assert _fixture_from_slip_ocr(ocr) == (
            "Sporting Club d'Escaldes - FC Santa Coloma"
        )

    def test_sin_cruce_devuelve_none(self):
        from app.services.telegram.pick_extractor import _fixture_from_slip_ocr

        assert _fixture_from_slip_ocr("SERIE A\nMÁS DE 7.0 CÓRNERS") is None


class TestPatasFromJoined:
    """Red de seguridad que parte el "A + B + C" de `seleccion` en patas
    (la usan el extractor en vivo y el backfill de combinadas legadas)."""

    def test_pata_que_empieza_en_digito_con_mercado_se_conserva(self):
        from app.services.telegram.pick_extractor import _patas_from_joined

        pick = ExtractedPick(
            es_apuesta=True,
            seleccion=(
                "Elias Ymer - Ganará el encuentro + " "1° set - Más de 7.5 juegos"
            ),
        )
        patas = _patas_from_joined(pick)
        assert len(patas) == 2
        assert patas[1].seleccion == "1° set - Más de 7.5 juegos"

    def test_trozo_numerico_suelto_sigue_fuera(self):
        from app.services.telegram.pick_extractor import _patas_from_joined

        pick = ExtractedPick(es_apuesta=True, seleccion="Atlético gana + 263 + 105")
        patas = _patas_from_joined(pick)
        assert [p.seleccion for p in patas] == ["Atlético gana"]

    def test_eventos_alineados_reparten_en_orden(self):
        from app.services.telegram.pick_extractor import _patas_from_joined

        pick = ExtractedPick(
            es_apuesta=True,
            seleccion="Juventus + Celtic",
            evento="Juventus vs NEC + Celtic vs Ferencvarosi TC",
        )
        patas = _patas_from_joined(pick)
        assert patas[0].evento == "Juventus vs NEC"
        assert patas[1].evento == "Celtic vs Ferencvarosi TC"


class TestNoisePatas:
    """Las cabeceras de torneo y eslóganes colados como pata bloqueaban
    combinadas enteras en pendiente (casos #2985, #3887)."""

    def _pata(self, seleccion, mercado=None, linea=None):
        return ExtractedPick(
            es_apuesta=True, seleccion=seleccion, mercado=mercado, linea=linea
        )

    def test_cabecera_torneo_es_ruido(self):
        from app.services.telegram.pick_extractor import _is_noise_pata

        assert _is_noise_pata(self._pata("CHALL MOUILLERON"))
        assert _is_noise_pata(self._pata("WTA GUADALAJARA"))
        assert _is_noise_pata(self._pata("CHALL GÉNOVA"))

    def test_eslogan_es_ruido(self):
        from app.services.telegram.pick_extractor import _is_noise_pata

        assert _is_noise_pata(self._pata("Siempre con cabeza"))
        assert _is_noise_pata(self._pata("Apuesta con responsabilidad"))

    def test_pata_real_no_es_ruido(self):
        from app.services.telegram.pick_extractor import _is_noise_pata

        assert not _is_noise_pata(self._pata("Durand gana"))
        assert not _is_noise_pata(self._pata("Barcelona -2", linea=-2.0))
        assert not _is_noise_pata(self._pata("Más de 2 goles"))

    def test_nombre_pelado_es_ganador_implicito(self):
        from app.services.telegram.pick_extractor import _is_noise_pata

        assert not _is_noise_pata(self._pata("Juventus"))


class TestCombinadaConRuido:
    """Una selección simple troceada (torneo + pick + eslogan) degrada
    a pick simple adoptando la única pata real."""

    def test_ruido_alrededor_degrada_a_simple(self):
        from app.services.telegram.pick_extractor import _ensure_combinada_shape

        pick = ExtractedPick(
            es_apuesta=True,
            deporte="tenis",
            evento="Neumayer vs Manzano",
            mercado="combinada",
            seleccion="CHALL GÉNOVA + Neumayer gana + Siempre con cabeza",
        )
        result = _ensure_combinada_shape(pick)
        assert result.patas == []
        assert result.seleccion == "Neumayer gana"
        assert result.mercado == "ganador"

    def test_dos_patas_reales_siguen_combinada(self):
        from app.services.telegram.pick_extractor import _ensure_combinada_shape

        pick = ExtractedPick(
            es_apuesta=True,
            deporte="tenis",
            mercado="combinada",
            seleccion="Brunold gana + +7,5 juegos en el 1° set",
        )
        result = _ensure_combinada_shape(pick)
        assert len(result.patas) == 2
        assert result.mercado == "combinada"


class TestDobleOportunidad1X:
    """ "Inglaterra 1X" no es hándicap: es doble oportunidad (#3612)."""

    def test_1x_clasifica_doble_oportunidad(self):
        from app.services.telegram.pick_extractor import _classify_leg_market

        assert _classify_leg_market("Inglaterra 1X") == "doble oportunidad"
        assert _classify_leg_market("X2 España") == "doble oportunidad"
