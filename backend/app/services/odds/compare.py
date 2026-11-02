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
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import fold_name
from app.services.results.espn import _translate_countries
from app.services.results.verifier import (
    _detect_over_under_direction,
    _extract_handicap_team,
    _extract_predicted_team,
)

_MIN_TEAM_SIMILARITY = 0.6

# Sujetos de over/under -> nombre de mercado del proveedor, por deporte.
# "Corners 3-Way" va PRIMERO: es el mercado real del tipster (totales
# a 3 vías de 1xBet en líneas enteras: Más de / Exactamente / Menos
# de, precios ~1.50) — en 3-way "Over 8" PIERDE si caen exactamente
# 8, no hay devolución asiática. La fila "Exactly" queda guardada
# como evidencia del resultado exacto aunque nunca case con el pick.
_OU_MARKETS_FUTBOL = (
    (
        re.compile(r"c[oó]rner|esquina", re.IGNORECASE),
        ("Corners 3-Way", "Corners 2-Way"),
    ),
    (
        re.compile(r"tarjeta|card|booking|amarilla|roja", re.IGNORECASE),
        ("Cards 3-Way", "Cards in match"),
    ),
    (re.compile(r"gol", re.IGNORECASE), ("Match goals",)),
)
_OU_MARKETS_TENIS = (
    (re.compile(r"\baces?\b", re.IGNORECASE), ("Total aces",)),
    (
        re.compile(r"doble\s*falta|double\s*fault", re.IGNORECASE),
        ("Total double faults",),
    ),
    (re.compile(r"tie\s*break|tiebreak", re.IGNORECASE), ("Total tie breaks",)),
    (re.compile(r"\bbreaks?\b|quiebre", re.IGNORECASE), ("Total breaks",)),
    (
        re.compile(r"juegos?\s+de\s+servicio|service\s+game", re.IGNORECASE),
        ("Total service games",),
    ),
    (re.compile(r"juego", re.IGNORECASE), ("Total games won",)),
    (re.compile(r"set", re.IGNORECASE), ("Total sets", "Total games won")),
)
# Sin sujeto: se intenta el mercado principal y, si ninguna línea casa,
# el resto de mercados O/U del evento desambigua por `choice_group`.
_OU_DEFAULT_FUTBOL = ("Match goals", "Corners 2-Way", "Cards in match")
# Totales por equipo (los publica el feed bet365 como "Team N ..."):
# "Madrid +7 córners" NO es el total del partido — caer en "Corners
# 2-Way" aunque la línea coincida compararía contra otro mercado.
_OU_TEAM_MARKET = {
    "Corners 3-Way": "Team {} corners",
    "Corners 2-Way": "Team {} corners",
    "Cards 3-Way": "Team {} cards",
    "Cards in match": "Team {} cards",
    "Match goals": "Team {} goals",
}
# Mercados de MITAD: "+1.5 goles 1ª parte" NO es el total del
# partido. Se traduce el sujeto base al nombre de mitad.
_OU_HALF_MARKET = {
    "Corners 3-Way": "{} half corners",
    "Corners 2-Way": "{} half corners",
    "Cards 3-Way": "{} half bookings",
    "Cards in match": "{} half bookings",
    "Match goals": "{} half goals",
}
_OU_DEFAULT_HALF = ("{} half goals", "{} half corners", "{} half bookings")

_HALF_1_RE = re.compile(
    r"primer[ae]s?\s+parte|primera\s+mitad|descanso|media\s+parte|"
    r"first\s+half|1[ªa]?\s*parte|primer\s+tiempo|\b1t\b",
    re.IGNORECASE,
)
_HALF_2_RE = re.compile(
    r"segund[ao]s?\s+parte|segunda\s+mitad|segundo\s+tiempo|"
    r"second\s+half|2[ªa]?\s*parte|\b2t\b",
    re.IGNORECASE,
)


def _detect_half(text: str) -> Optional[str]:
    """ "1st"/"2nd" si el texto habla de una parte concreta."""
    if _HALF_1_RE.search(text):
        return "1st"
    if _HALF_2_RE.search(text):
        return "2nd"
    return None


def _is_halftime_fulltime(text: str) -> bool:
    """Pick descanso/final o "gana ambas partes" — un solo contrato
    (HT/FT o To Win Both Halves), no comparables al 1X2 de una mitad.
    """
    if _HALF_1_RE.search(text) and _HALF_2_RE.search(text):
        return True
    if re.search(
        r"descanso\s*[/y\\-]\s*final|al\s+descanso\s+y\s+al\s+final|"
        r"media\s*parte\s*[/y\\-]\s*final|primer[ao]?\s+parte\s+y\s+"
        r"segunda|primer\w*\s+y\s+(?:la\s+)?segund\w*\s+parte|"
        r"half\s*time\s*[/y\\-]\s*full\s*time|win\s+both\s+halves",
        text,
        re.IGNORECASE,
    ):
        return True
    # "gana ambas partes" = To Win Both Halves (sin traducir). Pero
    # "MARCA en ambas partes" es otro mercado (Team To Score in Both
    # Halves) — se resuelve en la rama to-score, no es HT/FT.
    return bool(
        re.search(r"gan\w*|victoria|win", text)
        and re.search(r"ambas\s+partes|ambos\s+tiempos|both\s+halves", text)
    )


def _htft_choice(
    text: str, pick: ParsedPick, event: OddsEvent, team: str
) -> Optional[str]:
    """Código "HT/FT" del pick ("1/1", "X/2"...) o None si no se
    puede deducir.

    Solo son HT/FT los picks que dan UN resultado al descanso Y UN
    resultado del partido: "gana al descanso y al final" = "1/1",
    "empata al descanso y gana al final" = "X/1", "descanso/final:
    Betis" = "1/1". NO lo es "gana la primera Y la segunda parte"
    (eso es To Win Both Halves, otro contrato) ni "gana ambas
    partes".
    """
    m = re.search(r"\b([12x])\s*/\s*([12x])\b", (pick.seleccion or "").lower())
    if m:
        return f"{m.group(1).upper()}/{m.group(2).upper()}"
    side = _event_team_in_text(text, event)
    if side == _TEAM_AMBIGUOUS:
        return None
    if side is None and team:
        side = _side_index(team, event)
    if side is None:
        return None
    win, lose = str(side + 1), ("2" if side == 0 else "1")
    ht_c = ft_c = None
    for clause in re.split(r"[,;]|\by\b", text):
        if re.search(
            r"descanso|primer[ao]?\s*(parte|tiempo)|1ª|media\s+parte|" r"first\s+half",
            clause,
        ):
            ht_c = clause
        elif re.search(r"final|full\s+time|\bpartido\b|encuentro", clause):
            ft_c = clause
    if ht_c is None and ft_c is None:
        return None
    if ft_c is None and ht_c is not None:
        # Una sola cláusula con las dos referencias ("descanso/final
        # Betis", "gana al descanso y al final" sin cortar): el mismo
        # lado lidera al descanso y gana el partido.
        if re.search(r"final|\bpartido\b|encuentro", ht_c):
            ft_c = ht_c
        else:
            return None
    if ht_c is None:
        return None

    def _res(clause: str) -> str:
        if re.search(r"empat", clause):
            return "X"
        if re.search(r"pierd|perdedor|derrot", clause):
            return lose
        return win

    return f"{_res(ht_c)}/{_res(ft_c)}"


_WIN_MARKETS = ("Full time",)
_SET_WINNER_MARKETS = {
    "1": "First set winner",
    "2": "Second set winner",
    "3": "Third set winner",
}
_HANDICAP_MARKETS = ("Asian handicap", "Game handicap", "Set handicap")
# Tenis por set: "Alcaraz -1.5 juegos 1er set" NO es el hándicap del
# partido — es un mercado propio ("1st set game handicap").
_SET_GAME_HANDICAP = {
    "1": "1st set game handicap",
    "2": "2nd set game handicap",
    "3": "3rd set game handicap",
}

