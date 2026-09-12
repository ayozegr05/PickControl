"""Extractor híbrido de pronósticos a partir de texto libre.

Flujo:
1. Pre-filtro: detecta si el mensaje puede ser una apuesta.
2. Reglas: extrae de patrones muy claros sin llamar al LLM.
3. LLM (gpt-4o-mini): fallback para mensajes estructurados de forma libre.
"""

from __future__ import annotations

import json
import re
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
    metodo: str = "unknown"  # 'rule', 'llm' o 'rejected'
    confianza: float = 0.0


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
  "explicacion": string | null
}

Reglas:
- Si no es una apuesta, devuelve es_apuesta = false y el resto null.
- "seleccion" es lo recomendado (ej. "Titouan Droguet gana", "Real Sociedad B Hándicap Asiático +1.5").
- "evento" es el partido/competición (ej. "Tenis - Challenger Francia - Cassis").
- "mercado" es el tipo de apuesta (ej. "ganador", "hándicap asiático").
- Extrae cuota y stake como números. Si no están, null.
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
