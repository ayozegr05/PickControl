# ruff: noqa: E402
"""Re-descarga de Telegram el media de raws que quedaron sin fichero.

Orígenes cubiertos (que `retry_pending_ocr` no ve porque exige fichero
en disco):

- Fotos cuyo archivo se perdió del disco (`media_path` apunta a algo
  que ya no existe).
- Documentos-imagen nunca descargados (`media_path` NULL + sin texto):
  hasta nov-2026 el handler solo reconocía MessageMediaPhoto.

Por cada raw candidato pide el mensaje a Telegram (batches por canal),
y si trae foto o documento-imagen lo descarga + OCR vía
`fetch_message_content`. El raw queda `processed=False` para que el
ciclo de rescate normal extraiga el pick — nada se resuelve a mano.

Uso (en la VM, dentro del contenedor o con la venv del backend):

    python scripts/rescue_missing_media.py --dry-run           # solo cuenta
    python scripts/rescue_missing_media.py --apply             # re-descarga
    python scripts/rescue_missing_media.py --apply --days 90 --limit 500

La sesión de Telethon es SQLite (un solo cliente): para correrlo con el
backend vivo, copiar la sesión a otro nombre y pasarlo por env:

    cp controlpick_telegram_new.session /tmp/rescue.session
    TELEGRAM_SESSION_NAME=/tmp/rescue python scripts/rescue_missing_media.py --apply
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from collections import defaultdict

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.maintenance.rescue import (
    _rescue_cutoff,
    missing_media_candidates,
)
from app.services.telegram.catchup import _resolve_channel
from app.services.telegram.client import get_telegram_client, reset_telegram_client
from app.services.telegram.handlers import fetch_message_content

_BATCH = 50


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="re-descarga de verdad")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta (default)")
    parser.add_argument("--days", type=float, default=None, help="ventana en días")
    parser.add_argument("--limit", type=int, default=None, help="tope de raws")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    settings = get_settings()
    cutoff = _rescue_cutoff(settings, args.days)

    async with AsyncSessionLocal() as session:
        candidates = await missing_media_candidates(session, cutoff)
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Candidatos sin fichero: {len(candidates)}")
    por_canal: dict[int, list[TelegramRawMessage]] = defaultdict(list)
    for raw in candidates:
        por_canal[raw.channel_id].append(raw)
    for channel_id, raws in por_canal.items():
        print(f"  canal {channel_id} ({raws[0].channel_name}): {len(raws)} raws")
    if not apply:
        print("\nDry-run: sin descargas. Repite con --apply para re-descargar.")
        return

    client = get_telegram_client()
    await client.start(phone=settings.telegram_phone)
    stats = {"descargados": 0, "sin_media": 0, "fallos": 0, "no_encontrados": 0}
    try:
        for channel_id, raws in por_canal.items():
            entity, resolved_id = await _resolve_channel(client, str(channel_id))
            if entity is None:
                print(f"canal {channel_id}: no resoluble, salto {len(raws)} raws.")
                stats["fallos"] += len(raws)
                continue

            ids = [r.message_id for r in raws]
            by_id = {r.message_id: r for r in raws}
            async with AsyncSessionLocal() as session:
                for i in range(0, len(ids), _BATCH):
                    batch_ids = ids[i : i + _BATCH]
                    batch = await client.get_messages(entity, ids=batch_ids)
                    for j, message in enumerate(batch):
                        raw = by_id.pop(batch_ids[j], None)
                        if message is None or raw is None:
                            stats["no_encontrados"] += 1
                            continue
                        if not getattr(message, "media", None):
                            stats["sin_media"] += 1
                            continue
                        try:
                            text, media_path, extracted_text = (
                                await fetch_message_content(client, message)
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"  msg {message.id}: fallo descarga: {exc}")
                            stats["fallos"] += 1
                            continue
                        if media_path is None:
                            stats["sin_media"] += 1  # poll/vídeo/documento no imagen
                            continue
                        if text and not (raw.text or "").strip():
                            raw.text = text
                        raw.media_path = media_path
                        raw.extracted_text = extracted_text
                        raw.processed = False
                        session.add(raw)
                        stats["descargados"] += 1
                await session.commit()
            # ids que Telegram no devolvió (borrados) quedan en by_id.
            stats["no_encontrados"] += len(by_id)
            print(f"canal {channel_id}: {stats}")
    finally:
        await client.disconnect()
        reset_telegram_client()

    print(f"\nResumen: {stats}")
    print("Los raws re-descargados quedan processed=False: el ciclo de")
    print("rescate los reprocesa solo en la próxima pasada.")


if __name__ == "__main__":
    asyncio.run(main())
