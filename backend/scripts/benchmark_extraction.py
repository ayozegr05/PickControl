# ruff: noqa: E402
"""Benchmark de extracción: compara motores LLM contra lo ya guardado.

Para cada pick extraído en su día por OpenAI (metodo 'llm%'), vuelve a
extraer el MISMO texto con cada motor disponible (OpenAI, Groq,
Cerebras, OpenRouter, Gemini) y puntúa el acuerdo campo a campo contra
el pick almacenado. Sirve para decidir si la cascada gratuita puede
sustituir a OpenAI sin perder calidad.

Uso (dentro del contenedor o con .env cargado):
    python scripts/benchmark_extraction.py --limit 30
    python scripts/benchmark_extraction.py --limit 20 --engines groq,gemini
    python scripts/benchmark_extraction.py --ocr --limit 10
"""

import argparse
import asyncio
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.results import compat_llm
from app.services.results.base import fold_name
from app.services.telegram import ocr as ocr_mod
from app.services.telegram.openai_retry import call_with_retry
from app.services.telegram.pick_extractor import (
    _SYSTEM_PROMPT,
    ExtractedPick,
    _strip_slip_chrome,
)

logger = get_logger("scripts.benchmark_extraction")

_GEMINI_MODELS = ocr_mod._GEMINI_MODELS
_GEMINI_ENDPOINT = ocr_mod._GEMINI_ENDPOINT
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _user_content(text: str, informante: str | None, fecha) -> str:
    """Mismo user_content que arma `_llm_extract`."""
    fecha_str = fecha.strftime("%Y-%m-%d %H:%M") if fecha else "desconocida"
    return (
        f"Canal: {informante or 'desconocido'}\n"
        f"Fecha del mensaje: {fecha_str}\n\n"
        f"Mensaje:\n{text}"
    )


def _parse_pick(content: str | None) -> ExtractedPick | None:
    if not content:
        return None
    try:
        return ExtractedPick(**json.loads(_JSON_FENCE.sub("", content.strip())))
    except Exception:
        return None


async def _engine_openai(prompt: str, settings) -> str | None:
    if not settings.openai_api_key:
        return None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await call_with_retry(
            lambda: client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            ),
            "bench-openai",
        )
        return resp.choices[0].message.content
    except Exception as exc:
        return f"__ERROR__ {exc}"


