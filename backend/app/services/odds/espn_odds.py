"""Proveedor de odds vía `pickcenter` de ESPN (DraftKings embebido).

El endpoint `summary?event=` del site API de ESPN trae las cuotas de
DraftKings para fútbol/baloncesto: moneyline (1X2 o 2-way), spread y
total (over/under), en formato americano. Gratis, sin key ni cuota —
va primero en la cadena de odds y ahorra la cuota diaria de
allsportsapi2/tennisapi1.

Cobertura: solo los tres mercados que DraftKings expone a ESPN
(ganador, hándicap, total). Mercados ricos (córners, tarjetas, BTTS,
props) no existen en `pickcenter` — esos siguen en la familia Sofascore.

`event_ext_id` = "espn:{sport_path}:{league}:{event_id}" — la liga va
embebida porque el path de `summary` la necesita.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.logging import get_logger
from app.services.odds.base import EventRef, MarketChoice
from app.services.results.espn import (
    EspnBasketballProvider,
    EspnCoreProvider,
    EspnProvider,
    _competitor_name,
    _competitors,
)

logger = get_logger("app.odds.espn")


def _american_to_decimal(raw: object) -> Optional[float]:
    """Cuota americana -> decimal. -500 -> 1.20, +350 -> 4.50."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    if value > 0:
        return round(1.0 + value / 100.0, 4)
    return round(1.0 + 100.0 / abs(value), 4)


class EspnOddsProvider:
    """Odds de pickcenter para fútbol y baloncesto.

    Reutiliza el núcleo de resultados por composición (misma maquinaria
    de scoreboard/summary, mismas cachés por ciclo, mismo contador de
    llamadas en el panel bajo el nombre "espn").
    """

    NAME = "espn"
    # Prefijo del espacio de ids que emite `find_event` — el snapshotter
    # salta este provider al capturar ids de otra familia (sofascore:*).
    ID_PREFIX = "espn:"
    SUPPORTED_SPORTS = frozenset({"futbol", "baloncesto"})

    def __init__(self) -> None:
        self._engines: dict[str, EspnCoreProvider] = {
            "futbol": EspnProvider(),
            "baloncesto": EspnBasketballProvider(),
        }

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        """Scoreboard -> evento del pick. Ojo: NO exige partido
        terminado — las cuotas se capturan antes del inicio."""
        engine = self._engines.get(sport)
        if engine is None:
            return None
        found = await engine._find_event(date, team_hint)
        if found is None:
            return None
        event, competition, league = found
        home, away = _competitors(competition)
        if event.get("id") is None:
            return None
        start = None
        raw_date = competition.get("date") or competition.get("startDate")
        if raw_date:
            try:
                start = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                start = start.replace(tzinfo=None)
            except ValueError:
                pass
        return EventRef(
            event_ext_id=f"espn:{engine._SPORT_PATH}:{league}:{event['id']}",
            home_team=_competitor_name(home or {}),
            away_team=_competitor_name(away or {}),
            start=start,
        )

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        """Mercados de pickcenter mapeados a los nombres canónicos que
        ya consume el comparador ("Full time", "Match goals",
        "Asian handicap")."""
        engine = self._engines.get(sport)
        if engine is None:
            return None
        try:
            _, sport_path, league, event_id = event_ext_id.split(":", 3)
        except ValueError:
            return None
        if sport_path != engine._SPORT_PATH:
            return None
        summary = await engine._summary(league, event_id)
        if summary is None:
            return None

        # Nombres de equipo desde el header del summary (el comparador
        # casa `choice_name` con home/away del OddsEvent).
        header_comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
        home_c, away_c = _competitors(header_comp)
        home_name = _competitor_name(home_c or {})
        away_name = _competitor_name(away_c or {})

        choices: list[MarketChoice] = []
        books = summary.get("pickcenter") or summary.get("odds") or []
        for book in books:
            home_odds = book.get("homeTeamOdds") or {}
            away_odds = book.get("awayTeamOdds") or {}
            draw_odds = book.get("drawOdds") or {}

            # Ganador: "Full time" con nombres de equipo y "X" para el
            # empate — mismo convenio que los mirrors Sofascore.
            home_ml = _american_to_decimal(home_odds.get("moneyLine"))
            away_ml = _american_to_decimal(away_odds.get("moneyLine"))
            draw_ml = _american_to_decimal(draw_odds.get("moneyLine"))
            if home_ml is not None or away_ml is not None:
                for choice_name, cuota in (
                    (home_name, home_ml),
                    (away_name, away_ml),
                ):
                    if choice_name and cuota is not None:
                        choices.append(
                            MarketChoice(
                                market_name="Full time",
                                choice_name=choice_name,
                                cuota=cuota,
                            )
                        )
                if draw_ml is not None:
                    choices.append(
                        MarketChoice(
                            market_name="Full time",
                            choice_name="X",
                            cuota=draw_ml,
                        )
                    )

            # Total: "Match goals" (fútbol) / "Total points" (basket),
            # línea en choice_group como en Sofascore.
            line = book.get("overUnder")
            over = _american_to_decimal(book.get("overOdds"))
            under = _american_to_decimal(book.get("underOdds"))
            if line is not None and (over is not None or under is not None):
                market = "Match goals" if sport == "futbol" else "Total points"
                for choice_name, cuota in (("Over", over), ("Under", under)):
                    if cuota is not None:
                        choices.append(
                            MarketChoice(
                                market_name=market,
                                choice_name=choice_name,
                                cuota=cuota,
                                choice_group=str(line),
                            )
                        )

            # Hándicap: `spread` es la línea del FAVORITO (negativa).
            # away favorite -> (-spread) away / (+spread) home.
            spread = book.get("spread")
            away_spread_odds = _american_to_decimal(away_odds.get("spreadOdds"))
            home_spread_odds = _american_to_decimal(home_odds.get("spreadOdds"))
            if spread is not None and (
                away_spread_odds is not None or home_spread_odds is not None
            ):
                away_fav = bool(away_odds.get("favorite"))
                away_line = -abs(float(spread)) if away_fav else abs(float(spread))
                home_line = -away_line
                for choice_name, cuota, side_line in (
                    (away_name, away_spread_odds, away_line),
                    (home_name, home_spread_odds, home_line),
                ):
                    if choice_name and cuota is not None:
                        choices.append(
                            MarketChoice(
                                market_name="Asian handicap",
                                choice_name=f"({side_line:+g}) {choice_name}",
                                cuota=cuota,
                            )
                        )

        return choices
