"""Rescate de picks de estadísticas vía CSVs de football-data.co.uk.

Los mercados de córners/tarjetas/tiros solo se resuelven con
`/fixtures/statistics` de API-Football, que en el plan gratis solo sirve
fechas dentro de la ventana ±1 día — o con footapi7, si queda cuota.
Los que se escapan de ambas quedarían pendientes para siempre.

Este script descarga los CSVs gratuitos de football-data.co.uk (22
ligas europeas, incluidas La Liga y Segunda; actualizan ~2 veces por
semana) y liquida los pendientes que casen por fecha+equipos. Usa las
mismas funciones de resolución del verificador, así el criterio de
acierto/fallo es idéntico al del ciclo automático.

Pensado para correr semanalmente o bajo demanda:

    .\\.venv\\Scripts\\python.exe scripts\\backfill_stats_csv.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\backfill_stats_csv.py --apply    # escribe
    .\\.venv\\Scripts\\python.exe scripts\\backfill_stats_csv.py --apply --days 45
"""

import argparse
import asyncio
import csv
import io
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.services.results.base import MatchStats, match_score
from app.services.results.verifier import (
    _FIRST_HALF_PATTERN,
    _MIN_TEAM_SIMILARITY,
    _OU_NON_GOALS_PATTERN,
    _detect_over_under_direction,
    _extract_team_total_team,
    _resolve_stat_over_under,
    _settle_combinadas,
    _stat_keys_for,
)

# Ligas cubiertas por football-data.co.uk (divisiones principales).
LEAGUES = [
    "E0",
    "E1",
    "E2",
    "E3",
    "EC",  # Inglaterra
    "SC0",
    "SC1",
    "SC2",
    "SC3",  # Escocia
    "D1",
    "D2",  # Alemania
    "I1",
    "I2",  # Italia
    "SP1",
    "SP2",  # España
    "F1",
    "F2",  # Francia
    "N1",  # Países Bajos
    "B1",  # Bélgica
    "P1",  # Portugal
    "T1",  # Turquía
    "G1",  # Grecia
]

# Columna CSV -> clave canónica de estadística del verificador.
_CSV_STAT_MAP = {
    "HC": ("Corner Kicks", 0),
    "AC": ("Corner Kicks", 1),
    "HY": ("Yellow Cards", 0),
    "AY": ("Yellow Cards", 1),
    "HR": ("Red Cards", 0),
    "AR": ("Red Cards", 1),
    "HS": ("Total Shots", 0),
    "AS": ("Total Shots", 1),
    "HST": ("Shots on Goal", 0),
    "AST": ("Shots on Goal", 1),
    "HF": ("Fouls", 0),
    "AF": ("Fouls", 1),
    "HO": ("Offsides", 0),
    "AO": ("Offsides", 1),
}

_CSV_DATE_TOLERANCE = timedelta(days=1)


def _season_code(day: datetime) -> str:
    """Temporada europea '2526' para 2025/26 (empieza en agosto)."""
    year = day.year
    if day.month < 7:
        year -= 1
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


def _parse_csv_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


async def _load_league_rows(
    client: httpx.AsyncClient, league: str, season: str
) -> list[dict]:
    """Filas del CSV de una liga con fecha y stats normalizadas."""
    # Sin "www": el dominio con www redirige (302) al apex.
    url = f"https://football-data.co.uk/mmz4281/{season}/{league}.csv"
    try:
        response = await client.get(url, timeout=30, follow_redirects=True)
        if response.status_code != 200:
            return []
        # Latin-1 por si algún nombre lleva bytes no UTF-8.
        text = response.content.decode("utf-8-sig", errors="replace")
    except httpx.HTTPError:
        return []

    rows: list[dict] = []
    for raw in csv.DictReader(io.StringIO(text)):
        played = _parse_csv_date(raw.get("Date", ""))
        home = (raw.get("HomeTeam") or "").strip()
        away = (raw.get("AwayTeam") or "").strip()
        if not played or not home or not away:
            continue

        # Junta (home, away) por clave canónica.
        values: dict[str, list[int]] = {}
        for col, (key, idx) in _CSV_STAT_MAP.items():
            cell = (raw.get(col) or "").strip()
            if not cell.isdigit():
                continue
            values.setdefault(key, [0, 0])[idx] = int(cell)
        if not values:
            continue

        rows.append(
            {
                "date": played,
                "home": home,
                "away": away,
                "values": {k: (v[0], v[1]) for k, v in values.items()},
            }
        )
    return rows


