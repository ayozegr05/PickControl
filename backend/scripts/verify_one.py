# ruff: noqa: E402
"""Verifica UN pick pendiente por id (aplica el resultado en BD).

Mismo camino que el barrido de backlog (`verify_pick` + escritura +
`_settle_combinadas`), pero dirigido a un solo pick — útil tras un fix
del verificador sin quemar llamadas en el resto del backlog.

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_one.py 566 [--dry]
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.results.verifier import (
    _get_providers,
    _settle_combinadas,
    verify_pick,
)


async def main(pick_id: int, apply: bool) -> None:
    providers = await _get_providers()
    async with AsyncSessionLocal() as session:
        pick = await session.get(ParsedPick, pick_id)
        if pick is None:
            print(f"Pick {pick_id} no existe")
            return
        print(
            f"Pick {pick.id}: '{pick.seleccion}' · {pick.evento} · "
            f"{pick.fecha_evento} · {pick.mercado} linea={pick.linea}"
        )
        acierto, anulada = await verify_pick(pick, providers)
        tag = (
            "ANULADA"
            if anulada
            else (
                "PENDIENTE" if acierto is None else ("ACIERTO" if acierto else "FALLO")
            )
        )
        print(f"  -> {tag} (evento final: '{pick.evento}')")
        if apply and (acierto is not None or anulada):
            pick.acierto = acierto
            pick.anulada = anulada
            pick.verificado_por = "auto"
            pick.verificado_at = utc_now()
            session.add(pick)
            settled = await _settle_combinadas(session)
            await session.commit()
            print(f"  aplicado; padres liquidados: {[p.id for p in settled]}")


if __name__ == "__main__":
    pid = int(sys.argv[1])
    asyncio.run(main(pid, apply="--dry" not in sys.argv))
