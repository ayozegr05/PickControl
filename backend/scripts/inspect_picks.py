# ruff: noqa: E402
"""Herramienta de inspección y diagnóstico de mensajes de Telegram y picks.

Consolida las consultas ad-hoc que solemos lanzar desde consola para entender
qué ha llegado de Telegram, qué se ha parseado y qué está pendiente.

Uso:
    .venv\\Scripts\\python.exe scripts\\inspect_picks.py [subcomando]

Subcomandos disponibles:
    channels  - Canales observados y número de mensajes/picks por canal.
    parsed    - Lista de parsed_picks con sus detalles (método, confianza,
                acierto, verificado_por, ...).
    pending   - Picks que no han podido verificarse automáticamente.
    raw       - Mensajes crudos recibidos de Telegram.
    missing   - Mensajes crudos sin ningún pick asociado.
    duplicates- Posibles picks duplicados detectados por la heurística actual.
    summary   - Resumen global (totales, aciertos/fallos, métodos, ...).
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import func, select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.processor import _text_similarity, _word_set_similarity


def _status_label(pick: ParsedPick) -> str:
    if pick.anulada:
        return "anulada"
    if pick.acierto is None:
        return "pendiente"
    return "acierto" if pick.acierto else "fallo"


def _is_duplicate(a: ParsedPick, b: ParsedPick) -> bool:
    if not a.seleccion or not b.seleccion:
        return False
    similarity = _text_similarity(a.seleccion, b.seleccion)
    if similarity >= 0.8:
        return True
    if _word_set_similarity(a.seleccion, b.seleccion) >= 0.85:
        return True
    cuotas_coinciden = (
        a.cuota is not None and b.cuota is not None and abs(a.cuota - b.cuota) < 0.01
    )
    return cuotas_coinciden and similarity >= 0.6


async def _channels(session) -> None:
    raw_result = await session.exec(
        select(
            TelegramRawMessage.channel_id,
            TelegramRawMessage.channel_name,
            func.count(TelegramRawMessage.id).label("raw_count"),
        ).group_by(TelegramRawMessage.channel_id, TelegramRawMessage.channel_name)
    )
    raw_rows = list(raw_result.all())

    parsed_result = await session.exec(
        select(
            ParsedPick.informante,
            func.count(ParsedPick.id).label("parsed_count"),
        ).group_by(ParsedPick.informante)
    )
    parsed_rows = list(parsed_result.all())

    parsed_by_informante: dict[str | None, int] = {
        row[0]: row[1] for row in parsed_rows
    }

    print("Canales observados:")
    print("-" * 80)
    for channel_id, name, raw_count in raw_rows:
        parsed_count = parsed_by_informante.get(name, 0)
        print(f"  id={channel_id}  name={name!r}")
        print(f"    mensajes crudos: {raw_count}  picks: {parsed_count}")

    if not raw_rows:
        print("  (ningún canal con mensajes crudos)")

    total_raw = sum(r[2] for r in raw_rows)
    total_parsed = sum(parsed_by_informante.values())
    print("-" * 80)
    print(f"Total: {total_raw} mensajes crudos, {total_parsed} picks parseados")


async def _parsed(session, limit: int = 50) -> None:
    result = await session.exec(
        select(ParsedPick).order_by(ParsedPick.created_at.desc()).limit(limit)
    )
    picks = list(result.scalars().all())

    print(f"Últimos {len(picks)} picks parseados:")
    print("-" * 80)
    for pick in picks:
        raw_id = pick.raw_message_id
        status = _status_label(pick)
        print(
            f"  id={pick.id} raw={raw_id} es_apuesta={pick.es_apuesta} "
            f"metodo={pick.metodo} confianza={pick.confianza:.2f}"
        )
        print(f"    seleccion={pick.seleccion!r}")
        print(f"    mercado={pick.mercado!r} cuota={pick.cuota} stake={pick.stake}")
        print(
            f"    evento={pick.evento!r} fecha={pick.fecha_evento} "
            f"linea={pick.linea}"
        )
        print(
            f"    deporte={pick.deporte!r} informante={pick.informante!r} "
            f"casa={pick.casa!r}"
        )
        print(f"    acierto={pick.acierto} anulada={pick.anulada} " f"status={status}")
        if pick.verificado_por:
            print(f"    verificado_por={pick.verificado_por}")
        print()


async def _pending(session) -> None:
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta.is_(True))
        .where(ParsedPick.acierto.is_(None))
        .where(ParsedPick.anulada.is_(False))
        .order_by(ParsedPick.created_at.desc())
    )
    picks = list(result.scalars().all())

    print(f"Picks pendientes de verificación ({len(picks)}):")
    print("-" * 80)
    for pick in picks:
        print(f"  id={pick.id} raw={pick.raw_message_id}")
        print(f"    seleccion={pick.seleccion!r}")
        print(f"    mercado={pick.mercado!r} cuota={pick.cuota} linea={pick.linea}")
        print(f"    evento={pick.evento!r} fecha={pick.fecha_evento}")
        print(f"    informante={pick.informante!r}")
        print()


async def _raw(session, limit: int = 30) -> None:
    result = await session.exec(
        select(TelegramRawMessage)
        .order_by(TelegramRawMessage.received_at.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())

    print(f"Últimos {len(rows)} mensajes crudos:")
    print("-" * 80)
    for raw in rows:
        text = (raw.text or "")[:120].replace("\n", " ")
        print(f"  id={raw.id} msg={raw.message_id} channel={raw.channel_name!r}")
        print(f"    channel_id={raw.channel_id} processed={raw.processed}")
        print(f"    text={text!r}")
        print(f"    media_path={raw.media_path!r} received={raw.received_at}")
        print()


async def _missing(session) -> None:
    subq = select(ParsedPick.raw_message_id).distinct().subquery()
    result = await session.exec(
        select(TelegramRawMessage)
        .where(TelegramRawMessage.id.notin_(subq))
        .order_by(TelegramRawMessage.received_at.desc())
    )
    rows = list(result.scalars().all())

    print(f"Mensajes crudos sin pick asociado ({len(rows)}):")
    print("-" * 80)
    for raw in rows:
        text = (raw.text or "")[:100].replace("\n", " ")
        print(f"  id={raw.id} channel={raw.channel_name!r} msg={raw.message_id}")
        print(f"    processed={raw.processed} text={text!r}")
        print()


async def _duplicates(session) -> None:
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta.is_(True))
        .order_by(ParsedPick.informante, ParsedPick.created_at)
    )
    picks = list(result.scalars().all())

    by_channel: dict[str, list[ParsedPick]] = {}
    for pick in picks:
        by_channel.setdefault(pick.informante or "desconocido", []).append(pick)

    found = 0
    for channel, channel_picks in by_channel.items():
        kept: list[ParsedPick] = []
        for pick in channel_picks:
            duplicate_of = next((k for k in kept if _is_duplicate(k, pick)), None)
            if duplicate_of:
                print(
                    f"[{channel}] id={pick.id} '{pick.seleccion}' es duplicado "
                    f"de id={duplicate_of.id} '{duplicate_of.seleccion}'"
                )
                found += 1
            else:
                kept.append(pick)

    if found == 0:
        print("No se han detectado duplicados con la heurística actual.")
    else:
        print(f"\nTotal duplicados detectados: {found}")
        print("Este comando es informativo; no borra nada. Usa dedupe_parsed_picks.py.")


async def _summary(session) -> None:
    raw_count = await session.exec(select(func.count(TelegramRawMessage.id)))
    parsed_count = await session.exec(select(func.count(ParsedPick.id)))
    bet_count = await session.exec(
        select(func.count(ParsedPick.id)).where(ParsedPick.es_apuesta.is_(True))
    )
    pending_count = await session.exec(
        select(func.count(ParsedPick.id))
        .where(ParsedPick.es_apuesta.is_(True))
        .where(ParsedPick.acierto.is_(None))
        .where(ParsedPick.anulada.is_(False))
    )
    won_count = await session.exec(
        select(func.count(ParsedPick.id))
        .where(ParsedPick.es_apuesta.is_(True))
        .where(ParsedPick.acierto.is_(True))
    )
    lost_count = await session.exec(
        select(func.count(ParsedPick.id))
        .where(ParsedPick.es_apuesta.is_(True))
        .where(ParsedPick.acierto.is_(False))
    )
    void_count = await session.exec(
        select(func.count(ParsedPick.id))
        .where(ParsedPick.es_apuesta.is_(True))
        .where(ParsedPick.anulada.is_(True))
    )

    methods = await session.exec(
        select(ParsedPick.metodo, func.count(ParsedPick.id).label("count")).group_by(
            ParsedPick.metodo
        )
    )
    method_counts = list(methods.all())

    verifications = await session.exec(
        select(ParsedPick.verificado_por, func.count(ParsedPick.id).label("count"))
        .where(ParsedPick.verificado_por.isnot(None))
        .group_by(ParsedPick.verificado_por)
    )
    verification_counts = list(verifications.all())

    print("Resumen de la base de datos:")
    print("-" * 80)
    print(f"  mensajes crudos:     {raw_count.scalar_one()}")
    print(f"  picks totales:       {parsed_count.scalar_one()}")
    print(f"  picks reales:        {bet_count.scalar_one()}")
    print(f"  pendientes:          {pending_count.scalar_one()}")
    print(f"  acertados:           {won_count.scalar_one()}")
    print(f"  fallados:            {lost_count.scalar_one()}")
    print(f"  anulados:            {void_count.scalar_one()}")
    print()
    print("Métodos de extracción:")
    for method, count in method_counts:
        print(f"    {method}: {count}")
    print()
    print("Fuentes de verificación:")
    for source, count in verification_counts:
        print(f"    {source}: {count}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Herramienta de inspección de mensajes de Telegram y picks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_channels = subparsers.add_parser(
        "channels", help="Canales observados y conteos por canal."
    )
    _ = p_channels

    p_parsed = subparsers.add_parser(
        "parsed", help="Últimos picks parseados con detalles."
    )
    p_parsed.add_argument(
        "--limit", type=int, default=50, help="Número de picks a mostrar."
    )

    p_pending = subparsers.add_parser("pending", help="Picks sin verificar todavía.")
    _ = p_pending

    p_raw = subparsers.add_parser("raw", help="Últimos mensajes crudos.")
    p_raw.add_argument(
        "--limit", type=int, default=30, help="Número de mensajes a mostrar."
    )

    p_missing = subparsers.add_parser(
        "missing", help="Mensajes crudos sin pick asociado."
    )
    _ = p_missing

    p_duplicates = subparsers.add_parser(
        "duplicates", help="Posibles duplicados detectados."
    )
    _ = p_duplicates

    p_summary = subparsers.add_parser("summary", help="Resumen global.")
    _ = p_summary

    args = parser.parse_args()

    handlers = {
        "channels": _channels,
        "parsed": lambda s: _parsed(s, args.limit),
        "pending": _pending,
        "raw": lambda s: _raw(s, args.limit),
        "missing": _missing,
        "duplicates": _duplicates,
        "summary": _summary,
    }

    async with AsyncSessionLocal() as session:
        await handlers[args.command](session)


if __name__ == "__main__":
    asyncio.run(main())
