"""Extractor híbrido de pronósticos a partir de texto libre.

Flujo:
1. Pre-filtro: detecta si el mensaje puede ser una apuesta.
2. Reglas: extrae de patrones muy claros sin llamar al LLM.
3. LLM (gpt-4o-mini): fallback para mensajes estructurados de forma libre.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class ExtractedPick(BaseModel):
    """Pronóstico extraído, con metadatos del método usado."""

    es_apuesta: bool = Field(
        description="True si el mensaje contiene una apuesta clara, False en caso contrario"
    )
    deporte: Optional[str] = None
    evento: Optional[str] = None
    mercado: Optional[str] = None
    seleccion: Optional[str] = None
    cuota: Optional[float] = None
    stake: Optional[float] = None
    casa: Optional[str] = None
    informante: Optional[str] = None
    explicacion: Optional[str] = None
    # Fecha/hora del evento deportivo, si se pudo identificar en el texto.
    # Necesaria para poder verificar el resultado más adelante.
    fecha_evento: Optional[datetime] = None
    # Valor numérico de la línea de hándicap (con signo, ej. +1.5/-1.5) u
    # over/under (magnitud, ej. 2.5), si el mercado la tiene.
    linea: Optional[float] = None
    metodo: str = "unknown"  # 'rule', 'llm' o 'rejected'
    confianza: float = 0.0


_MESES_ES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def _extract_event_date(text: str) -> Optional[datetime]:
    """Intenta extraer la fecha/hora del evento con patrones habituales.

    Cubre formatos vistos en mensajes/OCR de tipsters, p. ej.:
    - "12.09.2026 14:00" (boletos/capturas de casas de apuestas)
    - "Sáb 12 sep 14:00" (interfaz de casa de apuestas)
    No inventa nada: si no encuentra un patrón claro, devuelve None.
    """
    match = re.search(
        r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text
    )
    if match:
        day, month, year, hour, minute = match.groups()
        try:
            return datetime(
                int(year), int(month), int(day), int(hour or 0), int(minute or 0)
            )
        except ValueError:
            pass

    match = re.search(
        r"\b(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)"
        r"[a-zñ]*\.?(?:\s+(\d{4}))?(?:\s+(\d{1,2}):(\d{2}))?",
        text.lower(),
    )
    if match:
        day, month_abbr, year, hour, minute = match.groups()
        try:
            return datetime(
                int(year) if year else datetime.now().year,
                _MESES_ES[month_abbr],
                int(day),
                int(hour or 0),
                int(minute or 0),
            )
        except (ValueError, KeyError):
            pass

    return None


def _extract_linea(seleccion: str) -> Optional[float]:
    """Extrae el valor numérico de la línea (hándicap u over/under).

    Cubre patrones como "+1.5"/"-1.5" (hándicap asiático, con signo) y
    "Over 2.5"/"Under 2.5"/"más de 2.5" (línea de goles/juegos, sin
    signo). Si no encuentra un patrón claro, devuelve None.
    """
    if not seleccion:
        return None

    signed_match = re.search(r"([+-])\s?(\d+(?:[.,]\d+)?)", seleccion)
    if signed_match:
        sign, value = signed_match.groups()
        number = float(value.replace(",", "."))
        return number if sign == "+" else -number

    unsigned_match = re.search(
        r"\b(?:over|under|más de|menos de)\s+(\d+(?:[.,]\d+)?)",
        seleccion.lower(),
    )
    if unsigned_match:
        return float(unsigned_match.group(1).replace(",", "."))

    return None


_POSITIVE_PATTERNS = [
    r"\bcuota\b",
    r"\bstake\b",
    r"\bunidades\b",
    r"\bhándicap\b",
    r"\bhandicap\b",
    r"\bover\b",
    r"\bunder\b",
    r"\bgana\b",
    r"\bvs\b",
    r"\bapuesta\b",
]

_NEGATIVE_PATTERNS = [
    r"\bpromo\b",
    r"\bacceso\b",
    r"\bplazas\b",
    r"\bsorteo\b",
    r"\bmega\s*reto\b",
    r"\breto\s*especial\b",
    r"\bcrear\s*apuesta\b",
    r"\bbuenos\s*días\b",
    r"\bgratis\b",
    r"\benlace\b",
    r"\blink\b",
    r"\bseguidores\b",
]


def _looks_like_bet(text: str) -> bool:
    """Heurística rápida para saber si merece la pena intentar extraer."""
    if not text:
        return False

    lowered = text.lower()
    if any(re.search(pattern, lowered) for pattern in _NEGATIVE_PATTERNS):
        return False

    return any(re.search(pattern, lowered) for pattern in _POSITIVE_PATTERNS)


def _rule_extract(
    text: str, informante: Optional[str] = None
) -> Optional[ExtractedPick]:
    """Extrae por regex de alta confianza. None si no es lo suficientemente claro."""

    lowered = text.lower()

    # Buscar cuota y stake con formato "Cuota X.XX" o "@ X.XX"
    cuota_match = re.search(r"cuota\s*[:=]?\s*([0-9]+[.,]?[0-9]*)", lowered)
    if not cuota_match:
        cuota_match = re.search(r"@\s*([0-9]+[.,]?[0-9]{2,})", lowered)

    stake_match = re.search(r"stake\s*[:=]?\s*([0-9]+[.,]?[0-9]*)", lowered)
    if not stake_match:
        stake_match = re.search(r"unidades\s*[:=]?\s*([0-9]+[.,]?[0-9]*)", lowered)

    if not cuota_match or not stake_match:
        return None

    # Buscar selección: primera línea con palabras clave
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    seleccion = None
    for line in lines:
        low = line.lower()
        if any(
            keyword in low
            for keyword in (
                "gana",
                "hándicap",
                "handicap",
                "over",
                "under",
                "+1.5",
                "-1.5",
            )
        ) and not low.startswith(("cuota", "stake", "unidades")):
            seleccion = line
            break

    if not seleccion:
        return None

    return ExtractedPick(
        es_apuesta=True,
        seleccion=seleccion,
        cuota=float(cuota_match.group(1).replace(",", ".")),
        stake=float(stake_match.group(1).replace(",", ".")),
        informante=informante,
        fecha_evento=_extract_event_date(text),
        linea=_extract_linea(seleccion),
        metodo="rule",
        confianza=0.85,
    )


_SYSTEM_PROMPT = """Eres un extractor de pronósticos deportivos.

