"""Detección de picks pendientes con extracción rota (ciclo repair)."""

from app.services.maintenance.repair import (
    _dc_ambiguous_dnb,
    _dnb_market_fix,
    _inherit_leg_verdicts,
    _pick_needs_repair,
    _supersede_legs,
    _tournament_only,
)
from app.services.telegram.pick_extractor import ExtractedPick


class _P:
    def __init__(self, **kw):
        self.es_combinada = kw.get("es_combinada", False)
        self.evento = kw.get("evento")
        self.mercado = kw.get("mercado")
        self.seleccion = kw.get("seleccion")


class TestTournamentOnly:
    def test_torneo_solo(self):
        assert _tournament_only("CHALLENGER BIELLA")
        assert _tournament_only("WTA GUADALAJARA")

    def test_cruce_con_torneo_no(self):
        assert not _tournament_only("Challenger Biella - Brunold vs Forti")
        assert not _tournament_only("Sakkari M. vs Fruhvirtova L.")

    def test_none_y_vacio(self):
        assert not _tournament_only(None)
        assert not _tournament_only("")


class TestPickNeedsRepair:
    def test_combinada_degenerada_sin_patas(self):
        assert _pick_needs_repair(_P(es_combinada=True, evento="A vs B"), [])

    def test_combinada_evento_torneo(self):
        legs = [_P(), _P()]
        assert _pick_needs_repair(_P(es_combinada=True, evento="ATP 250"), legs)

    def test_combinada_pata_torneo(self):
        legs = [_P(evento="A vs B"), _P(evento="CHALL GÉNOVA")]
        assert _pick_needs_repair(_P(es_combinada=True, evento="A vs B"), legs)

    def test_combinada_sana_no(self):
        legs = [_P(evento="A vs B"), _P(evento=None)]
        assert not _pick_needs_repair(_P(es_combinada=True, evento="A vs B"), legs)

    def test_combinada_patas_spam(self):
        """Boleto-anuncio (#1741): la mayoría de patas son líneas de
        publi/afiliación -> entra a reparación y la re-extracción lo
        descarta como no-apuesta."""
        legs = [
            _P(seleccion="LLEVA TUS APUESTAS AL SIGUIENTE NIVEL!"),
            _P(seleccion="COMIENZA A GANAR DESDE YA"),
            _P(seleccion="@dm7allsports"),
        ]
        assert _pick_needs_repair(_P(es_combinada=True, evento="A vs B"), legs)

    def test_combinada_una_pata_spam_no_dispara(self):
        """Una sola pata ruidosa entre selecciones reales no basta: la
        reparación tiene que ser claramente necesaria."""
        legs = [
            _P(seleccion="@canal_tipster"),
            _P(seleccion="Isak marca"),
            _P(seleccion="Menos de 9.5 córners"),
        ]
        assert not _pick_needs_repair(_P(es_combinada=True, evento="A vs B"), legs)

    def test_pata_vip_no_es_spam(self):
        """'vip' con frontera de palabra: un equipo tipo Vipava no debe
        disparar la señal de publi."""
        legs = [_P(seleccion="Vipava gana"), _P(seleccion="Olimpija gana")]
        assert not _pick_needs_repair(
            _P(es_combinada=True, evento="Vipava vs Olimpija"), legs
        )

    def test_simple_evento_torneo(self):
        assert _pick_needs_repair(_P(evento="Challenger Biella"), [])

    def test_simple_1x_como_handicap(self):
        p = _P(
            evento="Inglaterra - España",
            mercado="hándicap asiático",
            seleccion="Inglaterra 1X",
        )
        assert _pick_needs_repair(p, [])

    def test_simple_sano_no(self):
        p = _P(
            evento="Neumayer vs Manzano", mercado="ganador", seleccion="Neumayer gana"
        )
        assert not _pick_needs_repair(p, [])


