# ruff: noqa: E402
"""Reintenta el OCR de fotos que quedaron sin `extracted_text` por 429.

La lógica vive en `app/services/maintenance/rescue.py` — el loop de
`lifecycle.py` corre la misma función periódicamente; este script es
el wrapper manual (con dry-run para inspeccionar antes de escribir).

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
from app.models.parsed_pick import ParsedPick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.maintenance.rescue import _media_path, retry_pending_ocr


async def _list_pendientes(limit) -> list[TelegramRawMessage]:
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
        return pendientes[:limit] if limit else pendientes


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

    if not apply:
        pendientes = await _list_pendientes(limit)
        print(f"Fotos sin OCR con fichero presente: {len(pendientes)}")
        for r in pendientes[:20]:
            print(f"  msg {r.message_id} {r.channel_name} {r.received_at}")
        return

    resumen = await retry_pending_ocr(limit=limit)
    print(f"Fotos sin OCR con fichero presente: {resumen['pendientes']}")
    print(f"Resumen: {resumen['hechos']} OCR completados, {resumen['fallos']} fallos.")


if __name__ == "__main__":
    asyncio.run(main())