_GROUP_LINE = re.compile(r"^\(([-+]?\d+(?:\.\d+)?)\)")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, fold_name(a), fold_name(b)).ratio()


def _side_index(team: str, event: OddsEvent) -> Optional[int]:
    """0 si el equipo casa con home, 1 con away, None si con ninguno."""
    home = _pair_similar(team, event.home_team)
    away = _pair_similar(team, event.away_team)
    if max(home, away) < _MIN_TEAM_SIMILARITY:
        return None
    return 0 if home >= away else 1


# Centinela de _event_team_in_text: el pick contiene un token que
# casa con los DOS equipos del evento ("Madrid" en Real Madrid vs
# Atlético Madrid, "United" en Newcastle Utd vs West Ham Utd) — el
# equipo apostado no se puede saber y caer al total del partido sería
# una comparación falsa: miss honesto.
_TEAM_AMBIGUOUS = -1


def _team_hit_tokens(team: str, folded_text: str) -> frozenset:
    """Tokens del nombre del equipo presentes en el texto.

    El nombre completo cuenta como un token propio (su cadena), así
    "Real Madrid" entero y "Getafe" entero se distinguen luego como
    menciones a equipos distintos. Los tokens sueltos exigen >=4
    letras — filtra "FC", "de", "la", "San".
    """
    ft = fold_name(team or "")
    if not ft:
        return frozenset()
    if re.search(rf"\b{re.escape(ft)}\b", folded_text):
        return frozenset({ft})
    return frozenset(
        tok
        for tok in re.findall(r"\w+", ft)
        if len(tok) >= 4 and re.search(rf"\b{re.escape(tok)}\b", folded_text)
    )


def _event_team_in_text(text: str, event: OddsEvent) -> Optional[int]:
    """0/1 si el texto nombra a home/away con tokens propios.

    - None si ninguno casa.
    - None si cada equipo casa por un token DISTINTO ("Madrid-Getafe
      +9 córners" suele ser total del partido: desempate al mercado
      general).
    - _TEAM_AMBIGUOUS si el MISMO token casa con ambos nombres: el
      equipo apostado es indistinguible — el llamador debe dar miss
      honesto, nunca comparar contra el total del partido.

    Casa el nombre completo o cualquier token de >=4 letras del
    nombre ("Betis" por "Real Betis", "Rovers" por "Shamrock
    Rovers") — el canal abrevia constantemente.
    """
    folded = fold_name(text)
    # Selecciones nacionales: el canal escribe "Suiza"/"Inglaterra"
    # y el proveedor "Switzerland"/"England" — traducir el texto
    # permite que el token case (ver _COUNTRY_ALIASES en espn.py).
    folded_en = _translate_countries(folded)
    home_t = _team_hit_tokens(event.home_team, folded) | _team_hit_tokens(
        event.home_team, folded_en
    )
    away_t = _team_hit_tokens(event.away_team, folded) | _team_hit_tokens(
        event.away_team, folded_en
    )
    home_only, away_only = home_t - away_t, away_t - home_t
    if home_only and not away_only:
        return 0
    if away_only and not home_only:
        return 1
    if home_only and away_only:
        return None
    if home_t & away_t:
        return _TEAM_AMBIGUOUS
    return None


def _event_player_in_text(text: str, event: OddsEvent) -> Optional[int]:
    """Como `_event_team_in_text` pero para tenis: el canal suele
    escribir solo el apellido ("Alcaraz más de 12.5 juegos" refiere a
    "Carlos Alcaraz"). Casa el nombre completo o el último token del
    jugador (apellido) si tiene >=4 letras. Devuelve 0/1, o None si
    ninguno o AMBOS aparecen (dos nombres = eco del evento, no prop).
    """
    folded = fold_name(text)

    def _mentions(player: str) -> bool:
        fp = fold_name(player or "")
        if not fp:
            return False
        if re.search(rf"\b{re.escape(fp)}\b", folded):
            return True
        # OddsPapi nombra "Djokovic, Novak" (apellido primero) y el
        # canal suele escribir solo el apellido: casa cualquier token
        # de >=4 letras ("learner tien" -> "tien"/"learner"). Se sacan
        # los tokens con \w+ para ignorar la puntuación ("djokovic,").
        return any(
            len(tok) >= 4 and re.search(rf"\b{re.escape(tok)}\b", folded)
            for tok in re.findall(r"\w+", fp)
        )

    home, away = _mentions(event.home_team), _mentions(event.away_team)
    if home != away:
        return 0 if home else 1
    return None


_SET_NUMBER_RE = re.compile(
    r"([123])[ºaer°]{0,3}\s*set|set\s*([123])\b|"
    r"primer[ao]?\s+set|segundo\s+set|tercer\s+set|"
    r"first\s+set|second\s+set|third\s+set",
    re.IGNORECASE,
)


def _detect_set_number(text: str) -> Optional[str]:
    """ "1"/"2"/"3" si el texto habla de un set concreto."""
    m = _SET_NUMBER_RE.search(text)
    if not m:
        return None
    token = m.group(1) or m.group(2)
    if token:
        return token
    word = m.group(0).lower()
    if "primer" in word or "first" in word:
        return "1"
    return "2" if "segundo" in word or "second" in word else "3"


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
    """Opción Over/Under de la línea del pick en los mercados dados.

    Fallback 3-way -> 2-way: el tipster juega líneas enteras del
    mercado "Más/Exactamente/Menos" de bet365, que los feeds solo
    publican como O/U 2-way con línea .5. La equivalencia es exacta:
    "Menos de 12" (≤11) = Under 11.5; "Más de 8" (≥9) = Over 8.5.
    Solo aplica con línea entera y si la exacta no existe — la fila
    guardada sigue siendo la del mercado 2-way real (auditable)."""
    wanted = "Over" if direction == "over" else "Under"
    for row in snapshots:
        if row.market_name not in market_names or row.choice_name != wanted:
            continue
        line = _line_of(row)
        if line is not None and abs(line - linea) < 0.001:
            return _group_rows(snapshots, row)
    if linea == int(linea):
        shifted = linea + 0.5 if direction == "over" else linea - 0.5
        if shifted > 0:
            for row in snapshots:
                if row.market_name not in market_names or row.choice_name != wanted:
                    continue
                line = _line_of(row)
                if line is not None and abs(line - shifted) < 0.001:
                    return _group_rows(snapshots, row)
    return None


def _match_handicap(
    snapshots: list[OddsSnapshot],
    event: OddsEvent,
    team: str,
    linea: Optional[float],
    markets: tuple[str, ...] = _HANDICAP_MARKETS,
) -> Optional[MatchedOdds]:
    """Opción de hándicap del equipo predicho con su línea."""
    for row in _rows_of_market(snapshots, markets):
        name = _team_in_choice(row)
        if not name or _pair_similar(team, name) < _MIN_TEAM_SIMILARITY:
            continue
        if linea is not None:
            line = _line_of(row)
            # Comparación CON signo: "(línea) Jugador" ya codifica el
            # lado — "Hurkacz +2.5" no es "(-2.5) Hurkacz".
            if line is not None and abs(line - linea) > 0.001:
                continue
        return _group_rows(snapshots, row)
    return None


