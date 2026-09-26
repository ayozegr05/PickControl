# ruff: noqa: E402
"""Pasada manual del verificador usando SOLO los providers de ESPN.

Reutiliza `verify_pending_picks` completo (mercados, combinadas, push)
pero sustituye la cascada por los tres providers ESPN — gratis, sin
cuotas. Útil para medir cobertura ESPN o rescatar backlog sin gastar
RapidAPI.

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_espn.py
    docker exec controlpick-backend python scripts/verify_espn.py
"""

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


async def main() -> None:
    verifier._get_providers = _espn_only
    n = await verifier.verify_pending_picks()
    print(f"Pasada solo-ESPN: {n} picks verificados.")


if __name__ == "__main__":
    asyncio.run(main())
