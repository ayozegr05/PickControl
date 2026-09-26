"""Limpieza de picks basura y slips republicados (dry-run por defecto).

Tres detectores sobre los picks abiertos (`es_apuesta=True`, pendientes):

- **Slip republicado**: la casa imprime la fecha del partido en el
  boleto ("16 de septiembre de 2026, 19:00"). Si esa fecha cae en un
  DÍA ANTERIOR al del mensaje, el tipster republicó un slip ya jugado
  (marketing de "mira la que gané") — nunca fue apuesta abierta y se
  propone `es_apuesta=False`. Se compara por día, no por hora: un pick
  publicado el mismo día tras el pitido inicial puede ser una apuesta
  en vivo legítima y solo se reporta para revisión.
- **Fecha corregible**: slip con fecha el mismo día del mensaje o
  posterior, distinta de `fecha_evento` — la impresa manda sobre la del
  mensaje (corrige desfases por republicación a tiempo).
- **Extracción basura**: `evento` vacío o texto de marketing colado
  como selección ("Tendréis...", "@canal", "LLEVA TUS APUESTAS...").

`analyze_garbage` solo lee. `apply_garbage` escribe los cambios
propuestos — nunca borra filas (auditoría): los descartes son
`es_apuesta=False`, reversible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401

# Solo fechas largas inequívocas, con año explícito — el formato que
# imprimen las casas en el boleto ("16 de septiembre de 2026, 19:00").
_MESES_LARGO = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_SLIP_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|"
    r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+de\s+"
    r"(\d{4})\b(?:\s*[,·-]?\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)
# Cordura: la fecha impresa debe estar cerca de la del mensaje; fuera
# de este margen es ruido del OCR (fecha de emisión, otro evento...).
_SLIP_MAX_DISTANCE = timedelta(days=60)

# Texto de marketing que cuela como selección/evento. Palabras/frases
# que nunca aparecen en una apuesta real.
_MARKETING_RE = re.compile(
    r"(tendr[eé]is|siguiente nivel|accede|premium|suscri|gratis|"
    r"whatsapp|t\.me/|https?://|@\w+|canal de|gana ya|regístrate|"
    r"registrate|promo|bono de|bienvenida|link en|la apuesta del a[ñn]o)",
    re.IGNORECASE,
)


@dataclass
class GarbageAction:
    """Cambio propuesto sobre un pick (nada escrito hasta `apply`)."""

    pick_id: int
    action: str  # "flag" | "fix_date" | "review"
    reason: str
    detail: str = ""
    new_fecha_evento: Optional[datetime] = None


@dataclass
class GarbageReport:
    actions: list[GarbageAction] = field(default_factory=list)

    def by_action(self, action: str) -> list[GarbageAction]:
        return [a for a in self.actions if a.action == action]


def slip_printed_date(text: Optional[str]) -> Optional[datetime]:
    """Fecha del partido impresa en el boleto, o None.

    Solo acepta el formato largo con año ("16 de septiembre de 2026" /
    "16 de septiembre de 2026, 19:00") — inequívoco en slips de casas
    españolas. Sin fecha impresa clara no se inventa nada.
    """
    if not text:
        return None
    match = _SLIP_DATE_RE.search(text)
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    try:
        return datetime(
            int(year),
            _MESES_LARGO[month_name.lower()],
            int(day),
            int(hour or 0),
            int(minute or 0),
        )
    except (ValueError, KeyError):
        return None


def _slip_analysis(raw: TelegramRawMessage) -> tuple[Optional[datetime], str]:
    """(fecha impresa, fuente) buscando primero en el OCR del slip."""
    for source_name, text in (("ocr", raw.extracted_text), ("texto", raw.text)):
        found = slip_printed_date(text)
        if found is not None:
            return found, source_name
    return None, ""


def _analyze_raw(
    raw: TelegramRawMessage, picks: list[ParsedPick]
) -> list[GarbageAction]:
    """Reglas de fecha del slip para todos los picks de un mensaje."""
    slip_date, source = _slip_analysis(raw)
    if slip_date is None or raw.received_at is None:
        return []
    if abs(slip_date - raw.received_at) > _SLIP_MAX_DISTANCE:
        return []

    actions: list[GarbageAction] = []
    if slip_date.date() < raw.received_at.date():
        # La fecha impresa es de un día ANTERIOR al mensaje: el slip ya
        # estaba jugado cuando se publicó — republicación/marketing.
        actions.extend(
            GarbageAction(
                pick_id=pick.id,
                action="flag",
                reason="slip_republicado",
                detail=(
                    f"slip {source}: {slip_date:%Y-%m-%d %H:%M} < "
                    f"mensaje {raw.received_at:%Y-%m-%d %H:%M} "
                    f"(msg {raw.message_id})"
                ),
            )
            for pick in picks
        )
    elif slip_date.date() == raw.received_at.date() and slip_date < raw.received_at:
        # Mismo día pero tras el inicio: puede ser live-bet legítimo —
        # se reporta para revisión manual, no se descarta solo.
        actions.extend(
            GarbageAction(
                pick_id=pick.id,
                action="review",
                reason="slip_mismo_dia_post_inicio",
                detail=(
                    f"slip {source}: {slip_date:%Y-%m-%d %H:%M} < "
                    f"mensaje {raw.received_at:%Y-%m-%d %H:%M} "
                    f"(msg {raw.message_id})"
                ),
            )
            for pick in picks
        )
    else:
        # Mensaje anterior al evento: apuesta real; la fecha impresa
        # corrige `fecha_evento` cuando difiere de la guardada.
        for pick in picks:
            current = pick.fecha_evento
            if current is None or current.date() != slip_date.date():
                actions.append(
                    GarbageAction(
                        pick_id=pick.id,
                        action="fix_date",
                        reason="fecha_slip_corrige",
                        detail=(
                            f"{current} -> {slip_date} "
                            f"(slip {source}, msg {raw.message_id})"
                        ),
                        new_fecha_evento=slip_date,
                    )
                )
    return actions


def _analyze_pick_fields(pick: ParsedPick) -> Optional[GarbageAction]:
    """Extracción basura detectable sin mirar el raw."""
    seleccion = (pick.seleccion or "").strip()
    evento = (pick.evento or "").strip()
    if not evento and not pick.es_combinada:
        return GarbageAction(
            pick_id=pick.id,
            action="flag",
            reason="evento_vacio",
            detail=f"seleccion='{seleccion[:60]}'",
        )
    for campo, valor in (("seleccion", seleccion), ("evento", evento)):
        if valor and _MARKETING_RE.search(valor):
            return GarbageAction(
                pick_id=pick.id,
                action="flag",
                reason="marketing",
                detail=f"{campo}='{valor[:60]}'",
            )
    return None


async def analyze_garbage() -> GarbageReport:
    """Recorre los picks abiertos y propone acciones — SOLO LEE."""
    report = GarbageReport()
    async with AsyncSessionLocal() as session:
        picks = list(
            (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.es_apuesta == True)  # noqa: E712
                    .where(ParsedPick.acierto.is_(None))
                    .where(ParsedPick.anulada == False)  # noqa: E712
                )
            )
            .scalars()
            .all()
        )
        by_raw: dict[int, list[ParsedPick]] = {}
        for pick in picks:
            by_raw.setdefault(pick.raw_message_id, []).append(pick)

        flagged: set[int] = set()
        raws = list(
            (
                await session.exec(
                    select(TelegramRawMessage).where(
                        TelegramRawMessage.id.in_(list(by_raw))  # type: ignore[union-attr]
                    )
                )
            )
            .scalars()
            .all()
        )
        for raw in raws:
            raw_picks = by_raw.get(raw.id) or []
            raw_actions = _analyze_raw(raw, raw_picks)
            report.actions.extend(raw_actions)
            flagged.update(
                a.pick_id for a in raw_actions if a.action in ("flag", "fix_date")
            )

        for pick in picks:
            if pick.id in flagged:
                continue
            action = _analyze_pick_fields(pick)
            if action is not None:
                report.actions.append(action)

    return report


async def apply_garbage(report: GarbageReport) -> dict[str, int]:
    """Escribe las acciones `flag`/`fix_date` del informe.

    Nunca borra filas: el descarte es `es_apuesta=False` (sale de
    pendientes y stats, recuperable). Las entradas `review` solo se
    informan, nunca se aplican.
    """
    applied = {"flagged": 0, "fixed_dates": 0}
    async with AsyncSessionLocal() as session:
        for action in report.actions:
            pick = await session.get(ParsedPick, action.pick_id)
            if pick is None or not pick.es_apuesta:
                continue
            if action.action == "flag":
                pick.es_apuesta = False
                session.add(pick)
                applied["flagged"] += 1
            elif action.action == "fix_date" and action.new_fecha_evento:
                pick.fecha_evento = action.new_fecha_evento
                session.add(pick)
                applied["fixed_dates"] += 1
        await session.commit()
    return applied