Recibirás texto de canales de Telegram de tipsters. Devuelve ÚNICAMENTE un JSON con esta estructura:

{
  "es_apuesta": boolean,
  "deporte": string | null,
  "evento": string | null,
  "mercado": string | null,
  "seleccion": string | null,
  "cuota": number | null,
  "stake": number | null,
  "casa": string | null,
  "informante": string | null,
  "explicacion": string | null,
  "fecha_evento": string | null,
  "linea": number | null
}

Reglas:
- Si no es una apuesta, devuelve es_apuesta = false y el resto null.
- "seleccion" es lo recomendado (ej. "Titouan Droguet gana", "Real Sociedad B Hándicap Asiático +1.5").
- "evento" es el partido/competición (ej. "Tenis - Challenger Francia - Cassis").
- "mercado" es el tipo de apuesta: usa siempre una de estas etiquetas si aplica: "ganador", "hándicap asiático", "over/under" (o "over/under goles", "over/under juegos" según el deporte). Si no encaja en ninguna, describe brevemente el mercado.
- "deporte" debe ser una palabra normalizada y simple: "fútbol", "tenis", "baloncesto", etc.
- "linea" es el valor numérico de la línea cuando el mercado es hándicap asiático u over/under (ej. 1.5, -1.5, 2.5). Con signo si es hándicap (+1.5 a favor del equipo de "seleccion", -1.5 en contra). Sin signo si es over/under. Si el mercado no tiene línea (p. ej. "ganador"), déjalo null.
- Extrae "cuota" solo si aparece un número claramente asociado a la cuota/odds de la selección.
- Extrae "stake" ÚNICAMENTE si el texto menciona explícitamente la palabra "stake" o "unidades" seguida de un número (normalmente entre 1 y 10).
- NUNCA uses como "stake" importes en euros/dólares que aparezcan en capturas de pantalla del boleto de una casa de apuestas (p. ej. "Importe", "Imp:", "Ganancias", "Cerrar apuesta", saldo, importe apostado, importe a pagar). Esos son cantidades de dinero del boleto del tipster, no el sistema de unidades de stake. Si no hay mención explícita de "stake" o "unidades", deja "stake" = null.
- "fecha_evento" es la fecha (y hora si aparece) del partido/evento, en formato ISO 8601 (ej. "2026-09-12T14:00:00"). Solo si aparece explícitamente en el texto. Si no hay fecha, null.
- No inventes ni deduzcas valores (cuota, stake, casa, fecha, línea, etc.) que no estén explícitamente en el texto. Ante la duda, usa null.
- No añadas markdown, solo el JSON.
"""


async def _llm_extract(
    text: str, api_key: str, informante: Optional[str] = None
) -> Optional[ExtractedPick]:
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Canal: {informante or 'desconocido'}\n\nMensaje:\n{text}",
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    content = response.choices[0].message.content
    if not content:
        return None

    data = json.loads(content)
    pick = ExtractedPick(**data)
    pick.metodo = "llm"
    pick.confianza = 0.75 if pick.es_apuesta else 0.0
    pick.informante = pick.informante or informante
    if pick.fecha_evento is None:
        pick.fecha_evento = _extract_event_date(text)
    if pick.linea is None and pick.seleccion:
        pick.linea = _extract_linea(pick.seleccion)
    return pick


async def extract_pick(
    text: str, api_key: str, informante: Optional[str] = None
) -> Optional[ExtractedPick]:
    """Extractor híbrido."""
    if not _looks_like_bet(text):
        return ExtractedPick(
            es_apuesta=False, informante=informante, metodo="rejected", confianza=0.95
        )

    rule_result = _rule_extract(text, informante=informante)
    if rule_result:
        return rule_result

    return await _llm_extract(text, api_key, informante=informante)
