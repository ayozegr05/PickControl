# ruff: noqa: E402
"""Convierte los ParsedPick etiquetados como combinada en padre + patas.

Uso:
    .venv\\Scripts\\python.exe scripts\\backfill_combinadas.py           # dry-run
    .venv\\Scripts\\python.exe scripts\\backfill_combinadas.py --apply   # escribe

Reprocesa el mensaje CRUDO de cada candidato con el extractor nuevo (no
parte el `seleccion` a ciegas): si salen >=2 patas, el registro se
convierte en el padre de la combinada y se crean sus patas (self-FK);
si el extractor nuevo ve un pick simple o no-apuesta, el candidato se
reclasifica (los falsos positivos antiguos se limpian solos).

Idempotente: tras la conversión el padre queda `es_combinada=True` y el
reclasificado cambia de `mercado`, así que ambos salen del filtro de
candidatos en la siguiente ejecución. Los raws nunca se tocan.
"""

import asyncio
import os
import sys

# Permite importar `app` cuando se ejecuta desde scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import extract_pick


async def main() -> None:
    apply_changes = "--apply" in sys.argv
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return

    async with AsyncSessionLocal() as session:
        candidates = list(
            (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.mercado.ilike("%combinad%"))
                    .where(ParsedPick.es_combinada == False)  # noqa: E712
                    .where(ParsedPick.combinada_id == None)  # noqa: E711
                    .order_by(ParsedPick.id)
                )
            )
            .scalars()
            .all()
        )
        print(f"Candidatos etiquetados como combinada: {len(candidates)}")

        convertidas = reclasificadas = saltadas = 0
        for parent in candidates:
            # Red de seguridad anti-reEjecución: si ya tiene patas, solo
            # falta marcar el padre.
            legs = list(
                (
                    await session.exec(
                        select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
                    )
                )
                .scalars()
                .all()
            )
            if legs:
                parent.es_combinada = True
                session.add(parent)
                convertidas += 1
                print(f"  [{parent.id}] ya tenía {len(legs)} patas, se marca padre.")
                continue

            raw = await session.get(TelegramRawMessage, parent.raw_message_id)
            source_text = (
                ((raw.extracted_text or raw.text or "").strip()) if raw else ""
            )
            if not source_text:
                saltadas += 1
                print(f"  [{parent.id}] raw sin texto, salto.")
                continue

            pick = await extract_pick(
                source_text,
                settings.openai_api_key,
                informante=(raw.channel_name if raw else parent.informante),
                fecha_referencia=(raw.received_at if raw else None),
            )
            if pick is None:
                saltadas += 1
                print(f"  [{parent.id}] extractor devolvió None, salto.")
                continue

            if pick.es_apuesta and len(pick.patas) >= 2:
                # El registro existente se convierte en el PADRE: así se
                # conserva su id (y cualquier verificación manual que
                # tuviera) y las patas cuelgan de él.
                parent.es_combinada = True
                parent.mercado = "combinada"
                parent.seleccion = pick.seleccion or parent.seleccion
                parent.apuesta = parent.seleccion
                parent.cuota = pick.cuota or parent.cuota
                parent.stake = pick.stake or parent.stake
                parent.casa = pick.casa or parent.casa
                parent.deporte = pick.deporte or parent.deporte
                parent.evento = pick.evento or parent.evento
                parent.fecha_evento = (
                    pick.fecha_evento
                    or parent.fecha_evento
                    or (raw.received_at if raw else None)
                )
                parent.metodo = pick.metodo
                parent.confianza = pick.confianza
                session.add(parent)
                await session.flush()

                for orden, pata in enumerate(pick.patas):
                    session.add(
                        ParsedPick(
                            raw_message_id=parent.raw_message_id,
                            informante_id=parent.informante_id,
                            combinada_id=parent.id,
                            orden=orden,
                            es_apuesta=True,
                            es_reto=parent.es_reto,
                            apuesta=pata.seleccion,
                            deporte=pata.deporte or parent.deporte,
                            evento=pata.evento or parent.evento,
                            mercado=pata.mercado,
                            seleccion=pata.seleccion,
                            cuota=pata.cuota,
                            casa=parent.casa,
                            informante=parent.informante,
                            fecha_evento=pata.fecha_evento or parent.fecha_evento,
                            linea=pata.linea,
                            metodo=pick.metodo,
                            confianza=pick.confianza,
                        )
                    )
                convertidas += 1
                print(
                    f"  [{parent.id}] -> combinada con {len(pick.patas)} patas "
                    f"(cuota {parent.cuota})"
                )
            elif pick.es_apuesta:
                # Falso positivo antiguo: era un pick simple, no una
                # combinada. Se reclasifica con lo que dice el extractor.
                parent.mercado = pick.mercado
                parent.seleccion = pick.seleccion or parent.seleccion
                parent.apuesta = parent.seleccion
                parent.deporte = pick.deporte or parent.deporte
                parent.evento = pick.evento or parent.evento
                parent.cuota = pick.cuota or parent.cuota
                parent.metodo = pick.metodo
                parent.confianza = pick.confianza
                session.add(parent)
                reclasificadas += 1
                print(f"  [{parent.id}] -> reclasificado como simple")
            else:
                # El extractor nuevo ni siquiera lo ve apuesta.
                parent.es_apuesta = False
                parent.mercado = None
                session.add(parent)
                reclasificadas += 1
                print(f"  [{parent.id}] -> no es apuesta (es_apuesta=False)")

        print(
            f"\nResumen: {convertidas} combinadas, {reclasificadas} "
            f"reclasificadas, {saltadas} saltadas."
        )
        if apply_changes:
            await session.commit()
            print("Cambios aplicados.")
        else:
            await session.rollback()
            print("DRY-RUN: nada escrito. Relanza con --apply para aplicar.")


if __name__ == "__main__":
    asyncio.run(main())
