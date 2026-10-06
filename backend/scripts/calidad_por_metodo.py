# ruff: noqa: E402
"""Calidad de extracción por motor (`metodo`), para decidir la migración.

El criterio no es "se parece a OpenAI" sino si el pick EXTRAÍDO funciona
en la realidad: llega a verificación, encuentra evento de cuotas y no
se anula por malformación. Agrupado por metodo (llm = OpenAI,
llm-groq / llm-openrouter / rule+llm-* = cascada gratuita).

Uso (en el contenedor):
    python scripts/calidad_por_metodo.py [--dias 7]
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import func, select

from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401 (FK)


async def main_async(dias: int):
    from datetime import timedelta

    from app.core.dates import utc_now

    desde = utc_now() - timedelta(days=dias)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.exec(
                select(
                    ParsedPick.metodo,
                    func.count().label("total"),
                    func.count(ParsedPick.id)
                    .filter(ParsedPick.es_apuesta == True)  # noqa: E712
                    .label("apuestas"),
                    func.count(ParsedPick.id)
                    .filter(ParsedPick.acierto != None)  # noqa: E711
                    .label("resueltos"),
                    func.count(ParsedPick.id)
                    .filter(ParsedPick.odds_event_id != None)  # noqa: E711
                    .label("con_evento"),
                    func.count(ParsedPick.id)
                    .filter(ParsedPick.anulada == True)  # noqa: E712
                    .label("anuladas"),
                )
                .where(ParsedPick.created_at >= desde)
                .where(ParsedPick.combinada_id.is_(None))
                .group_by(ParsedPick.metodo)
                .order_by(func.count().desc())
            )
        ).all()
    print(f"\n=== Picks por motor (últimos {dias} días, sin patas) ===\n")
    print(
        f"{'metodo':22s} {'total':>5s} {'apuest':>6s} {'resuel':>6s} "
        f"{'evento':>6s} {'anulad':>6s}"
    )
    for metodo, total, apuestas, resueltos, con_evento, anuladas in rows:
        print(
            f"{metodo:22s} {total:5d} {apuestas or 0:6d} {resueltos or 0:6d} "
            f"{con_evento or 0:6d} {anuladas or 0:6d}"
        )
    print(
        "\nLeyenda: resuel = con veredicto (acierto no nulo) · "
        "evento = encontró fixture en provider de cuotas · "
        "anulad = anuladas\n"
        "Compara 'llm-*'/'rule+llm-*' (gratis) vs 'llm'/'rule+llm' (OpenAI): "
        "si resuelven/anulan parecido, la cascada sirve."
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=7)
    args = ap.parse_args()
    asyncio.run(main_async(args.dias))


if __name__ == "__main__":
    main()