def _map_over_under(
    sport: str,
    text: str,
    snapshots: list[OddsSnapshot],
    pick: ParsedPick,
    event: OddsEvent,
    half: Optional[str] = None,
) -> Optional[MatchedOdds]:
    """Over/under: el sujeto elige mercado; sin sujeto, la línea
    disponible en el evento desambigua."""
    # Dirección: primero la selección (el mercado suele ser el genérico
    # "over/under goles", que contiene "under" y anularía el match).
    direction = _detect_over_under_direction(
        pick.seleccion or ""
    ) or _detect_over_under_direction(pick.mercado or "")
    if direction is None and re.search(
        r"\+\s*\d|\d+(?:[.,]\d+)?\s*\+", pick.seleccion or ""
    ):
        # "+7,5 juegos" / "25+ aces": el signo marca el over sin la
        # palabra ("más de"). El "-" nunca se usa para under en los
        # canales, así que solo el "+" desambigua.
        direction = "over"
    if direction is None or pick.linea is None:
        return None
    if sport == "futbol":
        # Prop de jugador con línea ("Morata +1.5 goles", "Raya +3.5
        # paradas"): el nombre no es un equipo del evento — comparar
        # contra el total del partido sería otra cuota. Lo resuelve
        # la rama de props al final.
        prop_name = _extract_prop_player(pick.seleccion or "")
        if prop_name and _side_index(prop_name, event) is None:
            return None
    subjects = _OU_MARKETS_TENIS if sport == "tenis" else _OU_MARKETS_FUTBOL
    candidates: list[tuple[str, ...]] = [
        names for pattern, names in subjects if pattern.search(text)
    ]
    # "Sevilla +7 córners" es un total del EQUIPO: con un solo equipo
    # del evento nombrado en el texto solo casa el mercado de equipo —
    # el total del partido es otro mercado aunque la línea coincida.
    side = _event_team_in_text(text, event) if sport == "futbol" else None
    # Veto de alcance: "+9 córners en el partido del Betis" es el
    # total del PARTIDO aunque nombre al equipo.
    if re.search(
        r"del\s+partido|del\s+encuentro|del\s+match|" r"(?:en|durante)\s+el\s+partido",
        text,
    ):
        side = None
    if side == _TEAM_AMBIGUOUS:
        # El texto solo trae un token compartido por ambos equipos
        # ("Madrid" en Real Madrid vs Atlético Madrid): no se puede
        # saber el lado y el total del partido es OTRO mercado.
        return None
    if side is not None and candidates:
        team_market = _OU_TEAM_MARKET.get(candidates[0][0])
        if team_market:
            name = team_market.format(side + 1)
            if half:
                name = f"{half} half {name}"
            return _match_over_under(
                snapshots,
                (name,),
                pick.linea,
                direction,
            )
    if sport == "futbol" and half:
        # Pick de mitad sin equipo: el sujeto base se remapea a su
        # mercado de parte ("Match goals" -> "1st half goals").
        if candidates:
            candidates = [
                tuple(
                    mapped.format(half)
                    for name in names
                    if (mapped := _OU_HALF_MARKET.get(name))
                )
                for names in candidates
            ]
            candidates = [c for c in candidates if c]
        else:
            candidates = [(name.format(half),) for name in _OU_DEFAULT_HALF]
    if sport == "tenis":
        # La lógica set/jugador solo aplica a picks de JUEGOS: un
        # "+4.5 aces en el 1er set" no es juegos del set (miss honesto
        # antes que casar contra el mercado equivocado).
        games_subject = not candidates or bool(re.search(r"juego", text))
        nth = _detect_set_number(text) if games_subject else None
        player_side = _event_player_in_text(text, event) if games_subject else None
        if nth:
            if player_side is not None:
                # "Sinner +4.5 juegos en el 1er set" = juegos del
                # JUGADOR en ESE set: OddsPapi no publica ese mercado
                # — miss honesto (no caer al total del jugador del
                # partido ni al total del set).
                return None
            # Juegos por set: "Más de 7.5 juegos en el 1º/2º/3º set".
            suffix = {"1": "st", "2": "nd", "3": "rd"}[nth]
            candidates = [(f"{nth}{suffix} set games",), *candidates]
        elif player_side is not None:
            # "Alcaraz más de 12.5 juegos" = total del JUGADOR, no del
            # partido. Sin mercado del proveedor -> miss honesto.
            return _match_over_under(
                snapshots,
                (f"Player {player_side + 1} games",),
                pick.linea,
                direction,
            )
        elif games_subject and pick.linea is not None and pick.linea <= 13:
            # Una línea <=13 sin set ni jugador solo puede ser de set
            # (un total de partido arranca en ~19).
            candidates = [("1st set games",), *candidates]
    if not candidates and not half:
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


# Vocabulario de props traducido en theoddsapi.py (feed The Odds API).
_PROP_ANYTIME = ("Goalscorer anytime",)
_PROP_SCORE_OR_ASSIST = ("Goalscorer or assist",)
_PROP_FIRST = ("First goalscorer",)
_PROP_LAST = ("Last goalscorer",)
_PROP_ASSISTS = ("Player assists",)
_PROP_SHOTS_OT = ("Player shots on target",)
_PROP_SHOTS = ("Player shots", "Player shots on target")
_PROP_FOULS = ("Player fouls",)
_PROP_CARDS = ("Player to be booked",)
# "N+ goles" de jugador: el O/U oficial ("Player goals") y las filas
# de conteo "N+"/"0" del mercado `Player Goals`, que la traducción
# emite bajo "Goalscorer anytime" con grupo "Over N-0.5" — mismo
# contrato (la línea del pick filtra; "marca" a secas no entra aquí).
_PROP_GOALS = ("Player goals", "Goalscorer anytime")
_PROP_SAVES = ("Player saves",)
_PROP_TACKLES = ("Player tackles",)
_PROP_OFFSIDES = ("Player offsides",)

_PROP_PICK = re.compile(
    r"jugador|amonest|ve\s+tarjeta|recibe\s+tarjeta|tarjeta|"
    r"marca\s+o\s+asiste|asiste\b|asistencia|anota\b|"
    r"marca\s+(gol|su\s+gol|en\s+cualquier)|disparo|remate|shot|scorer|"
    r"doblete|hat.?trick|parada|tackle|entrada|fuera\s+de\s+juego|"
    r"offside|goleador|\d\s*\+\s*gole?s?|\+\s*\d+(?:[.,]\d+)?\s*gole?s?|"
    r"marca\w*\s+\d+\s+goles|\b(?:dos|tres|cuatro)\s+goles\b",
    re.IGNORECASE,
)
# El nombre del jugador: "Isak: Marca gol" / "Isak marca gol" /
# "Recibe amonestación Mac Allister" / "Amonestado Enciso".
_PROP_PLAYER_PATTERNS = (
    re.compile(r"(?:amonestaci[oó]n|tarjeta)\s+(?:a\s+|para\s+)?(.+)$", re.I),
    re.compile(r"^(.+?)\s*[:\-–]\s*(?:marca|anota|jugador|recib\w*|ve\b|asist)", re.I),
    # Nombre al INICIO seguido de cifra/línea: "Morata +1.5 goles",
    # "Raya +3.5 paradas". (Un equipo en esa posición lo descarta el
    # llamador con _side_index.)
    re.compile(
        r"^([A-ZÁÉÍÓÚÑÜ][\w'’.-]+(?:\s+[A-ZÁÉÍÓÚÑÜ][\w'’.-]+)*)\s*[+:\-–]\s*\d",
        re.UNICODE,
    ),
    re.compile(
        r"^(.+?)\s+(?:marca|anota|mete|asist\w*|ve\s+tarjeta|ver[aá]\b|"
        r"recib\w*|amonestad\w*|ser[aá]\s+amonest\w*|comete|dispara|"
        r"remata|hace|marcar[aá]|anotar[aá]|cae|estará)\b",
        re.I,
    ),
    # Nombre al FINAL del texto: "Anota gol en cualquier momento
    # Alexander Isak" / "Marca o asiste Cody Gakpo". Último recurso:
    # la ristra final de palabras con inicial mayúscula.
    re.compile(
        r"([A-ZÁÉÍÓÚÑÜ][\w'’.-]+(?:\s+[A-ZÁÉÍÓÚÑÜ][\w'’.-]+)*)\s*$",
        re.UNICODE,
    ),
)
_PROP_MIN_SIM = 0.7


