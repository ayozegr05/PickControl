"""Detección de picks pendientes con extracción rota (ciclo repair)."""

from app.services.maintenance.repair import (
    _inherit_leg_verdicts,
    _pick_needs_repair,
    _supersede_legs,
    _tournament_only,
)


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
