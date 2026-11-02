# ruff: noqa: E402
"""Pasada manual del verificador usando SOLO los providers de ESPN.

Reutiliza `verify_pending_picks` completo (mercados, combinadas, push)
pero sustituye la cascada por los tres providers ESPN — gratis, sin
cuotas. Útil para medir cobertura ESPN o rescatar backlog sin gastar
RapidAPI.

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_espn.py
    .venv\\Scripts\\python.exe scripts\\verify_espn.py --backlog   # incluye >14 días
    docker exec controlpick-backend python scripts/verify_espn.py --backlog
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import app.services.results.verifier as verifier
from app.models.informante import Informante  # noqa: F401  (FKs de ParsedPick)
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.results.espn import (
    EspnBasketballProvider,
    EspnProvider,
    EspnTennisProvider,
)


async def _espn_only():
    return [EspnProvider(), EspnTennisProvider(), EspnBasketballProvider()]


async def main(backlog: bool) -> None:
    verifier._get_providers = _espn_only
    if backlog:
        # El ciclo normal solo reintenta picks de <=14 días (con gracia
        # de 6 h para recién creados). --backlog levanta esa ventana:
        # equivale a verify_backlog.py pero sin gastar cuota RapidAPI.
        verifier._should_attempt_verification = lambda pick, now: True
    n = await verifier.verify_pending_picks()
    print(f"Pasada solo-ESPN: {n} picks verificados.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backlog",
        action="store_true",
        help="Ignora la ventana de 14 días: intenta todo el backlog.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.backlog))
