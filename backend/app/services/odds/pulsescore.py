"""PulseScore — espejo NORMALIZADO del feed bet365 (pulsescore.net).

Mismo dato que BetsAPI (los `eventId` son los FI de bet365: el
Ipswich-Liverpool es 196565074 en los dos) pero con otro esquema y
otra cuota: plan BASIC gratis = **500 req/MES**, 1 req/segundo,
auth por header `X-Secret`.

Igual que el wrapper RapidAPI, el prematch solo vive mientras el
partido no ha empezado — el evento desaparece del board al entrar
en vivo. Sirve para CONGELAR cuotas de futuros en el snapshotter;
el backlog histórico es cosa del cliente directo de pago.

Economía de cuota — la pieza clave es el board:
`GET /{sport}/events` devuelve los eventos próximos "soonest
first" **con todos los mercados dentro del payload**. Una sola
llamada trae 30 eventos con cuotas, así que `find_event` cachea
cada evento recibido y `fetch_odds` resuelve desde esa caché sin
segunda llamada. Además los `find_event` posteriores del mismo
ciclo buscan primero en la caché: un barrido de una página cubre
de golpe muchos picks del día.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.services.results.base import (
    is_rate_limited,
    log_remaining_quota,
    mark_cooldown,
    mark_rate_limited,
    utc_now,
)

from .base import EventRef, MarketChoice
from .betsapi import SPORT_IDS, BetsFixture, match_fixture

logger = logging.getLogger("app.odds.pulsescore")

NAME = "pulsescore"

# Deporte canónico -> prefijo de ruta del feed bet365 en PulseScore.
_SPORT_PATHS = {"futbol": "soccer", "tenis": "tennis", "baloncesto": "basketball"}

# Paginación del board: 30 eventos/página ordenados "soonest
# first". Con parada por horizonte de fecha la mayoría de lookups
# caben en 1-2 páginas; el tope protege la cuota mensual si un pick
# apunta a un evento lejano que aún no sale en el board.
_MAX_PAGES = 5
# Presupuesto de páginas POR CICLO del snapshotter, compartido por
# todos los find_event: sin él, N eventos aún no publicados harían
# N×5 llamadas por ciclo — el plan (500/mes) no llegaría a fin de
# mes. Las páginas ya traen los mercados dentro, así que cada una
# puede resolver varios eventos pendientes de golpe.
_MAX_PAGES_PER_CYCLE = 8
# Horizonte de parada: el board está ordenado por inicio — cuando
# los eventos recibidos ya superan la ventana ancha de match_fixture
# (±4 días, por picks con fecha de repost), el nuestro no está
# publicado todavía y seguir paginando solo gasta cuota.
_BOARD_STOP_TOLERANCE = timedelta(days=5)
# Caché de eventos recibidos (mercados incluidos): suficiente para
# unas ~13 páginas de fútbol sin vaciar la RAM.
_EVENT_CACHE_MAX = 400
# Plan BASIC: 1 req/segundo. Margen sobre el límite documentado —
# su `x-ratelimit-remaining` es por-ventana-de-segundo y un 429 sin
# headers RapidAPI aparcaba el provider el día entero.
_MIN_REQUEST_GAP = timedelta(seconds=1.5)
# Espera tras un 429: su ventana es ~1 s; 2.5 s da margen sobrado
# sin que un barrido se eternice.
_RETRY_GAP = timedelta(seconds=2.5)


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------


class PulseScoreClient:
    """Transporte `api.pulsescore.net` con el manejo de cuota del proyecto."""

    def __init__(
        self,
        api_key: str,
        host: str = "api.pulsescore.net",
        bookmaker: str = "v3/bet365",
        name: str = NAME,
    ) -> None:
        self.NAME = name
        self._base_url = f"https://{host}/api/{bookmaker}"
        self._headers = {
            "X-Secret": api_key,
            "Accept-Encoding": "gzip",
        }
        self.calls = 0
        # Plan BASIC: 1 req/segundo — sin pacing las páginas del
        # board salen a ~300 ms y el 429 aparcaba el provider el
        # DÍA entero (sus headers `x-ratelimit-remaining` no son
        # los de RapidAPI y `_cooldown_from` no los reconoce).
        self._next_slot: Optional[datetime] = None

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). 0 = red o cuota aparcada.

        401/403 (key inválida o restricción de plan) aparcan el
        provider — insistir solo quema el día. Un 429 aquí es el
        límite de 1 req/seg, no la cuota mensual: cooldown corto."""
        if is_rate_limited(self.NAME):
            return 0, None
        response: Optional[httpx.Response] = None
        for attempt in range(3):
            if self._next_slot is not None:
                wait = (self._next_slot - utc_now()).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
            self.calls += 1
            self._next_slot = utc_now() + _MIN_REQUEST_GAP
            try:
                response = await client.get(
                    f"{self._base_url}{path}", params=params, headers=self._headers
                )
            except httpx.HTTPError as exc:
                logger.warning("[ODDS:%s] Error de red en %s: %s", self.NAME, path, exc)
                return 0, None
            log_remaining_quota(self.NAME, response)
            if response.status_code != 429:
                break
            if attempt < 2:
                logger.info(
                    "[ODDS:%s] 429 (req/s) en %s — reintento tras pausa",
                    self.NAME,
                    path,
                )
                self._next_slot = utc_now() + _RETRY_GAP
        else:
            # 3 intentos seguidos en 429: más que un pico — cooldown
            # corto y se reintenta en el próximo ciclo.
            mark_cooldown(self.NAME, utc_now() + timedelta(seconds=60))
            logger.warning("[ODDS:%s] 429 persistente — cooldown 60s", self.NAME)
            return 429, None
        if response.status_code in (401, 403):
            mark_rate_limited(self.NAME, response)
            logger.warning(
                "[ODDS:%s] %s — provider aparcado",
                self.NAME,
                response.status_code,
            )
            return response.status_code, None
        if response.status_code >= 400:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    async def events(
        self, client: httpx.AsyncClient, sport_path: str, page: int
    ) -> tuple[int, Optional[list[dict]]]:
        """Una página del board pre-match del deporte (mercados incluidos)."""
        status, data = await self._get(
            client, f"/{sport_path}/events", params={"page": page, "limit": 30}
        )
        if status != 200 or not isinstance(data, dict):
            return status or 0, None
        events = data.get("events")
        return status, events if isinstance(events, list) else []

    async def event(
        self, client: httpx.AsyncClient, sport_path: str, event_id: str
    ) -> tuple[int, Optional[dict], bool]:
        """Un evento por FI -> (status, evento|None, desaparecido).

        `data: null` = evento desconocido o ya fuera del board
        (empezado): miss definitivo del prematch, no error."""
        status, data = await self._get(client, f"/{sport_path}/events/{event_id}")
        if status == 404:
            return status, None, True
        if status != 200 or not isinstance(data, dict):
            return status or 0, None, False
        event = data.get("data")
        if event is None:
            return status, None, True
        return status, event if isinstance(event, dict) else None, False


