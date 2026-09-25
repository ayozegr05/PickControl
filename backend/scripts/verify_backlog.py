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

from sqlalchemy import select

from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.results.verifier import (
    _get_providers,
    _settle_combinadas,
    verify_pick,
)


async def main(apply: bool, delay: float) -> None:
    providers = await _get_providers()
    if not providers:
        print("Sin API keys de resultados configuradas.")
        return

    async with AsyncSessionLocal() as session:
        # Mismo conjunto que verify_pending_picks pero SIN el filtro de
        # edad: es justo lo que el ciclo normal ya no reintenta.
        result = await session.execute(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
            .where(ParsedPick.es_combinada == False)  # noqa: E712
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
        )
        pending = list(result.scalars().all())
        # Recientes primero, igual que el ciclo normal.
        pending.sort(key=lambda p: p.fecha_evento, reverse=True)
        print(f"Pendientes totales a intentar: {len(pending)}")

        resolved = 0
        for i, pick in enumerate(pending):
            if i:
                # Ritmo suave: un barrido de ~80 picks a toda velocidad
                # dispara los límites por minuto de las APIs gratuitas
                # (TheSportsDB ~30 req/min, football-data ~10 req/min).
                await asyncio.sleep(delay)
            acierto, anulada = await verify_pick(pick, providers)
            if acierto is None and not anulada:
                continue
            resolved += 1
            tag = "ANULADA" if anulada else ("ACIERTO" if acierto else "FALLO")
            print(f"  id={pick.id} '{pick.seleccion}' -> {tag}")
            if apply:
                pick.acierto = acierto
                pick.anulada = anulada
                pick.verificado_por = "auto"
                pick.verificado_at = utc_now()
                session.add(pick)

        if apply:
            settled = await _settle_combinadas(session)
            await session.commit()
            print(
                f"\nResueltos: {resolved}. "
                f"Padres de combinadas re-liquidados: {len(settled)}."
            )
        else:
            print(f"\nDRY-RUN: {resolved} se resolverían. Usa --apply para escribir.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios.")
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Segundos entre picks (ritmo suave para límites por minuto).",
    )
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.delay))
