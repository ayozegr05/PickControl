"""Tests del provider determinista football24hours.com (cola larga).

El HTML de los fixtures replica el formato real verificado en producción:
listados con slugs `/{pais}/{liga}/{DD-MM-YYYY}-{home}-vs-{away}.html` y
páginas de partido con `board--match-summary-*` + `timeline--statistic-*`.
"""

from datetime import datetime

import pytest

import app.services.results.football24h as f24h
from app.services.results.football24h import Football24hProvider

LEAGUE_PAGE = """
<html><body>
<a href="/denmark/u21-ligaen/21-09-2026-sonderjyske-reserves-vs-silkeborg-reserves.html">x</a>
<a href="/denmark/u21-ligaen/24-09-2026-vejle-reserves-vs-ob-reserves.html">x</a>
<a href="/denmark/u21-ligaen/30-09-2026-hb-koge-reserves-vs-lyngby-reserves.html">x</a>
</body></html>
"""

FIRST_DIV_PAGE = """
<html><body>
<a href="/denmark/1st-division/21-09-2026-hb-koge-vs-hobro.html">x</a>
</body></html>
"""

MATCH_PAGE = """
<html><body>
<div class="board--match-summary">
<a href="/team/sonderjyske-reserves" class="board--match-summary-team board--match-summary-hometeam">
<span class="board--match-summary-team-square">
<p class="board--match-summary-team-square-label">SønderjyskE Reserves</p>
</span></a>
<div class="board--match-summary-scoreboard">
<div class="board--match-summary-scoreboard-scores">
<span class="board--match-summary-scoreboard-score">0</span><span> - </span>
<span class="board--match-summary-scoreboard-score">0</span>
</div></div>
<div class="board--match-summary-status">
<span class="board--match-summary-status-label board--match-summary-status-label-final_time">FINAL-TIME</span>
</div>
<a href="/team/silkeborg-reserves" class="board--match-summary-team board--match-summary-awayteam">
<span class="board--match-summary-team-square">
<p class="board--match-summary-team-square-label">Silkeborg Reserves</p>
</span></a>
</div>
<div class="timeline--statistics"> <div class="timeline--statistic-hometeam"> 39 </div>
<div class="timeline--statistic-type"> Possession </div>
<div class="timeline--statistic-awayteam"> 61 </div> </div>
<div class="timeline--statistics"> <div class="timeline--statistic-hometeam"> 2 </div>
<div class="timeline--statistic-type"> Corners </div>
<div class="timeline--statistic-awayteam"> 10 </div> </div>
</body></html>
"""

SCHEDULED_PAGE = MATCH_PAGE.replace(
    'final_time">FINAL-TIME', 'scheduled">SCHEDULED'
).replace("FINAL-TIME", "SCHEDULED")


@pytest.fixture(autouse=True)
def _limpiar_cache():
    f24h._list_cache.clear()
    yield
    f24h._list_cache.clear()


def _stub_pages(provider: Football24hProvider, pages: dict[str, str]):
    async def fake_fetch(path: str):
        return pages.get(path)

    provider._fetch = fake_fetch  # type: ignore[method-assign]


class TestFindMatch:
    async def test_resuelve_fixture_de_liga(self):
        provider = Football24hProvider()
        _stub_pages(
            provider,
            {
                "/denmark/u21-ligaen/": LEAGUE_PAGE,
                "/denmark/u21-ligaen/21-09-2026-sonderjyske-reserves-vs-silkeborg-reserves.html": MATCH_PAGE,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 21, 10, 5),
            "SønderjyskE Reserves vs Silkeborg Reserves",
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (0, 0)

    async def test_no_resuelve_si_no_es_final(self):
        provider = Football24hProvider()
        _stub_pages(
            provider,
            {
                "/denmark/u21-ligaen/": LEAGUE_PAGE,
                "/denmark/u21-ligaen/21-09-2026-sonderjyske-reserves-vs-silkeborg-reserves.html": SCHEDULED_PAGE,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 21, 10, 5),
            "SønderjyskE Reserves vs Silkeborg Reserves",
        )
        assert match is None

    async def test_alias_club_renombrado(self):
        """Lion City Sailors casa con el slug histórico 'home-united'."""
        provider = Football24hProvider()
        afc = """
        <a href="/asia/afc-cup/17-09-2026-home-united-vs-bangkok-glass.html">x</a>
        """
        page = MATCH_PAGE.replace("SønderjyskE Reserves", "Home United").replace(
            "Silkeborg Reserves", "Bangkok Glass"
        )
        _stub_pages(
            provider,
            {
                "/asia/afc-cup/": afc,
                "/asia/afc-cup/17-09-2026-home-united-vs-bangkok-glass.html": page,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 17, 10, 0),
            "Lion City Sailors FC - BG Pathum United F.C.",
        )
        assert match is not None

    async def test_ignora_fecha_distinta(self):
        provider = Football24hProvider()
        _stub_pages(provider, {"/denmark/u21-ligaen/": LEAGUE_PAGE})
        match = await provider.find_match(
            datetime(2026, 9, 25, 10, 5),
            "SønderjyskE Reserves vs Silkeborg Reserves",
        )
        assert match is None


class TestFindMatchStats:
    async def test_stats_con_claves_canonicas(self):
        provider = Football24hProvider()
        _stub_pages(
            provider,
            {
                "/denmark/u21-ligaen/": LEAGUE_PAGE,
                "/denmark/u21-ligaen/21-09-2026-sonderjyske-reserves-vs-silkeborg-reserves.html": MATCH_PAGE,
            },
        )
        stats = await provider.find_match_stats(
            datetime(2026, 9, 21, 10, 5),
            "SønderjyskE Reserves vs Silkeborg Reserves",
        )
        assert stats is not None
        assert stats.values["Corner Kicks"] == (2, 10)
        assert stats.values["Ball Possession"] == (39, 61)

    async def test_otra_liga_configurada(self):
        """El escaneo recorre todas las ligas configuradas."""
        provider = Football24hProvider()
        koge_page = (
            MATCH_PAGE.replace("SønderjyskE Reserves", "HB Køge")
            .replace("Silkeborg Reserves", "Hobro")
            .replace('score">0<', 'score">5<', 1)
        )
        _stub_pages(
            provider,
            {
                "/denmark/1st-division/": FIRST_DIV_PAGE,
                "/denmark/1st-division/21-09-2026-hb-koge-vs-hobro.html": koge_page,
            },
        )
        match = await provider.find_match(
            datetime(2026, 9, 21, 17, 0), "HB Køge - Hobro IK"
        )
        assert match is not None
        assert match.home_team == "HB Køge"
