# ruff: noqa: E402
"""Reintenta el OCR de fotos que quedaron sin `extracted_text` por 429.

Recorre los `telegram_raw_messages` con `media_path` pero sin OCR,
llama a `extract_text_from_image` (que ya usa `call_with_retry` ante
429) y guarda el resultado. Las fotos sin ParsedPick se marcan
`processed=False` para que `reprocess_raw.py` las recoja después.

Uso:
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py          # dry-run
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py --apply  # OCR real
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py --apply --limit 50
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
from app.services.telegram.ocr import extract_text_from_image


def _media_path(media_path: str) -> str:
    """`media_path` se guarda relativo con '/' y '\\' mezclados."""
    return os.path.normpath(os.path.join(os.getcwd(), media_path))


async def main() -> None:
    apply = "--apply" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    settings = get_settings()
    if apply and not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return
    print(f"MODO: {'APLICAR' if apply else 'DRY-RUN'}")

    async with AsyncSessionLocal() as session:
        raws = (
            (
                await session.exec(
                    select(TelegramRawMessage)
                    .where(TelegramRawMessage.media_path != None)  # noqa: E711
                    .order_by(TelegramRawMessage.received_at.desc())  # type: ignore[arg-type]
                )
            )
            .scalars()
            .all()
        )
        pendientes = [
            r
            for r in raws
            if not (r.extracted_text or "").strip()
            and r.media_path
            and os.path.exists(_media_path(r.media_path))
        ]
        if limit:
            pendientes = pendientes[:limit]
        print(f"Fotos sin OCR con fichero presente: {len(pendientes)}")
        if not apply:
            for r in pendientes[:20]:
                print(f"  msg {r.message_id} {r.channel_name} {r.received_at}")
            return

        hechos = fallos = 0
        for raw in pendientes:
            ruta = _media_path(raw.media_path)
            try:
                texto = await extract_text_from_image(ruta, settings.openai_api_key)
            except Exception as exc:  # noqa: BLE001
                print(f"  msg {raw.message_id}: ERROR {exc}")
                fallos += 1
                continue
            if not (texto or "").strip():
                print(f"  msg {raw.message_id}: OCR vacío, salto.")
                fallos += 1
                continue

            raw.extracted_text = texto
            # Si el raw nunca llegó a generar pick, se deja pendiente
            # para que `reprocess_raw.py` lo extraiga con el OCR nuevo.
            tiene_pick = (
                await session.exec(
                    select(ParsedPick.id).where(ParsedPick.raw_message_id == raw.id)
                )
            ).first()
            if not tiene_pick:
                raw.processed = False
            session.add(raw)
            await session.commit()
            hechos += 1
            print(
                f"  msg {raw.message_id} ({raw.channel_name}): OCR ok "
                f"({len(texto)} chars)"
            )

        print(f"\nResumen: {hechos} OCR completados, {fallos} fallos.")


if __name__ == "__main__":
    asyncio.run(main())
