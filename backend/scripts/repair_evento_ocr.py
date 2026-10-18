"""Repara picks con `evento` basura re-extrayendo su raw + slip emparejado.

No toca resultados a mano: corre `extract_pick` (el pipeline ya
corregido) sobre `raw.text + OCR del boleto vecino` (ventana ±10 min,
misma que `processor._PAIR_WINDOW`), actualiza los campos extraídos del
pick y resetea su liquidación (acierto/verificado_* -> NULL) para que el
verifier normal la rehaga contra el fixture correcto.

Uso:
    python scripts/repair_evento_ocr.py 5550 7605 3852          # dry-run
    python scripts/repair_evento_ocr.py 5550 7605 3852 --apply  # aplica
"""

import asyncio
import os
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Permite importar `app` cuando se ejecuta desde scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.pick_extractor import (
    _fixtures_from_slip_ocr,
    extract_pick,
)
from app.services.telegram.processor import _looks_like_slip, _persist_patas

PAIR_WINDOW = timedelta(minutes=10)

# Campos extraídos que el pipeline corregido puede rellenar mejor.
PATCH_FIELDS = (
    "evento",
    "deporte",
    "mercado",
    "seleccion",
    "apuesta",
    "cuota",
    "linea",
    "casa",
    "explicacion",
    "fecha_evento",
)


async def _slip_ocr(session: AsyncSession, raw: TelegramRawMessage) -> list[str]:
    """OCR de boletos del mismo canal en la ventana de emparejado."""
    q = await session.execute(
        select(TelegramRawMessage)
        .where(TelegramRawMessage.channel_id == raw.channel_id)
        .where(TelegramRawMessage.id != raw.id)
        .where(TelegramRawMessage.extracted_text.isnot(None))  # type: ignore[union-attr]
        .where(TelegramRawMessage.received_at >= raw.received_at - PAIR_WINDOW)
        .where(TelegramRawMessage.received_at <= raw.received_at + PAIR_WINDOW)
    )
    ocrs = [c.extracted_text for c in q.scalars() if c.extracted_text]
    slips = [o for o in ocrs if _looks_like_slip(o)]
    # Si ninguno parece boleto a ojos de _looks_like_slip pero alguno
    # contiene UN fixture inequívoco, ese OCR vale igual (slips
    # minimalistas tipo "X v Y + cuota" no siempre llevan los marcadores).
    if not slips:
        slips = [o for o in ocrs if len(_fixtures_from_slip_ocr(o)) == 1]
    return slips


async def repair(session: AsyncSession, pick_id: int, apply: bool) -> bool:
    pick = await session.get(ParsedPick, pick_id)
    if pick is None:
        print(f"[{pick_id}] pick no existe")
        return False
    raw = await session.get(TelegramRawMessage, pick.raw_message_id)
    if raw is None:
        print(f"[{pick_id}] raw {pick.raw_message_id} no existe")
        return False

    ocrs = await _slip_ocr(session, raw)
    combined = "\n\n".join(filter(None, [raw.text, *ocrs])).strip()
    if not combined:
        print(f"[{pick_id}] raw sin texto ni OCR emparejable")
        return False

    settings = get_settings()
    new = await extract_pick(
        combined,
        settings.openai_api_key or "",
        informante=raw.channel_name,
        fecha_referencia=raw.received_at,
    )
    if new is None or not new.es_apuesta:
        # Sin LLM (cuota suelta en OCR sin keyword) el extractor completo
        # puede devolver None aunque el fixture esté claro en el slip.
        # Fallback determinista: si el OCR del boleto tiene UN único
        # fixture inequívoco, solo se corrige `evento` — selección/cuota
        # ya extraídas se conservan tal cual.
        fixtures = _fixtures_from_slip_ocr(combined)
        if len(fixtures) != 1:
            print(
                f"[{pick_id}] sin extracción (metodo={new and new.metodo}) "
                f"y sin fixture único ({fixtures})"
            )
            return False
        print(
            f"[{pick_id}] fallback determinista: ev={pick.evento!r} "
            f"-> {fixtures[0]!r} (resto intacto)"
        )
        if not apply:
            return True
        pick.evento = fixtures[0]
        pick.acierto = None
        pick.anulada = False
        pick.motivo_anulada = None
        pick.verificado_por = None
        pick.verificado_provider = None
        pick.verificado_at = None
        session.add(pick)
        print("    -> aplicado")
        return True

    print(f"[{pick_id}] raw={raw.id} slips={len(ocrs)} metodo={new.metodo}")
    print(f"    viejo: ev={pick.evento!r} sel={pick.seleccion!r} cuota={pick.cuota}")
    print(
        f"    nuevo: ev={new.evento!r} sel={new.seleccion!r} cuota={new.cuota} "
        f"patas={len(new.patas)}"
    )
    if not apply:
        return True

    changed = False
    for field in PATCH_FIELDS:
        val = getattr(new, field, None)
        if val not in (None, "") and val != getattr(pick, field):
            setattr(pick, field, val)
            changed = True
    # Reabre la liquidación: el verifier re-resuelve contra el fixture
    # corregido. Nada se liquida a mano.
    pick.acierto = None
    pick.anulada = False
    pick.motivo_anulada = None
    pick.verificado_por = None
    pick.verificado_provider = None
    pick.verificado_at = None
    pick.metodo = new.metodo
    pick.confianza = new.confianza

    if len(new.patas) >= 2 and not pick.es_combinada:
        pick.es_combinada = True
        await session.flush()
        await _persist_patas(
            session,
            pick,
            new,
            pick.informante_id or 0,
            raw.channel_name,
            pick.es_reto,
        )
        print(f"    -> combinada con {len(new.patas)} patas creadas")
    session.add(pick)
    print(f"    -> aplicado (campos nuevos: {changed})")
    return True


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    apply = "--apply" in sys.argv
    ids = [int(a) for a in sys.argv[1:] if a.isdigit()]
    if not ids:
        print(__doc__)
        return
    async with AsyncSessionLocal() as session:
        for pid in ids:
            try:
                await repair(session, pid, apply)
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                print(f"[{pid}] ERROR — se continúa con el siguiente")
        if apply:
            await session.commit()
            print("COMMIT hecho.")
        else:
            print("DRY-RUN (sin cambios). Añade --apply para aplicar.")


if __name__ == "__main__":
    asyncio.run(main())
