"""Cosecha manual de cuotas históricas vía userscript (OddsPortal).

El canal es el espejo del backfill: el script de Tampermonkey navega
las páginas de partido en el navegador DEL USUARIO (que pasa
Cloudflare, la verificación de edad y la hidratación del SPA de
forma nativa) y POSTea las filas de cuotas que ve. Este módulo:

1. Sirve la cola: eventos con picks pendientes que aún no tienen
   cobertura de backfill (`GET /odds-harvest/queue`).
2. Recibe la cosecha: traduce los nombres de mercado del comparador
   al vocabulario interno de `map_pick_choices`, casa el evento con
   los picks pendientes por (deporte, fecha, equipos) y escribe las
   filas con `provider="oddsportal"` — una por opción, cuota de
   cierre anclada al inicio del evento (misma convención que las
   fuentes históricas: el cierre es el último precio prematch).
3. Idempotente: una fila (provider, event, market, choice, group)
   ya escrita no se duplica; las filas libres se re-sellan al pick
   que casó (mismo patrón que el backfill con fuentes lazy).

El evento queda registrado en `odds_events` con ext_id
`oddsportal:<match_id>` — el match_id (8 chars) sale del final de
la URL; sin él se deriva un id del par de equipos + fecha.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.schemas.odds_harvest import (
    HarvestEventPayload,
    HarvestIngestResult,
    HarvestOddsRow,
    HarvestPickInfo,
    HarvestQueueEvent,
)
from app.services.odds.compare import _PROP_PICK, map_pick_choices
from app.services.odds.snapshotter import _canonical_sport, _lookup_hint
from app.services.results.base import fold_name, match_score

NAME = "oddsportal"
_EXT_PREFIX = "oddsportal:"

# Match score mínimo para casar "Ipswich - Liverpool" del pick con el
# (home, away) que el script leyó en la página — mismo umbral que las
# fuentes de backfill.
_MIN_EVENT_SCORE = 0.6
# Ventana de fecha pick vs. fecha de la página: el `fecha_evento` del
# pick puede ser la del repost (±1 día de margen, como las fuentes).
_EVENT_DATE_TOLERANCE = timedelta(days=1)
# Ancla de la apertura cuando la página no la muestra por fila: la
# convención del backfill (CSV/Sofascore) la fija a inicio-7d.
_OPENING_ANCHOR = timedelta(days=7)

# Formatos de URL de partido vistos en cuotasahora/oddsportal:
#  - clásico: ".../ipswich-liverpool-Ab3Cd4Ef/"  (id al final del slug)
#  - h2h actual: "/football/h2h/arsenal-hA1Zm19f/everton-KluSTr9s/..."
#    con el match_id en el fragmento "#G0XLh1up"
_MATCH_ID = re.compile(r"-([A-Za-z0-9]{8})(?:/)?(?:[#?].*)?$")
_FRAGMENT_ID = re.compile(r"#([A-Za-z0-9]{8})\b")
_H2H_PAIR = re.compile(
    r"/h2h/[a-z0-9-]+?-([A-Za-z0-9]{8})/[a-z0-9-]+?-([A-Za-z0-9]{8})/", re.I
)


def event_ext_id(url: str, home: str, away: str, start: Optional[datetime]) -> str:
    """`oddsportal:<match_id>` — identificador estable del evento.

    Prioridad: match_id del fragmento (el real del comparador), luego
    el par de team-ids de la URL h2h, luego el id clásico del slug.
    Sin nada, se deriva un id del evento para que la re-cosecha no
    duplique.
    """
    u = url.strip()
    m = _FRAGMENT_ID.search(u)
    if m:
        return f"{_EXT_PREFIX}{m.group(1)}"
    m = _H2H_PAIR.search(u)
    if m:
        return f"{_EXT_PREFIX}{m.group(1)}-{m.group(2)}"
    m = _MATCH_ID.search(u)
    if m:
        return f"{_EXT_PREFIX}{m.group(1)}"
    base = f"{fold_name(home)}|{fold_name(away)}|{(start or datetime.min).date()}"
    return f"{_EXT_PREFIX}x{hashlib.sha1(base.encode()).hexdigest()[:10]}"


# ---------------------------------------------------------------------------
# Traducción mercado/selección del comparador -> vocabulario interno
# ---------------------------------------------------------------------------

# (patrón sobre el nombre normalizado, nombre interno por deporte).
# Los mercados internos son los que entiende `map_pick_choices`
# ("Full time", "Match goals", "Asian handicap"...).
# ORDEN IMPORTA: los mercados específicos van antes que el genérico
# O/U ("Corners O/U" también contiene "o/u") y que el 1X2
# ("resultado exacto" contiene "resultado").
_MARKET_MAP: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = (
    (
        re.compile(r"c[oó]rner"),
        {"futbol": "Corners 2-Way"},
    ),
    (
        re.compile(r"tarjeta|card|booking"),
        {"futbol": "Cards in match"},
    ),
    (
        re.compile(r"asian|asi[aá]tic"),
        {
            "futbol": "Asian handicap",
            "tenis": "Game handicap",
            "baloncesto": "Asian handicap",
        },
    ),
    (
        re.compile(r"both.?teams|btts|ambos"),
        {"futbol": "Both teams to score"},
    ),
    (
        re.compile(r"double.?chance|doble"),
        {"futbol": "Double chance"},
    ),
    (
        re.compile(r"draw.?no.?bet|empate\s+no|sin\s+empate"),
        {"futbol": "Draw no bet"},
    ),
    (
        re.compile(r"correct.?score|marcador"),
        {"futbol": "Correct score", "tenis": "Set score"},
    ),
    (
        re.compile(r"first.?set|1[ºaer]?\s*set|primer\s*set"),
        {"tenis": "First set winner"},
    ),
    (
        re.compile(r"half.?time|descanso|1[ªa]?\s*parte|primera\s*parte"),
        {"futbol": "1st half"},
    ),
    (
        # "Local/Visitante" es el moneyline de basket/tenis en
        # cuotasahora; "12" a veces etiqueta el 2-way sin empate.
        re.compile(
            r"^1x2$|^12$|full.?time|resultado(\s+final)?$|ganador|"
            r"local\s*/?\s*visitante|moneyline|home\s*/?\s*away"
        ),
        {"futbol": "Full time", "tenis": "Full time", "baloncesto": "Full time"},
    ),
    (
        re.compile(r"over.?under|o/u|totales|mas.?o.?menos|m[aá]s\s*/\s*menos|total\b"),
        {
            "futbol": "Match goals",
            "tenis": "Total games won",
            "baloncesto": "Total points",
        },
    ),
)


def translate_market(raw: str, sport: str) -> Optional[str]:
    """Nombre interno del mercado, o None si no es traducible."""
    text = fold_name(raw or "").strip()
    for pattern, by_sport in _MARKET_MAP:
        if pattern.search(text):
            return by_sport.get(sport)
    return None


# Etiquetas O/U en español/inglés -> "Over"/"Under" del comparador.
_OU_LABELS = (
    (re.compile(r"^(over|mas|más)\b", re.I), "Over"),
    (re.compile(r"^(under|menos|bajo)\b", re.I), "Under"),
)


@dataclass
class TranslatedRow:
    """Una fila cosechada ya en vocabulario interno."""

    market_name: str
    choice_name: str
    choice_group: Optional[str]
    closing: float
    opening: Optional[float]
    bookmaker: Optional[str]


def translate_row(
    row: HarvestOddsRow, sport: str, home: str, away: str
) -> Optional[TranslatedRow]:
    """Fila del script -> tripleta (market, choice, group) interna.

    None = fila no traducible (mercado desconocido, cuota ausente).
    El hándicap asiático llega como selección "1"/"2" + línea: se
    reescribe al formato que espera `_match_handicap` —
    `choice_group` = la línea, `choice_name` = el equipo del lado.
    """
    market = translate_market(row.market, sport)
    if market is None or row.closing is None or row.closing <= 1.0:
        return None
    selection = (row.selection or "").strip()
    group = f"{row.line:g}" if row.line is not None else None
    if market in ("Asian handicap", "Game handicap", "Set handicap"):
        if row.line is None:
            return None
        side = {"1": 0, "2": 1}.get(selection[:1])
        if side is None:
            return None
        team = home if side == 0 else away
        # En AH el signo de la línea viene dado por la página; si el
        # script no lo capturó con signo, el local lleva el negativo
        # (convención: "1 (-1.5) / 2 (+1.5)").
        line = row.line
        if side == 0 and line > 0:
            line = -line
        return TranslatedRow(
            market, team, f"{line:g}", row.closing, row.opening, row.bookmaker
        )
    for pattern, wanted in _OU_LABELS:
        if pattern.match(selection):
            return TranslatedRow(
                market, wanted, group, row.closing, row.opening, row.bookmaker
            )
    return TranslatedRow(
        market, selection, group, row.closing, row.opening, row.bookmaker
    )


# Bookmaker preferido por precio: bet365 es la casa del tipster (la
# cuota que hay que auditar); Pinnacle es la referencia sharp de
# respaldo; el resto por orden de llegada.
_BOOKMAKER_PRIORITY = ("bet365", "pinnacle", "marathonbet", "1xbet")


def _pick_rows(rows: list[TranslatedRow]) -> dict[tuple, TranslatedRow]:
    """Una fila por (market, choice, group): la del bookie preferido."""

    def rank(r: TranslatedRow) -> int:
        try:
            return _BOOKMAKER_PRIORITY.index((r.bookmaker or "").lower())
        except ValueError:
            return len(_BOOKMAKER_PRIORITY)

    best: dict[tuple, TranslatedRow] = {}
    for row in rows:
        key = (row.market_name, row.choice_name, row.choice_group)
        if key not in best or rank(row) < rank(best[key]):
            best[key] = row
    return best


# ---------------------------------------------------------------------------
# Cola de cosecha
# ---------------------------------------------------------------------------


def _split_teams(text: str) -> tuple[str, str]:
    """ "Ipswich - Liverpool" -> ("Ipswich", "Liverpool")."""
    parts = re.split(r"\s+-\s+|\s+vs?\.?\s+|\s+–\s+", text, maxsplit=1)
    home = parts[0].strip() if parts else ""
    away = parts[1].strip() if len(parts) > 1 else ""
    return home, away


# Mercados que el comparador (cuotasahora) NO publica como pestaña:
# córners, tarjetas, props de jugador, paradas, aces/dobles faltas,
# breaks... Un evento cuyos picks pendientes piden SOLO esto no puede
# casar nada — la visita solo navega para archivar filas que ningún
# pick consumirá. El filtro mira mercado+selección (los ~37 picks con
# `mercado='ganador'` heredado de acumulador llevan la prop en la
# selección; por eso no basta mirar el campo mercado).
_COMPARATOR_MISSING = re.compile(
    r"c[oó]rner|tarjeta|card|amonest|anot\w*\s+gol|scorer|"
    r"parada|portero|porter[ií]a|disparo|remate|asistenc|"
    r"aces?\b|doble[s]?\s*falta|break|primer\s*set|1[ºaer]?\s*set",
    re.IGNORECASE,
)


def _comparator_supported(pick: ParsedPick) -> bool:
    """El mercado del pick existe como pestaña en el comparador.

    Ojo: esto decide si la VISITA al comparador puede casar algo, no
    si el mercado es auditable en abstracto — córners/tarjetas sí son
    traducibles (`_MARKET_MAP`) para otras fuentes que sí los tienen
    (oddspapi), pero cuotasahora no los muestra.
    """
    if pick.es_combinada or fold_name(pick.mercado or "") == "combinada":
        return False  # el padre no es un mercado: se liquida por patas
    text = f"{pick.mercado or ''} {pick.seleccion or ''}"
    if _PROP_PICK.search(text) or _COMPARATOR_MISSING.search(text):
        return False
    return True


async def harvest_queue(session: AsyncSession) -> list[HarvestQueueEvent]:
    """Eventos con picks pendientes de cobertura, agrupados.

    Reutiliza el mismo concepto de "pendiente" que el backfill: el
    pick no tiene filas de ninguna fuente y le quedan fuentes por
    intentar — o está agotado de APIs pero aún sin auditar (la
    cosecha humana cubre justo esos huecos).
    """
    from app.core.config import get_settings
    from app.services.odds.historical_backfill import (
        _done_event_ids,
        _load_picks,
        _pick_pending,
        _resolved_pick_ids,
    )

    settings = get_settings()
    picks = await _load_picks(session, None)
    done = await _done_event_ids(session)
    resolved = await _resolved_pick_ids(session)
    pending = [p for p in picks if _pick_pending(p, done, settings, resolved)]

    # Agrupa por (deporte, día, evento normalizado): patas del mismo
    # partido comparten una sola página del comparador.
    groups: dict[tuple[str, date, str], list[ParsedPick]] = {}
    for pick in pending:
        sport = _canonical_sport(pick.deporte)
        if not sport or not pick.fecha_evento:
            continue
        hint = (_lookup_hint(pick, sport) or "").strip()
        if not hint:
            continue
        key = (sport, pick.fecha_evento.date(), fold_name(hint))
        groups.setdefault(key, []).append(pick)

    events: list[HarvestQueueEvent] = []
    for (sport, day, _folded), group in sorted(
        groups.items(), key=lambda kv: kv[0][1], reverse=True
    ):
        # Solo se ofrece el evento si ALGÚN pick del grupo pide un
        # mercado que el comparador publica — los eventos de solo
        # córners/tarjetas/props quedan pendientes pero no se intentan
        # por esta vía (miss honesto del proveedor, no silencio).
        if not any(_comparator_supported(p) for p in group):
            continue
        hint = (_lookup_hint(group[0], sport) or "").strip()
        home, away = _split_teams(hint)
        events.append(
            HarvestQueueEvent(
                sport=sport,
                date=day.isoformat(),
                home=home or hint,
                away=away,
                search=f"{home} {away}".strip() or hint,
                picks=[
                    HarvestPickInfo(
                        id=p.id or 0,
                        mercado=p.mercado,
                        seleccion=p.seleccion,
                        linea=p.linea,
                    )
                    for p in group
                ],
            )
        )
    return events


# ---------------------------------------------------------------------------
# Ingesta
# ---------------------------------------------------------------------------


async def _candidate_picks(
    session: AsyncSession, payload: HarvestEventPayload, sport: str
) -> list[ParsedPick]:
    """Picks pendientes cuyo (deporte, fecha, equipos) casa la página."""
    if payload.start is None:
        return []
    lo = payload.start - _EVENT_DATE_TOLERANCE
    hi = payload.start + _EVENT_DATE_TOLERANCE
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.fecha_evento != None)  # noqa: E711
        .where(ParsedPick.fecha_evento >= lo)
        .where(ParsedPick.fecha_evento <= hi)
    )
    out: list[ParsedPick] = []
    for pick in result.all():
        if _canonical_sport(pick.deporte) != sport:
            continue
        hint = (_lookup_hint(pick, sport) or "") if sport else ""
        if not hint:
            continue
        if match_score(hint, payload.home, payload.away) >= _MIN_EVENT_SCORE:
            out.append(pick)
    return out


async def ingest_harvest(
    session: AsyncSession, payload: HarvestEventPayload
) -> HarvestIngestResult:
    """Persiste una página cosechada como snapshots del evento.

    Flujo: traduce filas -> casa picks del evento -> registra el
    OddsEvent -> escribe las opciones (re-sellando filas libres a su
    pick) -> enlaza `pick.odds_event_id`. Nunca inventa: una fila sin
    cuota válida no entra y un pick sin opción comparable queda
    `unmatched` (se informa, no se resuelve).
    """
    sport = _canonical_sport(payload.sport)
    if sport is None:
        return HarvestIngestResult(
            event_ext_id="",
            accepted=False,
            detail=f"deporte desconocido: {payload.sport}",
        )
    ext_id = event_ext_id(payload.url, payload.home, payload.away, payload.start)
    start = payload.start

    translated = [
        t
        for t in (
            translate_row(r, sport, payload.home, payload.away) for r in payload.rows
        )
        if t is not None
    ]
    if not translated:
        return HarvestIngestResult(
            event_ext_id=ext_id, accepted=False, detail="sin filas traducibles"
        )
    by_key = _pick_rows(translated)

    event = OddsEvent(
        event_ext_id=ext_id,
        sport=sport,
        home_team=payload.home,
        away_team=payload.away,
        start=start,
    )
    # Filas virtuales para el comparador: cierre al inicio del
    # evento (último precio prematch publicado por la página).
    captured = start or datetime.utcnow()
    virtual = [
        OddsSnapshot(
            provider=NAME,
            event_ext_id=ext_id,
            market_name=t.market_name,
            choice_name=t.choice_name,
            choice_group=t.choice_group,
            cuota=t.closing,
            cuota_apertura=t.opening,
            captured_at=captured,
        )
        for t in by_key.values()
    ]

    candidates = await _candidate_picks(session, payload, sport)
    if not candidates:
        return HarvestIngestResult(
            event_ext_id=ext_id,
            accepted=False,
            detail="ningún pick pendiente casa con el evento",
        )

    # Claves ya escritas por esta fuente en el evento (re-cosecha).
    existing = (
        await session.exec(
            select(OddsSnapshot)
            .where(OddsSnapshot.provider == NAME)
            .where(OddsSnapshot.event_ext_id == ext_id)
        )
    ).all()
    have_keys = {(r.market_name, r.choice_name, r.choice_group) for r in existing}

    if not existing:
        try:
            session.add(event)
            await session.flush()
        except Exception:
            await session.rollback()  # la fila la creó otra pasada

    matched: list[int] = []
    unmatched: list[int] = []
    rows_written = 0

    for pick in candidates:
        found = map_pick_choices(pick, event, virtual)
        if found is None:
            unmatched.append(pick.id or 0)
            continue
        key = (found.market_name, found.choice_name, found.choice_group)
        t = by_key.get(key)
        if t is None:
            unmatched.append(pick.id or 0)
            continue
        matched.append(pick.id or 0)
        # Re-sella una fila libre del mismo key si existe; si no,
        # inserta la atribuida (picks hermanos comparten opción).
        free = next(
            (
                r
                for r in existing
                if (r.market_name, r.choice_name, r.choice_group) == key
                and r.parsed_pick_id is None
            ),
            None,
        )
        if free is not None:
            free.parsed_pick_id = pick.id
            session.add(free)
        else:
            row = OddsSnapshot(
                provider=NAME,
                event_ext_id=ext_id,
                market_name=t.market_name,
                choice_name=t.choice_name,
                choice_group=t.choice_group,
                cuota=t.closing,
                cuota_apertura=t.opening,
                captured_at=captured,
                parsed_pick_id=pick.id,
            )
            # Ancla de apertura como punto de curva real si la página
            # la publicó: dos puntos (apertura inicio-7d, cierre
            # inicio) — misma convención que Sofascore histórico.
            if t.opening and key not in have_keys:
                session.add(
                    OddsSnapshot(
                        provider=NAME,
                        event_ext_id=ext_id,
                        market_name=t.market_name,
                        choice_name=t.choice_name,
                        choice_group=t.choice_group,
                        cuota=t.opening,
                        captured_at=captured - _OPENING_ANCHOR,
                    )
                )
            session.add(row)
            rows_written += 1
        if not pick.odds_event_id:
            pick.odds_event_id = ext_id
            session.add(pick)

    # Opciones cosechadas que ningún pick necesitó: se guardan libres
    # (parsed_pick_id NULL) para que picks futuros del mismo evento
    # o el auditor las re-sellen sin otra cosecha.
    for key, t in by_key.items():
        if key in have_keys:
            continue
        session.add(
            OddsSnapshot(
                provider=NAME,
                event_ext_id=ext_id,
                market_name=t.market_name,
                choice_name=t.choice_name,
                choice_group=t.choice_group,
                cuota=t.closing,
                cuota_apertura=t.opening,
                captured_at=captured,
            )
        )
        rows_written += 1

    await session.commit()
    return HarvestIngestResult(
        event_ext_id=ext_id,
        accepted=True,
        matched_picks=matched,
        unmatched_picks=unmatched,
        rows_written=rows_written,
        detail=f"{len(by_key)} opciones cosechadas, "
        f"{len(matched)}/{len(candidates)} picks casados",
    )
