"""Diagnóstico de los proveedores de tenis: muestra el JSON crudo de
los partidos de un día en TheSportsDB (y opcionalmente el fallback de
RapidAPI con `--rapidapi`).

Uso:

    .venv/Scripts/python.exe scripts/debug_tennis_api.py 2026-09-15
    .venv/Scripts/python.exe scripts/debug_tennis_api.py 2026-09-15 --rapidapi "Carlos Alcaraz"
    .venv/Scripts/python.exe scripts/debug_tennis_api.py 2026-09-15 --tennisapi1

Sirve para validar el formato real de cada API (TheSportsDB:
`strEvent`/`strResult`; RapidAPI: `matches-played` por jugador;
tennisapi1: eventos Sofascore por categoría) y ajustar el parseo si la
respuesta real difiere de lo esperado.
"""

from __future__ import annotations

import asyncio
import json
import sys
from urllib.parse import quote

import httpx

from app.core.config import get_settings


async def _thesportsdb(date_str: str, key: str) -> None:
    url = f"https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params={"d": date_str, "s": "Tennis"})
        response.raise_for_status()
    events = response.json().get("events") or []
    finished = [e for e in events if e.get("strResult")]
    print(f"[TheSportsDB] {len(events)} partidos ({len(finished)} con resultado)\n")
    for event in (finished or events)[:3]:
        print(json.dumps(event, indent=2, ensure_ascii=False))
        print("---")


async def _rapidapi(date_str: str, key: str, host: str, player: str) -> None:
    year = date_str[:4]
    url = f"https://{host}/tennis/v2/profile/{quote(player)}/matches-played"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            url,
            params={"year": year, "limit": 10},
            headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": host},
        )
        response.raise_for_status()
    matches = response.json().get("singles") or []
    print(f"[RapidAPI] {len(matches)} partidos de {player} en {year}\n")
    for match in matches[:3]:
        print(json.dumps(match, indent=2, ensure_ascii=False))
        print("---")


async def _tennisapi1(date_str: str, key: str, host: str) -> None:
    from datetime import datetime

    from app.services.results.tennisapi1 import _CATEGORIES

    day = datetime.strptime(date_str, "%Y-%m-%d")
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": host}
    async with httpx.AsyncClient(timeout=20) as client:
        for category_id in _CATEGORIES:
            url = (
                f"https://{host}/api/tennis/category/{category_id}"
                f"/events/{day.day}/{day.month}/{day.year}"
            )
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                print(f"[cat {category_id}] HTTP {response.status_code}")
                continue
            events = response.json().get("events") or []
            finished = [
                e for e in events if (e.get("status") or {}).get("type") == "finished"
            ]
            print(
                f"[cat {category_id}] {len(events)} eventos, {len(finished)} terminados"
            )
            for event in finished[:2]:
                print(json.dumps(event, indent=2, ensure_ascii=False)[:1200])
                print("---")


async def main(
    date_str: str, use_rapidapi: bool, use_tennisapi1: bool, player: str
) -> None:
    settings = get_settings()
    if use_tennisapi1:
        if not settings.rapidapi_tennis_key:
            print("RAPIDAPI_TENNIS_KEY no está configurada en .env")
            sys.exit(1)
        await _tennisapi1(
            date_str,
            settings.rapidapi_tennis_key,
            settings.rapidapi_tennisapi1_host,
        )
    elif use_rapidapi:
        if not settings.rapidapi_tennis_key:
            print("RAPIDAPI_TENNIS_KEY no está configurada en .env")
            sys.exit(1)
        await _rapidapi(
            date_str,
            settings.rapidapi_tennis_key,
            settings.rapidapi_tennis_host,
            player,
        )
    else:
        await _thesportsdb(date_str, settings.api_tennis_key or "3")


if __name__ == "__main__":
    use_rapidapi = "--rapidapi" in sys.argv
    use_tennisapi1 = "--tennisapi1" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(
        main(
            args[0] if args else "2026-09-15",
            use_rapidapi,
            use_tennisapi1,
            args[1] if len(args) > 1 else "Carlos Alcaraz",
        )
    )
