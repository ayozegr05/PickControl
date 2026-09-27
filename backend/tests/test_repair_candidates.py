"""Detección de picks pendientes con extracción rota (ciclo repair)."""

from app.services.maintenance.repair import _pick_needs_repair, _tournament_only


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