async def _engine_gemini(prompt: str, settings) -> str | None:
    key = settings.google_api_key
    if not key:
        return None
    body = {
        "contents": [{"parts": [{"text": f"{_SYSTEM_PROMPT}\n\n{prompt}"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
        },
    }
    for model in _GEMINI_MODELS:
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    _GEMINI_ENDPOINT.format(model=model),
                    json=body,
                    headers={"x-goog-api-key": key},
                )
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            continue
    return None


async def _engine_compat(name: str, prompt: str, settings) -> str | None:
    ep = next((e for e in compat_llm._ENDPOINTS if e.name == name), None)
    key = getattr(settings, ep.env_key, None) if ep else None
    if not ep or not key:
        return None
    return await compat_llm._call(ep, key, f"{_SYSTEM_PROMPT}\n\n{prompt}", "bench")


# ------------------------- comparación -------------------------


def _same_float(a, b, tol: float = 0.011) -> bool | None:
    """None = no comparable (uno de los dos vacío)."""
    if a is None and b is None:
        return None
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _same_text(a, b) -> bool | None:
    if not a and not b:
        return None
    return fold_name(a or "") == fold_name(b or "")


@dataclass
class Score:
    calls: int = 0
    ok: int = 0
    errors: int = 0
    field_hits: dict[str, list[bool]] = field(default_factory=dict)
    flips_apuesta: int = 0
    diffs: list[str] = field(default_factory=list)

    def hit(self, name: str, same: bool | None):
        if same is None:
            return
        self.field_hits.setdefault(name, []).append(same)


def _compare(
    stored: ParsedPick,
    patas_stored: int,
    new: ExtractedPick | None,
    score: Score,
    pick_id: int,
):
    score.calls += 1
    if new is None:
        score.errors += 1
        return
    score.ok += 1
    score.hit("es_apuesta", stored.es_apuesta == new.es_apuesta)
    if stored.es_apuesta != new.es_apuesta:
        score.flips_apuesta += 1
    es_comb_new = bool(new.patas) or (new.mercado or "").lower() == "combinada"
    score.hit("es_combinada", bool(stored.es_combinada) == es_comb_new)
    score.hit("mercado", _same_text(stored.mercado, new.mercado))
    score.hit("seleccion", _same_text(stored.seleccion, new.seleccion))
    score.hit("evento", _same_text(stored.evento, new.evento))
    score.hit("casa", _same_text(stored.casa, new.casa))
    score.hit("cuota", _same_float(stored.cuota, new.cuota))
    score.hit("stake", _same_float(stored.stake, new.stake))
    score.hit("linea", _same_float(stored.linea, new.linea))
    if patas_stored:
        score.hit("n_patas", len(new.patas) == patas_stored)
    if stored.fecha_evento or new.fecha_evento:
        same = bool(
            stored.fecha_evento
            and new.fecha_evento
            and stored.fecha_evento.date() == new.fecha_evento.date()
        )
        score.hit("fecha_evento", same)
    if (
        stored.seleccion
        and new.seleccion
        and fold_name(stored.seleccion) != fold_name(new.seleccion)
    ):
        score.diffs.append(
            f"pick {pick_id}: sel '{stored.seleccion}' -> '{new.seleccion}'"
        )


async def _bench_picks(limit: int, engines: list[str], solo_ocr: bool):
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        picks = (
            (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.es_apuesta == True)  # noqa: E712
                    .where(ParsedPick.metodo.like("llm%"))
                    .where(ParsedPick.combinada_id.is_(None))
                    .order_by(ParsedPick.id.desc())
                    .limit(limit * 4 if solo_ocr else limit)
                )
            )
            .scalars()
            .all()
        )
        raw_ids = {p.raw_message_id for p in picks if p.raw_message_id}
        raws = (
            (
                await session.exec(
                    select(TelegramRawMessage).where(TelegramRawMessage.id.in_(raw_ids))
                )
            )
            .scalars()
            .all()
        )
        raw_by_id = {r.id: r for r in raws}
        if solo_ocr:
            # Solo picks cuyo raw pasó por el OCR de OpenAI
            # (extracted_text presente): todos los motores leen
            # exactamente el mismo texto extraído — comparación de
            # extracción honesta.
            picks = [
                p
                for p in picks
                if p.raw_message_id in raw_by_id
                and raw_by_id[p.raw_message_id].extracted_text
            ][:limit]
        n_legs: dict[int, int] = {}
        comb_parents = [p.id for p in picks if p.es_combinada and p.id]
        if comb_parents:
            leg_cids = (
                (
                    await session.exec(
                        select(ParsedPick.combinada_id).where(
                            ParsedPick.combinada_id.in_(comb_parents)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for cid in leg_cids:
                n_legs[cid] = n_legs.get(cid, 0) + 1

    scores = {e: Score() for e in engines}
    for pick in picks:
        raw = raw_by_id.get(pick.raw_message_id)
        if not raw:
            continue
        text = _strip_slip_chrome(
            "\n\n".join(t for t in (raw.text or "", raw.extracted_text or "") if t)
        )
        if not text:
            continue
        prompt = _user_content(text, raw.channel_name, raw.received_at)
        for engine in engines:
            if engine == "openai":
                content = await _engine_openai(prompt, settings)
            elif engine == "gemini":
                content = await _engine_gemini(prompt, settings)
            else:
                content = await _engine_compat(engine, prompt, settings)
            if content is None:
                scores[engine].calls += 1
                scores[engine].errors += 1
                continue
            if content.startswith("__ERROR__"):
                scores[engine].calls += 1
                scores[engine].errors += 1
                scores[engine].diffs.append(f"pick {pick.id}: {content[9:120]}")
                continue
            new = _parse_pick(content)
            _compare(
                pick, n_legs.get(pick.id or 0, 0), new, scores[engine], pick.id or 0
            )
    return scores, len(picks)


async def _bench_ocr(limit: int):
    """Compara el OCR de Gemini contra el `extracted_text` guardado
    (hecho con OpenAI). Ratio difflib por imagen."""
    settings = get_settings()
    if not settings.google_api_key:
        print("Sin GOOGLE_API_KEY — OCR Gemini no disponible")
        return
    async with AsyncSessionLocal() as session:
        raws = (
            (
                await session.exec(
                    select(TelegramRawMessage)
                    .where(TelegramRawMessage.media_path != None)  # noqa: E711
                    .where(TelegramRawMessage.extracted_text != None)  # noqa: E711
                    .order_by(TelegramRawMessage.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    ratios = []
    for raw in raws:
        path = raw.media_path
        if not os.path.exists(path):
            path = os.path.join("/app", raw.media_path or "")
        if not os.path.exists(path):
            continue
        text = await ocr_mod._ocr_gemini(path, settings.google_api_key)
        if not text:
            print(f"raw {raw.id}: gemini sin respuesta")
            continue
        ratio = difflib.SequenceMatcher(
            None, (raw.extracted_text or "").strip(), text.strip()
        ).ratio()
        ratios.append(ratio)
        print(f"raw {raw.id}: similitud OCR gemini-vs-openai = {ratio:.0%}")
    if ratios:
        print(
            f"\nOCR: {len(ratios)} imágenes, similitud media {sum(ratios)/len(ratios):.0%}"
        )


def _print_report(scores: dict[str, Score], n_picks: int):
    print(f"\n=== BENCHMARK sobre {n_picks} picks (guardados por OpenAI) ===\n")
    for engine, s in scores.items():
        if not s.calls:
            print(f"{engine}: sin llamadas (sin key)")
            continue
        print(
            f"{engine}: {s.ok}/{s.calls} JSON ok, {s.errors} errores, "
            f"{s.flips_apuesta} flips es_apuesta"
        )
        for field_name in (
            "es_apuesta",
            "es_combinada",
            "mercado",
            "seleccion",
            "evento",
            "casa",
            "cuota",
            "stake",
            "linea",
            "n_patas",
            "fecha_evento",
        ):
            vals = s.field_hits.get(field_name)
            if vals:
                print(
                    f"    {field_name:12s}: {sum(vals)}/{len(vals)} "
                    f"({sum(vals)/len(vals):.0%})"
                )
        for d in s.diffs[:8]:
            print(f"    ~ {d}")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument(
        "--engines",
        default="openai,groq,cerebras,openrouter,gemini",
        help="Motores a comparar (coma-separados)",
    )
    ap.add_argument(
        "--ocr",
        action="store_true",
        help="Benchmark de OCR (Gemini vs OpenAI guardado)",
    )
    ap.add_argument(
        "--solo-ocr",
        action="store_true",
        help="Solo picks cuyo raw pasó por el OCR de OpenAI (mismo input para todos)",
    )
    args = ap.parse_args()

    if args.ocr:
        asyncio.run(_bench_ocr(args.limit))
        return
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    scores, n = asyncio.run(_bench_picks(args.limit, engines, args.solo_ocr))
    _print_report(scores, n)


if __name__ == "__main__":
    main()
