"""Backfill de cuotas históricas de mercado en cascada de fuentes.

Descarga la curva de cuotas histórica de cada fixture y la guarda en
`odds_snapshots` con `provider` = fuente que la aportó y `captured_at`
real — así el comparador trata el histórico igual que los snapshots
en vivo.

Fuentes en cascada (orden = prioridad, no volumen):

1. `oddspapi` (bet36528): el rico — 33k mercados (córners, tarjetas,
   props...) pero BASIC son solo 200 req/mes.
2. `oddsfeed` (odds-feed): solo mercados principales (1X2, O/U,
   hándicap, BTTS) pero 500 req/mes y histórico real validado.
3. `gemini-odds`: último recurso por pick — el investigador Gemini
   lee la página del partido en un comparador (oddspedia, oddsportal,
   betexplorer) y extrae la cuota de cierre del mercado del pick.
   Doble validación: la cuota tiene que aparecer literal en el HTML y
   la tripleta mercado/opción/línea que propone el modelo tiene que
   casar con el pick vía `map_pick_choices` — lo determinista decide.

Reglas de negocio:
- Idempotente: un evento que ya tiene filas de CUALQUIER fuente de
  backfill se salta entero (una auditoría por evento basta).
- Fixtures sin historial quedan marcados como miss definitivo POR
  FUENTE ("<prov>|hist|<id>"), y los picks sin fixture casado como
  miss "<prov>|nofixture|<pick>" — una fuente puede cubrir eventos
  que la otra no, así que los misses no se comparten.
- No se inventan valores: un pick sin opción casada solo informa.
- `pick.odds_event_id` vacío se rellena con el id de la fuente que
  casó y se registra en `odds_events` — igual que el snapshotter.
- odds-feed lleva presupuesto de llamadas por ejecución
  (`_FEED_MAX_CALLS_PER_RUN`): 500/mes no da para vaciar el backlog
  histórico de golpe; cada pasada diaria muerde un trozo.

El CLI `scripts/backfill_historical_odds.py` es solo un wrapper de
`run()`; el loop diario de `lifecycle.py` llama a la misma función.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Protocol

import httpx
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401  (FKs de TelegramRawMessage)
from app.services.odds import oddsfeed, oddspapi
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddsfeed import (
    FeedFixture,
    OddsFeedClient,
    points_from_history,
    translate_market_books,
)
from app.services.odds.oddspapi import (
    HistoricalChoice,
    OddsPapiClient,
    OddsPapiFixture,
    PricePoint,
    downsample,
)
from app.services.odds.oddspapi import (
    event_ext_id as oddspapi_ext_id,
)
from app.services.odds.oddspapi import (
    match_fixture as oddspapi_match,
)
from app.services.odds.snapshotter import _canonical_sport, _lookup_hint
from app.services.results.base import is_missed, mark_missed
from app.services.results.gemini_research import GeminiResearchProvider

# La histórico del pick solo existe si el evento ya pasó (con margen
# para no pillar partidos en vivo a mitad de curva).
_EVENT_MARGIN = timedelta(hours=3)
# Pequeña pausa entre fixtures: cada respuesta pesa 1-3 MB — cortesía
# y margen de rate-limit.
_FETCH_DELAY = 0.2
# Tope de llamadas de odds-feed por ejecución: 500 req/mes exigen
# dosificar el backlog — ~15 eventos con 2-3 llamadas cada uno.
_FEED_MAX_CALLS_PER_RUN = 40
# Gemini-odds: provider/fuente de las filas que aporta el investigador
# y prefijo de su marca de procesado en el miss-store. Cada pick
# intentado cuesta 2-4 llamadas al modelo; el presupuesto por pasada
# comparte el free tier con la investigación de resultados.
_GEMINI_ODDS_PROVIDER = "gemini-odds"
_GEMINI_MAX_PICKS_PER_RUN = 8


@dataclass
class BackfillReport:
    picks: int = 0
    con_fixture: int = 0
    sin_fixture: int = 0
    sin_historial: int = 0
    mapeados: int = 0
    sin_opcion: int = 0
    filas: int = 0
    detalles: list[str] = field(default_factory=list)


def _sofascore_id(pick: ParsedPick) -> int | None:
    if pick.odds_event_id and pick.odds_event_id.startswith("sofascore:"):
        try:
            return int(pick.odds_event_id.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _nearest_price(points, when) -> float | None:
    if not points or when is None:
        return None
    return min(points, key=lambda p: abs((p.captured_at - when).total_seconds())).cuota


class _HistorySource(Protocol):
    """Una fuente de cuotas históricas para el backfill."""

    name: str  # provider en odds_snapshots / prefijo de claves de miss

    @property
    def sport_ids(self) -> dict[str, int]: ...

    async def fixtures(
        self, http: httpx.AsyncClient, sport_id: int, day: date
    ) -> Optional[list]: ...

    def match(self, fixtures: list, hint: str, dt, sofascore_id: Optional[int]): ...

    def ext_id(self, fixture) -> str: ...

    def fixture_key(self, fixture) -> str: ...

    async def choices(
        self, http: httpx.AsyncClient, fixture, sport: str, picks: list[ParsedPick]
    ) -> tuple[str, list]:
        """("ok", choices) | ("miss", []) definitivo | ("error", []) reintentar
        | ("budget", []) presupuesto de llamadas agotado para esta pasada."""
        ...


class _OddsPapiSource:
    """bet36528: catálogo /markets + /historical-odds por fixture."""

    name = oddspapi.NAME

    def __init__(self, api_key: str, api_host: str) -> None:
        self._client = OddsPapiClient(api_key=api_key, api_host=api_host)

    @property
    def sport_ids(self) -> dict[str, int]:
        return oddspapi.SPORT_IDS

    async def fixtures(self, http, sport_id: int, day: date):
        return await self._client.fixtures(http, sport_id, day, day)

    def match(self, fixtures, hint, dt, sofascore_id):
        return oddspapi_match(fixtures, hint, dt, sofascore_id)

    def ext_id(self, fixture: OddsPapiFixture) -> str:
        return oddspapi_ext_id(fixture)

    def fixture_key(self, fixture: OddsPapiFixture) -> str:
        return fixture.fixture_id

    async def choices(self, http, fixture: OddsPapiFixture, sport, picks):
        markets = await self._client.markets(http)
        if markets is None:
            return "error", []
        await asyncio.sleep(_FETCH_DELAY)
        status, payload = await self._client.historical_odds(http, fixture.fixture_id)
        if status == 404:
            return "miss", []
        if payload is None:
            return "error", []
        return "ok", oddspapi.translate_history(
            payload, markets, fixture.home_name, fixture.away_name
        )


class _OddsFeedSource:
    """odds-feed: /events + /events/markets + /markets/history.

    La curva se descarga SOLO de los market_books que casan con algún
    pick del evento (una llamada por mercado necesario, no por todos).
    """

    name = oddsfeed.NAME

    def __init__(self, api_key: str, api_host: str) -> None:
        self._client = OddsFeedClient(api_key=api_key, api_host=api_host)

    @property
    def sport_ids(self) -> dict[str, int]:
        return oddsfeed.SPORT_IDS

    def _over_budget(self) -> bool:
        return self._client.calls >= _FEED_MAX_CALLS_PER_RUN

    async def fixtures(self, http, sport_id: int, day: date):
        if self._over_budget():
            return None
        return await self._client.events(http, sport_id, day)

    def match(self, fixtures, hint, dt, sofascore_id):
        return oddsfeed.match_fixture(fixtures, hint, dt)

    def ext_id(self, fixture: FeedFixture) -> str:
        return oddsfeed.event_ext_id(fixture)

    def fixture_key(self, fixture: FeedFixture) -> str:
        return str(fixture.event_id)

    async def choices(self, http, fixture: FeedFixture, sport, picks):
        if self._over_budget():
            return "budget", []
        markets = await self._client.event_markets(http, fixture.event_id)
        if markets is None:
            return "error", []
        books = translate_market_books(
            markets, sport, fixture.home_name, fixture.away_name
        )
        if not books:
            return "miss", []

        # Filas virtuales con el precio ACTUAL de cada opción: sirven
        # para casar el pick con su (mercado, casa) sin gastar aún la
        # llamada de histórico, y como punto de cierre de la curva.
        event_stub = OddsEvent(
            event_ext_id=oddsfeed.event_ext_id(fixture),
            sport=sport,
            home_team=fixture.home_name,
            away_team=fixture.away_name,
            start=fixture.start,
        )
        virtual: list[OddsSnapshot] = []
        for book, defs in books:
            for idx, choice_name, choice_group in defs:
                virtual.append(
                    OddsSnapshot(
                        provider=self.name,
                        event_ext_id=event_stub.event_ext_id,
                        market_name=book.translated,
                        choice_name=choice_name,
                        choice_group=choice_group,
                        cuota=book.current.get(idx) or 0.0,
                        captured_at=fixture.start or utc_now(),
                    )
                )

        # Qué market_books hacen falta: los de las opciones casadas.
        needed_books: dict[int, dict] = {}
        for pick in picks:
            matched = map_pick_choices(pick, event_stub, virtual)
            if matched is None:
                continue
            for book, defs in books:
                if book.translated != matched.market_name:
                    continue
                if any(
                    cname == matched.choice_name and cgroup == matched.choice_group
                    for _, cname, cgroup in defs
                ):
                    needed_books[book.market_book_id] = book
                    break

        if not needed_books:
            return "ok", []

        # Una llamada de histórico por market_book necesario (cubre
        # todas sus opciones de golpe). Si viene vacía, queda el precio
        # actual como único punto de la curva.
        histories: dict[int, list] = {}
        for mb_id in needed_books:
            if self._over_budget():
                break
            await asyncio.sleep(_FETCH_DELAY)
            history = await self._client.market_history(http, mb_id)
            if history is None:
                return "error", []  # cuota/red: reintenta la pasada
            histories[mb_id] = history

        choices: list = []
        for book in needed_books.values():
            history = histories.get(book.market_book_id) or []
            for idx, choice_name, choice_group in _book_defs(books, book):
                points = points_from_history(history, idx)
                current = book.current.get(idx)
                if current and (not points or points[-1].cuota != current):
                    points.append(
                        PricePoint(
                            captured_at=fixture.start or utc_now(),
                            cuota=float(current),
                            suspended=False,
                        )
                    )
                if not points:
                    continue
                choices.append(
                    HistoricalChoice(
                        market_name=book.translated,
                        choice_name=choice_name,
                        choice_group=choice_group,
                        points=points,
                    )
                )
        return "ok", choices


def _book_defs(books, book) -> list:
    """Las (idx, choice_name, choice_group) del market_book dado."""
    for candidate, defs in books:
        if candidate.market_book_id == book.market_book_id:
            return defs
    return []


_BACKFILL_PROVIDERS = (oddspapi.NAME, oddsfeed.NAME, _GEMINI_ODDS_PROVIDER)


async def _load_picks(session, limit: int | None) -> list[ParsedPick]:
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.es_combinada == False)  # noqa: E712
        .where(ParsedPick.deporte != None)  # noqa: E711
        .where(ParsedPick.fecha_evento != None)  # noqa: E711
        .where(ParsedPick.fecha_evento <= utc_now() - _EVENT_MARGIN)
        .order_by(ParsedPick.fecha_evento)
    )
    sports = set().union(*(s.sport_ids for s in _sources(get_settings())))
    picks = [p for p in result.all() if _canonical_sport(p.deporte) in sports]
    return picks[:limit] if limit else picks


async def _done_event_ids(session) -> set[str]:
    """Eventos ya procesados por CUALQUIER fuente de backfill
    (idempotencia — una auditoría por evento basta)."""
    result = await session.exec(
        select(OddsSnapshot.event_ext_id)
        .where(OddsSnapshot.provider.in_(_BACKFILL_PROVIDERS))
        .distinct()
    )
    return set(result.all())


def _sources(settings) -> list[_HistorySource]:
    """Fuentes disponibles según la configuración, en orden de uso."""
    sources: list[_HistorySource] = []
    if settings.rapidapi_tennis_key:
        sources.append(
            _OddsPapiSource(
                api_key=settings.rapidapi_tennis_key,
                api_host=settings.rapidapi_oddspapi_host,
            )
        )
        sources.append(
            _OddsFeedSource(
                api_key=settings.rapidapi_tennis_key,
                api_host=settings.rapidapi_oddsfeed_host,
            )
        )
    return sources


def _gemini_done_key() -> str:
    """Marca de procesado de la fuente Gemini por pick."""
    return f"{_GEMINI_ODDS_PROVIDER}|done|{{}}"


def _pick_pending(pick: ParsedPick, done: set[str], settings) -> bool:
    """El pick aún tiene alguna fuente que no lo ha resuelto.

    Las fuentes API marcan "nofixture"; Gemini marca "done" cuando ya
    lo intentó (cubierto por filas existentes, sin cuota en la web o
    no buscable). Mientras alguna fuente le deba algo, sigue pendiente.
    """
    sources_missed = all(
        is_missed(f"{s.name}|nofixture|{pick.id}") for s in _sources(settings)
    )
    if pick.odds_event_id not in done and not sources_missed:
        return True
    if settings.google_api_key and not is_missed(_gemini_done_key().format(pick.id)):
        return True
    return False


async def count_pending_backfill(session) -> int:
    """Picks que aún necesitan pasada de backfill (para el early-exit
    del job diario — cero llamadas a la API)."""
    settings = get_settings()
    sources = _sources(settings)
    if not sources:
        return 0
    picks = await _load_picks(session, None)
    done = await _done_event_ids(session)
    return sum(1 for p in picks if _pick_pending(p, done, settings))


async def _run_source(
    source: _HistorySource,
    picks: list[ParsedPick],
    done: set[str],
    registered: set[str],
    session,
    http: httpx.AsyncClient,
    apply: bool,
    report: BackfillReport,
) -> None:
    """Pasa una fuente sobre los picks pendientes de SU cobertura
    deportiva. Añade a `done` los eventos que deja escritos."""
    nofixture_key = f"{source.name}|nofixture|{{}}"
    hist_key = f"{source.name}|hist|{{}}"

    # Resolución pick -> fixture agrupada por (deporte, día): una
    # llamada de eventos por cubo cubre todos sus picks.
    buckets: dict[tuple[int, object], list[ParsedPick]] = {}
    for pick in picks:
        sport = _canonical_sport(pick.deporte)
        if sport not in source.sport_ids:
            continue
        if pick.odds_event_id in done:
            continue
        if is_missed(nofixture_key.format(pick.id)):
            continue
        buckets.setdefault(
            (source.sport_ids[sport], pick.fecha_evento.date()), []
        ).append(pick)

    fixtures_picks: dict[str, tuple[object, str, list[ParsedPick]]] = {}
    for (sport_id, day), day_picks in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        sport = _canonical_sport(day_picks[0].deporte)
        # Un día por llamada y la caché del cliente dedup los días
        # compartidos entre buckets vecinos.
        fixtures: list = []
        api_error = False
        for delta in (-1, 0, 1):
            day_fixtures = await source.fixtures(
                http, sport_id, day + timedelta(days=delta)
            )
            if day_fixtures is None:
                api_error = True
                break
            fixtures += day_fixtures
        if api_error:
            report.detalles.append(
                f"  ! [{source.name}] error de API en eventos {sport} "
                f"{day}: {len(day_picks)} picks sin revisar"
            )
            continue
        for pick in day_picks:
            hint = _lookup_hint(pick, sport) or ""
            fixture = source.match(
                fixtures, hint, pick.fecha_evento, _sofascore_id(pick)
            )
            if fixture is None:
                report.sin_fixture += 1
                report.detalles.append(
                    f"  - pick {pick.id}: sin fixture "
                    f"({pick.evento or pick.seleccion}) [{source.name}]"
                )
                if apply:
                    mark_missed(nofixture_key.format(pick.id))
                continue
            ext_id = source.ext_id(fixture)
            if ext_id in done:
                continue
            slot = fixtures_picks.setdefault(
                source.fixture_key(fixture), (fixture, sport, [])
            )
            slot[2].append(pick)
            report.con_fixture += 1

    # Una curva por fixture alimenta todos sus picks.
    for fixture, sport, event_picks in fixtures_picks.values():
        ext_id = source.ext_id(fixture)
        if is_missed(hist_key.format(source.fixture_key(fixture))):
            report.sin_historial += len(event_picks)
            continue
        status, choices = await source.choices(http, fixture, sport, event_picks)
        if status == "miss":
            # El fixture nunca tuvo mercados en esta fuente.
            mark_missed(hist_key.format(source.fixture_key(fixture)))
            report.sin_historial += len(event_picks)
            report.detalles.append(
                f"  - {fixture.home_name} vs {fixture.away_name}: "
                f"sin historial [{source.name}]"
            )
            continue
        if status == "budget":
            report.detalles.append(
                f"  ! [{source.name}] presupuesto de llamadas agotado "
                f"({_FEED_MAX_CALLS_PER_RUN}/pasada) — sigue mañana"
            )
            return
        if status == "error":
            # Error/cuota: no se marca — reintenta en otra pasada.
            report.detalles.append(
                f"  ! {fixture.home_name} vs {fixture.away_name}: "
                f"error de API [{source.name}], se reintenta luego"
            )
            break

        # Filas virtuales con el formato exacto que lee el comparador.
        virtual = [
            OddsSnapshot(
                provider=source.name,
                event_ext_id=ext_id,
                market_name=c.market_name,
                choice_name=c.choice_name,
                choice_group=c.choice_group,
                cuota=p.cuota,
                cuota_apertura=c.cuota_apertura,
                suspended=p.suspended,
                captured_at=p.captured_at,
            )
            for c in choices
            for p in c.points
        ]
        event = OddsEvent(
            event_ext_id=ext_id,
            sport=sport,
            home_team=fixture.home_name,
            away_team=fixture.away_name,
            start=fixture.start,
        )
        by_key = {(c.market_name, c.choice_name, c.choice_group): c for c in choices}

        matched_keys: set[tuple] = set()
        trigger_pick = event_picks[0]
        for pick in event_picks:
            matched = map_pick_choices(pick, event, virtual)
            if matched is None:
                report.sin_opcion += 1
                report.detalles.append(
                    f"  - pick {pick.id}: sin opción comparable "
                    f"({pick.mercado} / {pick.seleccion}) [{source.name}]"
                )
                continue
            key = (matched.market_name, matched.choice_name, matched.choice_group)
            matched_keys.add(key)
            report.mapeados += 1
            choice = by_key[key]
            precio_pub = _nearest_price(choice.points, pick.created_at)
            report.detalles.append(
                f"  + pick {pick.id}: {matched.market_name} / {matched.choice_name}"
                f" | tipster {pick.cuota} vs mercado {precio_pub}"
                f" | {len(choice.points)} puntos [{source.name}]"
            )
            if not pick.odds_event_id:
                pick.odds_event_id = ext_id
                if apply:
                    session.add(pick)

        if apply and matched_keys:
            if ext_id not in registered:
                session.add(event)
                registered.add(ext_id)
            for key in matched_keys:
                for point in downsample(by_key[key].points):
                    session.add(
                        OddsSnapshot(
                            provider=source.name,
                            event_ext_id=ext_id,
                            market_name=key[0],
                            choice_name=key[1],
                            choice_group=key[2],
                            cuota=point.cuota,
                            cuota_apertura=by_key[key].cuota_apertura,
                            suspended=point.suspended,
                            captured_at=point.captured_at,
                            parsed_pick_id=trigger_pick.id,
                        )
                    )
                    report.filas += 1
            done.add(ext_id)
        elif matched_keys:
            report.filas += sum(len(downsample(by_key[k].points)) for k in matched_keys)


_EVENT_SPLIT = re.compile(r"\s+(?:vs\.?|v|[-–—])\s+")


def _synthetic_event(pick: ParsedPick, sport: str, ext_id: str) -> OddsEvent:
    """OddsEvent armado desde `pick.evento` ("A vs B") para picks sin
    fixture de API. Equipos vacíos si el formato no se entiende: los
    mercados de lado fallarán el autocheck y los de línea (O/U) no."""
    home = away = ""
    parts = _EVENT_SPLIT.split((pick.evento or "").strip(), maxsplit=1)
    if len(parts) == 2:
        home, away = parts[0].strip(), parts[1].strip()
    return OddsEvent(
        event_ext_id=ext_id,
        sport=sport,
        home_team=home,
        away_team=away,
        start=pick.fecha_evento,
    )


def _market_desc(pick: ParsedPick) -> str:
    """Descripción natural de la apuesta para el prompt del modelo."""
    parts = [p for p in (pick.mercado, pick.seleccion) if p]
    desc = " / ".join(str(p) for p in parts)
    if pick.linea is not None:
        desc += f" (línea {pick.linea})"
    return desc.strip()


async def _run_gemini_pass(
    picks: list[ParsedPick],
    done: set[str],
    registered: set[str],
    session,
    apply: bool,
    report: BackfillReport,
) -> None:
    """Última fuente de la cascada: Gemini lee comparadores web.

    Recorre picks que ninguna fuente determinista dejó casados — ya
    sea porque su evento no tiene filas o porque su mercado no estaba
    cubierto (córners/tarjetas...). Para cada uno busca la cuota de
    cierre en la web, y solo la escribe si la tripleta propuesta casa
    con el pick vía `map_pick_choices` (autocheck determinista).
    """
    settings = get_settings()
    if not settings.google_api_key:
        return
    done_key = _gemini_done_key()
    candidates = [
        p for p in picks if p.id is not None and not is_missed(done_key.format(p.id))
    ]
    if not candidates:
        return

    # Cobertura ya existente: picks cuyo evento guarda filas de
    # cualquier provider y casan — se marcan procesados sin gastar
    # una sola llamada al modelo.
    event_ids = {p.odds_event_id for p in candidates if p.odds_event_id}
    events: dict[str, OddsEvent] = {}
    rows_by_event: dict[str, list[OddsSnapshot]] = {}
    if event_ids:
        for e in (
            await session.exec(
                select(OddsEvent).where(OddsEvent.event_ext_id.in_(event_ids))
            )
        ).all():
            events[e.event_ext_id] = e
        for row in (
            await session.exec(
                select(OddsSnapshot)
                .where(OddsSnapshot.event_ext_id.in_(event_ids))
                .order_by(OddsSnapshot.captured_at)
            )
        ).all():
            rows_by_event.setdefault(row.event_ext_id, []).append(row)

    pending: list[ParsedPick] = []
    for pick in candidates:
        event = events.get(pick.odds_event_id or "")
        if event is not None and map_pick_choices(
            pick, event, rows_by_event.get(event.event_ext_id, [])
        ):
            if apply:
                mark_missed(done_key.format(pick.id))
            continue
        pending.append(pick)

    for pick in pending[:_GEMINI_MAX_PICKS_PER_RUN]:
        sport = _canonical_sport(pick.deporte)
        hint = (_lookup_hint(pick, sport) or "") if sport else ""
        desc = _market_desc(pick)
        if sport not in GeminiResearchProvider.SUPPORTED_SPORTS or not hint or not desc:
            if apply:
                mark_missed(done_key.format(pick.id))
            continue

        ext_id = pick.odds_event_id or f"{_GEMINI_ODDS_PROVIDER}:{pick.id}"
        event = events.get(ext_id) or _synthetic_event(pick, sport, ext_id)

        provider = GeminiResearchProvider(sport, settings.google_api_key)
        found = await provider.find_closing_odds(pick.fecha_evento, hint, desc)
        if found is None:
            if provider._in_cooldown():
                # Cuota/ritmo agotado: ni marca ni reintento — se
                # retoma en la próxima pasada.
                report.detalles.append(
                    "  ! [gemini-odds] cooldown del free tier — "
                    "el resto sigue en la próxima pasada"
                )
                break
            if apply:
                mark_missed(done_key.format(pick.id))
            report.detalles.append(
                f"  - pick {pick.id}: sin cuota en comparadores "
                f"[{_GEMINI_ODDS_PROVIDER}]"
            )
            continue

        # Autocheck: la propuesta del modelo tiene que casar con el
        # pick por la misma función que usa el comparador. Si el
        # evento sintético tiene los lados invertidos se prueba la
        # orientación contraria antes de descartar.
        probe = OddsSnapshot(
            provider=_GEMINI_ODDS_PROVIDER,
            event_ext_id=ext_id,
            market_name=found.market_name,
            choice_name=found.choice_name,
            choice_group=found.choice_group,
            cuota=found.cuota,
            captured_at=pick.fecha_evento,
        )
        matched = map_pick_choices(pick, event, [probe])
        if (
            matched is None
            and event.home_team
            and event.away_team
            and event is not events.get(ext_id)
        ):
            event = OddsEvent(
                event_ext_id=event.event_ext_id,
                sport=event.sport,
                home_team=event.away_team,
                away_team=event.home_team,
                start=event.start,
            )
            matched = map_pick_choices(pick, event, [probe])
        key = (found.market_name, found.choice_name, found.choice_group)
        if (
            matched is None
            or (
                matched.market_name,
                matched.choice_name,
                matched.choice_group,
            )
            != key
        ):
            if apply:
                mark_missed(done_key.format(pick.id))
            report.detalles.append(
                f"  - pick {pick.id}: propuesta no verificable "
                f"({found.market_name}/{found.choice_name}) "
                f"[{_GEMINI_ODDS_PROVIDER}]"
            )
            continue

        report.mapeados += 1
        report.detalles.append(
            f"  + pick {pick.id}: {found.market_name} / {found.choice_name}"
            f" | cierre {found.cuota} ({found.casa or '?'}) "
            f"[{_GEMINI_ODDS_PROVIDER} via {found.source_url}]"
        )
        if apply:
            if ext_id not in registered:
                session.add(event)
                registered.add(ext_id)
            session.add(
                OddsSnapshot(
                    provider=_GEMINI_ODDS_PROVIDER,
                    event_ext_id=ext_id,
                    market_name=found.market_name,
                    choice_name=found.choice_name,
                    choice_group=found.choice_group,
                    cuota=found.cuota,
                    captured_at=pick.fecha_evento,
                    parsed_pick_id=pick.id,
                )
            )
            if not pick.odds_event_id:
                pick.odds_event_id = ext_id
                session.add(pick)
            done.add(ext_id)
            report.filas += 1


async def run(apply: bool, limit: int | None) -> BackfillReport:
    settings = get_settings()
    report = BackfillReport()
    sources = _sources(settings)
    if not sources:
        report.detalles.append("Sin RAPIDAPI_TENNIS_KEY configurada.")
        return report

    async with AsyncSessionLocal() as session, httpx.AsyncClient(timeout=60) as http:
        picks = await _load_picks(session, limit)
        report.picks = len(picks)
        done = await _done_event_ids(session)
        # Early-exit antes de tocar la API: si no hay backlog pendiente
        # el job diario no gasta ni una llamada.
        if not any(_pick_pending(p, done, settings) for p in picks):
            report.detalles.append("Sin backlog pendiente; no se llama a la API.")
            return report

        registered = set((await session.exec(select(OddsEvent.event_ext_id))).all())

        for source in sources:
            await _run_source(
                source, picks, done, registered, session, http, apply, report
            )
        await _run_gemini_pass(picks, done, registered, session, apply, report)

        if apply:
            await session.commit()

    return report