def _prop_line_value(group: Optional[str]) -> float:
    """Línea numérica de "Over 2.5"; sin línea -> infinito (peor)."""
    m = re.search(r"(\d+(?:\.\d+)?)", group or "")
    return float(m.group(1)) if m else float("inf")


def _prop_markets_for(text: str) -> Optional[tuple[str, ...]]:
    """Mercados del proveedor que corresponden al prop pedido.

    None = el pick no es de jugador (no entra en la rama de props).
    Si la fuente no publica el mercado devuelto (p.ej. "Player to be
    booked" solo existe en OddsPapi) -> miss honesto al buscar filas.
    """
    if not _PROP_PICK.search(text):
        return None
    if re.search(r"marca\s+o\s+asiste|asiste\s+o\s+marca|score\s+or\s+assist", text):
        return _PROP_SCORE_OR_ASSIST
    if re.search(r"primer.{0,10}(gol|marcad|scor)|first.{0,8}scor", text):
        return _PROP_FIRST
    if re.search(r"[uú]ltim.{0,10}(gol|marcad|scor)|last.{0,8}scor", text):
        return _PROP_LAST
    if re.search(r"amonest|tarjeta|card", text):
        return _PROP_CARDS
    if re.search(r"asist", text):
        return _PROP_ASSISTS
    if re.search(r"on\s*target|a\s+puerta|al\s+arco", text):
        return _PROP_SHOTS_OT
    if re.search(r"disparo|remate|shot|tiro", text):
        return _PROP_SHOTS
    if re.search(r"parada|portero|save", text):
        return _PROP_SAVES
    if re.search(r"tackle|entrada", text):
        return _PROP_TACKLES
    if re.search(r"fuera\s+de\s+juego|offside", text):
        return _PROP_OFFSIDES
    if re.search(r"falta|foul", text):
        return _PROP_FOULS
    # Prop de goles con cantidad: "Morata +1.5 goles", "marca 2
    # goles", "doblete" -> "Player goals" (Over). "Marca gol" a
    # secas sigue siendo el scorer anytime.
    if re.search(
        r"\d\s*\+\s*gole?s?|\+\s*\d+(?:[.,]\d+)?\s*gole?s?|"
        r"m[aá]s\s+de\s+\d+(?:[.,]\d+)?\s*gole?s?|"
        r"marca\w*\s+\d+\s+goles|\b(?:dos|tres|cuatro)\s+goles\b|"
        r"doblete|hat.?trick",
        text,
    ):
        return _PROP_GOALS
    return _PROP_ANYTIME


# Un "nombre" que empieza por vocabulario del prop no es un jugador:
# "Marca o asiste Cody Gakpo" capturaba "Marca o" con el patrón
# nombre+verbo — se descarta y el patrón siguiente queda con el
# nombre real al final de la frase.
_PROP_NAME_STOP = re.compile(
    r"^(?:marca|anota|mete|asist\w*|ve|ver[aá]|recib\w*|amonestad\w*|"
    r"comete|dispara|remata|hace|cae|estará|o|y|jugador)\b",
    re.IGNORECASE,
)


def _extract_prop_player(seleccion: str) -> Optional[str]:
    """Nombre del jugador dentro de la selección del pick."""
    s = seleccion.strip()
    for pattern in _PROP_PLAYER_PATTERNS:
        m = pattern.search(s)
        if m:
            name = m.group(1).strip(" .:;-–")
            if len(name) >= 3 and not _PROP_NAME_STOP.match(name):
                return name
    return None


