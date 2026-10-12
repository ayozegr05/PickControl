"""Backfill de cuotas históricas de mercado en cascada de fuentes.

Descarga la curva de cuotas histórica de cada fixture y la guarda en
`odds_snapshots` con `provider` = fuente que la aportó y `captured_at`
real — así el comparador trata el histórico igual que los snapshots
en vivo.

Fuentes en cascada (orden = prioridad, no volumen):

0. `footballdata` (football-data.co.uk): CSVs GRATIS por liga y
   temporada (22 ligas principales + 16 "extra") con cierre y apertura
   de 1X2, O/U 2.5 y hándicap asiático. Sin key ni cuota. Además su
   listado de temporada entera hace de sondeo de fecha: casa picks
   cuya `fecha_evento` guardada es la del repost, y el backfill les
   escribe la fecha real del fixture — el resto de fuentes busca ya
   en el día correcto.
1. `tennisdata` (tennis-data.co.uk): XLSX anuales GRATIS de cuotas de
   ganador ATP/WTA (Pinnacle/B365). Sin key ni cuota — drena los picks
   de tenis `mercado="ganador"` sin gastar una sola llamada de las
   APIs de pago. No cubre Challengers/ITF ni mercados de juegos.
2. `oddspapi` (bet36528): el rico — 33k mercados (córners, tarjetas,
   props...) pero BASIC son solo 200 req/mes.
3. `oddsfeed` (odds-feed): solo mercados principales (1X2, O/U,
   hándicap, BTTS) pero 500 req/mes y histórico real validado.
4. `llm-odds`: último recurso por pick — búsqueda web + extracción
   LLM (cascada Groq→Cerebras→OpenRouter, Gemini de respaldo)
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
import inspect
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Protocol, Union

import httpx
from sqlalchemy import distinct, func
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401  (FKs de TelegramRawMessage)
from app.services.odds import (
    football_csv,
    oddsfeed,
    oddspapi,
    static_pages,
    tennisdata,
)
from app.services.odds.base import MarketChoice
from app.services.odds.compare import map_pick_choices
from app.services.odds.event_rescue import looks_like_match_hint, rescue_evento
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
from app.services.odds.sofascore import SofaScoreOddsProvider
from app.services.results import compat_llm
from app.services.results.base import (
    clear_missed,
    count_missed_prefix,
    count_odds_rows_written,
    count_provider_call,
    is_missed,
    is_rate_limited,
    loop_history,
    loop_summary,
    mark_missed,
    match_score,
    rate_limited_until,
    record_loop_summary,
)
from app.services.results.espn import _translate_countries
from app.services.results.gemini_research import GeminiResearchProvider
from app.services.results.sofascore_native import (
    SofaScoreNativeOddsProvider,
    direct_transport,
    rapidapi_transport,
    sofascore6_transport,
)

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

# Marca NEUTRA del pipeline web+LLM: las marcas `llm-odds|done|{pick}`
# las comparten todos los extractores — un intento fallido con Groq no
# debe repetirse gastando cuota de Gemini (ni viceversa).
_LLM_ODDS_MARK = "llm-odds"
# Provider de cada fila = el motor que la extrajo de verdad:
# groq-odds, gemini-odds, cerebras-odds, openrouter-odds. `llm-odds`
# también es provider válido: etiqueta de las filas históricas
# escritas antes de la atribución por motor (extractor desconocido).
_LLM_EXTRACTOR_PROVIDERS = tuple(
    f"{e}-odds" for e in ("groq", "cerebras", "openrouter", "gemini", "llm")
)


def _llm_odds_configured(settings) -> bool:
    """Extractor de cuotas configurado: Gemini o la cascada compat
    (Groq/Cerebras/OpenRouter — los endpoints gratis dan más cuota
    que el free tier de Google)."""
    return bool(settings.google_api_key) or compat_llm.configured(settings)


def _llm_odds_ready(settings) -> bool:
    """Camino de extracción vivo AHORA: endpoint compat libre o
    Gemini fuera de cooldown. Sin él, correr la pasada solo quema
    rescates para picks que nadie va a extraer."""
    if compat_llm.has_live_endpoint(settings):
        return True
    return bool(settings.google_api_key) and not is_rate_limited(_GEMINI_ODDS_PROVIDER)


# Presupuesto por pasada en EVENTOS (no picks): varias patas del mismo
# partido comparten una sola búsqueda web — el grupo entero se resuelve
# con una extracción por página. Es un techo de seguridad: el cooldown
# del free tier corta antes la pasada, así que el ritmo real se
# auto-regula solo.
_GEMINI_MAX_EVENTS_PER_RUN = 32
# La pasada diaria y el loop de goteo comparten el pass de Gemini:
# el candado evita que se pisen (mismo proceso, mismo candidatos).
_gemini_pass_lock = asyncio.Lock()
# Una fuente aparcada por quota más allá de este horizonte (~1 día,
# p. ej. el reset mensual de una suscripción) no bloquea el drenaje
# del backlog ni se le llama: no es un cooldown de ritmo, no hay nada
# que reintentar hasta que renueve (caso oddspapi, cuota mensual).
_LONG_QUOTA_HORIZON = timedelta(hours=24)


def _source_quota_parked(source: _HistorySource) -> bool:
    """True si la fuente está aparcada por quota más allá de ~24 h."""
    until = rate_limited_until(source.name)
    return until is not None and until > utc_now() + _LONG_QUOTA_HORIZON


@dataclass
class BackfillReport:
    picks: int = 0
    con_fixture: int = 0
    sin_fixture: int = 0
    sin_historial: int = 0
    mapeados: int = 0
    sin_opcion: int = 0
    filas: int = 0
    # Picks a los que no se llegó: la fuente se quedó sin presupuesto
    # de llamadas o la API falló — quedan pendientes para mañana, sin
    # marca (no son "nadie los tenía").
    sin_revisar: int = 0
    # `fecha_evento` corregidas con la fecha real del fixture (la
    # guardada era la del repost — sondeo de fecha del CSV).
    fechas_corregidas: int = 0
    # Filas escritas por fuente — el resumen de pasada muestra quién
    # aportó qué ("tennisdata 87 · oddsfeed 3").
    filas_por_fuente: dict[str, int] = field(default_factory=dict)
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
    # True si puede ofrecer datos NUEVOS sobre un evento ya procesado
    # (los listados estáticos descargan el detalle de partido lazy
    # por mercado: un pick re-etiquetado DNB en un evento "done" aún
    # puede recibir su fila "Draw no bet"). Las APIs traen todo el
    # mercado en una llamada — un evento hecho no tiene nada nuevo.
    revisit_done: bool = False

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


class _TennisDataSource:
    """tennis-data.co.uk: un XLSX anual por circuito, sin key ni cuota.

    El "listado de eventos del día" es la fila del archivo con esa
    fecha; el "histórico" son las cuotas de ganador que ya trae la
    misma fila — cero llamadas extra por partido. El archivo se
    descarga una vez por año y pasada (caché en memoria: puede
    actualizarse durante la temporada)."""

    name = tennisdata.NAME

    def __init__(self) -> None:
        self._years: dict[int, Optional[list[tennisdata.TennisMatch]]] = {}

    @property
    def sport_ids(self) -> dict[str, int]:
        return {"tenis": 0}

    async def _year_rows(self, http, year: int):
        if year not in self._years:
            self._years[year] = await tennisdata.fetch_year(http, year)
            if self._years[year] is not None:
                count_provider_call(self.name)
        return self._years[year]

    async def fixtures(self, http, sport_id: int, day: date):
        rows = await self._year_rows(http, day.year)
        if rows is None:
            return None
        return [row for row in rows if row.day == day]

    def match(self, fixtures, hint, dt, sofascore_id):
        return tennisdata.match_row(fixtures, hint)

    def ext_id(self, fixture: tennisdata.TennisMatch) -> str:
        return tennisdata.event_ext_id(fixture)

    def fixture_key(self, fixture: tennisdata.TennisMatch) -> str:
        return self.ext_id(fixture)

    async def choices(self, http, fixture: tennisdata.TennisMatch, sport, picks):
        choices = tennisdata.row_choices(fixture)
        return ("ok", choices) if choices else ("miss", [])


class _FootballCsvSource:
    """football-data.co.uk: un CSV por liga y temporada, sin key ni
    cuota — el gemelo de tenis de `tennisdata`.

    `fixtures()` devuelve TODA la temporada del día pedido (más las
    adyacentes) en vez de solo ese día: el listado completo hace de
    sondeo de fecha — `match()` casa por nombres en una ventana de
    ±60 días, así un pick cuya fecha guardada es la del repost
    encuentra su fixture real, y `_run_source` corrige su
    `fecha_evento` antes de que las APIs de pago busquen en el día
    equivocado. Las descargas se cachean por temporada y pasada."""

    name = football_csv.NAME

    def __init__(self) -> None:
        self._seasons: dict[str, Optional[list[football_csv.FootballFixture]]] = {}
        self._extra: Optional[list[football_csv.FootballFixture]] = None
        self._extra_fetched = False

    @property
    def sport_ids(self) -> dict[str, int]:
        return {"futbol": 0}

    async def _season_rows(self, http, code: str):
        if code not in self._seasons:
            self._seasons[code] = await football_csv.fetch_season(http, code)
            if self._seasons[code] is not None:
                count_provider_call(self.name)
        return self._seasons[code]

    async def _extra_rows(self, http):
        if not self._extra_fetched:
            self._extra = await football_csv.fetch_extra(http)
            self._extra_fetched = True
            if self._extra is not None:
                count_provider_call(self.name)
        return self._extra

    async def fixtures(self, http, sport_id: int, day: date):
        rows: list = []
        for code in football_csv.season_codes(day):
            season = await self._season_rows(http, code)
            if season is None:
                return None
            rows += season
        extra = await self._extra_rows(http)
        if extra is None:
            return None
        return rows + extra

    def match(self, fixtures, hint, dt, sofascore_id):
        return football_csv.match_row(fixtures, hint, dt)

    def ext_id(self, fixture: football_csv.FootballFixture) -> str:
        return football_csv.event_ext_id(fixture)

    def fixture_key(self, fixture: football_csv.FootballFixture) -> str:
        return self.ext_id(fixture)

    async def choices(self, http, fixture: football_csv.FootballFixture, sport, picks):
        choices = football_csv.row_choices(fixture)
        return ("ok", choices) if choices else ("miss", [])


class _StaticListingSource:
    """Adapter común para los listados estáticos (`static_pages`).

    El "listado de eventos del día" ES la página del día — una
    descarga trae todos los partidos con su cuota de cierre inline,
    así `choices` no hace ninguna llamada extra. Las descargas se
    cachean por día dentro de la pasada (la fuente se instancia por
    `run()`).

    `fixtures()` devuelve None no solo ante error de red sino ante la
    página "sospechosa de vacía" (descargada grande, cero filas —
    layout roto): el pick se reintenta en la próxima pasada en vez
    de comerse un `nofixture` falso, y el canario (`scraper_health`)
    alarma si la rotura persiste."""

    fetcher: Any = None
    matcher: Any = None
    choicer: Any = None
    # Detalle lazy por mercado: un evento "done" puede seguir sirviendo
    # mercados que no se pidieron antes (reparación DC->DNB, picks que
    # llegan tarde a un partido ya auditado en 1X2).
    revisit_done = True

    def __init__(self) -> None:
        self._days: dict[date, Optional[list[static_pages.StaticMatch]]] = {}

    async def fixtures(self, http, sport_id: int, day: date):
        if day not in self._days:
            try:
                rows, suspicious = await type(self).fetcher(http, day)
                self._days[day] = None if suspicious else rows
            except Exception:
                self._days[day] = None
        return self._days[day]

    def match(self, fixtures, hint, dt, sofascore_id):
        return type(self).matcher(fixtures, hint)

    def ext_id(self, fixture: static_pages.StaticMatch) -> str:
        return fixture.ext_id

    def fixture_key(self, fixture: static_pages.StaticMatch) -> str:
        return fixture.ext_id

    async def choices(self, http, fixture: static_pages.StaticMatch, sport, picks):
        choices = type(self).choicer(fixture)
        return ("ok", choices) if choices else ("miss", [])


class _TennisExplorerSource(_StaticListingSource):
    """tennisexplorer.com: listado diario estático con cuotas de
    ganador — cubre ATP/WTA/**ITF** (el hueco que ninguna API ni
    bet365 toca). Fetch directo, sin key ni cuota.

    El detalle de partido (/match-detail/?id= — el `ext_id` ya lo
    guarda el listado) es también estático y trae TODOS los mercados
    en una página: O/U de sets/juegos, hándicap de juegos/sets y
    marcador exacto de sets ("gana 2-0"). Se descarga lazy, solo si
    algún pick del partido pide algo más que el ganador — una llamada
    compartida por todos sus picks."""

    name = static_pages.NAME_TE
    fetcher = staticmethod(static_pages.fetch_tennisexplorer_day)
    matcher = staticmethod(static_pages.te_match)
    choicer = staticmethod(static_pages.te_choices)

    def __init__(self) -> None:
        super().__init__()
        # ext_id -> opciones del detalle. None = fetch falló
        # (transitorio: status "error", reintento próxima pasada).
        self._details: dict[str, Optional[list[HistoricalChoice]]] = {}

    @property
    def sport_ids(self) -> dict[str, int]:
        return {"tenis": 0}

    async def _detail(
        self, http, fixture: static_pages.StaticMatch
    ) -> Optional[list[HistoricalChoice]]:
        if fixture.ext_id not in self._details:
            code = fixture.ext_id.split(":", 1)[-1]
            html = await static_pages.fetch_tennisexplorer_detail(http, code)
            self._details[fixture.ext_id] = (
                None
                if html is None
                else static_pages.te_market_choices(
                    html, fixture.home_name, fixture.away_name, fixture.start
                )
            )
        return self._details[fixture.ext_id]

    async def choices(self, http, fixture: static_pages.StaticMatch, sport, picks):
        choices = static_pages.te_choices(fixture)
        need = any(
            static_pages.te_pick_needs_detail(pick.mercado, pick.seleccion)
            for pick in picks
        )
        if need or not choices:
            detail = await self._detail(http, fixture)
            if detail is None:
                # Transitorio: si no hay nada del listado no quemamos
                # el fixture con un miss permanente.
                return ("error", []) if not choices else ("ok", choices)
            # El "Full time" de la pestaña duplica el del listado:
            # se descarta si ya venía (mismo key).
            have = {(c.market_name, c.choice_name, c.choice_group) for c in choices}
            choices += [
                c
                for c in detail
                if (c.market_name, c.choice_name, c.choice_group) not in have
            ]
        return ("ok", choices) if choices else ("miss", [])


class _BetExplorerSource(_StaticListingSource):
    """betexplorer.com: cierres 1X2 de casi todas las ligas del
    mundo en `data-odd`, HTML estático. Dos vistas del mismo dato:

    - `/football/results/` del día: todo el fútbol mundial, pero
      solo embebe cuotas para fechas muy recientes (~ayer).
    - `/football/{pais}/{liga}/results/`: temporada entera con
      cuota por fila — la vía histórica.

    Cobertura en dos niveles: las ligas semilla `_BE_LEAGUES` se
    descargan una vez por pasada (sus filas corrigen `fecha_evento`
    malas y resuelven días viejos directo), y cuando un partido
    casado del listado diario no trae cuota se descarga SU liga bajo
    demanda vía `league_path` — auto-descubrimiento de las ~360
    competiciones del índice (selecciones, femenino, menores) sin
    mantener el mapa a mano.
    """

    name = static_pages.NAME_BE
    matcher = staticmethod(static_pages.be_match)
    choicer = staticmethod(static_pages.be_choices)

    def __init__(self) -> None:
        super().__init__()
        self._leagues: Optional[dict[str, list[static_pages.StaticMatch]]] = None
        # Detalle de partido: (ext_id, bettype) -> opciones. None =
        # fetch falló (transitorio: no marcar miss, reintenta luego).
        self._markets: dict[tuple[str, str], Optional[list[HistoricalChoice]]] = {}

    @property
    def sport_ids(self) -> dict[str, int]:
        return {"futbol": 0}

    async def _league(self, http, path: str) -> list[static_pages.StaticMatch]:
        """Filas de una página de liga, cacheadas por ruta en la
        pasada (un 404/slug malo cachea lista vacía, no rompe)."""
        if self._leagues is None:
            self._leagues = {}
        if path not in self._leagues:
            self._leagues[path] = await static_pages.fetch_betexplorer_league(
                http, path
            )
        return self._leagues[path]

    async def _base_leagues(self, http) -> list[static_pages.StaticMatch]:
        """Las ligas semilla `_BE_LEAGUES`, descargadas una vez por
        pasada (lazy — solo cuando un día viejo lo pide)."""
        if self._leagues is None:
            for path in static_pages._BE_LEAGUES:
                await self._league(http, path)
        assert self._leagues is not None
        return [r for rows in self._leagues.values() for r in rows]

    async def fixtures(self, http, sport_id: int, day: date):
        if day in self._days:
            return self._days[day]
        try:
            day_rows, suspicious = await static_pages.fetch_betexplorer_day(http, day)
            league_rows: list[static_pages.StaticMatch] = []
            if utc_now().date() - day > static_pages._BE_DAY_ODDS_MAX_AGE:
                base = await self._base_leagues(http)
                league_rows = [r for r in base if r.start.date() == day]
            # Mismo partido en las dos vistas (mismo ext_id): la fila
            # de liga gana porque es la que trae la cuota de cierre.
            by_id = {r.ext_id: r for r in day_rows}
            for row in league_rows:
                by_id[row.ext_id] = row
            rows = list(by_id.values())
            self._days[day] = None if (not rows and suspicious) else rows
        except Exception:
            self._days[day] = None
        return self._days[day]

    async def _market(
        self, http, fixture: static_pages.StaticMatch, bettype: str
    ) -> Optional[list[HistoricalChoice]]:
        """Opciones de un mercado del detalle del partido, cacheadas
        por (partido, bettype) en la pasada: varios picks del mismo
        partido comparten una sola descarga."""
        key = (fixture.ext_id, bettype)
        if key not in self._markets:
            code = fixture.ext_id.split(":", 1)[-1]
            html = await static_pages.fetch_betexplorer_market(http, code, bettype)
            self._markets[key] = (
                None
                if html is None
                else static_pages.be_market_choices(
                    html,
                    bettype,
                    fixture.home_name,
                    fixture.away_name,
                    fixture.start,
                )
            )
        return self._markets[key]

    async def choices(self, http, fixture: static_pages.StaticMatch, sport, picks):
        choices = static_pages.be_choices(fixture)
        if not choices and fixture.league_path:
            # La fila del día no embebe data-odd: su página de liga sí.
            path = fixture.league_path.strip("/").removeprefix("football/")
            rows = await self._league(http, path)
            row = next((r for r in rows if r.ext_id == fixture.ext_id), None)
            if row is not None:
                fixture.odds1, fixture.oddsX, fixture.odds2 = (
                    row.odds1,
                    row.oddsX,
                    row.odds2,
                )
                choices = static_pages.be_choices(fixture)
        # Detalle del partido (/match-odds/): solo los bettypes que
        # piden los picks — O/U con escalera exacta, doble oportunidad,
        # BTTS, AH, DNB. Sin 1X2 del listado se pide también el "1x2"
        # del detalle: el endpoint histórico lo sirve aunque el
        # listado del día ya no embeba data-odd.
        needed = {
            bt
            for pick in picks
            if (bt := static_pages.be_pick_bettype(pick.mercado, pick.seleccion))
        }
        if not choices:
            needed.add("1x2")
        detail_failed = False
        for bettype in sorted(needed):
            rows = await self._market(http, fixture, bettype)
            if rows is None:
                detail_failed = True
            else:
                choices += rows
        if not choices:
            # Fallo transitorio del endpoint -> "error" (reintento), no
            # "miss" — no se quema la marca hist del fixture.
            return ("error", []) if detail_failed else ("miss", [])
        return ("ok", choices)


@dataclass
class _SofaFixture:
    """Fixture Sofascore mínimo para el contrato de fuente."""

    event_id: int
    home_name: str
    away_name: str
    start: Optional[datetime]

    @property
    def ext_id(self) -> str:
        return f"sofascore:{self.event_id}"


@dataclass
class _SofaSearchSpace:
    """Marcador de "modo búsqueda": los listados `scheduled-events`
    no están disponibles (directo vetado por Cloudflare a nivel IP,
    espejos sin cuota o sin ruta de calendario) y los picks del cubo
    se resuelven por búsqueda dual de evento contra los mirrors
    RapidAPI — las peticiones salen de sus servidores, no de la VM.
    No es un listado: `match()` lo detecta y resuelve pick a pick."""

    sport: str
    providers: list
    http: httpx.AsyncClient


# Retorno de `match()` en modo búsqueda cuando la API falla (cuota o
# red): el loop lo cuenta "sin revisar" como un error de fixtures —
# NO marca `nofixture`, porque no sabemos si el evento existe.
_SEARCH_FAILED = object()

# Tolerancia del modo búsqueda: la búsqueda dual devuelve TODAS las
# ediciones históricas del enfrentamiento (Suecia-Polonia se repite
# cada año); la fecha del pick desambigúa — mismo margen que el
# listado del loop (±1 día).
_SEARCH_DATE_TOLERANCE = timedelta(days=1)


def _split_event_hint(hint: str) -> tuple[str, str]:
    """ "Suecia - Polonia" / "A vs B" -> ("Suecia", "Polonia")."""
    parts = re.split(r"[/+&]|\s+-\s+|\s+vs?\.?\s+", hint, maxsplit=1)
    home = parts[0].strip() if parts else ""
    away = parts[1].strip() if len(parts) > 1 else ""
    return home, away


def _search_query(hint: str) -> str:
    """Consulta para `/api/search/{q}`: "Suecia - Polonia" ->
    "Sweden Poland" (los eventos se buscan con ambos nombres)."""
    home, away = _split_event_hint(_translate_countries(hint))
    return f"{home} {away}".strip()


class _SofaScoreHistSource:
    """Sofascore: listado del día (`scheduled-events`) + odds completas
    por evento (`/api/v1/event/{id}/odds/1/all`).

    Cubre lo que las fuentes anteriores no tenían: selecciones
    nacionales, femenino, ligas exóticas y mercados extra del partido
    (córners, tarjetas, BTTS, escalera O/U completa, props de jugador
    cuando la casa los ofreció). Los eventos terminados siguen
    sirviendo apertura (`initialFractionalValue`) y cierre — dos
    puntos de curva reales, misma convención de anclas que el CSV
    (apertura a inicio-7d, cierre al inicio).

    Transportes:
    - Listado por día: solo rutas nativas — sportapi7 (si está
      suscrito) y el directo curl_cffi. Los espejos de esquema propio
      (allsportsapi2/sofascore6) no tienen listado por fecha.
    - Odds por evento: primero allsportsapi2 (cuota dedicada del
      snapshotter), luego los nativos sportapi7/sofascore6 y el
      directo al final (expone la IP a Cloudflare — último recurso,
      mismo orden que el snapshotter).

    `revisit_done=True`: un evento ya auditado en 1X2 por las páginas
    estáticas puede recibir aquí córners/tarjetas/props — una llamada
    trae TODOS los mercados.
    """

    name = "sofascore"
    revisit_done = True
    _MIN_SCORE = 0.6
    _SPORT_IDS = {"futbol": 1, "tenis": 2, "baloncesto": 3}
    _SLUGS = {1: "football", 2: "tennis", 3: "basketball"}
    _SPORT_NAMES = {v: k for k, v in _SPORT_IDS.items()}
    # Convención football_csv: la apertura se ancla a inicio-7d.
    _OPENING_ANCHOR = timedelta(days=7)

    def __init__(self, settings) -> None:
        self._listing: list = []
        self._odds: list = []
        # Providers de esquema propio que además sirven la búsqueda
        # dual de eventos (`/api/search/{local} {visitante}`) — el
        # plan B para cuando los listados por día están vetados. Van
        # primero los hosts con cuota propia y holgada (footapi7,
        # tennisapi1) y después la dedicada compartida con el
        # snapshotter (allsportsapi2 — la más disputada).
        self._search_providers: list[SofaScoreOddsProvider] = []
        sports = frozenset(self._SPORT_IDS)
        rapidapi_key = getattr(settings, "rapidapi_tennis_key", None)
        if rapidapi_key:
            if getattr(settings, "rapidapi_sportapi7_enabled", False):
                t7 = rapidapi_transport(
                    "sportapi7",
                    settings.rapidapi_sportapi7_host,
                    rapidapi_key,
                )
                self._listing.append(t7)
                self._odds.append(SofaScoreNativeOddsProvider(t7, sports))
            footapi_host = getattr(settings, "rapidapi_footapi_host", None)
            if footapi_host:
                p = SofaScoreOddsProvider(
                    name="footapi7",
                    api_key=rapidapi_key,
                    api_host=footapi_host,
                    sports=frozenset({"futbol"}),
                )
                self._odds.append(p)
                self._search_providers.append(p)
            tennis1_host = getattr(settings, "rapidapi_tennisapi1_host", None)
            if tennis1_host:
                p = SofaScoreOddsProvider(
                    name="tennisapi1",
                    api_key=rapidapi_key,
                    api_host=tennis1_host,
                    sports=frozenset({"tenis"}),
                )
                self._odds.append(p)
                self._search_providers.append(p)
            # Cuota dedicada de odds (esquema propio, solo tenis+futbol).
            allsports_host = getattr(settings, "rapidapi_allsports_host", None)
            if allsports_host:
                p = SofaScoreOddsProvider(
                    name="allsportsapi2",
                    api_key=rapidapi_key,
                    api_host=allsports_host,
                    sports=frozenset({"tenis", "futbol"}),
                )
                self._odds.append(p)
                self._search_providers.append(p)
            if getattr(settings, "rapidapi_sofascore6_enabled", False):
                self._odds.append(
                    SofaScoreNativeOddsProvider(
                        sofascore6_transport(
                            settings.rapidapi_sofascore6_host,
                            rapidapi_key,
                        ),
                        sports,
                    )
                )
        if getattr(settings, "sofascore_direct_enabled", True):
            t_direct = direct_transport()
            self._listing.append(t_direct)
            self._odds.append(SofaScoreNativeOddsProvider(t_direct, sports))
        self._days: dict[
            tuple[int, date], Optional[Union[list[_SofaFixture], _SofaSearchSpace]]
        ] = {}

    @property
    def sport_ids(self) -> dict[str, int]:
        return self._SPORT_IDS

    @staticmethod
    def _parse_event(raw: dict) -> Optional[_SofaFixture]:
        eid = raw.get("id")
        home = (raw.get("homeTeam") or {}).get("name") or ""
        away = (raw.get("awayTeam") or {}).get("name") or ""
        ts = raw.get("startTimestamp")
        if not eid or not home or not away:
            return None
        start = (
            datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
            if ts
            else None
        )
        return _SofaFixture(
            event_id=int(eid), home_name=home, away_name=away, start=start
        )

    async def fixtures(self, http, sport_id: int, day: date):
        """Todos los eventos del día; None = error/listing no disponible
        (reintento próxima pasada — un día vacío legítimo devuelve []).
        Si NINGÚN transporte de listado responde y hay provider de
        búsqueda configurado, devuelve `_SofaSearchSpace`: los picks
        del cubo se resolverán por búsqueda dual de evento."""
        key = (sport_id, day)
        if key in self._days:
            return self._days[key]
        slug = self._SLUGS.get(sport_id)
        found: Optional[list[_SofaFixture]] = None
        if slug:
            for transport in self._listing:
                data = await transport.get_json(
                    f"/api/v1/sport/{slug}/scheduled-events/{day:%Y-%m-%d}"
                )
                if data is None:
                    continue
                events = data.get("events")
                if events is None:
                    continue
                found = [f for f in (self._parse_event(e) for e in events) if f]
                break
        if found is None and self._search_providers:
            space = _SofaSearchSpace(
                sport=self._SPORT_NAMES.get(sport_id, "futbol"),
                providers=self._search_providers,
                http=http,
            )
            self._days[key] = space
            return space
        self._days[key] = found
        return found

    def match(self, fixtures, hint, dt, sofascore_id):
        if isinstance(fixtures, _SofaSearchSpace):
            # Modo búsqueda: el loop espera la corrutina devuelta.
            return self._search_match(fixtures, hint, dt, sofascore_id)
        if sofascore_id is not None:
            for f in fixtures:
                if f.event_id == sofascore_id:
                    return f
        # Los tipsters escriben selecciones en español ("Suecia -
        # Polonia") y Sofascore las nombra en inglés — se puntúa con
        # la traducción y con el original y se queda la mejor.
        hints = [hint]
        translated = _translate_countries(hint)
        if translated != hint:
            hints.append(translated)
        best: Optional[_SofaFixture] = None
        best_score = 0.0
        for f in fixtures:
            score = max(match_score(h, f.home_name, f.away_name) for h in hints)
            if score > best_score:
                best, best_score = f, score
        return best if best_score >= self._MIN_SCORE else None

    async def _search_match(self, space: _SofaSearchSpace, hint, dt, sofascore_id):
        """Resolución por búsqueda dual cuando el listado del día no
        está disponible: `/api/search/{local} {visitante}` devuelve
        entidades `event` de TODAS las ediciones del enfrentamiento —
        se filtra por fecha del pick (±1d) y se puntúa como el match
        normal. Devuelve `_SEARCH_FAILED` si la API falla (transitorio:
        sin marca `nofixture`, reintenta en la próxima pasada)."""
        if sofascore_id is not None:
            # El snapshotter en vivo ya identificó el evento — misma
            # familia de ids: gratis, sin llamada de búsqueda.
            home, away = _split_event_hint(hint)
            return _SofaFixture(sofascore_id, home, away, dt)
        query = _search_query(hint)
        if not query:
            return None
        # Primero los hosts cuya cuota está libre y son del deporte
        # del pick; allsportsapi2 (compartida con el snapshotter) va
        # al final de su grupo. Ningún resultado utilizable y todos
        # en error -> _SEARCH_FAILED (transitorio, no miss).
        providers = sorted(
            space.providers,
            key=lambda p: 0 if space.sport in p.SUPPORTED_SPORTS else 1,
        )
        results = None
        saw_live = False
        for provider in providers:
            if is_rate_limited(provider.NAME):
                continue
            saw_live = True
            results = await provider._search(space.http, space.sport, query)
            if results is not None:
                break
        else:
            if not saw_live:
                return _SEARCH_FAILED
        if results is None:
            return _SEARCH_FAILED
        hints = [hint]
        translated = _translate_countries(hint)
        if translated != hint:
            hints.append(translated)
        day = dt.date() if isinstance(dt, datetime) else dt
        best: Optional[_SofaFixture] = None
        best_score = 0.0
        for res in results:
            if res.get("type") != "event":
                continue
            fx = self._parse_event(res.get("entity") or {})
            if fx is None:
                continue
            if (
                day is not None
                and fx.start is not None
                and abs(fx.start - dt) > _SEARCH_DATE_TOLERANCE
            ):
                continue
            score = max(match_score(h, fx.home_name, fx.away_name) for h in hints)
            if score > best_score:
                best, best_score = fx, score
        return best if best_score >= self._MIN_SCORE else None

    def ext_id(self, fixture: _SofaFixture) -> str:
        return fixture.ext_id

    def fixture_key(self, fixture: _SofaFixture) -> str:
        return fixture.ext_id

    @staticmethod
    def _to_choices(rows: list[MarketChoice], start) -> list[HistoricalChoice]:
        """MarketChoice (cierre + apertura) -> HistoricalChoice con la
        curva de dos puntos (convención del CSV). Sin precio no hay fila."""
        out: list[HistoricalChoice] = []
        for c in rows:
            points: list[PricePoint] = []
            if c.cuota_apertura:
                points.append(
                    PricePoint(
                        captured_at=start - _SofaScoreHistSource._OPENING_ANCHOR,
                        cuota=c.cuota_apertura,
                        suspended=c.suspended,
                    )
                )
            if c.cuota:
                points.append(
                    PricePoint(captured_at=start, cuota=c.cuota, suspended=c.suspended)
                )
            if points:
                out.append(
                    HistoricalChoice(
                        market_name=c.market_name,
                        choice_name=c.choice_name,
                        choice_group=c.choice_group,
                        points=points,
                    )
                )
        return out

    async def choices(self, http, fixture: _SofaFixture, sport, picks):
        """Todas las opciones del evento por la cadena de transportes.
        [] explícito (evento sin mercados) = miss definitivo; todos
        None = error transitorio (cuota/red) -> reintento."""
        saw_empty = False
        # Los hosts especializados del deporte van primero (footapi7,
        # tennisapi1 — cuota propia holgada); la dedicada compartida
        # con el snapshotter y los nativos directos quedan de reserva.
        ordered = sorted(
            self._odds,
            key=lambda p: 0 if sport in getattr(p, "SUPPORTED_SPORTS", ()) else 1,
        )
        for provider in ordered:
            rows = await provider.fetch_odds(fixture.ext_id, sport)
            if rows is None:
                continue
            choices = self._to_choices(rows, fixture.start or utc_now())
            if choices:
                return "ok", choices
            saw_empty = True
        return ("miss", []) if saw_empty else ("error", [])


def _book_defs(books, book) -> list:
    """Las (idx, choice_name, choice_group) del market_book dado."""
    for candidate, defs in books:
        if candidate.market_book_id == book.market_book_id:
            return defs
    return []


_BACKFILL_PROVIDERS = (
    football_csv.NAME,
    tennisdata.NAME,
    static_pages.NAME_TE,
    static_pages.NAME_BE,
    _SofaScoreHistSource.name,
    oddspapi.NAME,
    oddsfeed.NAME,
    *_LLM_EXTRACTOR_PROVIDERS,
)


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


async def _resolved_pick_ids(session) -> set[int]:
    """Picks con filas de backfill a su nombre (`parsed_pick_id`).

    Complementa `_done_event_ids`: un pick cuyo `odds_event_id`
    apunta a un evento de otra familia ("sofascore:..." del
    snapshotter en vivo) sigue teniendo su cuota auditada si una
    fuente de backfill le escribió filas — sin esta comprobación el
    embudo lo cuenta "en cola" para siempre."""
    result = await session.exec(
        select(OddsSnapshot.parsed_pick_id)
        .where(OddsSnapshot.provider.in_(_BACKFILL_PROVIDERS))
        .where(OddsSnapshot.parsed_pick_id != None)  # noqa: E711
        .distinct()
    )
    return set(result.all())


def _sources(settings) -> list[_HistorySource]:
    """Fuentes disponibles según la configuración, en orden de uso."""
    sources: list[_HistorySource] = [
        _FootballCsvSource(),
        _TennisDataSource(),
        _TennisExplorerSource(),
        _BetExplorerSource(),
        _SofaScoreHistSource(settings),
    ]
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


def _llm_done_key() -> str:
    """Marca de procesado del pipeline web+LLM por pick (neutra:
    compartida por groq/gemini/cerebras — el intento cuenta una vez
    sea cual sea el motor que lo hizo)."""
    return f"{_LLM_ODDS_MARK}|done|{{}}"


def _pick_pending(
    pick: ParsedPick,
    done: set[str],
    settings,
    resolved_picks: set[int] | None = None,
) -> bool:
    """El pick aún tiene alguna fuente VIVA que no lo ha resuelto.

    Las fuentes API marcan "nofixture"; Gemini marca "done" cuando ya
    lo intentó (cubierto por filas existentes, sin cuota en la web o
    no buscable). Una fuente aparcada por quota larga (>~24 h, p. ej.
    reset mensual) cuenta como saldada: no hay nada que reintentar
    hasta que renueve, y las marcas caducan solas si vuelve.
    """
    # Cobertura ya escrita: el evento tiene filas de una fuente de
    # backfill — resuelto, nada más que hacer (ni el barrido gratis
    # de Gemini hace falta). O el pick tiene filas a su nombre aunque
    # su odds_event_id apunte a otra familia (snapshotter en vivo).
    if pick.odds_event_id in done or (
        resolved_picks is not None and pick.id in resolved_picks
    ):
        return False
    # Solo cuentan las fuentes que cubren el deporte del pick — un
    # fútbol nunca lo intenta tennisdata y no debe quedar "en cola"
    # suyo para siempre.
    sport = _canonical_sport(pick.deporte)
    covering = [s for s in _sources(settings) if sport in s.sport_ids]
    sources_missed = all(
        is_missed(f"{s.name}|nofixture|{pick.id}") or _source_quota_parked(s)
        for s in covering
    )
    if not sources_missed:
        return True
    if _llm_odds_configured(settings) and not is_missed(
        _llm_done_key().format(pick.id)
    ):
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
    resolved = await _resolved_pick_ids(session)
    return sum(1 for p in picks if _pick_pending(p, done, settings, resolved))


async def backfill_breakdown(session) -> dict:
    """Desglose del backlog de cuotas históricas para la pantalla
    depurativa — el embudo en números:

    - `resueltos`: el evento ya tiene cuotas registradas.
    - `en_cola`: alguna fuente VIVA le debe el intento (o Gemini) —
      trabajo real que irá bajando con cada pasada.
    - `aplazados`: solo le deben fuentes aparcadas por quota larga —
      no hay nada que hacer hasta que renueven (vuelven solos: las
      marcas `missed` caducan).
    - `agotados`: todas las fuentes lo intentaron y nadie lo tenía.
    """
    settings = get_settings()
    sources = _sources(settings)
    if not sources:
        return {}
    picks = await _load_picks(session, None)
    done = await _done_event_ids(session)
    resolved_picks = await _resolved_pick_ids(session)
    parked = {s.name for s in sources if _source_quota_parked(s)}
    resueltos = en_cola = aplazados = agotados = 0
    por_fuente = {s.name: 0 for s in sources}
    llm_pendientes = 0
    for pick in picks:
        if pick.odds_event_id in done or pick.id in resolved_picks:
            resueltos += 1
            continue
        sport = _canonical_sport(pick.deporte)
        owing = [
            s
            for s in sources
            if sport in s.sport_ids and not is_missed(f"{s.name}|nofixture|{pick.id}")
        ]
        live_owing = [s for s in owing if s.name not in parked]
        for s in live_owing:
            por_fuente[s.name] += 1
        llm_owes = _llm_odds_configured(settings) and not is_missed(
            _llm_done_key().format(pick.id)
        )
        if llm_owes:
            llm_pendientes += 1
        if live_owing or llm_owes:
            en_cola += 1
        elif owing:
            aplazados += 1
        else:
            agotados += 1
    # Quién resolvió qué: picks (distintos) cuya cuota vino de cada
    # fuente de backfill — las filas llevan parsed_pick_id del pick
    # que disparó la descarga.
    resueltos_por_fuente = {
        str(provider): n
        for provider, n in (
            await session.exec(
                select(
                    OddsSnapshot.provider,
                    func.count(distinct(OddsSnapshot.parsed_pick_id)),
                )
                .where(OddsSnapshot.provider.in_(_BACKFILL_PROVIDERS))
                .where(OddsSnapshot.parsed_pick_id != None)  # noqa: E711
                .group_by(OddsSnapshot.provider)
            )
        ).all()
    }
    return {
        "total": len(picks),
        "resueltos": resueltos,
        "resueltos_por_fuente": resueltos_por_fuente,
        "en_cola": en_cola,
        "aplazados": aplazados,
        "agotados": agotados,
        "por_fuente": por_fuente,
        "llm_pendientes": llm_pendientes,
        # Acumulado histórico del pipeline web+LLM: intentos marcados
        # `llm-odds|done` (resueltos con fila o descartados sin
        # cuota publicada) — la cola "ya masticada" que no se repite.
        "llm_procesados": count_missed_prefix(f"{_LLM_ODDS_MARK}|done|"),
        "fuentes_en_pausa": {
            s.name: rate_limited_until(s.name).isoformat()  # type: ignore[union-attr]
            for s in sources
            if s.name in parked
        },
        "ultima_pasada": loop_summary("odds_backfill"),
        "ultimas_pasadas": loop_history("odds_backfill"),
        # Auditoría semanal de consistencia (odds/audit.py): picks
        # auditados y cuántas filas guardadas sigue casando el
        # mapeador — la red de seguridad de las comparaciones.
        "ultima_auditoria": loop_summary("odds_audit"),
    }


async def _run_source(
    source: _HistorySource,
    picks: list[ParsedPick],
    done: set[str],
    registered: set[str],
    session,
    http: httpx.AsyncClient,
    apply: bool,
    report: BackfillReport,
    resolved_picks: set[int] | None = None,
) -> None:
    """Pasa una fuente sobre los picks pendientes de SU cobertura
    deportiva. Añade a `done` los eventos que deja escritos."""
    if _source_quota_parked(source):
        report.detalles.append(
            f"  ! [{source.name}] quota agotada hasta "
            f"{rate_limited_until(source.name)} — pasada saltada, "
            "sus pendientes no bloquean el drenaje"
        )
        return
    nofixture_key = f"{source.name}|nofixture|{{}}"
    hist_key = f"{source.name}|hist|{{}}"

    # Resolución pick -> fixture agrupada por (deporte, día): una
    # llamada de eventos por cubo cubre todos sus picks.
    buckets: dict[tuple[int, object], list[ParsedPick]] = {}
    for pick in picks:
        sport = _canonical_sport(pick.deporte)
        if sport not in source.sport_ids:
            continue
        if pick.odds_event_id in done and not getattr(source, "revisit_done", False):
            continue
        if resolved_picks is not None and pick.id in resolved_picks:
            continue
        if is_missed(nofixture_key.format(pick.id)):
            continue
        buckets.setdefault(
            (source.sport_ids[sport], pick.fecha_evento.date()), []
        ).append(pick)

    fixtures_picks: dict[str, tuple[object, str, list[ParsedPick]]] = {}
    # Días más recientes primero: los picks frescos (los que más
    # importan) se resuelven antes de que se agote la paga diaria.
    for (sport_id, day), day_picks in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], -item[0][1].toordinal()),
    ):
        sport = _canonical_sport(day_picks[0].deporte)
        # Un día por llamada y la caché del cliente dedup los días
        # compartidos entre buckets vecinos.
        fixtures: list = []
        search_space: Optional[_SofaSearchSpace] = None
        api_error = False
        for delta in (-1, 0, 1):
            day_fixtures = await source.fixtures(
                http, sport_id, day + timedelta(days=delta)
            )
            if day_fixtures is None:
                api_error = True
                break
            if isinstance(day_fixtures, _SofaSearchSpace):
                search_space = search_space or day_fixtures
                continue
            fixtures += day_fixtures
        if api_error:
            report.sin_revisar += len(day_picks)
            report.detalles.append(
                f"  ! [{source.name}] error de API en eventos {sport} "
                f"{day}: {len(day_picks)} picks sin revisar"
            )
            continue
        # Con listado vacío y modo búsqueda disponible, los picks se
        # resuelven evento a evento contra allsportsapi2.
        match_space = search_space if not fixtures else None
        for pick in day_picks:
            hint = _lookup_hint(pick, sport) or ""
            found = source.match(
                match_space if match_space is not None else fixtures,
                hint,
                pick.fecha_evento,
                _sofascore_id(pick),
            )
            fixture = await found if inspect.isawaitable(found) else found
            if fixture is _SEARCH_FAILED:
                report.sin_revisar += 1
                report.detalles.append(
                    f"  ! [{source.name}] error de API resolviendo "
                    f"pick {pick.id} — reintento próxima pasada"
                )
                continue
            if fixture is None:
                report.sin_fixture += 1
                report.detalles.append(
                    f"  - pick {pick.id}: sin fixture "
                    f"({pick.evento or pick.seleccion}) [{source.name}]"
                )
                if apply:
                    mark_missed(nofixture_key.format(pick.id))
                continue
            real_start = getattr(fixture, "start", None)
            if (
                real_start is not None
                and pick.fecha_evento is not None
                and pick.fecha_evento.date() != real_start.date()
            ):
                # La fecha guardada era la del repost: la del fixture
                # es la verdad del provider. La corregimos para que el
                # resto de fuentes busquen el día correcto, y limpiamos
                # las marcas "nofixture" dejadas con la fecha mala.
                report.detalles.append(
                    f"  ~ pick {pick.id}: fecha {pick.fecha_evento.date()} "
                    f"-> {real_start.date()} [{source.name}]"
                )
                report.fechas_corregidas += 1
                pick.fecha_evento = real_start
                if apply:
                    session.add(pick)
                    for other in _sources(get_settings()):
                        if other.name != source.name:
                            clear_missed(f"{other.name}|nofixture|{pick.id}")
            ext_id = source.ext_id(fixture)
            if ext_id in done and not getattr(source, "revisit_done", False):
                continue
            slot = fixtures_picks.setdefault(
                source.fixture_key(fixture), (fixture, sport, [])
            )
            slot[2].append(pick)
            report.con_fixture += 1

    # Una curva por fixture alimenta todos sus picks.
    pendientes_curva = sum(len(picks_) for _, _, picks_ in fixtures_picks.values())
    procesados_curva = 0
    for fixture, sport, event_picks in fixtures_picks.values():
        ext_id = source.ext_id(fixture)
        if is_missed(hist_key.format(source.fixture_key(fixture))):
            procesados_curva += len(event_picks)
            report.sin_historial += len(event_picks)
            continue
        status, choices = await source.choices(http, fixture, sport, event_picks)
        if status == "miss":
            # El fixture nunca tuvo mercados en esta fuente.
            mark_missed(hist_key.format(source.fixture_key(fixture)))
            procesados_curva += len(event_picks)
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
            report.sin_revisar += pendientes_curva - procesados_curva
            return
        if status == "error":
            # Error/cuota: no se marca — reintenta en otra pasada.
            report.sin_revisar += pendientes_curva - procesados_curva
            report.detalles.append(
                f"  ! {fixture.home_name} vs {fixture.away_name}: "
                f"error de API [{source.name}], se reintenta luego"
            )
            break
        procesados_curva += len(event_picks)

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

        # key -> pick que casó esa opción: la fila se atribuye al
        # pick real, no al primero del grupo (un pick O/U que no
        # mapea nada no puede quedar "resuelto" por la fila del
        # ganador de otro pick del mismo partido).
        matched_keys: dict[tuple, int] = {}
        matched_pick_ids: set[int] = set()
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
            matched_keys.setdefault(key, pick.id)
            matched_pick_ids.add(pick.id)
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
            # Eventos revisitados (fuentes lazy): las opciones ya
            # escritas no se duplican — una fila libre del mismo key
            # se re-sella al pick que casó (cubre el caso del pick
            # re-etiquetado cuya fila buena ya existía sin dueño).
            existing_keys: set[tuple] = set()
            if ext_id in done:
                existing_keys = {
                    (r[0], r[1], r[2])
                    for r in (
                        await session.exec(
                            select(
                                OddsSnapshot.market_name,
                                OddsSnapshot.choice_name,
                                OddsSnapshot.choice_group,
                            )
                            .where(OddsSnapshot.event_ext_id == ext_id)
                            .where(OddsSnapshot.provider == source.name)
                        )
                    ).all()
                }
            for key, owner_id in matched_keys.items():
                if key in existing_keys:
                    free = (
                        await session.exec(
                            select(OddsSnapshot)
                            .where(OddsSnapshot.provider == source.name)
                            .where(OddsSnapshot.event_ext_id == ext_id)
                            .where(OddsSnapshot.market_name == key[0])
                            .where(OddsSnapshot.choice_name == key[1])
                            .where(
                                (OddsSnapshot.choice_group == key[2])
                                if key[2] is not None
                                else (OddsSnapshot.choice_group == None)  # noqa: E711
                            )
                            .where(OddsSnapshot.parsed_pick_id == None)  # noqa: E711
                        )
                    ).all()
                    if free:
                        for row in free:
                            row.parsed_pick_id = owner_id
                            session.add(row)
                        report.detalles.append(
                            f"  ~ pick {owner_id}: {key[0]}/{key[1]} "
                            f"re-sellado ({len(free)} filas libres) "
                            f"[{source.name}]"
                        )
                        continue
                    # Todas atribuidas a picks hermanos: se escribe la
                    # copia propia (misma convención que el snapshotter,
                    # una fila por pick resuelto) cayendo al write de abajo.
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
                            parsed_pick_id=owner_id,
                        )
                    )
                    report.filas += 1
                    report.filas_por_fuente[source.name] = (
                        report.filas_por_fuente.get(source.name, 0) + 1
                    )
            done.add(ext_id)
            if resolved_picks is not None:
                resolved_picks |= matched_pick_ids
        elif matched_keys:
            n = sum(len(downsample(by_key[k].points)) for k in matched_keys)
            report.filas += n
            report.filas_por_fuente[source.name] = (
                report.filas_por_fuente.get(source.name, 0) + n
            )


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
    resolved_picks: set[int] | None = None,
) -> None:
    """Última fuente de la cascada: Gemini lee comparadores web.

    Recorre picks que ninguna fuente determinista dejó casados — ya
    sea porque su evento no tiene filas o porque su mercado no estaba
    cubierto (córners/tarjetas...). Para cada uno busca la cuota de
    cierre en la web, y solo la escribe si la tripleta propuesta casa
    con el pick vía `map_pick_choices` (autocheck determinista).
    """
    settings = get_settings()
    if not _llm_odds_ready(settings):
        # Sin key no hay nada que hacer; en cooldown tampoco — salir
        # aquí evita quemar rescates de OpenAI para picks que no se
        # van a buscar en esta pasada.
        return
    done_key = _llm_done_key()
    resolved = resolved_picks or set()
    candidates = [
        p
        for p in picks
        if p.id is not None
        and p.id not in resolved
        and not is_missed(done_key.format(p.id))
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

    # Agrupa por evento: patas/picks del mismo partido comparten la
    # página del comparador — una búsqueda + una extracción por página
    # resuelven el grupo entero en vez de un ciclo por pick.
    groups: dict[tuple[str, date, str], list[ParsedPick]] = {}
    # Los grupos cuyo cruce ya venía bien en el pick gastan las
    # búsquedas antes que los que necesitaron rescate del raw (hint
    # más fiable = búsqueda más útil con la cuota escasa).
    group_rank: dict[tuple[str, date, str], int] = {}
    for pick in pending:
        sport = _canonical_sport(pick.deporte)
        hint = (_lookup_hint(pick, sport) or "") if sport else ""
        desc = _market_desc(pick)
        if sport not in GeminiResearchProvider.SUPPORTED_SPORTS or not desc:
            if apply:
                mark_missed(done_key.format(pick.id))
            continue
        # En tenis el hint es el jugador (vale cualquier nombre); en
        # los demás deportes hace falta un cruce "A vs B" — si el
        # `evento` guardó la apuesta o basura OCR, la búsqueda es
        # imposible y se intenta rescatar el partido del raw
        # (texto -> mini-LLM -> imagen del slip) antes de rendirlo.
        # La forma del cruce se comprueba sobre el `evento` ORIGINAL:
        # `translate_team_aliases` (dentro de _lookup_hint) devuelve el
        # nombre canónico en minúsculas y "a - b" en minúscula se
        # rechaza a propósito (ambiguo con rangos "8 - 10") — un
        # evento ya bueno tipo "Ipswich - Liverpool" se re-rescataba
        # en cada pasada por culpa de esa bajada de caja.
        usable = bool(hint) and (
            sport == "tenis"
            or looks_like_match_hint((pick.evento or "").strip())
            or looks_like_match_hint(hint)
        )
        rescued_ok = False
        if not usable and sport != "tenis":
            rescued = await rescue_evento(session, pick, settings)
            # El rescate también tiene que tener forma de cruce:
            # basura tipo "Sáb - sep" haría una búsqueda imposible.
            if rescued and looks_like_match_hint(rescued):
                # In-place: la mutación solo se persiste si `run()`
                # hace commit al final (modo apply).
                pick.evento = rescued
                session.add(pick)
                hint = rescued
                usable = True
                rescued_ok = True
                report.detalles.append(
                    f"  ~ pick {pick.id}: evento rescatado del raw -> {rescued}"
                )
            elif rescued:
                report.detalles.append(
                    f"  - pick {pick.id}: rescate sin forma de partido "
                    f"({rescued}) [{_LLM_ODDS_MARK}]"
                )
            else:
                report.detalles.append(
                    f"  - pick {pick.id}: evento no rescatable "
                    f"(ni texto ni OCR ni imagen) [{_LLM_ODDS_MARK}]"
                )
        if not usable:
            if apply:
                mark_missed(done_key.format(pick.id))
            continue
        evento = re.sub(r"\s+", " ", (pick.evento or hint).strip().lower())
        key = (sport, pick.fecha_evento.date(), evento)
        groups.setdefault(key, []).append(pick)
        group_rank[key] = min(group_rank.get(key, 1), 1 if rescued_ok else 0)

    for (sport, _day, _evento), group in sorted(
        groups.items(), key=lambda kv: group_rank[kv[0]]
    )[:_GEMINI_MAX_EVENTS_PER_RUN]:
        provider = GeminiResearchProvider(
            sport, settings.google_api_key, calls_as=_GEMINI_ODDS_PROVIDER
        )
        hint = _lookup_hint(group[0], sport) or ""
        results = await provider.find_closing_odds_multi(
            group[0].fecha_evento, hint, [_market_desc(p) for p in group]
        )
        cooled = provider._in_cooldown()
        for idx, pick in enumerate(group):
            found = results.get(idx)
            if found is None:
                if cooled:
                    # Búsqueda abortada por ritmo/cuota: sin marca,
                    # se retoma en la próxima pasada.
                    continue
                if apply:
                    mark_missed(done_key.format(pick.id))
                report.detalles.append(
                    f"  - pick {pick.id}: sin cuota en comparadores "
                    f"[{_LLM_ODDS_MARK}]"
                )
                continue
            _record_gemini_closing(
                session, events, registered, done, pick, found, apply, report
            )
        if cooled:
            report.detalles.append(
                "  ! [gemini-odds] cooldown del free tier — "
                "el resto sigue en la próxima pasada"
            )
            break


def _record_gemini_closing(
    session,
    events: dict[str, OddsEvent],
    registered: set[str],
    done_events: set[str],
    pick: ParsedPick,
    found,
    apply: bool,
    report: BackfillReport,
) -> None:
    """Valida y persiste la cuota que el extractor propuso para UN pick.

    Autocheck: la propuesta tiene que casar con el pick por la misma
    función que usa el comparador. Si el evento sintético tiene los
    lados invertidos se prueba la orientación contraria antes de
    descartar. La fila se firma con el motor que la extrajo
    (`{extractor}-odds`) — la auditoría dice quién produjo cada cuota.
    """
    sport = _canonical_sport(pick.deporte) or ""
    row_provider = f"{found.extractor or 'gemini'}-odds"
    ext_id = pick.odds_event_id or f"{row_provider}:{pick.id}"
    event = events.get(ext_id) or _synthetic_event(pick, sport, ext_id)
    probe = OddsSnapshot(
        provider=row_provider,
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
            mark_missed(_llm_done_key().format(pick.id))
        report.detalles.append(
            f"  - pick {pick.id}: propuesta no verificable "
            f"({found.market_name}/{found.choice_name}) "
            f"[{row_provider}]"
        )
        return

    report.mapeados += 1
    report.detalles.append(
        f"  + pick {pick.id}: {found.market_name} / {found.choice_name}"
        f" | cierre {found.cuota} ({found.casa or '?'}) "
        f"[{row_provider} via {found.source_url}]"
    )
    if apply:
        if ext_id not in registered:
            session.add(event)
            registered.add(ext_id)
        session.add(
            OddsSnapshot(
                provider=row_provider,
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
        done_events.add(ext_id)
        report.filas += 1
        report.filas_por_fuente[row_provider] = (
            report.filas_por_fuente.get(row_provider, 0) + 1
        )


async def run(apply: bool, limit: int | None) -> BackfillReport:
    settings = get_settings()
    report = BackfillReport()
    sources = _sources(settings)
    if not sources:  # imposible hoy: tennisdata no pide key
        report.detalles.append("Sin fuentes de histórico disponibles.")
        return report

    async with AsyncSessionLocal() as session, httpx.AsyncClient(timeout=60) as http:
        picks = await _load_picks(session, limit)
        report.picks = len(picks)
        done = await _done_event_ids(session)
        resolved = await _resolved_pick_ids(session)
        # Early-exit antes de tocar la API: si no hay backlog pendiente
        # el job diario no gasta ni una llamada.
        if not any(_pick_pending(p, done, settings, resolved) for p in picks):
            report.detalles.append("Sin backlog pendiente; no se llama a la API.")
            return report

        registered = set((await session.exec(select(OddsEvent.event_ext_id))).all())

        for source in sources:
            await _run_source(
                source,
                picks,
                done,
                registered,
                session,
                http,
                apply,
                report,
                resolved,
            )
        if _gemini_pass_lock.locked():
            report.detalles.append("  ! [llm-odds] pasada de goteo en curso — se omite")
        else:
            async with _gemini_pass_lock:
                await _run_gemini_pass(
                    picks, done, registered, session, apply, report, resolved
                )

        if apply:
            await session.commit()
            count_odds_rows_written(report.filas)
            # Resumen visible en la pantalla depurativa (qué hizo la
            # última pasada sin abrir logs del contenedor).
            record_loop_summary(
                "odds_backfill",
                {
                    "revisados": report.picks,
                    "filas": report.filas,
                    "con_fixture": report.con_fixture,
                    "sin_fixture": report.sin_fixture,
                    "sin_historial": report.sin_historial,
                    "sin_opcion": report.sin_opcion,
                    "mapeados": report.mapeados,
                    "sin_revisar": report.sin_revisar,
                    "fechas_corregidas": report.fechas_corregidas,
                    "filas_por_fuente": report.filas_por_fuente,
                },
            )

    return report


async def run_gemini_odds_pass(apply: bool) -> Optional[BackfillReport]:
    """Solo el tramo Gemini del backfill histórico (drenaje a goteo).

    El cooldown del free tier dura ~20 min — esperar a la pasada
    diaria dejaba el grifo cerrado 24 h. Este drenaje se engancha a
    un loop corto propio (`_run_gemini_odds_loop` en lifecycle):

    - sin key, provider aparcado o sin candidatos: sale gratis (unas
      consultas a la BD y nada más);
    - si la pasada diaria ya tiene el pass en curso: se omite para
      no duplicar búsquedas sobre los mismos candidatos.

    Devuelve el reporte si la pasada corrió, None si salió antes.
    """
    settings = get_settings()
    if not _llm_odds_ready(settings):
        return None
    if _gemini_pass_lock.locked():
        return None
    async with _gemini_pass_lock, AsyncSessionLocal() as session:
        picks = await _load_picks(session, None)
        done = await _done_event_ids(session)
        resolved = await _resolved_pick_ids(session)
        done_key = _llm_done_key()
        if not any(
            p.id is not None
            and p.id not in resolved
            and not is_missed(done_key.format(p.id))
            for p in picks
        ):
            return None
        registered = set((await session.exec(select(OddsEvent.event_ext_id))).all())
        report = BackfillReport()
        await _run_gemini_pass(
            picks, done, registered, session, apply, report, resolved
        )
        if apply:
            await session.commit()
            # Visible en la pantalla depurativa como "llm_odds":
            # qué hizo el último goteo sin abrir logs del contenedor.
            record_loop_summary(
                "llm_odds",
                {
                    "mapeados": report.mapeados,
                    "filas": report.filas,
                    "detalles": report.detalles[:20],
                },
            )
        return report
