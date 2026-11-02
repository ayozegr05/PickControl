# ruff: noqa: E402
"""Backfill masivo del emparejado foto-boleto + texto.

Recorre TODOS los mensajes de texto con un pick simple real que esté
incompleto — cuota/stake NULL o `evento` que no es un cruce
("TENIS - Copa davis", "España Segunda División")— y, si existe un
boleto pareja en la ventana `_PAIR_WINDOW`, re-extrae sobre
`texto + OCR` combinados y enriquece el pick (misma lógica que el
pipeline en vivo).

También elimina el duplicado real cuando la foto y el texto crearon un
pick cada uno (caso Dm7 Gratuito). Solo toca `parsed_picks`: los raws
nunca se borran.

Uso:
    .venv\\Scripts\\python.exe scripts\\backfill_pairs.py          # dry-run
    .venv\\Scripts\\python.exe scripts\\backfill_pairs.py --apply  # escribe
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from openai import RateLimitError
from sqlalchemy import and_, select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import (
    _SELECCION_KEYWORD,
    _extract_eventos,
    _fixture_from_slip_ocr,
    extract_pick,
)
from app.services.telegram.processor import (
    _enrich_paired_pick,
    _find_pair_slip,
    _persist_patas,
)

APPLY = "--apply" in sys.argv


def _incompleto(pick: ParsedPick) -> bool:
    """El pick necesita datos que el boleto puede aportar."""
    if pick.cuota is None or pick.stake is None:
        return True
    if pick.evento is None:
        return True
    # Evento que no casa con ningún patrón de cruce: competición.
    return not _extract_eventos([pick.evento])


def _era_multi(pick: ParsedPick) -> bool:
    """La selección guardada ya expresaba dos apuestas unidas
    ('X gana + Y gana', 'X gana y +7.5 juegos'): si la nueva
    extracción no produce patas, estaríamos perdiendo una pata."""
    partes = re.split(r"\s*\+\s*|\s+y\s+", pick.seleccion or "")
    con_keyword = sum(1 for p in partes if _SELECCION_KEYWORD.search(p.lower()))
    return con_keyword >= 2


def _patas_son_reales(pick) -> bool:
    """Guarda anti-regresión: en el texto combinado el LLM a veces
    convierte la cabecera ("CHALL SZCZECIN") o el footer ("Siempre con
    cabeza") en patas de una combinada fantasma. Una pata real siempre
    lleva keyword de selección ("gana", "+7.5", "menos de"...); si
    alguna no, la extracción combinada es sospechosa y se descarta."""
    if len(pick.patas) < 2:
        return True
    return all(
        pata.seleccion and _SELECCION_KEYWORD.search(pata.seleccion.lower())
        for pata in pick.patas
    )


# Palabras de mercado que no forman parte del nombre del equipo. Sirven
# para aislar los tokens de equipo dentro de una selección.
_TEAM_WORDS_STOP = re.compile(
    r"^(?:gana|ganar[aá]?|ganador|empate|partido|encuentro|m[aá]s|menos|"
    r"over|under|c[oó]rners?|tarjetas?|goles?|juegos?|sets?|h[aá]ndicap|"
    r"handicap|asi[aá]tico|doble|oportunidad|ambos|equipos|marc[ao]n?|"
    r"asiste|s[íi]|no|stake|cuota|recibir[aá]|recibe|jugador|anotar[aá]n?|"
    r"anota|primer[ao]?|segundo[ao]?|tiempo|parte|descanso|total|"
    r"superior|inferior|gana|win|odds)$",
    re.IGNORECASE,
)


def _team_tokens(seleccion: str | None) -> list[str]:
    """Tokens de equipo (>=4 letras, no palabra de mercado) de la
    selección. "Empate o Real Sociedad" -> ["real", "sociedad"];
    "Más de 7.0 córners" -> []."""
    words = re.findall(r"[a-záéíóúñü]+", (seleccion or "").lower())
    return [w for w in words if len(w) >= 4 and not _TEAM_WORDS_STOP.fullmatch(w)]


def _seleccion_casa_con_evento(seleccion: str | None, evento: str | None) -> bool:
    """Guarda anti-mispair: si la selección nombra un equipo, TODOS sus
    tokens deben aparecer (exacto o casi: "Adalh" vs "Adahl" por OCR) en
    el evento. Si no, el slip probablemente pertenecía a OTRA apuesta del
    tipster en la misma ventana (caso real: texto "Empate o Real
    Sociedad" emparejado con slip "Sabadell - Real Oviedo" — "real" solo
    no basta)."""
    from difflib import SequenceMatcher

    tokens = _team_tokens(seleccion)
    if not tokens or not evento:
        return True  # línea de mercado pura: nada que contrastar
    ev_words = re.findall(r"[a-záéíóúñü]+", evento.lower())
    return all(
        any(
            t == w or (len(t) >= 4 and SequenceMatcher(None, t, w).ratio() >= 0.8)
            for w in ev_words
        )
        for t in tokens
    )


# `_fixture_from_slip_ocr` vive en pick_extractor (la usa también el
# pipeline en vivo como fallback post-LLM).


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return
    print(f"MODO: {'APLICAR' if APPLY else 'DRY-RUN'}")

    async with AsyncSessionLocal() as session:
        # Textos (sin media) con pick simple real e incompleto.
        rows = (
            await session.exec(
                select(ParsedPick, TelegramRawMessage)
                .join(
                    TelegramRawMessage,
                    TelegramRawMessage.id == ParsedPick.raw_message_id,  # type: ignore[arg-type]
                )
                .where(TelegramRawMessage.media_path.is_(None))  # type: ignore[union-attr]
                .where(ParsedPick.es_apuesta == True)  # noqa: E712
                .where(ParsedPick.es_combinada == False)  # noqa: E712
                .where(ParsedPick.es_reto == False)  # noqa: E712
                .where(ParsedPick.combinada_id == None)  # noqa: E711
                .order_by(TelegramRawMessage.received_at.desc())  # type: ignore[arg-type]
            )
        ).all()

        candidatos = [(p, r) for p, r in rows if _incompleto(p)]
        print(f"Picks simples en textos: {len(rows)}; incompletos: {len(candidatos)}")

        emparejados = cambiados = duplicados = llm_blocked = 0
        for pick_row, raw in candidatos:
            slip = await _find_pair_slip(
                session, raw.channel_id, raw.received_at, raw.message_id
            )
            if slip is None:
                continue
            emparejados += 1

            source = f"{raw.text}\n\n{slip.extracted_text}"
            try:
                pick = await extract_pick(
                    source,
                    settings.openai_api_key,
                    informante=raw.channel_name,
                    fecha_referencia=raw.received_at,
                )
            except RateLimitError:
                llm_blocked += 1
                continue
            if pick is None or not pick.es_apuesta:
                print(
                    f"  msg {raw.message_id} ({raw.channel_name}): "
                    "extracción combinada no ve apuesta, salto."
                )
                continue
            if _era_multi(pick_row) and len(pick.patas) < 2:
                print(
                    f"  msg {raw.message_id} ({raw.channel_name}, "
                    f"pick {pick_row.id}): la selección original era "
                    "múltiple y la nueva no, salto."
                )
                continue
            if not _patas_son_reales(pick):
                print(
                    f"  msg {raw.message_id} ({raw.channel_name}): "
                    f"patas sospechosas "
                    f"{[p.seleccion for p in pick.patas]}, salto."
                )
                continue

            # Rescate de cruce: el LLM a veces devuelve la cabecera de
            # liga ("Italia - Serie A") aunque el OCR tenga "Como v
            # Parma" en una línea propia.
            if not _extract_eventos([pick.evento or ""]):
                fixture = _fixture_from_slip_ocr(slip.extracted_text)
                if fixture:
                    pick.evento = fixture

            # Guarda anti-mispair: si la selección nombra un equipo que
            # no está en el evento nuevo, el slip era de OTRA apuesta
            # del tipster dentro de la misma ventana de 10 min.
            if not _seleccion_casa_con_evento(
                pick.seleccion or pick_row.seleccion, pick.evento
            ):
                print(
                    f"  msg {raw.message_id} ({raw.channel_name}, "
                    f"pick {pick_row.id}): evento {pick.evento!r} no casa "
                    "con la selección (¿slip de otra apuesta?), salto."
                )
                continue

            # Mejora neta: el cruce pasa a ser real, o se rellenan
            # cuota/stake. Si no aporta nada (solo reordena la
            # selección o inventa patas), no merece el riesgo.
            evento_mejora = (
                bool(pick.evento)
                and _extract_eventos([pick.evento])
                and (pick_row.evento is None or not _extract_eventos([pick_row.evento]))
            )
            rellena = (pick_row.cuota is None and pick.cuota) or (
                pick_row.stake is None and pick.stake
            )
            if not evento_mejora and not rellena:
                print(
                    f"  msg {raw.message_id} ({raw.channel_name}, "
                    f"pick {pick_row.id}): sin mejora neta, salto."
                )
                continue

            cambia = (
                (pick.cuota is not None and pick_row.cuota != pick.cuota)
                or (pick.stake is not None and pick_row.stake != pick.stake)
                or (pick.evento and pick_row.evento != pick.evento)
                or (pick.seleccion and pick_row.seleccion != pick.seleccion)
                or (pick.mercado and pick_row.mercado != pick.mercado)
                or len(pick.patas) >= 2
            )
            if not cambia:
                continue
            cambiados += 1
            print(
                f"\n  msg {raw.message_id} ({raw.channel_name}, "
                f"pick {pick_row.id}, slip {slip.message_id}):"
            )
            print(
                f"    antes: sel={pick_row.seleccion!r:.55} "
                f"ev={pick_row.evento!r:.45} cuota={pick_row.cuota} "
                f"stake={pick_row.stake} mercado={pick_row.mercado!r}"
            )
            print(
                f"    nuevo: sel={pick.seleccion!r:.55} "
                f"ev={pick.evento!r:.45} cuota={pick.cuota} "
                f"stake={pick.stake} mercado={pick.mercado!r} "
                f"patas={len(pick.patas)}"
            )
            if not APPLY:
                continue

            _enrich_paired_pick(pick_row, pick)
            if len(pick.patas) >= 2 and not pick_row.es_combinada:
                pick_row.es_combinada = True
                await session.flush()
                await _persist_patas(
                    session,
                    pick_row,
                    pick,
                    pick_row.informante_id,
                    raw.channel_name,
                    pick_row.es_reto,
                )
            session.add(pick_row)

            # Duplicado real: el slip también generó su propio pick.
            slip_picks = (
                (
                    await session.exec(
                        select(ParsedPick).where(
                            and_(
                                ParsedPick.raw_message_id == slip.id,
                                ParsedPick.es_apuesta == True,  # noqa: E712
                                ParsedPick.combinada_id == None,  # noqa: E711
                                ParsedPick.id != pick_row.id,
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            for dup in slip_picks:
                print(
                    f"    eliminando pick duplicado id={dup.id} "
                    f"(sel={dup.seleccion!r:.50})"
                )
                # Si el duplicado es padre de combinada, sus patas lo
                # referencian por combinada_id: hay que borrarlas antes.
                patas_dup = (
                    (
                        await session.exec(
                            select(ParsedPick).where(ParsedPick.combinada_id == dup.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for pata in patas_dup:
                    await session.delete(pata)
                if patas_dup:
                    await session.flush()
                await session.delete(dup)
                duplicados += 1
            await session.commit()

        print(
            f"\nResumen: {emparejados} con slip pareja, "
            f"{cambiados} picks mejorados, {duplicados} duplicados eliminados, "
            f"{llm_blocked} pendientes por falta de cuota de OpenAI."
        )


if __name__ == "__main__":
    asyncio.run(main())