def _match_prop_player(
    rows: list[OddsSnapshot],
    player: str,
    text: str,
    linea: Optional[float] = None,
) -> Optional[MatchedOdds]:
    """La opción del jugador en el mercado prop (nombre fuzzy).

    En props de línea (asistencias, disparos — `choice_group`
    "Over 0.5") solo casa la dirección pedida: un "asiste" es Over,
    nunca Under. Con `linea` explícita la línea del grupo debe casar
    (un "Morata +1.5 goles" no es el Over 0.5; la forma "N+" del
    tipster equivale a Over N-0.5). Sin dirección explícita se toma
    "Over" (el prop es afirmativo por naturaleza: "marca", "asiste").
    """
    ou_rows = [r for r in rows if r.choice_group]
    if ou_rows:
        direction = "Under" if re.search(r"menos\s+de|under|no\s+", text) else "Over"
        # Las filas sin grupo son el outcome "Yes" del mercado (el
        # "No" no se emite): equivalen a Over 0.5 — solo cuentan con
        # dirección afirmativa, un "no marca" nunca las toca.
        rows = [
            r
            for r in rows
            if (r.choice_group or "").startswith(direction)
            or (direction == "Over" and not r.choice_group)
        ]
        if linea is not None:
            rows = [
                r
                for r in rows
                if abs(_prop_line_value(r.choice_group) - linea) < 0.001
                or abs(_prop_line_value(r.choice_group) - (linea - 0.5)) < 0.001
            ]
    best: Optional[OddsSnapshot] = None
    best_score = 0.0
    best_names: set[str] = set()
    for row in rows:
        candidate = _team_in_choice(row)
        if not candidate:
            continue
        score = _pair_similar(player, candidate)
        # A igualdad de nombre: sin línea pedida gana primero la fila
        # "Yes" del mercado (group None — ES el contrato "marca"/
        # "asiste" literal) y si solo hay líneas, la MENOR ("al menos
        # una vez" = Over 0.5). Si el empate es entre JUGADORES
        # distintos ("Silva" vs Bernardo y André Silva) el pick es
        # ambiguo -> miss honesto.
        rank = (
            0 if row.choice_group is None else 1,
            _prop_line_value(row.choice_group),
        )
        best_rank = (
            (0 if best.choice_group is None else 1, _prop_line_value(best.choice_group))
            if best
            else (2, float("inf"))
        )
        if score > best_score:
            best, best_score, best_names = row, score, {candidate.casefold()}
        elif score == best_score and score > 0:
            best_names.add(candidate.casefold())
            if rank < best_rank:
                best = row
    if best is None or best_score < _PROP_MIN_SIM or len(best_names) > 1:
        return None
    return _group_rows(rows, best)


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
    # Pick de una parte concreta: no puede caer en mercados de tiempo
    # completo ("Full time", "Match goals") — sería comparar cuotas
    # de mercados distintos. "descanso" cuenta como primera parte.
    half = _detect_half(text)
    ht_pick = half == "1st"
    if _is_halftime_fulltime(text) and not re.search(
        r"marc\w*|anot\w*|met\w*|score", text
    ):
        # Contrato único descanso-final: "gana al descanso y al
        # final" = "1/1" del mercado "Half time/Full time". "Gana
        # ambas partes" (To Win Both Halves) NO es 1/1 — sin ese
        # mercado queda miss honesto. Los picks "marca en ambas
        # partes" sí tienen mercado propio ("Team N to score both
        # halves") — los resuelve la rama to-score más abajo.
        code = _htft_choice(text, pick, event, team)
        if code:
            for row in _rows_of_market(snapshots, ("Half time/Full time",)):
                if row.choice_name == code:
                    return _group_rows(snapshots, row)
        return None

    if "over" in text or "under" in text or "más de" in text or "menos" in text:
        matched = _map_over_under(event.sport, text, snapshots, pick, event, half=half)
        if matched is not None and (
            half is None or matched.market_name.startswith(f"{half} half")
        ):
            return matched

    # "hándicap córners -3.5 Betis" -> "Corners handicap". Y una
    # línea NEGATIVA sobre córners/tarjetas solo puede ser hándicap
    # (los totales siempre son positivos): "Betis -3.5 córners" va
    # al mismo mercado aunque el tipster no escriba la palabra.
    _signed_stat_line = (
        event.sport == "futbol"
        and pick.linea is not None
        and pick.linea < 0
        and re.search(r"c[oó]rner|tarjeta|card|booking", text)
    )
    if re.search(r"h[aá]ndicap", text) or _signed_stat_line:
        ah_team = _extract_handicap_team(pick.seleccion or "") or team
        hc_markets = _HANDICAP_MARKETS
        if event.sport == "futbol":
            if re.search(r"europeo|european", text):
                # Hándicap EUROPEO = 1X2 con línea (empate es opción
                # "X", no push). La línea viaja en choice_group y es
                # la del LOCAL: el visitante la invierte ("Getafe +1"
                # es la opción "2" del grupo "-1").
                mkt = f"{half} half european handicap" if half else "European handicap"
                eh_rows = _rows_of_market(snapshots, (mkt,))

                def _eh_line(row: OddsSnapshot, want: float) -> bool:
                    ln = _line_of(row)
                    return ln is not None and abs(ln - want) < 0.001

                if re.search(r"empate|empat\w*|\bx\b", text):
                    for row in eh_rows:
                        if row.choice_name == "X" and (
                            pick.linea is None or _eh_line(row, pick.linea)
                        ):
                            return _group_rows(snapshots, row)
                elif ah_team:
                    side_eh = _side_index(ah_team, event)
                    if side_eh is not None and pick.linea is not None:
                        want_ln = pick.linea if side_eh == 0 else -pick.linea
                        eh_rows = [r for r in eh_rows if _eh_line(r, want_ln)]
                    matched = _match_side(eh_rows, event, ah_team)
                    if matched is not None:
                        return matched
                return None
            if re.search(r"c[oó]rner", text):
                hc_markets = (
                    (f"{half} half corners handicap",)
                    if half
                    else ("Corners handicap",)
                )
            elif re.search(r"tarjeta|card|booking", text):
                hc_markets = (
                    (f"{half} half cards handicap",) if half else ("Cards handicap",)
                )
            elif half:
                hc_markets = (f"{half} half handicap",)
        if event.sport == "tenis":
            # El extractor deja "Alcaraz juegos 1er set" — mejor el
            # lado detectado por apellido sobre el evento.
            side_hc = _event_player_in_text(text, event)
            if side_hc is not None:
                ah_team = (event.home_team, event.away_team)[side_hc]
            nth_hc = _detect_set_number(text)
            if nth_hc:
                hc_markets = (_SET_GAME_HANDICAP[nth_hc],)
        if ah_team:
            matched = _match_handicap(snapshots, event, ah_team, pick.linea, hc_markets)
            if matched is not None:
                return matched

    if re.search(
        r"ambos\s+(equipos\s+)?(marcan|anotan|anotar[aá]n|marcar[aá]n)|btts",
        text,
    ):
        # "ambos marcan en la 1ª parte" -> "1st half btts" (la 2ª no
        # la publica OddsPapi -> miss honesto).
        btts_name = f"{half} half btts" if half else "Both teams to score"
        for row in _rows_of_market(snapshots, (btts_name,)):
            if row.choice_name == "Yes":
                return _group_rows(snapshots, row)
    if "no anotan ambos" in text or "no marcan ambos" in text:
        btts_name = f"{half} half btts" if half else "Both teams to score"
        for row in _rows_of_market(snapshots, (btts_name,)):
            if row.choice_name == "No":
                return _group_rows(snapshots, row)

    if "doble oportunidad" in text or "double chance" in text:
        # "1X en la primera parte" -> "1st half double chance"; sin
        # ese mercado miss honesto, nunca el DC del tiempo completo.
        dc = _rows_of_market(
            snapshots,
            (f"{half} half double chance",) if half else ("Double chance",),
        )
        low = (pick.seleccion or "").strip().lower()
        # El código puede venir solo ("1X") o junto al equipo
        # ("Valencia 1X"): fullmatch solo cubría el primer caso.
        m_code = re.search(r"\b(1x|x2|12)\b", low)
        if m_code:
            for row in dc:
                if row.choice_name.lower() == m_code.group(1):
                    return _group_rows(snapshots, row)
        # "Equipo o/y empate", "equipo gana o empata": la mención al
        # empate decide 1X/X2 por el lado del equipo.
        if re.search(r"empat\w*|\bdraw\b", low):
            dc_team = re.sub(
                r"\b(empat\w*|draw|o|y|e|or|and|gan\w*|el|la|los|las|"
                r"partido|doble|oportunidad|double|chance)\b",
                " ",
                low,
            ).strip()
            if dc_team:
                side = _side_index(dc_team, event)
                code = {0: "1X", 1: "X2"}.get(side)
                for row in dc:
                    if code and row.choice_name == code:
                        return _group_rows(snapshots, row)
        # "EquipoA o EquipoB" (gana cualquiera) -> 12.
        elif re.search(r"\s+o\s+|\s*/\s*|\s+or\s+|cualquiera", low):
            for row in dc:
                if row.choice_name == "12":
                    return _group_rows(snapshots, row)
        # Sin señal explícita (empate/o/12) NO se adivina por el lado
        # del equipo: una selección truncada ("X gana") compararía la
        # cuota contra la pata equivocada — y de hecho los casos reales
        # (picks 5429/5645) eran "resultado sin empate" mal extraído,
        # otro mercado distinto. Miss honesto antes que inventar.
        return None

    if re.search(
        r"empate\s+no\s+v[aá]lido|empate\s+no\s+cuenta|empate\s+no\s+vale|"
        r"resultado\s+sin\s+empate|draw\s+no\s+bet|sin\s+empate",
        text,
    ):
        # DNB es un mercado propio con su precio (el empate devuelve
        # la apuesta): si el proveedor no publica "Draw no bet" no se
        # puede comparar contra la victoria seca — miss honesto, igual
        # que con el marcador exacto. Lo mismo por mitad ("empate no
        # válido 1ª parte" -> "1st half draw no bet").
        dnb_name = f"{half} half draw no bet" if half else "Draw no bet"
        if team:
            matched = _match_side(_rows_of_market(snapshots, (dnb_name,)), event, team)
            if matched is not None:
                return matched
        return None

    if event.sport == "futbol" and pick.linea is None:
        # 1X2 de stats: "saca más córners el Betis", "empate a
        # córners", "más tarjetas el Getafe" -> "Corners/Cards 1X2"
        # (quién saca más, no el total O/U — por eso sin línea).
        subj_1x2 = None
        if re.search(r"c[oó]rner", text):
            subj_1x2 = "corners"
        elif re.search(r"tarjeta|card|booking|amonestaci", text):
            subj_1x2 = "cards"
        if subj_1x2 and re.search(r"m[aá]s|saca|gana\w*|domina|favor|empat", text):
            base_1x2 = "Corners 1X2" if subj_1x2 == "corners" else "Cards 1X2"
            mkt = f"{half} half {subj_1x2} 1X2" if half else base_1x2
            rows_1x2 = _rows_of_market(snapshots, (mkt,))
            if re.search(r"empat", text):
                for row in rows_1x2:
                    if row.choice_name == "X":
                        return _group_rows(snapshots, row)
                return None
            side_1x2 = _event_team_in_text(text, event)
            if side_1x2 == _TEAM_AMBIGUOUS:
                return None
            if side_1x2 is not None:
                want = str(side_1x2 + 1)
                for row in rows_1x2:
                    if row.choice_name == want:
                        return _group_rows(snapshots, row)
                return None

    if event.sport == "futbol":
        # Mercados Yes/No por equipo: portería a 0, win to nil y
        # "equipo marca". "Suiza - Sí" / "Inglaterra gana sin
        # encajar" son los casos reales (picks 6150/3662).
        side_yn = _event_team_in_text(text, event)
        if side_yn == _TEAM_AMBIGUOUS:
            return None
        # "gana sin encajar" / "gana a cero" -> "Team N win to nil".
        if side_yn is not None and re.search(
            r"gan\w*.{0,15}sin\s+encajar|gan\w*.{0,15}a\s+cero|"
            r"win\s+to\s+nil|gan\w*\s+porter[ií]a\s+a\s+(0|cero)",
            text,
        ):
            mkt = (
                f"{half} half Team {side_yn + 1} win to nil"
                if half
                else f"Team {side_yn + 1} win to nil"
            )
            for row in _rows_of_market(snapshots, (mkt,)):
                if row.choice_name == "Yes":
                    return _group_rows(snapshots, row)
            return None
        # "portería a 0" -> "Team N clean sheet". La respuesta la da
        # el sufijo "- Sí/- No" del canal; "no/sin encaja(r)" también
        # es YES (no encajar = portería a 0).
        if side_yn is not None and re.search(
            r"porter[ií]a\s+a\s+(0|cero)|porter[ií]a\s+imbatida|"
            r"clean\s+sheet|sin\s+encajar|\bencaja\b|\bencajar\b",
            text,
        ):
            sel_low = (pick.seleccion or "").lower()
            want = "Yes"
            if re.search(r"[\-–:]\s*no\b", sel_low) or (
                re.search(r"encaja|recibe\s+gol|sufre\s+gol", sel_low)
                and not re.search(r"no\s+encaja|sin\s+encajar", sel_low)
            ):
                want = "No"
            mkt = (
                f"{half} half Team {side_yn + 1} clean sheet"
                if half
                else f"Team {side_yn + 1} clean sheet"
            )
            for row in _rows_of_market(snapshots, (mkt,)):
                if row.choice_name == want:
                    return _group_rows(snapshots, row)
            return None
        # "Betis marca" -> "Team N to score" (Yes/No). "en ambas
        # partes" -> mercado both halves; mitad -> "Nth half Team N
        # to score". No capturar "marca primero" (First team to
        # score) ni picks de ambos (btts ya corrió antes).
        if (
            side_yn is not None
            and re.search(r"\b(marc\w*|anot\w*|met\w*|score\w*)\b", text)
            and not re.search(
                # "primer"/"primero" excluye (marca primero) pero
                # "primera parte/mitad" no — es to score de la 1ª parte.
                r"primer(?!a\b)|first|[uú]ltim|last|siguiente|next|ambos",
                text,
            )
        ):
            want = (
                "No"
                if re.search(
                    r"no\s+(marc|anot|mete)|sin\s+(marcar|anotar|meter)",
                    text,
                )
                else "Yes"
            )
            if re.search(
                r"ambas\s+partes|primera\s+y\s+segunda\s+parte|"
                r"las\s+dos\s+partes|both\s+halves",
                text,
            ):
                mkt = f"Team {side_yn + 1} to score both halves"
            elif half:
                mkt = f"{half} half Team {side_yn + 1} to score"
            else:
                mkt = f"Team {side_yn + 1} to score"
            for row in _rows_of_market(snapshots, (mkt,)):
                if row.choice_name == want:
                    return _group_rows(snapshots, row)
            return None

    # "X gana el Nº set" -> "First/Second/Third set winner". Guardas:
    # `linea is None` (los "+7.5 juegos 2º set" no son ganador) y sin
    # marcador en la selección ("gana el 1er set 6-4" es marcador
    # correcto del set, no set winner).
    nth_set = _detect_set_number(text) if pick.linea is None else None
    if nth_set and team and not re.search(r"\b\d\s*-\s*\d\b", pick.seleccion or ""):
        set_winner = _SET_WINNER_MARKETS[nth_set]
        matched = _match_side(_rows_of_market(snapshots, (set_winner,)), event, team)
        if matched is not None:
            return matched

    # Set en juego ("gana el set actual/en curso"): el snapshotter guarda
    # "Current set winner" con choices "1"/"2" como el primer set.
    if re.search(r"set\s+actual|set\s+en\s+curso|este\s+set", text) and team:
        matched = _match_side(
            _rows_of_market(snapshots, ("Current set winner",)), event, team
        )
        if matched is not None:
            return matched

    # 1X2 del descanso ("gana X la primera parte"): mercado "1st half"
    # de Sofascore. Y "marca primero" -> "First team to score" (choices
    # "1"/"2"/"X" = ninguno marca).
    # Un marcador "1-0" en la selección convierte "gana X 1-0 la 1ª
    # parte" en apuesta de marcador, no de ganador de la mitad — se
    # resuelve en la rama score_m (miss honesto: los Correct Score
    # por mitad no están traducidos).
    _score_in_sel = bool(re.search(r"\b\d\s*-\s*\d\b", pick.seleccion or ""))
    if ht_pick and not _score_in_sel:
        ht_rows = _rows_of_market(snapshots, ("1st half",))
        if "empate" in text:
            for row in ht_rows:
                if row.choice_name == "X":
                    return _group_rows(snapshots, row)
        if team:
            matched = _match_side(ht_rows, event, team)
            if matched is not None:
                return matched
    if half == "2nd" and not _score_in_sel:
        # "gana X la segunda parte" -> "2nd half result" (1X2 de la
        # 2ª mitad; OddsPapi lo publica como "Second Half Result").
        sh_rows = _rows_of_market(snapshots, ("2nd half result",))
        if "empate" in text:
            for row in sh_rows:
                if row.choice_name == "X":
                    return _group_rows(snapshots, row)
        if team:
            matched = _match_side(sh_rows, event, team)
            if matched is not None:
                return matched

    if re.search(
        r"marca\s+primero|primer\s+gol|anota\s+primero|"
        r"primer\s+en\s+marcar|first\s+team\s+to\s+score",
        text,
    ):
        # "marca primero en la 1ª parte": el mercado FT no es el
        # contrato — sin mercado de mitad traducido, miss honesto.
        if half:
            return None
        fts_rows = _rows_of_market(snapshots, ("First team to score",))
        if re.search(r"sin\s+goles|ninguno|nadie|no\s+goals", text):
            for row in fts_rows:
                if row.choice_name == "X":
                    return _group_rows(snapshots, row)
        # La frase del mercado no es el equipo: "Betis marca primero"
        # -> "Betis" antes de casar el lado.
        fts_team = re.sub(
            r"marca\s+primero|primer\s+gol|anota\s+primero|"
            r"primer\s+en\s+marcar|first\s+team\s+to\s+score",
            " ",
            pick.seleccion or "",
            flags=re.IGNORECASE,
        ).strip()
        if fts_team:
            matched = _match_side(fts_rows, event, fts_team)
            if matched is not None:
                return matched

    if re.search(
        r"marca\s+(el\s+)?[uú]ltimo|[uú]ltimo\s+gol|"
        r"[uú]ltimo\s+en\s+marcar|last\s+team\s+to\s+score|"
        r"last\s+goal\b",
        text,
    ):
        # "marca el último gol" -> "Last team to score" (mismo
        # formato 1/X/2 que el primero; X = no hubo goles).
        if half:
            return None
        lts_rows = _rows_of_market(snapshots, ("Last team to score",))
        if re.search(r"sin\s+goles|ninguno|nadie|no\s+goals", text):
            for row in lts_rows:
                if row.choice_name == "X":
                    return _group_rows(snapshots, row)
        lts_team = re.sub(
            r"marca\s+(el\s+)?[uú]ltimo|[uú]ltimo\s+gol|"
            r"[uú]ltimo\s+en\s+marcar|last\s+team\s+to\s+score|"
            r"last\s+goal\b",
            " ",
            pick.seleccion or "",
            flags=re.IGNORECASE,
        ).strip()
        if lts_team:
            matched = _match_side(lts_rows, event, lts_team)
            if matched is not None:
                return matched

    if event.sport == "futbol":
        # "número par/impar de goles" -> "Goals odd/even" (+ "Team N
        # goals odd/even" y variantes de mitad). Córners/tarjetas
        # par/impar: mercados del catálogo sin traducir aún — miss
        # honesto, nunca caer al FT.
        if re.search(r"\bpar\b|impar\b|pares\b|\beven\b|\bodd\b", text):
            if re.search(r"c[oó]rner|tarjeta|card|booking", text):
                return None
            want_oe = "Even" if re.search(r"\bpar\b|pares\b|\beven\b", text) else "Odd"
            side_oe = _event_team_in_text(text, event)
            if side_oe == _TEAM_AMBIGUOUS:
                return None
            if side_oe is not None:
                oe_market = f"Team {side_oe + 1} goals odd/even"
            elif half:
                oe_market = f"{half} half goals odd/even"
            else:
                oe_market = "Goals odd/even"
            for row in _rows_of_market(snapshots, (oe_market,)):
                if row.choice_name == want_oe:
                    return _group_rows(snapshots, row)
            return None

        # "gana remontando" / "remonta el partido" -> "To win from
        # behind" (choices 1/X/2).
        if re.search(
            r"remont\w*|tras\s+ir\s+perdiendo|win\s+from\s+behind|" r"comeback",
            text,
        ):
            wfb_rows = _rows_of_market(snapshots, ("To win from behind",))
            wfb_team = re.sub(
                r"gana\w*|remont\w*|tras\s+ir\s+perdiendo|comeback|"
                r"el\s+partido|win\s+from\s+behind",
                " ",
                pick.seleccion or "",
                flags=re.IGNORECASE,
            ).strip()
            if wfb_team:
                matched = _match_side(wfb_rows, event, wfb_team)
                if matched is not None:
                    return matched
            return None

        # "se marcan más goles en la 2ª parte" / "la parte con más
        # goles es la primera" -> "Highest scoring half" (1st/X/2nd).
        if half and re.search(
            r"m[aá]s\s+goles|m[aá]s\s+anotaciones|highest\s+scoring|"
            r"parte\s+(?:m[aá]s\s+)?goleadora",
            text,
        ):
            hsh_rows = _rows_of_market(snapshots, ("Highest scoring half",))
            if re.search(r"empat|igual|mism", text):
                want_hsh = "X"
            else:
                want_hsh = "1st" if half == "1st" else "2nd"
            for row in hsh_rows:
                if row.choice_name == want_hsh:
                    return _group_rows(snapshots, row)
            return None

    # "gana 2-0" / "3-1" = marcador de sets (el extractor lo etiqueta
    # a veces como mercado "ganador", pero el precio real es el de
    # marcador, ~1.5-2.0, no el de la victoria seca ~1.15). Si el
    # proveedor no publica mercado de marcador -> miss honesto, no
    # comparar contra el Full time (bug real: picks 662/4007/4551).
    score_m = re.search(r"\b(\d)\s*-\s*(\d)\b", pick.seleccion or "")
    if score_m:
        target = f"{score_m.group(1)}-{score_m.group(2)}"
        if event.sport == "futbol" and half:
            # "gana 1-0 en la primera parte" -> "1st half correct
            # score"; si el proveedor no lo publica, miss honesto —
            # nunca el marcador del tiempo completo.
            for row in _rows_of_market(snapshots, (f"{half} half correct score",)):
                if target in (row.choice_name or "").replace(" ", ""):
                    return _group_rows(snapshots, row)
            return None
        # Tenis con set explícito: "gana el 1er set 6-4" es el marcador
        # EN JUEGOS de ese set, no el marcador del partido en sets.
        nth_cs = _detect_set_number(text) if event.sport == "tenis" else None
        if nth_cs:
            cs_names = {
                "1": "1st set correct score",
                "2": "2nd set correct score",
                "3": "3rd set correct score",
            }
            cs_markets = (cs_names[nth_cs],)
        else:
            cs_markets = ("Correct score", "Set score", "Set betting")
        for row in _rows_of_market(snapshots, cs_markets):
            if target in (row.choice_name or "").replace(" ", ""):
                return _group_rows(snapshots, row)
        return None

    if event.sport == "tenis":
        # "X gana un set" / "X no gana ningún set" -> "Player N to win
        # a set" (Yes/No). "Ambos ganan un set" -> "Both to win a set".
        if re.search(
            r"gan\w*\s+(?:\w+\s+){0,3}(un|algún|ningún)\s+set\b", text
        ) and not _detect_set_number(text):
            if re.search(r"ambos|los\s+dos|las\s+dos", text):
                want_both = "No" if re.search(r"\bno\b", text) else "Yes"
                for row in _rows_of_market(snapshots, ("Both to win a set",)):
                    if row.choice_name == want_both:
                        return _group_rows(snapshots, row)
            else:
                side_ws = _event_player_in_text(text, event)
                if side_ws is not None:
                    want_ws = "No" if re.search(r"\bno\b|ningún", text) else "Yes"
                    market_ws = f"Player {side_ws + 1} to win a set"
                    for row in _rows_of_market(snapshots, (market_ws,)):
                        if row.choice_name == want_ws:
                            return _group_rows(snapshots, row)

        # "se juegan 3 sets" / "partido a 5 sets" -> "Exact sets".
        # El dígito no puede venir de un decimal ("2.5 sets" es O/U).
        m_es = re.search(r"(?<![\d.,])([2-5])\s*sets?\b", text)
        if m_es and not re.search(r"over|under|m[aá]s|menos", text):
            want_sets = m_es.group(1)
            for row in _rows_of_market(snapshots, ("Exact sets",)):
                if row.choice_name == want_sets:
                    return _group_rows(snapshots, row)

        # "juegos pares/impares" -> "Games odd/even".
        if re.search(
            r"juegos?\s+(par|impar)\b|(par|impar)\s+de\s+juegos|"
            r"n[uú]mero\s+(par|impar)",
            text,
        ):
            want_oe = "Odd" if "impar" in text else "Even"
            for row in _rows_of_market(snapshots, ("Games odd/even",)):
                if row.choice_name == want_oe:
                    return _group_rows(snapshots, row)

    # Props de jugador (The Odds API: "Goalscorer anytime" y
    # familia): la selección trae el NOMBRE DEL JUGADOR, no un
    # equipo. Si la fuente no publica el mercado del prop -> miss
    # honesto, nunca se compara contra el 1X2.
    prop_markets = _prop_markets_for(text)
    if prop_markets is not None:
        player = _extract_prop_player(pick.seleccion or "")
        if player:
            matched = _match_prop_player(
                _rows_of_market(snapshots, prop_markets),
                player,
                text,
                pick.linea,
            )
            if matched is not None:
                return matched
        return None

    # Por defecto: mercado ganador ("Full time" en ambos deportes).
    # Un pick de descanso nunca se compara con la cuota del FT, un
    # pick de hándicap tampoco ("Alcaraz -1.5 juegos 1er set" sin ese
    # mercado no es "Alcaraz gana"), y un pick CON LÍNEA que no
    # encontró su mercado tampoco ("Betis -3.5 córners" sin el
    # mercado de córners no es "Betis gana").
    if (
        team
        and half is None
        and pick.linea is None
        and not re.search(r"h[aá]ndicap", text)
    ):
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
    # True si la cuota declarada es una supercuota/promo de afiliado
    # ("CUOTA 5.00 que debería ser 1.22" — bdeal.io y similares): el
    # precio anunciado nunca existió en mercado, compararlo distorsiona
    # inflada/CLV. Los agregados la excluyen; los valores se conservan.
    cuota_promo: bool = False


