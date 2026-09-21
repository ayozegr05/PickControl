"""Fallback de baloncesto: allsportsapi2 (Sofascore vía RapidAPI).

Host: allsportsapi2.p.rapidapi.com — mismo backend Sofascore que
footapi7, pero con cuota diaria propia: los rescates de basket no
restan llamadas al provider de fútbol.

Reutiliza `FootApiStatsProvider` cambiando solo lo específico del
deporte: el filtro `sport.slug` de `/api/search` ("basketball"), la
etiqueta de las claves de miss y el marcador al descanso (en basket
Sofascore reparte el marcador por cuartos: HT = period1 + period2).

Cadena de baloncesto: api-basketball (api-sports, ventana ±1 día)
-> este provider (sin ventana — rescata partidos ya caducados para
el plan free). `NAME = "allsportsapi2"` a propósito: es la misma
suscripción que tenis/odds, así que un 429 de cualquiera marca la
cuota compartida y los misses usan la etiqueta `baloncesto` para no
colisionar con `allsportsapi2|results|tenis|...`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.services.results.base import MatchEvents, MatchPlayers, MatchStats
from app.services.results.footapi_stats import FootApiStatsProvider


class SofascoreBasketballProvider(FootApiStatsProvider):
    """Resultados de baloncesto vía allsportsapi2 (Sofascore)."""

    NAME = "allsportsapi2"
    SUPPORTED_SPORTS = frozenset({"baloncesto"})
    _SPORT_SLUG = "basketball"
    _SPORT_TAG = "baloncesto"
    # Los ids de entidad son globales en Sofascore: con namespace propio
    # un equipo de basket nunca choca con un jugador de tenis cacheado
    # bajo la misma suscripción "allsportsapi2".
    _ENTITY_NS = "allsportsapi2-basket"

    def _ht_scores(self, event: dict) -> tuple[Optional[int], Optional[int]]:
        """HT = suma de los dos primeros cuartos (period1 + period2)."""
        home, away = event.get("homeScore") or {}, event.get("awayScore") or {}
        q1h, q2h = home.get("period1"), home.get("period2")
        q1a, q2a = away.get("period1"), away.get("period2")
        if None in (q1h, q2h, q1a, q2a):
            return None, None
        return q1h + q2h, q1a + q2a

    # Los mercados de baloncesto solo necesitan el marcador: los métodos
    # de stats/incidents de la clase base son fútbol-específicos
    # (córners, tarjetas, goles) y consultarlos quemaría cuota para
    # nada. Se desactivan devolviendo None.
    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        return None

    async def find_match_stats_1h(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        return None

    async def find_match_events(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchEvents]:
        return None

    async def find_match_players(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchPlayers]:
        return None