# ---------------------------------------------------------------------------
# Parseo de eventos -> BetsFixture (reutiliza match_fixture de betsapi)
# ---------------------------------------------------------------------------


def _parse_start(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return (
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


def _parse_event(raw: dict, sport: str) -> Optional[BetsFixture]:
    """Evento del board -> BetsFixture para `match_fixture`.

    Mismos FI que BetsAPI — el `sport_id` se reutiliza del mapa
    bet365 para no duplicar tablas."""
    if raw.get("live"):
        return None  # ya empezado: el prematch no sirve para congelar
    league = str(raw.get("league") or "")
    if re.search(r"esoccer|e-soccer|virtual|simulated", league, re.I):
        return None
    event_id = raw.get("eventId")
    home = str(raw.get("home") or "").strip()
    away = str(raw.get("away") or "").strip()
    if not event_id or not home or not away:
        return None
    return BetsFixture(
        fixture_id=str(event_id),
        home_name=home,
        away_name=away,
        start=_parse_start(raw.get("startTime")),
        sport_id=SPORT_IDS.get(sport, 0),
        league_name=league,
    )


# ---------------------------------------------------------------------------
# Traducción de mercados normalizados -> vocabulario de compare.py
# ---------------------------------------------------------------------------


def _cuota(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _sel_line(sel: dict) -> Optional[str]:
    """Línea del mercado tal como la publica bet365.

    En escaleras 3-way el `line` varía por opción (Over 9.5 /
    Exactly 9 / Under 8.5) pero `moreInfo.CO_LINE`/`HA` lleva la
    línea entera compartida ('9') — la que escribe el tipster."""
    info = sel.get("moreInfo") or {}
    for key in ("CO_LINE", "HA"):
        value = info.get(key)
        if value is not None and str(value).strip():
            return str(value)
    line = sel.get("line")
    if line is None:
        return None
    try:
        return f"{float(line):g}"
    except (TypeError, ValueError):
        return str(line)


def _is_exactly(sel: dict) -> bool:
    return str(sel.get("rawName") or "").strip().lower() == "exactly"


def _ou_choice(sel: dict) -> Optional[str]:
    outcome = str(sel.get("canonicalOutcome") or "").upper()
    raw = str(sel.get("rawName") or "").strip().lower()
    if outcome == "OVER" or raw == "over":
        return "Over"
    if outcome == "UNDER" or raw == "under":
        return "Under"
    if _is_exactly(sel):
        return "Exactly"
    return None


class _Emitter:
    def __init__(self) -> None:
        self.choices: list[MarketChoice] = []
        self._seen: set[tuple] = set()

    def add(
        self,
        market: str,
        choice: str,
        group: Optional[str],
        sel: dict,
    ) -> None:
        key = (market, choice, group)
        cuota = _cuota(sel.get("odds"))
        # Sin precio no hay comparación posible (suspended del feed
        # llega a veces sin cuota) y `cuota` es NOT NULL en la tabla.
        if key in self._seen or not choice or cuota is None:
            return
        self._seen.add(key)
        self.choices.append(
            MarketChoice(
                market_name=market,
                choice_name=choice,
                cuota=cuota,
                # varchar(80): grupos crudos del feed (etiquetas de
                # especiales) la sobrepasaban y el flush rechazaba la
                # tanda entera del evento.
                choice_group=group[:80] if group else group,
                suspended=not sel.get("isActive", True),
            )
        )

    def ou_market(self, selections: list[dict], market_2w: str, market_3w: str) -> None:
        """O/U con nombre por contenido: si la escalera trae 'Exactly'
        es el 3-way de líneas enteras del tipster."""
        market = market_3w if any(_is_exactly(s) for s in selections) else market_2w
        for sel in selections:
            choice = _ou_choice(sel)
            if choice:
                self.add(market, choice, _sel_line(sel), sel)

    def team_ou(self, selections: list[dict], market_tpl: str) -> None:
        """Totales por equipo: 'Team Cards' llega con lado en
        canonicalOutcome y la dirección en moreInfo.HD ('Over 1.5')."""
        for sel in selections:
            outcome = str(sel.get("canonicalOutcome") or "").upper()
            side = {"HOME": "1", "AWAY": "2"}.get(outcome)
            hd = str((sel.get("moreInfo") or {}).get("HD") or "")
            m = re.match(r"(over|under)", hd.strip(), re.I)
            choice = _ou_choice(sel) or (m.group(1).title() if m else None)
            line = _sel_line(sel)
            if side and choice and line:
                self.add(market_tpl.format(side), choice, line, sel)


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


# Mercados O/U etiquetados por equipo -> plantilla "Team {1|2} X".
_TEAM_OU = {
    "team cards": "Team {} cards",
}


def translate_event(
    event: dict, sport: str, home: str = "", away: str = ""
) -> list[MarketChoice]:
    """Evento del board/evento único -> opciones en vocabulario del
    comparador. Lo no reconocido se guarda con su `rawName`: es
    evidencia auditable aunque el comparador aún no lo case."""
    emitter = _Emitter()
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        selections = [
            s for s in (market.get("selections") or []) if isinstance(s, dict)
        ]
        if not selections:
            continue
        canon = str(market.get("canonicalMarket") or "").upper()
        raw = _norm(market.get("rawName"))
        period = str(market.get("period") or "").upper()
        full_time = period in ("", "FULL_TIME")

        if canon == "MATCH_RESULT" and full_time:
            for sel in selections:
                choice = {"HOME": "1", "DRAW": "X", "AWAY": "2"}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                if choice:
                    emitter.add("Full time", choice, None, sel)
        elif (
            canon == "MATCH_RESULT"
            and period == "FIRST_HALF"
            or canon == "HALF_TIME_RESULT"
        ):
            for sel in selections:
                choice = {"HOME": "1", "DRAW": "X", "AWAY": "2"}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                if choice:
                    emitter.add("1st half", choice, None, sel)
        elif canon == "DOUBLE_CHANCE" and full_time:
            for sel in selections:
                choice = {
                    "HOME_DRAW": "1X",
                    "DRAW_AWAY": "X2",
                    "HOME_AWAY": "12",
                }.get(str(sel.get("canonicalOutcome") or "").upper())
                if choice:
                    emitter.add("Double chance", choice, None, sel)
        elif canon == "DRAW_NO_BET" and full_time:
            for sel in selections:
                side = {"HOME": home, "AWAY": away}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                emitter.add("Draw no bet", side or str(sel.get("rawName")), None, sel)
        elif canon == "BOTH_TEAMS_TO_SCORE" and full_time:
            for sel in selections:
                name = str(sel.get("rawName") or "").strip().title()
                emitter.add("Both teams to score", name or "?", None, sel)
        elif canon == "FIRST_TEAM_TO_SCORE" and full_time:
            for sel in selections:
                outcome = str(sel.get("canonicalOutcome") or "").upper()
                raw_name = str(sel.get("rawName") or "").lower()
                if outcome == "HOME":
                    choice = "1"
                elif outcome == "AWAY":
                    choice = "2"
                elif "no" in raw_name or "none" in raw_name:
                    choice = "X"
                else:
                    continue
                emitter.add("First team to score", choice, None, sel)
        elif canon == "CORRECT_SCORE" and full_time:
            for sel in selections:
                emitter.add(
                    "Correct score", str(sel.get("rawName") or "").strip(), None, sel
                )
        elif canon == "OVER_UNDER" and full_time:
            # Totales: fútbol/basket -> goles|puntos; tenis -> juegos.
            name = "Total games won" if sport == "tenis" else "Match goals"
            emitter.ou_market(selections, name, name)
        elif canon == "OVER_UNDER" and period in ("FIRST_SET", "SET_1"):
            emitter.ou_market(selections, "1st set games", "1st set games")
        elif canon in ("ASIAN_HANDICAP", "HANDICAP") and full_time:
            name = "Game handicap" if sport == "tenis" else "Asian handicap"
            for sel in selections:
                side = {"HOME": home, "AWAY": away}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                line = _sel_line(sel)
                if side and line:
                    emitter.add(name, f"({line}) {side}", line, sel)
        elif canon in ("ASIAN_HANDICAP", "HANDICAP") and period in (
            "FIRST_SET",
            "SET_1",
        ):
            for sel in selections:
                side = {"HOME": home, "AWAY": away}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                line = _sel_line(sel)
                if side and line:
                    emitter.add("Set handicap", f"({line}) {side}", line, sel)
        elif canon == "SET_BETTING" or (
            canon == "MATCH_RESULT" and period.startswith("SET")
        ):
            for sel in selections:
                side = {"HOME": home or "1", "AWAY": away or "2"}.get(
                    str(sel.get("canonicalOutcome") or "").upper()
                )
                if side:
                    emitter.add("First set winner", side, None, sel)
        elif canon == "CORNERS_OVER_UNDER" and full_time:
            emitter.ou_market(selections, "Corners 2-Way", "Corners 3-Way")
        elif (
            canon in ("HOME_CORNERS_OVER_UNDER", "AWAY_CORNERS_OVER_UNDER")
            and full_time
        ):
            side = "1" if canon.startswith("HOME") else "2"
            emitter.ou_market(
                selections, f"Team {side} corners", f"Team {side} corners"
            )
        elif canon in ("HOME_OVER_UNDER", "AWAY_OVER_UNDER") and full_time:
            side = "1" if canon.startswith("HOME") else "2"
            emitter.ou_market(selections, f"Team {side} goals", f"Team {side} goals")
        elif canon == "ANYTIME_GOALSCORER":
            for sel in selections:
                emitter.add(
                    "Goalscorer anytime",
                    str(sel.get("rawName") or "").strip(),
                    None,
                    sel,
                )
        elif canon == "FIRST_GOALSCORER":
            for sel in selections:
                emitter.add(
                    "First goalscorer", str(sel.get("rawName") or "").strip(), None, sel
                )
        elif canon == "LAST_GOALSCORER":
            for sel in selections:
                emitter.add(
                    "Last goalscorer", str(sel.get("rawName") or "").strip(), None, sel
                )
        elif canon == "PLAYER_CARDS":
            for sel in selections:
                emitter.add(
                    "Player cards", str(sel.get("rawName") or "").strip(), None, sel
                )
        elif raw == "cards" and full_time:
            # Escalera de tarjetas 3-way (Over/Exactly/Under por línea
            # entera — el mercado 'Exactamente N tarjetas' del tipster).
            emitter.ou_market(selections, "Cards in match", "Cards 3-Way")
        elif raw in ("number of cards in match", "asian total cards"):
            emitter.ou_market(selections, "Cards in match", "Cards in match")
        elif raw == "team cards":
            emitter.team_ou(selections, "Team {} cards")
        elif raw == "score or assist":
            for sel in selections:
                emitter.add(
                    "Goalscorer or assist",
                    str(sel.get("rawName") or "").strip(),
                    None,
                    sel,
                )
        elif raw in ("either to assist", "player assists", "assists"):
            for sel in selections:
                emitter.add(
                    "Player assists", str(sel.get("rawName") or "").strip(), None, sel
                )
        elif raw in ("shots", "player shots"):
            for sel in selections:
                player = str(sel.get("rawName") or "").strip()
                co = str((sel.get("moreInfo") or {}).get("CO_LINE") or "")
                m = re.match(r"(\d+)\s*\+", co)
                if player and m:
                    emitter.add(
                        "Player shots", player, f"Over {int(m.group(1)) - 0.5:g}", sel
                    )
        elif raw in ("shots on target", "player shots on target"):
            for sel in selections:
                player = str(sel.get("rawName") or "").strip()
                co = str((sel.get("moreInfo") or {}).get("CO_LINE") or "")
                m = re.match(r"(\d+)\s*\+", co)
                if player and m:
                    emitter.add(
                        "Player shots on target",
                        player,
                        f"Over {int(m.group(1)) - 0.5:g}",
                        sel,
                    )
        elif raw.startswith("multi scorers"):
            group = raw.split("—")[-1].strip() or None
            for sel in selections:
                emitter.add(
                    "Multi scorers", str(sel.get("rawName") or "").strip(), group, sel
                )
        elif raw.startswith("team goalscorer"):
            for sel in selections:
                emitter.add(
                    "Team goalscorer", str(sel.get("rawName") or "").strip(), None, sel
                )
        else:
            # Mercado no mapeado: se conserva con su nombre original.
            market_name = str(market.get("rawName") or "").strip()
            if not market_name:
                continue
            for sel in selections:
                name = str(sel.get("rawName") or "").strip()
                emitter.add(market_name, name or "?", _sel_line(sel), sel)
    return emitter.choices


# ---------------------------------------------------------------------------
# Provider del snapshotter (plan BASIC — 500 req/mes)
# ---------------------------------------------------------------------------


class PulseScoreOddsProvider:
    """OddsProvider sobre PulseScore (espejo bet365 normalizado).

    Congela el prematch de eventos futuros — su especialidad frente
    al wrapper RapidAPI es el esquema normalizado (canonicalMarket +
    moreInfo.CO_LINE) y que el board trae los mercados dentro: una
    página de 30 eventos resuelve el lookup Y las cuotas de todos.

    NUNCA entra en la cascada histórica: el board solo sirve eventos
    sin empezar (los terminados devuelven `data: null`)."""

    NAME = NAME
    ID_PREFIX = "pulsescore:"
    SUPPORTED_SPORTS = frozenset(_SPORT_PATHS)

    def __init__(
        self,
        api_key: str,
        api_host: str = "api.pulsescore.net",
        bookmaker: str = "v3/bet365",
    ) -> None:
        self._client = PulseScoreClient(
            api_key=api_key, host=api_host, bookmaker=bookmaker, name=self.NAME
        )
        # eventId -> evento crudo con mercados: fetch_odds resuelve
        # desde aquí sin gastar una segunda llamada.
        self._events: dict[str, dict] = {}
        # Páginas de board ya gastadas en este ciclo (el provider se
        # instancia por ciclo — el presupuesto se reinicia solo).
        self._pages_spent = 0

    def _cache_events(self, events: list[dict]) -> None:
        for raw in events:
            event_id = str(raw.get("eventId") or "")
            if event_id:
                self._events[event_id] = raw
        while len(self._events) > _EVENT_CACHE_MAX:
            self._events.pop(next(iter(self._events)))

    def _cached_fixtures(self, sport: str) -> list[BetsFixture]:
        return [
            fx
            for fx in (_parse_event(raw, sport) for raw in self._events.values())
            if fx is not None
        ]

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        sport_path = _SPORT_PATHS.get(sport)
        if sport_path is None or not team_hint:
            return None
        # 1) Caché del board ya paginado en este ciclo: un evento
        #    resuelto antes trajo sus mercados (y los de otros 29
        #    eventos) gratis.
        fixture = match_fixture(self._cached_fixtures(sport), team_hint, date)
        if fixture is not None:
            event_id = fixture.fixture_id
            return EventRef(
                event_ext_id=f"{self.ID_PREFIX}{event_id}",
                home_team=fixture.home_name,
                away_team=fixture.away_name,
                start=fixture.start,
            )
        # 2) Board "soonest first": se para cuando los eventos ya
        #    superan la fecha del pick + tolerancia (no está publicado).
        collected: list[BetsFixture] = []
        stop_after = date + _BOARD_STOP_TOLERANCE
        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(1, _MAX_PAGES + 1):
                if self._pages_spent >= _MAX_PAGES_PER_CYCLE:
                    return None
                status, events = await self._client.events(client, sport_path, page)
                self._pages_spent += 1
                if status != 200 or events is None:
                    return None
                if not events:
                    break
                self._cache_events(events)
                last_start = None
                for raw in events:
                    fx = _parse_event(raw, sport)
                    if fx is not None:
                        collected.append(fx)
                        if fx.start and (last_start is None or fx.start > last_start):
                            last_start = fx.start
                fixture = match_fixture(collected, team_hint, date)
                if fixture is not None:
                    return EventRef(
                        event_ext_id=f"{self.ID_PREFIX}{fixture.fixture_id}",
                        home_team=fixture.home_name,
                        away_team=fixture.away_name,
                        start=fixture.start,
                    )
                if last_start and last_start > stop_after:
                    break  # el evento aún no está en el board
        return None

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        if not event_ext_id.startswith(self.ID_PREFIX):
            return None
        sport_path = _SPORT_PATHS.get(sport)
        if sport_path is None:
            return None
        event_id = event_ext_id.split(":", 1)[1]
        raw = self._events.get(event_id)
        if raw is None:
            async with httpx.AsyncClient(timeout=30) as client:
                status, raw, gone = await self._client.event(
                    client, sport_path, event_id
                )
            if gone:
                return []  # fuera del board: empezado o desconocido
            if raw is None:
                return None  # error/cuota: reintento en el próximo ciclo
            self._cache_events([raw])
        return translate_event(
            raw,
            sport,
            str(raw.get("home") or ""),
            str(raw.get("away") or ""),
        )
