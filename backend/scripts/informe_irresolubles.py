"""Informe de picks pendientes de cuota, categorizado por causa.

Una pasada: lista cada pick pendiente con sus marcas por proveedor y
lo encuadra en una categoría accionable. Sin llamadas a APIs — solo
BD + provider_state.json.

Uso: docker exec controlpick-backend python scripts/informe_irresolubles.py
"""

import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/app")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

STATE = "/app/provider_state.json"

SQL_PENDIENTES = """
SELECT p.id, p.evento, p.mercado, p.seleccion, p.linea, p.cuota,
       p.deporte, p.fecha_evento, p.odds_event_id
FROM parsed_picks p
WHERE p.es_apuesta = true
  AND p.es_combinada = false
  AND p.deporte IS NOT NULL
  AND p.fecha_evento IS NOT NULL
  AND p.fecha_evento < now() - interval '6 hours'
  AND p.id NOT IN (
      SELECT DISTINCT parsed_pick_id FROM odds_snapshots
      WHERE parsed_pick_id IS NOT NULL
  )
ORDER BY p.fecha_evento
"""

SPORTS = {
    "fútbol": "futbol",
    "futbol": "futbol",
    "tenis": "tenis",
    "baloncesto": "baloncesto",
    "basket": "baloncesto",
}


def categoria(row, marks):
    pid = row.id
    m = [k for k in marks if k.endswith(f"|{pid}")]
    if any(k == "betsapi|nomarket" or k.startswith("betsapi|nomarket") for k in m):
        return "nomarket_bet365", m
    if any(k.startswith("betsapi|nofixture") for k in m):
        if not row.evento:
            return "extraccion_corrupta", m
        return "no_listado_bet365", m
    if any("|nomarket|" in k for k in m):
        return "nomarket_otros", m
    if not row.evento:
        return "extraccion_corrupta", m
    if m:
        return "nofixture_otros", m
    return "sin_revisar", m


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as conn:
        rows = (await conn.execute(text(SQL_PENDIENTES))).all()

    try:
        marks = json.load(open(STATE)).get("missed", {})
    except OSError:
        marks = {}

    cats: dict[str, list] = {}
    for row in rows:
        cat, m = categoria(row, marks)
        cats.setdefault(cat, []).append((row, m))

    print(f"\n=== PENDIENTES DE CUOTA: {len(rows)} ===\n")
    print(Counter({c: len(v) for c, v in cats.items()}))
    for cat in (
        "nomarket_bet365",
        "no_listado_bet365",
        "extraccion_corrupta",
        "nomarket_otros",
        "nofixture_otros",
        "sin_revisar",
    ):
        group = cats.get(cat) or []
        if not group:
            continue
        print(f"\n--- {cat} ({len(group)}) ---")
        for row, m in group:
            ev = (row.evento or "(sin evento)")[:45]
            sel = (row.seleccion or "")[:40]
            mm = [k.split("|")[0] + ":" + k.split("|")[1] for k in m if "|" in k]
            print(
                f"  {row.id} | {row.fecha_evento:%Y-%m-%d} | {ev} | "
                f"{row.mercado} {row.linea or ''} | {sel} | {','.join(mm)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
