# ruff: noqa: E402
"""Reclasifica ParsedPicks que la heurística antigua descartó sin llegar
al extractor (metodo='rejected').

Uso:
    .venv\\Scripts\\python.exe scripts\\reclassify_rejected.py
    .venv\\Scripts\\python.exe scripts\\reclassify_rejected.py --apply

Sin --apply es un dry-run: solo re-evalúa el filtro nuevo (regex,
sin llamadas a OpenAI) y lista qué mensajes pasarían. Con --apply llama
a extract_pick (reglas + fallback LLM) y, si el pick resulta ser una
apuesta real, actualiza el ParsedPick existente respetando la
deduplicación por canal y ventana de 6h.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import _looks_like_bet, extract_pick
from app.services.telegram.processor import _find_duplicate_pick, _merge_pick_data


async def main() -> None:
    apply = "--apply" in sys.argv
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick, TelegramRawMessage)
            .join(
                TelegramRawMessage,
                ParsedPick.raw_message_id == TelegramRawMessage.id,
            )
            .where(
                ParsedPick.es_apuesta == False,  # noqa: E712
                ParsedPick.metodo == "rejected",
            )
            .order_by(TelegramRawMessage.received_at.asc())
        )
        candidates = result.all()

        print(f"Rechazados por heurística: {len(candidates)}")

        # Fase 1 (gratis): re-evaluar solo el filtro, sin llamar al LLM.
        passing = [
            (parsed, raw)
            for parsed, raw in candidates
            if _looks_like_bet((raw.extracted_text or raw.text or "").strip())
        ]
        print(f"Pasan el filtro nuevo: {len(passing)}")
        for parsed, raw in passing:
            preview = (raw.extracted_text or raw.text or "")[:80].replace("\n", " / ")
            print(f"  msg {raw.message_id} [{raw.channel_name}]: {preview}")

        if not apply:
            print(
                "\nDry-run: no se ha llamado a OpenAI ni modificado la BD. "
                "Repite con --apply para reclasificar de verdad."
            )
            return

        if not settings.openai_api_key:
            print("OPENAI_API_KEY no está configurada.")
            return

        # Fase 2: extracción real (reglas + fallback LLM) y actualización.
        updated = duplicated = still_not_bet = 0
        for parsed, raw in passing:
            source_text = (raw.extracted_text or raw.text or "").strip()
            pick = await extract_pick(
                source_text,
                settings.openai_api_key,
                informante=raw.channel_name,
                fecha_referencia=raw.received_at,
            )
            if not pick or not pick.es_apuesta:
                still_not_bet += 1
                continue

            if pick.fecha_evento is None and raw.received_at is not None:
                pick.fecha_evento = raw.received_at

            duplicate = await _find_duplicate_pick(session, raw.channel_name, pick)
            if duplicate:
                duplicated += 1
                print(
                    f"  msg {raw.message_id}: duplicado de ParsedPick "
                    f"id={duplicate.id} ('{pick.seleccion}'), se queda rejected."
                )
                if _merge_pick_data(duplicate, pick):
                    session.add(duplicate)
                continue

            parsed.es_apuesta = True
            parsed.apuesta = pick.seleccion
            parsed.deporte = pick.deporte
            parsed.evento = pick.evento
            parsed.mercado = pick.mercado
            parsed.seleccion = pick.seleccion
            parsed.cuota = pick.cuota
            parsed.stake = pick.stake
            parsed.casa = pick.casa
            parsed.explicacion = pick.explicacion
            parsed.fecha_evento = pick.fecha_evento
            parsed.linea = pick.linea
            parsed.metodo = pick.metodo
            parsed.confianza = pick.confianza
            session.add(parsed)
            updated += 1
            print(
                f"  msg {raw.message_id}: RECLASIFICADO -> '{pick.seleccion}' "
                f"@ {pick.cuota} stake {pick.stake} ({pick.metodo})"
            )

        await session.commit()
        print(
            f"\nReclasificación completada: {updated} picks recuperados, "
            f"{duplicated} duplicados, {still_not_bet} siguen sin ser apuesta."
        )


if __name__ == "__main__":
    asyncio.run(main())