# Ratio tipster/mercado a partir del cual la cuota declarada solo puede
# ser promocional: una supercuota de casa suele doblar el precio, un
# pick legítimo jamás mejora el mercado un 80%+.
_PROMO_RATIO = 1.8


def _comparison_from_match(
    pick: ParsedPick, matched: MatchedOdds, event_start: Optional[datetime] = None
) -> PickOddsComparison:
    """Rellena los puntos temporales y derivadas sobre las filas de la
    opción casada (apertura / publicación / cierre, disponible, CLV)."""
    result = PickOddsComparison(
        mapeado=True,
        mercado_api=matched.market_name,
        opcion_api=matched.choice_name,
        linea_api=matched.choice_group,
        cuota_tipster=pick.cuota,
        capturas=len(matched.rows),
    )
    for row in matched.rows:
        if row.cuota_apertura is not None:
            result.cuota_apertura = row.cuota_apertura
            break
    # Los puntos suspendidos (mercado cerrado momentáneamente; el feed
    # los marca con cuota ~1.00) no son un precio apostable: no pueden
    # ser referencia de publicación ni de cierre. Sin este filtro, un
    # pick cuyo `created_at` cae cerca de una suspensión comparaba
    # contra una cuota que nadie podía tomar -> falso "inflada".
    valid = [r for r in matched.rows if not r.suspended and r.cuota > 1.0]
    if valid:
        # El cierre es PREMATCH: el último precio antes del inicio del
        # evento. Los puntos live ya reflejan un partido decidido
        # (cuotas 51.0/1.01) y comparar contra ellos fingiría un CLV
        # imposible. Si toda la curva es live, no hay cierre real ->
        # None honesto. Sin `event_start` se conserva el último punto.
        close_rows = (
            [r for r in valid if r.captured_at <= event_start]
            if event_start is not None
            else valid
        )
        if close_rows:
            result.cuota_cierre = close_rows[-1].cuota
        if pick.created_at:
            nearest = min(
                valid,
                key=lambda r: abs((r.captured_at - pick.created_at).total_seconds()),
            )
            result.cuota_publicacion = nearest.cuota
    if pick.cuota is not None and result.cuota_publicacion is not None:
        result.cuota_disponible = pick.cuota <= result.cuota_publicacion
        result.cuota_promo = pick.cuota > result.cuota_publicacion * _PROMO_RATIO
    if (
        pick.cuota is not None
        and result.cuota_cierre is not None
        and result.cuota_cierre > 0
    ):
        result.clv_pct = round((pick.cuota / result.cuota_cierre - 1.0) * 100.0, 2)
    return result


