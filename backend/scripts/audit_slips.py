# ruff: noqa: E402
"""Auditoría post-liquidación: re-OCR de todas las fotos con picks vivos.

Los OCR antiguos omitían ticks y sellos de slips liquidados — los
verdes republicados (marketing de "mira la que gané") se colaron como
picks abiertos y algunos ya contaron como aciertos del tipster.

Este barrido vuelve a pasar visión por cada imagen asociada a picks
vivos (`es_apuesta=True`, pendientes y resueltos) y aplica los
detectores actuales sobre el OCR NUEVO — sin escribir nada en BD.
La salida es la lista de raws confirmados para revisar antes de
descartar con `es_apuesta=False` (reversible).

Uso:
    .venv\\Scripts\\python.exe scripts\\audit_slips.py
    .venv\\Scripts\\python.exe scripts\\audit_slips.py --limit 20
    .venv\\Scripts\\python.exe scripts\\audit_slips.py --raws 372,1833
    docker exec controlpick-backend python scripts/audit_slips.py
"""

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FKs de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.maintenance.garbage import slip_printed_date
from app.services.telegram.ocr import extract_text_from_image
from app.services.telegram.pick_extractor import _is_settled_ticket

# Cabecera de boleto en mayúsculas con cuota: >=2 en una captura =
# pantallazo de "mis apuestas" (slips de suscriptor, no pick).
_MULTI_SLIP_RE = re.compile(r"CREAR APUESTA\s+[\d.,]+")
_CELEBRATION_RE = re.compile(
    r"eres un crack|para dentro|mil gracias|apuesta.{0,15}ganada|"
    r"cobrad[oa]|premio cobrado|estamoss",
    re.IGNORECASE,
)
# Cortesía con el TPM de OpenAI — el retry ya cubre el 429.
_DELAY = 0.4


def _reasons(raw: TelegramRawMessage, new_ocr: str | None) -> list[str]:
    """Detectores sobre el OCR nuevo + el texto del mensaje."""
    full = "\n".join(t for t in (new_ocr, raw.text) if t)
    found: list[str] = []
    if _is_settled_ticket(full):
        found.append("LIQUIDADO")
    slip_date = slip_printed_date(new_ocr) or slip_printed_date(raw.text)
    if (
        slip_date is not None
        and raw.received_at is not None
        and slip_date.date() < raw.received_at.date()
    ):
        found.append(f"REPOST({slip_date:%d-%m}<{raw.received_at:%d-%m})")
    if len(_MULTI_SLIP_RE.findall(full)) >= 2:
        found.append("MULTI-SLIP")
    if _CELEBRATION_RE.search(full):
        found.append("celebracion")
    return found


async def main(limit: int | None, only_raws: list[int] | None, save_ocr: bool) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print("Sin OPENAI_API_KEY — no se puede hacer OCR.")
        return
    async with AsyncSessionLocal() as session:
        picks = list(
            (await session.exec(select(ParsedPick).where(ParsedPick.es_apuesta)))
            .scalars()
            .all()
        )
        by_raw: dict[int, list[ParsedPick]] = {}
        for p in picks:
            by_raw.setdefault(p.raw_message_id, []).append(p)
        stmt = select(TelegramRawMessage).where(
            TelegramRawMessage.id.in_(list(by_raw)),  # type: ignore[union-attr]
            TelegramRawMessage.media_path.is_not(None),  # type: ignore[union-attr]
        )
        if only_raws:
            stmt = stmt.where(TelegramRawMessage.id.in_(only_raws))  # type: ignore[union-attr]
        raws = list((await session.exec(stmt)).scalars().all())

        raws.sort(key=lambda r: r.id)
        if limit:
            raws = raws[:limit]
        print(f"PICKS VIVOS: {len(picks)} | FOTOS A RE-OCR: {len(raws)}")
        print()

        suspects = 0
        skipped = 0
        saved = 0
        for i, raw in enumerate(raws, 1):
            # La ruta guardada puede llevar separador Windows (\) si la
            # ingesta corrió en local — el contenedor usa '/'.
            path = (raw.media_path or "").replace("\\", "/")
            if not os.path.exists(path):
                skipped += 1
                continue
            new_ocr = await extract_text_from_image(path, settings.openai_api_key)
            if save_ocr and new_ocr and new_ocr != raw.extracted_text:
                # Transcripción mejor de la MISMA imagen: el prompt
                # actual sí transcribe ticks/marcadores — dejarla en el
                # raw permite que analyze_garbage detecte el slip por
                # el conducto normal.
                raw.extracted_text = new_ocr
                session.add(raw)
                saved += 1
            reasons = _reasons(raw, new_ocr)
            if reasons:
                suspects += 1
                rps = by_raw.get(raw.id, [])
                est = {"P": 0, "W": 0, "L": 0}
                for p in rps:
                    est["P" if p.acierto is None else ("W" if p.acierto else "L")] += 1
                ev = (rps[0].evento or "?")[:45] if rps else "?"
                print(
                    f"[{i}/{len(raws)}] SOSPECHOSO raw={raw.id} "
                    f"{','.join(reasons)} picks={len(rps)} "
                    f"(P{est['P']}/W{est['W']}/L{est['L']}) | {ev}"
                )
            elif i % 10 == 0:
                print(f"[{i}/{len(raws)}] limpio...")
            await asyncio.sleep(_DELAY)

        if save_ocr:
            await session.commit()

    print()
    print(
        f"FIN: {suspects} raws sospechosos, {skipped} sin fichero"
        + (f", {saved} OCR guardados" if save_ocr else "")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--raws",
        type=str,
        default=None,
        help="ids de raw separados por coma (p. ej. 372,1833)",
    )
    parser.add_argument(
        "--save-ocr",
        action="store_true",
        help="persiste el OCR nuevo en extracted_text del raw",
    )
    args = parser.parse_args()
    only = [int(x) for x in args.raws.split(",")] if args.raws else None
    asyncio.run(main(args.limit, only, args.save_ocr))
