"""Re-verificación read-only de las filas `llm-odds`/`{motor}-odds`.

Para cada snapshot escrito por el pipeline web+LLM:

- Si conocemos la URL fuente (mapa /tmp/llm_odds_map.json generado
  desde summaries_history): se re-descarga la página y se re-executa
  `_closing_from_text` con la tripleta de la fila — la MISMA barrera
  determinista de producción, sin llamada LLM.
- Si no hay URL registrada (pasadas viejas fuera del historial): se
  re-lanza `find_closing_odds` sobre el pick y se compara la cuota.

NO escribe en BD ni toca marcas. Imprime un veredicto por fila.

Uso (dentro del contenedor backend):
    python /tmp/verify_llm_odds.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlmodel import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.odds_snapshot import OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.odds.historical_backfill import _market_desc
from app.services.odds.snapshotter import _canonical_sport, _lookup_hint
from app.services.results.gemini_research import GeminiResearchProvider

_PROVIDERS = (
    "llm-odds",
    "groq-odds",
    "cerebras-odds",
    "openrouter-odds",
    "gemini-odds",
)


def _provider(sport: str, cache: dict, settings) -> GeminiResearchProvider:
    if sport not in cache:
        cache[sport] = GeminiResearchProvider(sport, settings.google_api_key)
    return cache[sport]


async def main() -> None:
    try:
        urlmap = {
            int(k): v for k, v in json.load(open("/tmp/llm_odds_map.json")).items()
        }
    except OSError:
        urlmap = {}
    settings = get_settings()
    cache: dict = {}
    async with AsyncSessionLocal() as session:
        rows = (
            await session.exec(
                select(OddsSnapshot, ParsedPick)
                .where(OddsSnapshot.provider.in_(_PROVIDERS))
                .where(OddsSnapshot.parsed_pick_id == ParsedPick.id)
            )
        ).all()
    print(f"{len(rows)} filas a verificar\n", flush=True)
    for snap, pick in rows:
        sport = _canonical_sport(pick.deporte) or "football"
        prov = _provider(sport, cache, settings)
        data = {
            "market_name": snap.market_name,
            "choice_name": snap.choice_name,
            "choice_group": snap.choice_group,
            "cuota_decimal": snap.cuota,
            "cuota_texto": f"{snap.cuota:.2f}",
            "casa": "",
        }
        tag = (
            f"pick {pick.id} [{snap.provider}] {snap.market_name}/"
            f"{snap.choice_name} {snap.choice_group or ''} @{snap.cuota}"
        )
        entry = urlmap.get(pick.id)
        if entry:
            text = await prov._fetch_page_text(entry["url"])
            if not text:
                print(f"{tag} -> FETCH-FAIL {entry['url']}", flush=True)
                continue
            found = prov._closing_from_text(text, data, entry["url"])
            verdict = "OK" if found else "FAIL"
            extra = "" if found else " (literal no revalida en la página)"
            print(f"{tag} -> {verdict}{extra}", flush=True)
            continue
        # Sin URL registrada: re-extracción completa (cuesta llamadas
        # al extractor vivo — por eso solo para filas sin URL).
        hint = _lookup_hint(pick, sport) or ""
        try:
            again = await prov.find_closing_odds(
                pick.fecha_evento, hint, _market_desc(pick)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{tag} -> ERROR reextract: {exc}", flush=True)
            continue
        if again is None:
            print(f"{tag} -> NO-RESULT (pipeline no reencuentra)", flush=True)
        elif abs(again.cuota - snap.cuota) <= 0.02:
            print(
                f"{tag} -> OK-REEXTRACT ({again.extractor or '?'} "
                f"via {again.source_url})",
                flush=True,
            )
        else:
            print(
                f"{tag} -> MISMATCH reextract {again.cuota} "
                f"({again.extractor or '?'} via {again.source_url})",
                flush=True,
            )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