async def compare_pick(session: AsyncSession, pick: ParsedPick) -> PickOddsComparison:
    """Compara la cuota del pick con las snapshots de su evento.

    - `cuota_publicacion`: la captura más cercana a `pick.created_at`
      (lo que el mercado ofrecía cuando el tipster publicó — el pick se
      importa casi en tiempo real).
    - `cuota_cierre`: la última captura prematch (la última antes del
      inicio del evento, no un precio live).
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
    return _comparison_from_match(pick, matched, event.start)


async def compare_picks(
    session: AsyncSession, picks: list[ParsedPick]
) -> dict[int, PickOddsComparison]:
    """Comparación por lotes, para los agregados por canal.

    Una sola query de `odds_events` y otra de `odds_snapshots` para
    todos los picks: comparar pick a pick haría un N+1 de cientos de
    consultas por canal. Devuelve pick.id -> comparación (los picks
    sin evento o sin mercado equivalente quedan `mapeado=False`).
    """
    comparisons: dict[int, PickOddsComparison] = {
        p.id: PickOddsComparison(mapeado=False, cuota_tipster=p.cuota)
        for p in picks
        if p.id is not None
    }
    event_ids = {p.odds_event_id for p in picks if p.odds_event_id}
    if not event_ids:
        return comparisons

    events = {
        e.event_ext_id: e
        for e in (
            await session.exec(
                select(OddsEvent).where(OddsEvent.event_ext_id.in_(event_ids))
            )
        ).all()
    }
    rows_by_event: dict[str, list[OddsSnapshot]] = {}
    for row in (
        await session.exec(
            select(OddsSnapshot)
            .where(OddsSnapshot.event_ext_id.in_(event_ids))
            .order_by(OddsSnapshot.captured_at)
        )
    ).all():
        rows_by_event.setdefault(row.event_ext_id, []).append(row)

    for pick in picks:
        if pick.id is None or not pick.odds_event_id:
            continue
        event = events.get(pick.odds_event_id)
        rows = rows_by_event.get(pick.odds_event_id, [])
        if event is None or not rows:
            continue
        matched = map_pick_choices(pick, event, rows)
        if matched is not None:
            comparisons[pick.id] = _comparison_from_match(pick, matched, event.start)
    return comparisons


@dataclass
class ChannelOddsStats:
    """Agregados de la auditoría de cuotas de un conjunto de picks.

    El veredicto global del tipster: un pick aislado no dice nada, el
    patrón sí. Cada porcentaje usa como denominador solo los picks
    donde la métrica era calculable (un pick sin cuota de cierre no
    puede bajar el % de CLV, igual que los pendientes no cuentan en
    el % de aciertos).
    """

    picks: int = 0
    con_evento: int = 0
    mapeados: int = 0
    pct_mapeado: float = 0.0
    auditables: int = 0
    cuotas_infladas: int = 0
    pct_cuota_inflada: float = 0.0
    # Supercuotas de afiliado excluidas de inflada/CLV (el precio
    # declarado es promocional, no de mercado).
    cuotas_promo: int = 0
    con_clv: int = 0
    clv_medio: Optional[float] = None
    pct_bate_cierre: float = 0.0


def aggregate_odds_stats(
    picks: list[ParsedPick], comparisons: dict[int, PickOddsComparison]
) -> ChannelOddsStats:
    """Agrega las comparaciones individuales en el veredicto del canal:
    % de picks con cuota inflada, CLV medio y % que bate el cierre."""
    stats = ChannelOddsStats(
        picks=len(picks),
        con_evento=sum(1 for p in picks if p.odds_event_id),
    )
    clvs: list[float] = []
    for pick in picks:
        comparison = comparisons.get(pick.id) if pick.id is not None else None
        if comparison is None or not comparison.mapeado:
            continue
        stats.mapeados += 1
        # Supercuotas de afiliado: el precio declarado nunca existió en
        # mercado — meterlo en "inflada" o en CLV distorsiona ambos.
        if comparison.cuota_promo:
            stats.cuotas_promo += 1
            continue
        if comparison.cuota_disponible is not None:
            stats.auditables += 1
            if comparison.cuota_disponible is False:
                stats.cuotas_infladas += 1
        if comparison.clv_pct is not None:
            clvs.append(comparison.clv_pct)
    if stats.con_evento:
        stats.pct_mapeado = round(stats.mapeados / stats.con_evento * 100, 2)
    if stats.auditables:
        stats.pct_cuota_inflada = round(
            stats.cuotas_infladas / stats.auditables * 100, 2
        )
    stats.con_clv = len(clvs)
    if clvs:
        stats.clv_medio = round(sum(clvs) / len(clvs), 2)
        stats.pct_bate_cierre = round(
            sum(1 for v in clvs if v > 0) / len(clvs) * 100, 2
        )
    return stats
