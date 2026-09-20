"""Migra picks legados `mercado="combinada"` sin patas a padre + patas.

Los primeros picks de combinada se guardaron como una sola fila con la
selección unida por " + " (`es_combinada=False`, `combinada_id=NULL`):
el verificador los salta a propósito (una combinada no se resuelve con
un solo marcador) y quedaban pendientes para siempre.

Este script reutiliza `_patas_from_joined`/`_ensure_combinada_shape`
del extractor — la misma red de seguridad que parte el "A + B + C" del
LLM hoy — para convertir el pick en el PADRE (`es_combinada=True`) y
crear cada pata como fila hija (`combinada_id`, `orden`). Las patas
entran en la verificación normal y el padre se liquida con
`settle_combinada`.

Reglas de seguridad:
- Menos de 2 patas parseables -> se deja como está (basura de
  extracción tipo "263 + 105" queda para revisión manual).
- Si los eventos no se pueden alinear con las patas, la pata queda
  con `evento=NULL` en vez de heredar el evento unido (que casaría
  mal): se resuelve por su propia selección o queda pendiente.
- Cuota por pata solo si el slip la traía (casi nunca): NULL, así
  `cuota_efectiva` no se inventa.
- Idempotente: el pick migrado pasa a `es_combinada=True` y sale de
  la consulta.

    .\\.venv\\Scripts\\python.exe scripts\\backfill_combinada_legs.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\backfill_combinada_legs.py --apply    # escribe
"""

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401  (FKs de TelegramRawMessage)
from app.services.telegram.pick_extractor import (
    ExtractedPick,
    _ensure_combinada_shape,
)

# Líneas de slip que la clasificación por palabras no etiqueta:
# "Más 1.5 Goles totales" (sin "de"), "2+ faltas", "20 o más juegos".
_LEG_OU_HINT = re.compile(
    r"m[aá]s\s+(?:de\s+)?\d|menos\s+(?:de\s+)?\d|\d+\s*(?:\+|o\s+m[aá]s)|"
    r"\bover\b|\bunder\b",
    re.IGNORECASE,
)
_LEG_AT_LEAST = re.compile(r"(\d+)\s*(?:\+|o\s+m[aá]s)", re.IGNORECASE)


def _polish_leg(pata: ExtractedPick) -> None:
    """Mercado/línea por defecto en patas que el clasificador dejó
    vacías: "Más 1.5 Goles totales" -> over/under 1.5; un nombre suelto
    ("Juventus") dentro de una combinada solo puede ser "gana"."""
    sel = pata.seleccion or ""
    if pata.mercado in (None, "jugador") and _LEG_OU_HINT.search(sel):
        pata.mercado = "over/under"
    if pata.mercado == "over/under":
        at_least = _LEG_AT_LEAST.search(sel)
        if at_least and (pata.linea is None or pata.linea < 0):
            # "Romero - 2+ faltas" parseaba línea -2.0: N+ = over N-0.5.
            pata.linea = int(at_least.group(1)) - 0.5
        elif pata.linea is None:
            m = re.search(
                r"(?:m[aá]s|menos)\s+(?:de\s+)?(\d+(?:[.,]\d+)?)",
                sel,
                re.IGNORECASE,
            )
            if m:
                pata.linea = float(m.group(1).replace(",", "."))
    pata.mercado = pata.mercado or "ganador"


def _legacy_to_shim(pick: ParsedPick) -> ExtractedPick:
    """El pick legado como ExtractedPick para reusar el parser de patas."""
    return ExtractedPick(
        es_apuesta=True,
        deporte=pick.deporte,
        evento=pick.evento,
        mercado=pick.mercado,
        seleccion=pick.seleccion,
        fecha_evento=pick.fecha_evento,
        metodo=pick.metodo,
        confianza=pick.confianza,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Escribe los cambios (por defecto solo muestra el plan).",
    )
    args = parser.parse_args()

    migrated = skipped = 0
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick).where(
                ParsedPick.mercado == "combinada",
                ParsedPick.es_combinada == False,  # noqa: E712
                ParsedPick.combinada_id == None,  # noqa: E711
                ParsedPick.acierto == None,  # noqa: E711
            )
        )
        legacies = result.all()
        print(f"{len(legacies)} picks legados mercado='combinada' sin patas.\n")

        for pick in legacies:
            shim = _ensure_combinada_shape(_legacy_to_shim(pick), pick.fecha_evento)
            if len(shim.patas) < 2:
                skipped += 1
                print(
                    f"[SKIP] id={pick.id} sin >=2 patas parseables: "
                    f"{(pick.seleccion or '')[:70]!r}"
                )
                continue

            migrated += 1
            print(
                f"[OK] id={pick.id} -> {len(shim.patas)} patas  "
                f"cuota={pick.cuota} stake={pick.stake}"
            )
            for orden, pata in enumerate(shim.patas):
                _polish_leg(pata)
                # El evento unido "A - B + C - D" no es un partido: si la
                # pata no consiguió evento propio, mejor NULL que un cruce
                # envenenado que casaría mal.
                evento = (
                    pata.evento if pata.evento and " + " not in pata.evento else None
                )
                print(
                    f"     pata {orden}: sel={(pata.seleccion or '')[:55]!r} "
                    f"ev={(evento or '')[:40]!r} mercado={pata.mercado} "
                    f"linea={pata.linea}"
                )
                if args.apply:
                    session.add(
                        ParsedPick(
                            raw_message_id=pick.raw_message_id,
                            informante_id=pick.informante_id,
                            informante=pick.informante,
                            es_apuesta=True,
                            deporte=pata.deporte or pick.deporte,
                            evento=evento,
                            mercado=pata.mercado,
                            seleccion=pata.seleccion,
                            linea=pata.linea,
                            cuota=pata.cuota,  # casi siempre None: no se inventa
                            es_reto=pick.es_reto,
                            fecha_evento=pata.fecha_evento or pick.fecha_evento,
                            metodo="backfill",
                            confianza=pick.confianza,
                            combinada_id=pick.id,
                            orden=orden,
                        )
                    )
            if args.apply:
                pick.es_combinada = True
                session.add(pick)

        if args.apply:
            await session.commit()
            print(f"\nCOMMIT: {migrated} combinadas migradas, {skipped} sin patas.")
        else:
            print(
                f"\nDRY-RUN: {migrated} combinadas migrables, "
                f"{skipped} sin patas. --apply para escribir."
            )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
