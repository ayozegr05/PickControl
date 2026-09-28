"""Tests del provider determinista transfermarkt.es.

El HTML de los fixtures replica el formato real verificado: listado
`/live/index?datum=YYYY-MM-DD` con filas `begegnungZeile` (equipos en
`verein-heim`/`verein-gast`, `matchresult finished` con marcador
inline) y tabla de stats `unterueberschrift` + `sb-statistik-zahl`.
"""

from datetime import datetime

import pytest

import app.services.results.transfermarkt as tm
from app.services.results.transfermarkt import TransfermarktProvider

LIVE_ROW = """
<tr id="4909564" class="begegnungZeile">
<td colspan="1" class="zeit hide-for-small"> Jornada 6 </td>
<td class="club club-live verein-heim">
<a href="/fc-elche/spielplan/verein/1531"> Elche CF
<img src="x.png" title="Elche CF" alt="Elche CF" class="lazy" /> </a> </td>
<td class="ergebnis"> <a title="Informe" href="/spielbericht/index/spielbericht/4909564">
<span class="matchresult finished">2:3</span></a> </td>
<td class="club club-live away verein-gast">
<a href="/real-madrid/spielplan/verein/418"> Real Madrid
<img src="x.png" title="Real Madrid" alt="Real Madrid" class="lazy" /> </a> </td>
</tr>
"""

LIVE_PAGE = f"<html><body><table>{LIVE_ROW}</table></body></html>"

LIVE_PAGE_SCHEDULED = LIVE_PAGE.replace(
    'matchresult finished">2:3', 'matchresult">-:-'
).replace('id="4909564"', 'id="4909564"')

STATS_PAGE = """
<html><body>
<div class="unterueberschrift"> Disparos totales </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-wert">
<div class="sb-statistik-mitte"></div>
<div class="sb-statistik-zahl" style="color: #fff;">11</div> </div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-wert">
<div class="sb-statistik-mitte"></div>
<div class="sb-statistik-zahl" style="color: #fff;">20</div> </div> </li>
</ul> </div>
<div class="unterueberschrift"> Disparos fuera </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-zahl">6</div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-zahl">11</div> </li>
</ul> </div>
<div class="unterueberschrift"> Paraden </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-zahl">2</div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-zahl">1</div> </li>
</ul> </div>
<div class="unterueberschrift"> C&oacute;rneres </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-zahl">2</div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-zahl">5</div> </li>
</ul> </div>
<div class="unterueberschrift"> Faltas </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-zahl">21</div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-zahl">8</div> </li>
</ul> </div>
<div class="unterueberschrift"> Fueras de juego </div>
<div class="sb-statistik"> <ul>
<li class="sb-statistik-heim"> <div class="sb-statistik-zahl">1</div> </li>
<li class="sb-statistik-gast"> <div class="sb-statistik-zahl">5</div> </li>
</ul> </div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _limpiar_cache():
    tm._list_cache.clear()
    yield
    tm._list_cache.clear()


def _stub_pages(provider: TransfermarktProvider, pages: dict[str, str]):
    async def fake_fetch(url: str):
        return pages.get(url)

    provider._fetch = fake_fetch  # type: ignore[method-assign]


class TestFindMatch:
    async def test_resuelve_fixture_terminado(self):
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (2, 3)
        assert match.home_team == "Elche CF"

    async def test_no_resuelve_si_no_ha_terminado(self):
        """Sin 'finished' no hay marcador: reintentable, no miss."""
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE_SCHEDULED,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert match is None

    async def test_tolerancia_fecha_mas_menos_un_dia(self):
        """El partido en el listado del día anterior casa igual."""
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-14": LIVE_PAGE,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (2, 3)

    async def test_ignora_fixture_que_no_casa(self):
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 15, 22, 0), "Sevilla - Betis"
        )
        assert match is None


class TestFindMatchStats:
    async def test_stats_con_claves_canonicas(self):
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE,
                "https://www.transfermarkt.es/statistik/index/spielbericht/4909564": STATS_PAGE,
            },
        )
        stats = await provider.find_match_stats(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert stats is not None
        assert stats.values["Corner Kicks"] == (2, 5)
        assert stats.values["Fouls"] == (21, 8)
        assert stats.values["Offsides"] == (1, 5)
        assert stats.values["Total Shots"] == (11, 20)
        assert stats.values["Goalkeeper Saves"] == (2, 1)

    async def test_tiros_a_puerta_derivados(self):
        """Shots on Goal = totales - fuera cuando no hay fila explícita."""
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE,
                "https://www.transfermarkt.es/statistik/index/spielbericht/4909564": STATS_PAGE,
            },
        )
        stats = await provider.find_match_stats(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert stats is not None
        assert stats.values["Shots on Goal"] == (5, 9)

    async def test_sin_partido_terminado_no_descarga_stats(self):
        provider = TransfermarktProvider()
        _stub_pages(
            provider,
            {
                "https://www.transfermarkt.es/live/index?datum=2026-09-15": LIVE_PAGE_SCHEDULED,
            },
        )
        stats = await provider.find_match_stats(
            datetime(2026, 9, 15, 22, 0), "Elche CF - Real Madrid"
        )
        assert stats is None