def _find_row(rows: list[dict], fecha: datetime, team_hint: str) -> dict | None:
    """La fila CSV que mejor casa por fecha (±1 día) y equipos."""
    naive_fecha = fecha.replace(tzinfo=None)
    best = None
    best_score = 0.0
    for row in rows:
        if abs(row["date"] - naive_fecha) > _CSV_DATE_TOLERANCE:
            continue
        score = match_score(team_hint, row["home"], row["away"])
        if score > best_score:
            best_score = score
            best = row
    if best_score < _MIN_TEAM_SIMILARITY:
        return None
    return best


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Escribe los resultados en BD."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=45,
        help="Antigüedad máxima de los picks a intentar (def: 45).",
    )
    parser.add_argument(
        "--leagues",
        default=",".join(LEAGUES),
        help="Códigos de liga separados por comas (def: todas).",
    )
    args = parser.parse_args()
    leagues = [lg.strip() for lg in args.leagues.split(",") if lg.strip()]

    async with AsyncSessionLocal() as session:
        await _run(session, leagues, args.days, args.apply)


async def _run(
    session: AsyncSession, leagues: list[str], days: int, apply: bool
) -> None:
    cutoff = utc_now() - timedelta(days=days)
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.acierto == None)  # noqa: E711
        .where(ParsedPick.anulada == False)  # noqa: E712
        .where(ParsedPick.es_combinada == False)  # noqa: E712
        .where(ParsedPick.combinada_id == None)  # noqa: E711
        .where(ParsedPick.fecha_evento != None)  # noqa: E711
        .where(ParsedPick.fecha_evento >= cutoff)
    )
    candidates = [
        p
        for p in result.all()
        if (p.deporte or "futbol") == "futbol"
        # Mercado "combinada" sin patas modeladas (picks antiguos): el
        # texto de las patas puede mencionar córners pero no hay una
        # línea propia que resolver — mismo criterio que verify_pick.
        and (p.mercado or "").lower() not in ("combinada", "combinado")
        and _OU_NON_GOALS_PATTERN.search(f"{p.seleccion or ''} {p.mercado or ''}")
        and _stat_keys_for(f"{p.seleccion or ''} {p.mercado or ''}")
        and not _FIRST_HALF_PATTERN.search(f"{p.seleccion or ''} {p.mercado or ''}")
    ]
    print(f"[CSV] {len(candidates)} picks de stats pendientes (<= {days} días)")

    # Descarga una temporada por liga (la del pick; con --days 45 basta
    # la temporada actual, pero se cubre el cruce agosto/septiembre).
    seasons = {_season_code(p.fecha_evento) for p in candidates}
    league_rows: dict[tuple[str, str], list[dict]] = {}
    async with httpx.AsyncClient() as client:
        for season in seasons:
            for league in leagues:
                rows = await _load_league_rows(client, league, season)
                if rows:
                    league_rows[(league, season)] = rows
    print(f"[CSV] {len(league_rows)} CSVs con datos descargados")

    resolved = skipped = 0
    for pick in candidates:
        ou_text = f"{pick.seleccion or ''} {pick.mercado or ''}"
        keys = _stat_keys_for(ou_text)
        direction = _detect_over_under_direction(ou_text)
        team = _extract_team_total_team(pick.seleccion or "")
        hint = team or pick.evento or ""
        if not keys or not direction or not hint or pick.linea is None:
            skipped += 1
            continue

        row = _find_row(
            [r for (lg, s), r in _iter_rows(league_rows)], pick.fecha_evento, hint
        )
        if row is None:
            skipped += 1
            continue

        stats = MatchStats(
            home_team=row["home"], away_team=row["away"], values=row["values"]
        )
        acierto, anulada = _resolve_stat_over_under(
            stats, keys, team, direction, pick.linea
        )
        if acierto is None and not anulada:
            skipped += 1
            continue

        print(
            f"[CSV] pick {pick.id} ({hint} @ {pick.fecha_evento:%Y-%m-%d}) "
            f"-> {row['home']} vs {row['away']} {row['date']:%d/%m}: "
            f"{'ANULADA' if anulada else ('ACIERTO' if acierto else 'FALLO')}"
        )
        resolved += 1
        if apply:
            pick.acierto = acierto
            pick.anulada = anulada
            session.add(pick)

    if apply:
        await session.commit()
        settled = await _settle_combinadas(session)
        await session.commit()
        print(f"[CSV] Combinadas liquidadas tras el rescate: {len(settled)}")

    print(
        f"[CSV] {'APLICADO' if apply else 'DRY-RUN'}: "
        f"{resolved} resueltos, {skipped} siguen pendientes"
    )


def _iter_rows(league_rows: dict[tuple[str, str], list[dict]]):
    for key, rows in league_rows.items():
        for row in rows:
            yield key, row


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
