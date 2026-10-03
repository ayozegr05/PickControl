# ruff: noqa: E402
"""Barrido puntual: verifica picks pendientes FUERA de la ventana normal
de 14 días (`_MAX_VERIFICATION_AGE`) que el verificador ya no reintenta.

Motivo (roadmap, apartado D): Lady Bets acumulaba picks de tenis de
julio-septiembre que nunca se resolvieron — los proveedores los
buscaron antes de que el partido terminara y los marcaron `missed`.
Con el fix A (missed provisional vs definitivo) y C.2 (búsqueda por
jugador en tennisapi1) muchos sí son resolubles: los resultados
históricos son estáticos y están en los archivos de las APIs.

Coste de cuota: TheSportsDB (gratis) intenta primero los ATP/WTA Tour;
RapidAPI ATP/WTA/ITF y tennisapi1 solo se consultan si el anterior
falla, y los `missed` definitivos impiden re-quemar llamadas en
reintentos. Si la cuota diaria se agota a mitad, el proveedor se marca
rate-limited y el resto queda para la siguiente ejecución.

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_backlog.py            # dry-run
    .venv\\Scripts\\python.exe scripts\\verify_backlog.py --apply    # escribe
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from datetime import timedelta

from app.services.maintenance.backlog import run_backlog_verification


async def main(apply: bool, delay: float, older_than_days: float | None) -> None:
    stats = await run_backlog_verification(
        apply=apply,
        delay=delay,
        older_than=(
            timedelta(days=older_than_days) if older_than_days is not None else None
        ),
    )
    if not apply:
        print(
            f"\nDRY-RUN: {stats['resueltos']} se resolverían. Usa --apply para escribir."
        )
    else:
        print(
            f"\nResueltos: {stats['resueltos']}. "
            f"Padres de combinadas re-liquidados: {stats['padres_liquidados']}."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios.")
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Segundos entre picks (ritmo suave para límites por minuto).",
    )
    parser.add_argument(
        "--older-than-days",
        type=float,
        default=14.0,
        help="Solo pendientes más viejos que N días (0 = todos).",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            args.apply,
            args.delay,
            args.older_than_days or None,
        )
    )