class TestDcAmbiguousDnb:
    """El extractor confundió 'resultado sin empate' (DNB) con 'doble
    oportunidad' — la fuente lo delata (casos reales 5429/5645)."""

    def test_slip_resultado_sin_empate(self):
        p = _P(mercado="doble oportunidad", seleccion="Atlético Mineiro gana")
        assert _dc_ambiguous_dnb(p, "Atlético Mineiro 1.50 Resultado sin empate")

    def test_texto_empate_no_cuenta(self):
        p = _P(mercado="doble oportunidad", seleccion="Irlanda gana")
        assert _dc_ambiguous_dnb(p, "IRLANDA GANA EL PARTIDO EMPATE NO CUENTA")

    def test_draw_no_bet_en_ingles(self):
        p = _P(mercado="double chance", seleccion="Arsenal gana")
        assert _dc_ambiguous_dnb(p, "Arsenal draw no bet @1.60")

    def test_seleccion_explicita_no_es_ambigua(self):
        """'X o empate' ya dice empate — no es extracción truncada."""
        p = _P(mercado="doble oportunidad", seleccion="Levante y empate")
        assert not _dc_ambiguous_dnb(p, "Resultado sin empate Levante")

    def test_mercado_no_dc_no_dispara(self):
        p = _P(mercado="ganador", seleccion="Levante gana")
        assert not _dc_ambiguous_dnb(p, "Resultado sin empate Levante")

    def test_fuente_sin_dnb_no_dispara(self):
        p = _P(mercado="doble oportunidad", seleccion="Levante gana")
        assert not _dc_ambiguous_dnb(p, "Doble oportunidad Levante o empate")


class TestDnbMarketFix:
    """Override determinista tras la re-extracción: si la fuente prueba
    DNB, el mercado se corrige aunque el LLM repita 'doble oportunidad'."""

    def test_corrige_mercado(self):
        new = ExtractedPick(
            es_apuesta=True, mercado="doble oportunidad", seleccion="Irlanda gana"
        )
        assert _dnb_market_fix("EMPATE NO CUENTA", new) == 1
        assert new.mercado == "resultado sin empate"

    def test_corrige_patas_de_combinada(self):
        new = ExtractedPick(
            es_apuesta=True,
            mercado="combinada",
            patas=[
                ExtractedPick(
                    es_apuesta=True,
                    mercado="doble oportunidad",
                    seleccion="X gana",
                ),
                ExtractedPick(es_apuesta=True, mercado="ganador", seleccion="Y gana"),
            ],
        )
        assert _dnb_market_fix("resultado sin empate", new) == 1
        assert new.patas[0].mercado == "resultado sin empate"
        assert new.patas[1].mercado == "ganador"

    def test_fuente_sin_dnb_no_toca(self):
        new = ExtractedPick(
            es_apuesta=True, mercado="doble oportunidad", seleccion="X y empate"
        )
        assert _dnb_market_fix("doble oportunidad X y empate", new) == 0
        assert new.mercado == "doble oportunidad"


