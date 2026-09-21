# ruff: noqa: E402
"""Reintenta el OCR de fotos que quedaron sin `extracted_text` por 429.

La lógica vive en `app/services/maintenance/rescue.py` — el loop de
`lifecycle.py` corre la misma función periódicamente; este script es
el wrapper manual (con dry-run para inspeccionar antes de escribir).

Solo se consideran raws dentro de `rescue_max_age_days` (30 días por
defecto); `--max-age-days 0` desactiva el límite para reintentar fotos
de cualquier antigüedad (p. ej. si reapareció un fichero viejo).

Uso:
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py          # dry-run
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py --apply  # OCR real
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py --apply --limit 50
    .venv\\Scripts\\python.exe scripts\\retry_ocr.py --apply --max-age-days 0
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.maintenance.rescue import (
    _ocr_candidates,
    _rescue_cutoff,
    retry_pending_ocr,
)


def _arg_value(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


async def main() -> None:
    apply = "--apply" in sys.argv
    limit = int(v) if (v := _arg_value("--limit")) else None
    max_age = float(v) if (v := _arg_value("--max-age-days")) else None

    settings = get_settings()
    if apply and not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return
    print(f"MODO: {'APLICAR' if apply else 'DRY-RUN'}")

    if not apply:
        cutoff = _rescue_cutoff(settings, max_age)
        async with AsyncSessionLocal() as session:
            raws, pendientes = await _ocr_candidates(session, cutoff)
        if limit:
            pendientes = pendientes[:limit]
        print(
            f"Fotos sin OCR dentro de la ventana: {len(raws)} "
            f"({len(pendientes)} con fichero, {len(raws) - len(pendientes)} sin fichero)"
        )
        for r in pendientes[:20]:
            print(f"  msg {r.message_id} {r.channel_name} {r.received_at}")
        return

    resumen = await retry_pending_ocr(limit=limit, max_age_days=max_age)
    print(
        f"Fotos sin OCR con fichero presente: {resumen['pendientes']} "
        f"({resumen['sin_fichero']} sin fichero, se saltan)"
    )
    print(f"Resumen: {resumen['hechos']} OCR completados, {resumen['fallos']} fallos.")


if __name__ == "__main__":
    asyncio.run(main())
