"""Comparación de la cuota del tipster con la cuota real de mercado.

El pick y la API de odds "hablan idiomas distintos": el pick guarda
`mercado="ganador"`, `seleccion="Cecchinato gana"`, `linea=22.5`; la
API devuelve mercados con nombres propios ("Full time",
"Total games won") y opciones ("1"/"2", "Over"/"Under") con la línea
en `choice_group` o en el propio nombre ("(-1.5) Betis").

Este módulo hace la traducción: dado un pick, localiza en los
snapshots de su evento la opción comparable y devuelve sus puntos
temporales (apertura, cuota más cercana a la publicación, cierre).
Mercados sin equivalente en el proveedor (props de jugador, primera
parte en tenis...) devuelven None — no se inventa el comparador,
igual que `cuota_efectiva` NULL en combinadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.results.api_tennis import _pair_similar
from app.services.results.verifier import (
    _detect_over_under_direction,
    _extract_handicap_team,
    _extract_predicted_team,
)

_MIN_TEAM_SIMILARITY = 0.6

# Sujetos de over/under -> nombre de mercado del proveedor, por deporte.
_OU_MARKETS_FUTBOL = (
    (re.compile(r"c[oó]rner|esquina", re.IGNORECASE), ("Corners 2-Way",)),
    (
        re.compile(r"tarjeta|card|booking|amarilla|roja", re.IGNORECASE),
        ("Cards in match",),
    ),
    (re.compile(r"gol", re.IGNORECASE), ("Match goals",)),
)
_OU_MARKETS_TENIS = (
    (re.compile(r"juego", re.IGNORECASE), ("Total games won",)),
    (re.compile(r"set", re.IGNORECASE), ("Total sets", "Total games won")),
)
# Sin sujeto: se intenta el mercado principal y, si ninguna línea casa,
# el resto de mercados O/U del evento desambigua por `choice_group`.
_OU_DEFAULT_FUTBOL = ("Match goals", "Corners 2-Way", "Cards in match")

_WIN_MARKETS = ("Full time",)
_FIRST_SET_MARKETS = ("First set winner",)
_HANDICAP_MARKETS = ("Asian handicap", "Game handicap", "Set handicap")

_GROUP_LINE = re.compile(r"^\(([-+]?\d+(?:\.\d+)?)\)")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _side_index(team: str, event: OddsEvent) -> Optional[int]:
    """0 si el equipo casa con home, 1 con away, None si con ninguno."""
    home = _pair_similar(team, event.home_team)
    away = _pair_similar(team, event.away_team)
    if max(home, away) < _MIN_TEAM_SIMILARITY:
        return None
    return 0 if home >= away else 1


def _line_of(row: OddsSnapshot) -> Optional[float]:
    """Línea numérica de la opción: `choice_group` ("22.5", "+0.75")
    o prefijo "(N)" del nombre ("(-1.5) Betis")."""
    if row.choice_group:
        try:
            return float(row.choice_group)
        except ValueError:
            pass
    m = _GROUP_LINE.match(row.choice_name or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _team_in_choice(row: OddsSnapshot) -> str:
    """Nombre de equipo/jugador dentro del choice ("(-1.5) Betis" -> "Betis")."""
    return _GROUP_LINE.sub("", row.choice_name or "").strip()


@dataclass
class MatchedOdds:
    """La opción de mercado comparable al pick y sus snapshots."""

    market_name: str
    choice_name: str
    choice_group: Optional[str]
    rows: list[OddsSnapshot]


def _rows_of_market(
    snapshots: list[OddsSnapshot], names: tuple[str, ...]
) -> list[OddsSnapshot]:
    return [r for r in snapshots if r.market_name in names]


def _match_side(
    snapshots: list[OddsSnapshot], event: OddsEvent, team: str
) -> Optional[MatchedOdds]:
    """Opción "1"/"2" (o con nombre) del lado del equipo predicho."""
    side = _side_index(team, event)
    if side is None:
        return None
    wanted = ("1", "2")[side]
    for row in snapshots:
        if row.choice_name == wanted:
            return _group_rows(snapshots, row)
    # Mercados con nombre propio en vez de "1"/"2" ("(-0.5) Betis").
    for row in snapshots:
        name = _team_in_choice(row)
        if name and _pair_similar(team, name) >= _MIN_TEAM_SIMILARITY:
            return _group_rows(snapshots, row)
    return None


def _group_rows(snapshots: list[OddsSnapshot], ref: OddsSnapshot) -> MatchedOdds:
    """Todas las capturas de la MISMA opción (misma tripleta
    market/choice/group) que `ref`, ordenadas por captura."""
    rows = [
        r
        for r in snapshots
        if r.market_name == ref.market_name
        and r.choice_name == ref.choice_name
        and r.choice_group == ref.choice_group
    ]
    rows.sort(key=lambda r: r.captured_at)
    return MatchedOdds(
        market_name=ref.market_name,
        choice_name=ref.choice_name,
        choice_group=ref.choice_group,
        rows=rows,
    )


def _match_over_under(
    snapshots: list[OddsSnapshot],
    market_names: tuple[str, ...],
    linea: float,
    direction: str,
) -> Optional[MatchedOdds]:
    """Opción Over/Under de la línea del pick en los mercados dados."""
    wanted = "Over" if direction == "over" else "Under"
    for row in snapshots:
        if row.market_name not in market_names or row.choice_name != wanted:
            continue
        line = _line_of(row)
        if line is not None and abs(line - linea) < 0.001:
            return _group_rows(snapshots, row)
    return None


def _match_handicap(
    snapshots: list[OddsSnapshot],
    event: OddsEvent,
    team: str,
    linea: Optional[float],
) -> Optional[MatchedOdds]:
    """Opción de hándicap del equipo predicho con su línea."""
    for row in _rows_of_market(snapshots, _HANDICAP_MARKETS):
        name = _team_in_choice(row)
        if not name or _pair_similar(team, name) < _MIN_TEAM_SIMILARITY:
            continue
        if linea is not None:
            line = _line_of(row)
            if line is not None and abs(abs(line) - abs(linea)) > 0.001:
                continue
        return _group_rows(snapshots, row)
    return None


def _map_over_under(
    sport: str, text: str, snapshots: list[OddsSnapshot], pick: ParsedPick
) -> Optional[MatchedOdds]:
    """Over/under: el sujeto elige mercado; sin sujeto, la línea
    disponible en el evento desambigua."""
    # Dirección: primero la selección (el mercado suele ser el genérico
    # "over/under goles", que contiene "under" y anularía el match).
    direction = _detect_over_under_direction(
        pick.seleccion or ""
    ) or _detect_over_under_direction(pick.mercado or "")
    if direction is None or pick.linea is None:
        return None
    subjects = _OU_MARKETS_TENIS if sport == "tenis" else _OU_MARKETS_FUTBOL
    candidates: list[tuple[str, ...]] = [
        names for pattern, names in subjects if pattern.search(text)
    ]
    if not candidates:
        candidates = [
            (name,) for name in (_OU_DEFAULT_FUTBOL if sport == "futbol" else ())
        ]
        if sport == "tenis":
            candidates = [("Total games won",)]
    for names in candidates:
        matched = _match_over_under(snapshots, names, pick.linea, direction)
        if matched is not None:
            return matched
    return None


def map_pick_choices(
    pick: ParsedPick, event: OddsEvent, snapshots: list[OddsSnapshot]
) -> Optional[MatchedOdds]:
    """Localiza la opción de mercado comparable al pick.

    None si el mercado del pick no tiene equivalente en el proveedor
    (props de jugador, mercados no cubiertos) — el comparador queda
    NULL en vez de inventarse.
    """
    text = f"{pick.mercado or ''} {pick.seleccion or ''}".strip().lower()
    team = _extract_predicted_team(pick.seleccion or "", pick.mercado)

    if "over" in text or "under" in text or "más de" in text or "menos" in text:
        matched = _map_over_under(event.sport, text, snapshots, pick)
        if matched is not None:
            return matched

    if re.search(r"h[aá]ndicap", text):
        ah_team = _extract_handicap_team(pick.seleccion or "") or team
        if ah_team:
            matched = _match_handicap(snapshots, event, ah_team, pick.linea)
            if matched is not None:
                return matched

    if "ambos marcan" in text or "btts" in text:
        for row in _rows_of_market(snapshots, ("Both teams to score",)):
            if row.choice_name == "Yes":
                return _group_rows(snapshots, row)
    if "no anotan ambos" in text or "no marcan ambos" in text:
        for row in _rows_of_market(snapshots, ("Both teams to score",)):
            if row.choice_name == "No":
                return _group_rows(snapshots, row)

    if "doble oportunidad" in text or "double chance" in text:
        dc = _rows_of_market(snapshots, ("Double chance",))
        low = (pick.seleccion or "").strip().lower()
        for code in ("1x", "x2", "12"):
            if re.fullmatch(code, low):
                for row in dc:
                    if row.choice_name.lower() == code:
                        return _group_rows(snapshots, row)
        # "Equipo o/y empate": el equipo decide el lado (1X o X2).
        if re.search(r"\bempate\b|\bdraw\b", low):
            dc_team = re.sub(r"\b(empate|draw|o|y|e|or|and)\b", " ", low).strip()
            if dc_team:
                side = _side_index(dc_team, event)
                code = {0: "1X", 1: "X2"}.get(side)
                for row in dc:
                    if code and row.choice_name == code:
                        return _group_rows(snapshots, row)
        elif team:
            side = _side_index(team, event)
            code = {0: "1X", 1: "X2"}.get(side)
            for row in dc:
                if code and row.choice_name == code:
                    return _group_rows(snapshots, row)
        # "EquipoA o EquipoB" (gana cualquiera) -> 12.
        if re.search(r"\s+o\s+|\s*/\s*|\s+or\s+", low):
            for row in dc:
                if row.choice_name == "12":
                    return _group_rows(snapshots, row)

    if re.search(
        r"empate\s+no\s+v[aá]lido|resultado\s+sin\s+empate|draw\s+no\s+bet", text
    ):
        if team:
            matched = _match_side(
                _rows_of_market(snapshots, ("Draw no bet",)), event, team
            )
            if matched is not None:
                return matched

    if re.search(r"primer\s+set|1er\s+set|set\s+1", text) and team:
        matched = _match_side(
            _rows_of_market(snapshots, _FIRST_SET_MARKETS), event, team
        )
        if matched is not None:
            return matched

    # Por defecto: mercado ganador ("Full time" en ambos deportes).
    if team:
        if "empate" in (pick.seleccion or "").lower():
            for row in _rows_of_market(snapshots, _WIN_MARKETS):
                if row.choice_name == "X":
                    return _group_rows(snapshots, row)
        matched = _match_side(_rows_of_market(snapshots, _WIN_MARKETS), event, team)
        if matched is not None:
            return matched

    return None


@dataclass
class PickOddsComparison:
    """Comparación final de un pick contra su mercado."""

    mapeado: bool
    mercado_api: Optional[str] = None
    opcion_api: Optional[str] = None
    linea_api: Optional[str] = None
    cuota_tipster: Optional[float] = None
    cuota_apertura: Optional[float] = None
    cuota_publicacion: Optional[float] = None
    cuota_cierre: Optional[float] = None
    capturas: int = 0
    # Derivadas (None si falta algún punto de la comparación):
    # True si la cuota anunciada existía en mercado al publicar
    # (tipster <= mercado); False = cuota inflada/irreproducible.
    cuota_disponible: Optional[bool] = None
    # CLV % = (cuota_tipster / cuota_cierre - 1) * 100.
    # Positivo = el tipster batió el cierre (valor real); negativo = peor.
    clv_pct: Optional[float] = None


async def compare_pick(session: AsyncSession, pick: ParsedPick) -> PickOddsComparison:
    """Compara la cuota del pick con las snapshots de su evento.

    - `cuota_publicacion`: la captura más cercana a `pick.created_at`
      (lo que el mercado ofrecía cuando el tipster publicó — el pick se
      importa casi en tiempo real).
    - `cuota_cierre`: la última captura (la más cercana al cierre).
    - `cuota_apertura`: el precio de apertura que regala la API.
    """
    result = PickOddsComparison(mapeado=False, cuota_tipster=pick.cuota)
    if not pick.odds_event_id:
        return result
    event = await session.get(OddsEvent, pick.odds_event_id)
    if event is None:
        return result
    rows = list(
        (
            await session.exec(
                select(OddsSnapshot)
                .where(OddsSnapshot.event_ext_id == pick.odds_event_id)
                .order_by(OddsSnapshot.captured_at)
            )
        ).all()
    )
    if not rows:
        return result

    matched = map_pick_choices(pick, event, rows)
    if matched is None:
        return result

    result.mapeado = True
    result.mercado_api = matched.market_name
    result.opcion_api = matched.choice_name
    result.linea_api = matched.choice_group
    result.capturas = len(matched.rows)

    for row in matched.rows:
        if row.cuota_apertura is not None:
            result.cuota_apertura = row.cuota_apertura
            break
    last = matched.rows[-1]
    result.cuota_cierre = last.cuota
    if pick.created_at:
        nearest = min(
            matched.rows,
            key=lambda r: abs((r.captured_at - pick.created_at).total_seconds()),
        )
        result.cuota_publicacion = nearest.cuota
    if pick.cuota is not None and result.cuota_publicacion is not None:
        result.cuota_disponible = pick.cuota <= result.cuota_publicacion
    if (
        pick.cuota is not None
        and result.cuota_cierre is not None
        and result.cuota_cierre > 0
    ):
        result.clv_pct = round((pick.cuota / result.cuota_cierre - 1.0) * 100.0, 2)
    return result