class TestFixDnbLabels:
    """Barrido determinista sobre la BD: picks DC ambiguos cuya fuente
    prueba DNB se re-etiquetan en sitio (también liquidados)."""

    async def test_reliquida_dc_ambiguo_con_fuente_dnb(self, session):
        from app.models.parsed_pick import ParsedPick
        from app.models.telegram_raw_message import TelegramRawMessage
        from app.services.maintenance.repair import fix_dnb_labels

        raw = TelegramRawMessage(
            id=901,
            channel_id=-1001,
            message_id=1,
            channel_name="canal",
            text="IRLANDA GANA EL PARTIDO EMPATE NO CUENTA",
        )
        pick = ParsedPick(
            id=901,
            raw_message_id=901,
            es_apuesta=True,
            acierto=True,  # ya liquidado — también se re-etiqueta
            mercado="doble oportunidad",
            seleccion="Irlanda gana",
        )
        session.add_all([raw, pick])
        await session.commit()

        fixed = await fix_dnb_labels(session)
        await session.commit()
        await session.refresh(pick)

        assert fixed == [901]
        assert pick.mercado == "resultado sin empate"
        assert "dnbcorr" in (pick.metodo or "")

    async def test_no_toca_dc_explicito_ni_fuente_limpia(self, session):
        from app.models.parsed_pick import ParsedPick
        from app.models.telegram_raw_message import TelegramRawMessage
        from app.services.maintenance.repair import fix_dnb_labels

        raws = [
            TelegramRawMessage(
                id=902,
                channel_id=-1001,
                message_id=2,
                channel_name="c",
                text="doble oportunidad levante o empate",
            ),
            TelegramRawMessage(
                id=903,
                channel_id=-1001,
                message_id=3,
                channel_name="c",
                text="LEVANTE GANA EL PARTIDO",
            ),
        ]
        picks = [
            # Selección explícita + fuente con "sin empate": no es
            # ambigua, no se toca.
            ParsedPick(
                id=902,
                raw_message_id=902,
                es_apuesta=True,
                mercado="doble oportunidad",
                seleccion="Levante y empate",
            ),
            # Ambigua pero fuente sin DNB: no se toca.
            ParsedPick(
                id=903,
                raw_message_id=903,
                es_apuesta=True,
                mercado="doble oportunidad",
                seleccion="Levante gana",
            ),
        ]
        session.add_all(raws + picks)
        await session.commit()

        assert await fix_dnb_labels(session) == []


class _Leg:
    """Pata mínima para probar supersede/herencia sin BD."""

    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.orden = kw.get("orden", 0)
        self.seleccion = kw.get("seleccion")
        self.acierto = kw.get("acierto")
        self.anulada = kw.get("anulada", False)
        self.es_apuesta = kw.get("es_apuesta", True)
        self.motivo_anulada = kw.get("motivo_anulada")
        self.verificado_por = kw.get("verificado_por")
        self.verificado_at = kw.get("verificado_at")
        self.verificado_provider = kw.get("verificado_provider")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Sesión falsa: exec devuelve siempre las filas inyectadas."""

    def __init__(self, rows):
        self._rows = rows
        self.added = []

    async def exec(self, _stmt):
        return _Result(self._rows)

    async def flush(self):
        return None

    def add(self, obj):
        self.added.append(obj)


class TestLegVerdictInheritance:
    async def test_supersede_captura_veredicto_antes_de_marcar(self):
        old = [
            _Leg(orden=0, seleccion="GANA DE JONG", acierto=True, anulada=True),
            _Leg(orden=1, seleccion="+7,5 JUEGOS", anulada=True),
            _Leg(orden=2, seleccion="PENDIENTE"),
        ]
        session = _Session(old)
        legs, verdicts = await _supersede_legs(session, _Leg())
        assert len(legs) == 3
        # Solo las cerradas entran en el mapa (antes de pisar anulada).
        assert len(verdicts) == 2
        # Las viejas quedan excluidas del escaneo.
        assert all(leg.es_apuesta is False and leg.anulada for leg in old)

    async def test_pata_nueva_hereda_veredicto_de_su_gemela(self):
        """La pata clonada por re-extracción hereda el cierre de la vieja."""
        twin = _Leg(orden=1, seleccion="+7,5 JUEGOS", anulada=True)
        new = _Leg(orden=1, seleccion="+7,5 JUEGOS", es_apuesta=True)
        session = _Session([new])
        inherited = await _inherit_leg_verdicts(
            session, _Leg(), {(1, "+7,5 juegos"): twin}
        )
        assert inherited == 1
        assert new.anulada is True

    async def test_pata_sin_gemela_queda_pendiente(self):
        new = _Leg(orden=0, seleccion="OTRA APUESTA", es_apuesta=True)
        session = _Session([new])
        inherited = await _inherit_leg_verdicts(
            session, _Leg(), {(1, "+7,5 juegos"): _Leg(anulada=True)}
        )
        assert inherited == 0
        assert new.anulada is False
        assert new.acierto is None
