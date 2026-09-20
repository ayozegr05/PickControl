"""Extractor híbrido de pronósticos a partir de texto libre.

Flujo:
1. Pre-filtro: detecta si el mensaje puede ser una apuesta.
2. Reglas: extrae de patrones muy claros sin llamar al LLM.
3. LLM (gpt-4o-mini): fallback para mensajes estructurados de forma libre.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from app.core.logging import get_logger
from app.services.telegram.openai_retry import call_with_retry

logger = get_logger("app.telegram.extractor")


class ExtractedPick(BaseModel):
    """Pronóstico extraído, con metadatos del método usado."""

    es_apuesta: bool = Field(
        default=False,
        description="True si el mensaje contiene una apuesta clara, False en caso contrario",
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
    # Patas de una combinada ("crear apuesta"/acumulador): cada una es
    # una selección independiente que se verifica por separado; el pick
    # padre se liquida en conjunto y `cuota` es la cuota combinada total.
    # Lista vacía en picks simples.
    patas: list[ExtractedPick] = Field(default_factory=list)

    @field_validator("patas", mode="before")
    @classmethod
    def _none_patas_a_lista(cls, v: object) -> object:
        # El prompt pide "patas": null para picks simples — se normaliza
        # a lista vacía para no romper el parseo del JSON del LLM.
        return [] if v is None else v


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
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def _extract_event_date(
    text: str, fecha_referencia: Optional[datetime] = None
) -> Optional[datetime]:
    """Intenta extraer la fecha/hora del evento con patrones habituales.

    Cubre formatos vistos en mensajes/OCR de tipsters, p. ej.:
    - "12.09.2026 14:00" (boletos/capturas de casas de apuestas)
    - "Sáb 12 sep 14:00" (interfaz de casa de apuestas)
    No inventa nada: si no encuentra un patrón claro, devuelve None.
    Cuando el texto no trae año se usa el de `fecha_referencia` (la fecha
    real del mensaje); sin referencia, el año actual.
    """
    anio = (fecha_referencia or datetime.now()).year
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

    # OJO: "set" no se admite como abreviatura de septiembre — en picks de
    # tenis "en el 1 set"/"2 set" se interpretaba como fecha (1-2 de sep).
    match = re.search(
        r"\b(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)"
        r"[a-zñ]*\.?(?:\s+(\d{4}))?(?:\s+(\d{1,2}):(\d{2}))?",
        text.lower(),
    )
    if match:
        day, month_abbr, year, hour, minute = match.groups()
        try:
            return datetime(
                int(year) if year else anio,
                _MESES_ES[month_abbr],
                int(day),
                int(hour or 0),
                int(minute or 0),
            )
        except (ValueError, KeyError):
            pass

    return None


# Margen de plausibilidad de la fecha del evento frente a la del mensaje:
# un pick abierto no puede ser de un partido ya jugado hace días ni de
# algo a meses vista. Si el extractor (regex o LLM) devuelve una fecha
# fuera de margen es un error de lectura — casos reales: el LLM pone el
# año 2023 a un "15 sep" del OCR, o un slip trae la fecha de emisión del
# boleto y no la del partido.
_EVENT_MAX_PAST = timedelta(days=2)
_EVENT_MAX_FUTURE = timedelta(days=30)


def _sanitize_event_date(
    fecha_evento: Optional[datetime],
    fecha_referencia: Optional[datetime],
) -> Optional[datetime]:
    """Descarta fechas de evento implausibles respecto al mensaje.

    Normaliza datetimes tz-aware a UTC naive (las columnas de BD son
    TIMESTAMP WITHOUT TIME ZONE) y devuelve None cuando la fecha extraída
    se desvía demasiado de `fecha_referencia`; el procesador usará
    entonces la fecha real del mensaje como aproximación.
    """
    if fecha_evento is None:
        return None
    if fecha_evento.tzinfo is not None:
        fecha_evento = fecha_evento.astimezone(timezone.utc).replace(tzinfo=None)
    if fecha_referencia is not None:
        delta = fecha_evento - fecha_referencia
        if delta < -_EVENT_MAX_PAST or delta > _EVENT_MAX_FUTURE:
            logger.warning(
                "[EXTRACTOR] fecha_evento %s descartada: fuera de margen "
                "respecto a la fecha del mensaje %s",
                fecha_evento,
                fecha_referencia,
            )
            return None
    return fecha_evento


def _extract_linea(seleccion: str) -> Optional[float]:
    """Extrae el valor numérico de la línea (hándicap u over/under).

    Cubre patrones como "+1.5"/"-1.5" (hándicap asiático, con signo) y
    "Over 2.5"/"Under 2.5"/"más de 2.5" (línea de goles/juegos, sin
    signo). Si no encuentra un patrón claro, devuelve None.
    """
    if not seleccion:
        return None

    # El signo no puede ir pegado a otro dígito: "0-0" es un marcador,
    # no el hándicap "-0" (sin el lookbehind salía linea=-0.0).
    signed_match = re.search(r"(?<!\d)([+-])\s?(\d+(?:[.,]\d+)?)", seleccion)
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

    # "21+ juegos" / "20 o más juegos" = over N-0.5 (N o más).
    plus_match = re.search(r"(\d+(?:[.,]\d+)?)\s*\+", seleccion)
    o_mas_match = re.search(r"(\d+(?:[.,]\d+)?)\s+o\s+m[aá]s", seleccion.lower())
    for m in (plus_match, o_mas_match):
        if m:
            return float(m.group(1).replace(",", ".")) - 0.5

    return None


# --- Combinadas -------------------------------------------------------
#
# Una combinada es UNA apuesta con N selecciones (patas): bet-builders
# de una casa ("CREA TU APUESTA 5 pronósticos") o acumuladores entre
# partidos ("Combinada @3.40: A gana + B gana"). El padre lleva la cuota
# combinada total; cada pata se verifica por separado.

# Señales de que el mensaje es una combinada.
_COMBINADA_PATTERN = re.compile(
    r"\bcombinad[ao]\b|\bacumulador\b|\bacumulada\b|\bm[uú]ltiple\b|"
    r"\bparlay\b|\bcrea(?:r)?\s+(?:tu\s+)?apuesta\b|\bbet\s*builder\b|"
    r"\b\d+\s+pron[oó]sticos\b",
    re.IGNORECASE,
)

# Viñeta que precede a cada pata en el texto/OCR del boleto.
_LEG_BULLET_PATTERN = re.compile(r"^\s*[✔✅☑•·▪►➤‣\-–—*]\s*(?P<leg>\S.*)$")

# Una línea con viñeta que NO es una pata: metadatos del boleto
# (cuota/stake/importe), cabeceras o líneas de fecha/hora.
_LEG_NOISE_PATTERN = re.compile(
    r"cuota|stake|unidades|importe|ganancias|ganaste|apostad|"
    r"pron[oó]stico|combinad|acumulador|crea(?:r)?\s|"
    # Footer de juego responsable del boleto ("Apuesta con
    # responsabilidad. +18") colaba como pata en slips bet365.
    r"responsabilidad|juega\s+seguro|\+18\b|"
    # Una línea con viñeta que es un link/CTA ("[Ya lo pegamos](t.me/…)",
    # "🛍 premiumpay.pro/…") nunca es una selección.
    r"https?://|t\.me/|\]\(|"
    # Escalera de reto "1⃣PASO 30€ - 96€": dinero→dinero, no selección.
    r"\d[\d.,]*\s*€\s*[-–—➜→]+\s*\d[\d.,]*\s*€|" r"^\d{1,2}\s*[:./-]\s*\d{1,2}",
    re.IGNORECASE,
)


def _classify_leg_market(leg: str) -> Optional[str]:
    """Mercado probable de una pata suelta, por palabras clave.

    El verificador re-detecta el mercado real a partir de
    `seleccion`/`linea`, pero necesita una pista inicial para entrar en
    la rama correcta (p. ej. "over/under" para llegar a las stats de
    córners o al fallback de props de jugador).
    """
    low = leg.lower()
    if re.search(r"h[aá]ndicap|handicap", low):
        return "hándicap asiático"
    if re.search(r"doble\s+oportunidad|double\s*chance", low):
        return "doble oportunidad"
    if re.search(
        r"empate\s+no\s+v[aá]lido|resultado\s+sin\s+empate|draw\s+no\s+bet", low
    ):
        return "empate no válido"
    if re.search(
        r"ambos\s+(?:equipos?\s+)?(?:marcan|anotan)|both\s+teams|\bbtts\b", low
    ):
        return "ambos marcan"
    if re.search(
        r"resultado\s+exacto|marcador\s+(?:exacto|correcto)|correct\s+score", low
    ):
        return "resultado exacto"
    if re.search(r"\bover\b|\bunder\b|m[aá]s\s+de|menos\s+de|\d+\s*o\s+m[aá]s", low):
        return "over/under"
    if re.search(r"marca|asiste|tarjeta|goleador|scorer", low):
        return "jugador"
    if re.search(r"\bgana\b|\bganador\b|\bvence\b|vencedor|moneyline", low):
        return "ganador"
    return None


# Una línea que menciona la competición no es un enfrentamiento:
# "🎾 TENIS - Copa davis" casa con el patrón "A - B" pero es la
# cabecera del torneo. Un cruce real nombra dos participantes, no
# vocabulario de competición.
_COMPETITION_WORD = re.compile(
    r"\b(?:copa|liga|league|divisi[oó]n|challenger|atp|wta|itf|"
    r"champions|euroleague|euroliga|nba|torneo|premier|bundesliga|"
    r"eredivisie|serie\s+a|ligue|mls|europa\s+league|segunda|primera)\b",
    re.IGNORECASE,
)

# En los slips el mercado va tras el nombre separado por guion
# ("Zizou Bergs - Ganará el encuentro"): el lado derecho no es un
# participante. Palabras de mercado que nunca son nombre de equipo.
_MARKET_WORD = re.compile(
    r"\b(?:ganar[áa]?|ganador|vencedor|total|resultado|ambos|"
    r"m[aá]s|menos|h[aá]ndicap|empate|marca|descanso|set|juegos|"
    r"goles|c[oó]rners?|tarjetas?)\b",
    re.IGNORECASE,
)


def _extract_eventos(lines: list[str]) -> list[str]:
    """Líneas "EquipoA - EquipoB" / "A vs B" / "A v B" del mensaje, en orden.

    Se ignoran las líneas con viñeta: una pata tipo "Ambos equipos
    marcan - Sí" casa con el patrón de evento pero es una selección.
    También las cabeceras de competición ("TENIS - Copa davis"): casan
    con "A - B" sin ser un cruce.
    """
    eventos = []
    for line in lines:
        if _LEG_BULLET_PATTERN.match(line) or _LEG_NOISE_PATTERN.search(line):
            continue
        match = _TEAM_VS_TEAM_PATTERN.search(line)
        if match:
            # El filtro va sobre el match, no la línea: "LIGA: Alavés -
            # Valencia" conserva el cruce, "TENIS - Copa davis" no.
            # Y "Zizou Bergs - Ganará el encuentro" del slip tampoco:
            # "Ganará" es mercado, no participante.
            if not _COMPETITION_WORD.search(match.group(0)) and not _MARKET_WORD.search(
                match.group(0)
            ):
                eventos.append(match.group(0))
        elif re.search(r"\bv(?:s)?\.?\b", line, re.IGNORECASE):
            # "A vs B" del tipster o "A v B" del boleto (OCR):
            # "Dominko/Sesko v Cukierman/Shimanov". Aquí la línea
            # entera es el evento, así que el filtro va sobre ella.
            if not _COMPETITION_WORD.search(line):
                eventos.append(line.strip())
    return eventos


def _extract_combinada_cuota(lowered: str) -> Optional[float]:
    """Cuota total de la combinada: "cuota total X", "cuota X", "@X.XX"."""
    for pattern in (
        r"cuota\s+total\s*:?\s*([0-9]+[.,]?[0-9]*)",
        r"cuota\s*[:=]?\s*([0-9]+[.,]?[0-9]*)",
        r"@\s*([0-9]+[.,]?[0-9]{2,})",
    ):
        match = re.search(pattern, lowered)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def _rule_extract_combinada(
    text: str,
    informante: Optional[str] = None,
    fecha_referencia: Optional[datetime] = None,
) -> Optional[ExtractedPick]:
    """Parte una combinada por reglas: >=2 patas en líneas con viñeta.

    El evento se hereda a las patas cuando el mensaje trae UN solo
    partido (bet-builder) o tantos eventos como patas (acumulador en el
    mismo orden). Devuelve None si no se puede partir con confianza —
    entonces lo intenta el LLM.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    legs: list[str] = []
    for line in lines:
        match = _LEG_BULLET_PATTERN.match(line)
        if not match:
            continue
        leg = match.group("leg").strip()
        if len(leg) < 3 or _LEG_NOISE_PATTERN.search(leg):
            continue
        if leg not in legs:
            legs.append(leg)
    if len(legs) < 2:
        return None

    eventos = _extract_eventos(lines)
    shared_fecha = _extract_event_date(text, fecha_referencia)
    patas = []
    for idx, leg in enumerate(legs):
        if len(eventos) == 1:
            evento = eventos[0]
        elif len(eventos) == len(legs):
            evento = eventos[idx]
        else:
            evento = None
        patas.append(
            ExtractedPick(
                es_apuesta=True,
                seleccion=leg,
                evento=evento,
                mercado=_classify_leg_market(leg),
                linea=_extract_linea(leg),
                fecha_evento=shared_fecha,
                metodo="rule",
                confianza=0.8,
            )
        )

    return ExtractedPick(
        es_apuesta=True,
        evento=eventos[0] if len(eventos) == 1 else (" + ".join(eventos) or None),
        mercado="combinada",
        seleccion=" + ".join(legs),
        cuota=_extract_combinada_cuota(text.lower()),
        informante=informante,
        fecha_evento=shared_fecha,
        metodo="rule",
        confianza=0.8,
        patas=patas,
    )


def _patas_from_joined(pick: ExtractedPick) -> list[ExtractedPick]:
    """Reconstruye patas a partir del "A + B + C" clásico de `seleccion`.

    Red de seguridad para respuestas del LLM que unen las patas con
    " + " sin rellenar el array `patas`. Un trozo que parece una línea
    suelta ("+7.5 juegos", "2-1") no es una pata.
    """
    seleccion = pick.seleccion or ""
    if " + " not in seleccion:
        return []
    parts = [p.strip() for p in seleccion.split(" + ") if p.strip()]
    eventos = [e.strip() for e in pick.evento.split(" + ")] if pick.evento else []
    patas = []
    for idx, part in enumerate(parts):
        if len(part) < 3 or re.match(r"^[+-]?\s*\d", part):
            continue
        patas.append(
            ExtractedPick(
                es_apuesta=True,
                seleccion=part,
                evento=(
                    eventos[idx]
                    if len(eventos) == len(parts)
                    else (pick.evento if len(eventos) == 1 else None)
                ),
                mercado=_classify_leg_market(part),
                linea=_extract_linea(part),
                metodo=pick.metodo,
                confianza=pick.confianza,
            )
        )
    return patas


def _ensure_combinada_shape(
    pick: ExtractedPick, fecha_referencia: Optional[datetime] = None
) -> ExtractedPick:
    """Normaliza un pick combinada tras reglas o LLM.

    - Rellena `patas` desde el "A + B" de `seleccion` si el LLM no las
      devolvió.
    - Las patas heredan del padre lo que no tengan (deporte, evento,
      fecha, mercado clasificado, línea).
    - La `fecha_evento` del padre pasa a ser la de la ÚLTIMA pata: la
      combinada no se liquida hasta que acaban todos los partidos.
    - Si quedan menos de 2 patas degrada a pick simple (p. ej. "Brunold
      gana +7.5 juegos" no es una combinada aunque el LLM lo etiquete).
    """
    if not pick.es_apuesta:
        pick.patas = []
        return pick
    if not pick.patas:
        pick.patas = _patas_from_joined(pick)
    if len(pick.patas) < 2:
        pick.patas = []
        if pick.mercado and "combinada" in pick.mercado.lower():
            pick.mercado = None
        return pick

    pick.mercado = "combinada"
    if not pick.seleccion:
        pick.seleccion = " + ".join(p.seleccion or "" for p in pick.patas)
    for pata in pick.patas:
        # Una pata es por definición una selección de la combinada.
        pata.es_apuesta = True
        pata.deporte = pata.deporte or pick.deporte
        pata.evento = pata.evento or pick.evento
        pata.fecha_evento = (
            _sanitize_event_date(pata.fecha_evento, fecha_referencia)
            or pick.fecha_evento
        )
        pata.mercado = pata.mercado or _classify_leg_market(pata.seleccion or "")
        if pata.linea is None and pata.seleccion:
            pata.linea = _extract_linea(pata.seleccion)
        if pata.metodo == "unknown":
            pata.metodo = pick.metodo
        if not pata.confianza:
            pata.confianza = pick.confianza
    fechas = [p.fecha_evento for p in pick.patas if p.fecha_evento]
    if fechas:
        pick.fecha_evento = max(fechas)
    return pick


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
    r"\bpron[oó]stico\b",
    r"\bdoble\s*oportunidad\b",
    r"\bempate\b",
    r"\bresultado\b",
    r"\bganador\b",
    r"\bc[oó]rners?\b",
    r"\btarjetas?\b",
    r"\bmarcador\b",
    r"\bambos\s*equipos\s*marcan\b",
]

# Dos nombres propios separados por un guion (p. ej. "Levante - Athletic de
# Bilbao"), la forma más habitual de anunciar un partido sin usar "vs".
# Los conectores en minúscula ("de", "del", "la"...) forman parte del
# nombre: sin ellos "Celta de Vigo - Racing de Santander" se capturaba
# truncado como "Vigo - Racing".
_TEAM_NAME = (
    r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.]*"
    r"(?:\s+(?:(?:de|del|la|los|las|y|da|di|van|von)\s+)?"
    r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.]*)*"
)
_TEAM_VS_TEAM_PATTERN = re.compile(rf"\b{_TEAM_NAME}\s-\s{_TEAM_NAME}")

_NEGATIVE_PATTERNS = [
    r"\bpromo\b",
    r"\bacceso\b",
    r"\bacc(?:ede|eso)\b",
    r"\bplazas?\b",
    r"\bsorteo\b",
    r"\bmega\s*reto\b",
    r"\bretos?\s+del\s+año\b",
    r"\breto\s*especial\b",
    # Anuncios de escalera de reto/afiliado: "PASO 30€ - 96€",
    # "RETO 30€ ➜ 10.000€", "empieza a ganar", premiumpay. Una
    # apuesta real nunca expresa una progresión dinero→dinero
    # ni vende plazas — y si trajera cuota/stake propios, la
    # señal fuerte seguiría ganando.
    r"\d+[\d.,]*\s*€\s*[-–—➜→]+\s*\d+[\d.,]*\s*€",
    r"\bempieza\s+a\s+ganar\b",
    r"premiumpay",
    r"\bbuenos\s*días\b",
    r"\bgratis\b",
    r"\benlace\b",
    r"\blink\b",
    r"\bseguidores\b",
    # Anuncios de casas de apuestas (bonos/supercuotas): el OCR de la
    # promo "EL SUVIDÓN ... REAL MADRID GANA 1.90 ... SOLO NUEVOS
    # USUARIOS" generaba un pick fantasma. Un slip real nunca lleva
    # letra pequeña de promo.
    r"\bnuevos\s+usuarios\b",
    r"t&c'?s?\b",
    r"sujetas\s+a\s+cambios",
    r"\breg[ií]strate\b",
    r"cr[eé]ditos\s+de\s+apuesta",
    r"multiplica\s+tus\s+ganancias",
    # Celebración de aciertos pasados: "¡OTRO APUESTÓN GANADO!",
    # "acertando otra vez", "ya lo conseguimos". Son marketing del
    # tipster, no picks abiertos.
    r"\bacertad[oa]\b",
    r"\bganad[oa]\b",
    r"\bconseguimos\b",
    r"\bclavamos\b",
    # Caption de celebración bajo el boleto verde reposteado ("otro
    # verde para adentro", "seguimos sumando"). Solo rechazan si el
    # mensaje no trae cuota/stake propios — un pick real que dijera
    # "a por otro verde" + "CUOTA 1.50" sigue entrando.
    r"\botro\s+verde\b",
    r"\bseguimos\s+sumando\b",
    r"\btotal\s+ganado\b",
    r"\bpara\s+adentro\b",
    r"haceros\s+ganar",
]

# Señales estructurales fuertes: "cuota"/"stake"/"unidades" seguidas de un
# número. Una promo pura nunca las lleva, pero un pick real con publi de
# afiliado al final ("GANA 200€ GRATIS AQUÍ", links bdeal.io...) sí — sin
# esta precedencia, un negativo como "gratis" descartaba picks válidos.
_STRONG_PICK_PATTERNS = [
    r"\bcuota\s*[:•.\-@]?\s*\d",
    r"\bstake\s*[:•.\-]?\s*\d",
    r"\bunidades\s*[:•.\-]?\s*\d",
]


def _looks_like_bet(text: str) -> bool:
    """Heurística rápida para saber si merece la pena intentar extraer."""
    if not text:
        return False

    # Telegram envuelve en markdown ("__Cuota 1.50__"): "_" es carácter
    # de palabra y rompe los \b de los patrones ("__cuota" no tiene
    # borde antes de "cuota"). Se quita el formato antes de evaluar.
    lowered = re.sub(r"[*_~`]+", "", text).lower()
    if any(re.search(pattern, lowered) for pattern in _STRONG_PICK_PATTERNS):
        return True

    if any(re.search(pattern, lowered) for pattern in _NEGATIVE_PATTERNS):
        return False

    if any(re.search(pattern, lowered) for pattern in _POSITIVE_PATTERNS):
        return True

    # "Equipo - Equipo" (sin "vs" explícito) se busca sobre el texto
    # original, sin pasar a minúsculas, porque depende de las mayúsculas.
    return bool(_TEAM_VS_TEAM_PATTERN.search(text))


# --- Higiene de campos extraídos --------------------------------------
#
# El texto de Telegram llega con markdown y emojis que acababan dentro
# de `seleccion` ("**Chidek gana ****🇫🇷**"): ensucian el matching
# con proveedores, el dedup por texto y la UI.
_MARKDOWN_NOISE = re.compile(r"[*_~`]+")
_EMOJI_NOISE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictogramas, emoticonos, banderas
    "\U00002600-\U000027bf"  # símbolos varios + dingbats (❗➡✅)
    "\U00002b00-\U00002bff"  # flechas/suplementarios (⬆)
    "\ufe0f\u200d"  # variation selector + ZWJ
    "]+"
)
_LEADING_BULLETS = re.compile(r"^[•·►▶➤→\s]+")


def _clean_text_field(value: Optional[str]) -> Optional[str]:
    """Quita markdown/emojis/bullets de un campo de texto extraído."""
    if not value:
        return value
    cleaned = _MARKDOWN_NOISE.sub("", value)
    cleaned = _EMOJI_NOISE.sub("", cleaned)
    cleaned = _LEADING_BULLETS.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.rstrip(" -•·").strip() or None


# Señales léxicas para inferir `deporte` cuando el path de reglas no lo
# rellena: sin él, el verificador consulta primero las APIs de fútbol y
# un pick de tenis quema llamadas a proveedores equivocados.
_TENIS_SIGNAL = re.compile(
    r"tenis|\bchall(?:enger)?\.?\b|\batp\b|\bwta\b|\bitf\b|"
    r"grand\s*slam|tie[\s-]?break",
    re.IGNORECASE,
)
_FUTBOL_SIGNAL = re.compile(
    r"f[úu]tbol|la\s*liga|premier|champions|bundesliga|serie\s*a\b|"
    r"ligue\s*1\b|eredivisie|segunda\s*divisi[oó]n|copa\s+del\s+rey|"
    r"\bgoles?\b",
    re.IGNORECASE,
)
_BALONCESTO_SIGNAL = re.compile(
    r"baloncesto|basket|\bnba\b|euroleague|euroliga|\bncaa\b|liga\s+endesa",
    re.IGNORECASE,
)
_MOTOR_SIGNAL = re.compile(
    r"automovilismo|f[óo]rmula\s*1|\bf1\b|grand\s*prix|gran\s+premio|"
    r"\bmotogp\b|\bnascar\b|\bcoches\b|\bgp\b",
    re.IGNORECASE,
)
# Deportes sin proveedor de resultados configurado: el pick se guarda
# como rejected (auditable en la pantalla de debug) en lugar de quedar
# pendiente para siempre; un reproceso del raw lo recupera cuando haya
# soporte. El LLM puede devolver variantes — se comparan normalizadas.
_UNSUPPORTED_SPORTS = {
    "automovilismo",
    "f1",
    "formula 1",
    "fórmula 1",
    "motorsport",
    "motogp",
    "nascar",
    "motor",
}


def _detect_deporte(text: Optional[str]) -> Optional[str]:
    """Deporte inferido de las señales del mensaje completo."""
    if not text:
        return None
    if _TENIS_SIGNAL.search(text):
        return "tenis"
    if _FUTBOL_SIGNAL.search(text):
        return "fútbol"
    if _BALONCESTO_SIGNAL.search(text):
        return "baloncesto"
    if _MOTOR_SIGNAL.search(text):
        return "automovilismo"
    return None


# Cola de cabecera en mayúsculas pegada a la selección ("Menos de 3,5
# goles ESPAÑA"): el tipster pone "pick + competición" en la misma
# línea. Solo se recorta si queda texto en minúscula — una selección
# íntegra en mayúsculas ("MALLORCA RESULTADO SIN EMPATE") se conserva.
_TRAILING_CAPS = re.compile(r"(?:\s+[A-ZÁÉÍÓÚÑ]+)+\s*$")


def _strip_trailing_competition(seleccion: Optional[str]) -> Optional[str]:
    if not seleccion:
        return seleccion
    trimmed = _TRAILING_CAPS.sub("", seleccion)
    if trimmed and re.search(r"[a-záéíóúñ]", trimmed):
        return trimmed.strip()
    return seleccion


def _normalize_pick(pick: ExtractedPick, text: str) -> ExtractedPick:
    """Limpieza final común a reglas y LLM: quita markdown/emojis de los
    campos de texto e infiere `deporte` de las señales del mensaje si el
    extractor no lo rellenó (las pistas deportivas suelen estar en la
    cabecera — "CHALL RENNES 🎾" — no en la selección)."""
    pick.seleccion = _strip_trailing_competition(_clean_text_field(pick.seleccion))
    pick.evento = _clean_text_field(pick.evento)
    if pick.es_apuesta and not pick.deporte:
        pick.deporte = _detect_deporte(text)
    if pick.es_apuesta and (pick.deporte or "").strip().lower() in _UNSUPPORTED_SPORTS:
        pick.es_apuesta = False
        pick.metodo = "rejected"
    for pata in pick.patas:
        _normalize_pick(pata, text)
    return pick


# Palabras que delatan la línea del pick. Con límites de palabra (\b) —
# el matching por substring confundía "mover" con "over" y "ganar" con
# "gana" — y en español, porque los tipsters escriben "Menos de 3,5
# goles" o "Más de 11 córners", no "under"/"over". "ganar" se excluye a
# propósito: aparece en la prosa del análisis ("necesita ganar"), no en
# la etiqueta del pick.
_SELECCION_KEYWORD = re.compile(
    r"\b(?:gana(?:r[aá])?|h[aá]ndicap|handicap|over|under|"
    r"menos\s+de|m[aá]s\s+de|ambos\s+marcan|ambas\s+marcan|empate|"
    r"doble\s+oportunidad|c[oó]rners?|corners?|tarjetas?|"
    r"resultado\s+sin\s+empate|draw\s+no\s+bet)\b|"
    r"(?<!\d)[+-]\s*\d+(?:[.,]\d+)?",
    re.IGNORECASE,
)

# Una selección es una etiqueta corta ("Menos de 3,5 goles", "Bergs
# gana"); por encima de esto es prosa del análisis.
_SELECCION_MAX_LEN = 120


def _rule_extract(
    text: str,
    informante: Optional[str] = None,
    fecha_referencia: Optional[datetime] = None,
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

    # Buscar selección: primera línea con palabras clave, saltando las
    # líneas de publi/afiliación (llevan enlaces o "gratis"). Sin este
    # filtro, un footer tipo "GANA 200€ GRATIS AQUÍ bdeal.io/..." se
    # confundía con la selección porque contiene "gana".
    promo_line = re.compile(r"https?://|www\.|t\.me/|\]\(|gratis", re.IGNORECASE)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    seleccion = None
    for line in lines:
        low = line.lower()
        if promo_line.search(low):
            continue
        # Una selección es una etiqueta corta ("Menos de 3,5 goles");
        # un párrafo de análisis nunca lo es. Sin la guarda, la prosa
        # colaba por keywords sueltas ("un Celta que necesita ganar")
        # y se guardaba entera como `seleccion`.
        if len(line) > _SELECCION_MAX_LEN:
            continue
        if _SELECCION_KEYWORD.search(low) and not low.startswith(
            ("cuota", "stake", "unidades")
        ):
            seleccion = line
            break

    if not seleccion:
        return None

    # Enfrentamiento explícito ("A - B" / "A vs B" en alguna línea del
    # mensaje): se rellena `evento` gratis por reglas. Cuando el rival
    # solo aparece en la prosa del análisis (formato Bet Fran), evento
    # queda a None y el caller decide si gasta una pasada LLM extra.
    evento = next(iter(_extract_eventos(lines)), None)

    return ExtractedPick(
        es_apuesta=True,
        evento=evento,
        mercado=_classify_leg_market(seleccion),
        seleccion=seleccion,
        cuota=float(cuota_match.group(1).replace(",", ".")),
        stake=float(stake_match.group(1).replace(",", ".")),
        informante=informante,
        fecha_evento=_extract_event_date(text, fecha_referencia),
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
  "linea": number | null,
  "patas": array | null
}

Reglas:
- Si no es una apuesta, devuelve es_apuesta = false y el resto null.
- NO son apuestas (es_apuesta = false) aunque mencionen selecciones o cuotas:
  * Mensajes que celebran aciertos pasados ("acertamos", "ganado", "✅ apuesta acertada", "llevamos X aciertos", "ya lo conseguimos", "ver como ganáis dinero"): son marketing del tipster, no picks abiertos.
  * Anuncios o promociones de casas de apuestas: bonos de bienvenida, supercuotas ("Suvidón", "supercuota", "multiplica tus ganancias"), "solo nuevos usuarios", "T&C", "créditos de apuesta", "regístrate".
  * Boletos ya liquidados reposteados como prueba: sello "GANADOR"/"GANADA" o "Ganancias <importe>" SIN la palabra "potenciales" (en un slip abierto siempre pone "Ganancias potenciales").
  Una apuesta abierta real es una recomendación de algo que AÚN no se ha jugado.
- "seleccion" es lo recomendado (ej. "Titouan Droguet gana", "Real Sociedad B Hándicap Asiático +1.5"). Si es una combinada/"crear apuesta" (varias selecciones en un mismo boleto), únelas con " + " (ej. "Más de 1 gol + Más de 2 tarjetas").
- "patas": SOLO si es una combinada/"crear apuesta"/acumulador (varias selecciones en un mismo boleto): un array con UN objeto por selección del boleto, cada uno con la misma estructura {"seleccion", "evento", "mercado", "linea", "deporte", "fecha_evento", "cuota"} y las mismas reglas de formato ("cuota" por pata solo si aparece explícita; si no, null). En una combinada cada pata puede ser de un partido distinto (rellena su "evento" propio) o del mismo partido (bet-builder: repite el mismo "evento" en todas). Si NO es combinada, "patas" = null. Si solo puedes identificar UNA selección, NO es combinada: "patas" = null y trátala como pick simple.
- "evento" es el enfrentamiento concreto (ej. "Sevilla - Barcelona", "Zizou Bergs vs Jurij Rodionov"). Si el texto muestra "EquipoA - EquipoB" o "EquipoA vs EquipoB", usa ese formato completo con ambos — nunca solo uno. Si los dos participantes aparecen sueltos en el texto sin "vs" (p. ej. el rival solo se menciona en el análisis), forma el evento con ambos nombres ("Bergs vs Rodionov"). Si SOLO aparece la competición ("Copa Davis", "LaLiga") sin los dos participantes, usa la competición tal cual (sirve de pista al verificador en tenis). OJO con el OCR de boletos EN VIVO (bet365): el cruce aparece en su propia línea como "EquipoA v EquipoB" o "EquipoA 0 0 EquipoB" (el "0 0" es el marcador en directo, no parte del nombre) — usa ese cruce como "evento", NUNCA la cabecera de liga de arriba ("Italia - Serie A" no es un partido).
- "mercado" es el tipo de apuesta: usa siempre una de estas etiquetas si aplica: "ganador", "hándicap asiático", "over/under" (o "over/under goles", "over/under juegos" según el deporte). Si es una combinada/"crear apuesta" con varias selecciones, usa "combinada". Si no encaja en ninguna, describe brevemente el mercado.
- "deporte" debe ser una palabra normalizada y simple: "fútbol", "tenis", "baloncesto", etc.
- "linea" es el valor numérico de la línea cuando el mercado es hándicap asiático u over/under (ej. 1.5, -1.5, 2.5). Con signo si es hándicap (+1.5 a favor del equipo de "seleccion", -1.5 en contra). Sin signo si es over/under. Si el mercado no tiene línea (p. ej. "ganador"), déjalo null.
- Extrae "cuota" solo si aparece un número claramente asociado a la cuota/odds de la selección.
- Extrae "stake" ÚNICAMENTE si el texto menciona explícitamente la palabra "stake" o "unidades" seguida de un número (normalmente entre 1 y 10).
- NUNCA uses como "stake" importes en euros/dólares que aparezcan en capturas de pantalla del boleto de una casa de apuestas (p. ej. "Importe", "Imp:", "Ganancias", "Cerrar apuesta", saldo, importe apostado, importe a pagar). Esos son cantidades de dinero del boleto del tipster, no el sistema de unidades de stake. Si no hay mención explícita de "stake" o "unidades", deja "stake" = null.
- "fecha_evento" es la fecha (y hora si aparece) del partido/evento, en formato ISO 8601 (ej. "2026-09-12T14:00:00"). Solo si aparece explícitamente en el texto. Si el texto da día y mes pero NO año (p. ej. "15 sep 21:00"), usa EXACTAMENTE el año de la fecha del mensaje indicada en la cabecera — nunca otro año. Si la fecha que aparece es ANTERIOR a la fecha del mensaje, devuelve null: seguramente es la fecha de emisión del boleto u otra cosa, no la del partido. OJO: en tenis, "1 set"/"2 set" NO son fechas, son sets del partido. Si no hay fecha, null.
- No inventes ni deduzcas valores (cuota, stake, casa, fecha, línea, etc.) que no estén explícitamente en el texto. Ante la duda, usa null.
- No añadas markdown, solo el JSON.
"""


_FIXTURE_V_LINE = re.compile(r"^(.{2,50}?)\s+v\s+(.{2,50}?)$", re.IGNORECASE)
_FIXTURE_SCORE_LINE = re.compile(r"^(.{2,40}?)\s+\d+\s*[-–—]?\s*\d+\s+(.{2,40}?)$")


def _fixture_from_slip_ocr(ocr_text: str | None) -> str | None:
    """El cruce "A v B" o "A 0-0 B" que el boleto en vivo imprime en su
    propia línea ("Como v Parma", "Sporting d'Escaldes 0 - 2 Santa
    Coloma"). Fallback determinista para cuando el LLM devuelve la
    cabecera de liga ("Italia - Serie A") en vez del partido."""
    if not ocr_text:
        return None
    score_line = v_line = None
    for line in ocr_text.splitlines():
        line = line.strip()
        m = _FIXTURE_SCORE_LINE.match(line)
        if m and score_line is None:
            score_line = f"{m.group(1)} - {m.group(2)}"
            continue
        m = _FIXTURE_V_LINE.match(line)
        if m and v_line is None:
            v_line = f"{m.group(1)} - {m.group(2)}"
    return score_line or v_line


async def _llm_extract(
    text: str,
    api_key: str,
    informante: Optional[str] = None,
    fecha_referencia: Optional[datetime] = None,
) -> Optional[ExtractedPick]:
    client = AsyncOpenAI(api_key=api_key)
    fecha_str = (
        fecha_referencia.strftime("%Y-%m-%d %H:%M")
        if fecha_referencia
        else "desconocida"
    )
    response = await call_with_retry(
        lambda: client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Canal: {informante or 'desconocido'}\n"
                        f"Fecha del mensaje: {fecha_str}\n\n"
                        f"Mensaje:\n{text}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        ),
        f"extracción LLM ({informante or 'desconocido'})",
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
        pick.fecha_evento = _extract_event_date(text, fecha_referencia)
    if pick.linea is None and pick.seleccion:
        pick.linea = _extract_linea(pick.seleccion)
    if not _extract_eventos([pick.evento or ""]):
        # El LLM a veces devuelve la cabecera de liga ("Italia - Serie
        # A") o null aunque el OCR del boleto tenga el cruce ("Como v
        # Parma") en una línea propia.
        fixture = _fixture_from_slip_ocr(text)
        if fixture:
            pick.evento = fixture
    return pick


# Marcadores de boleto ya LIQUIDADO: el OCR de un slip liquidado lleva el
# sello "GANADOR"/"GANADA" en mayúsculas (p. ej. fotos de "verdes" que el
# tipster reposte a como prueba). No es una apuesta abierta aunque traiga
# selección y cuota. Se busca sobre el texto original SIN pasar a
# minúsculas para no confundir el sello con el mercado "ganador" de un
# pick real.
_SETTLED_TICKET_PATTERN = re.compile(
    r"\bGANADOR\b|\bGANADA\b|APUESTA\s+GANADA|\bWON\b|(?i:\breturned\b)"
)

# Boleto cobrado SIN el sello (el OCR a veces lo pierde): la línea de
# premio pagado es "<importe>€ Ganancias" — importe DELANTE de la
# palabra. En un slip abierto es al revés ("Ganancias 3.570,00€" o
# "Ganancias potenciales") y además lleva "Cerrar apuesta"/"Añadir
# selección"/"hoja de apuestas", así que esos marcadores descartan el
# caso abierto (p. ej. "Imp: 100,00€ Ganancias ... Cerrar apuesta").
# El "$" multilínea evita falsos positivos con "Imp: 100€ Ganancias 180€"
# (importe seguido del premio): solo casa cuando "Ganancias" cierra la
# línea, p. ej. el payout final "23333,33€ Ganancias" del slip cobrado.
_SETTLED_PAYOUT_PATTERN = re.compile(r"\d[\d.,]*\s*€\s*Ganancias\s*$", re.MULTILINE)
_OPEN_SLIP_PATTERN = re.compile(
    r"Cerrar apuesta|Añadir selecci[oó]n|potencial|Crear apuesta|"
    r"Reutilizar|hoja de apuestas|Compartir",
    re.IGNORECASE,
)


def _is_settled_ticket(text: str) -> bool:
    """True si el texto parece un boleto YA liquidado/cobrado."""
    if _SETTLED_TICKET_PATTERN.search(text):
        return True
    low = text.lower()
    # Premio pagado sin "potencial": en slips ABIERTOS de bet365 el premio
    # siempre es "Ganancias potenciales"; la pareja "Imp:/Importe: <x>€" +
    # "Ganancias <y>€" a secas es la vista de un boleto ya cobrado que el
    # tipster repostea como prueba ("verdes"). "Crear apuesta" no vale
    # como marcador de abierto aquí porque también es el nombre del
    # mercado bet-builder en slips liquidados.
    if (
        re.search(r"\bimp(?:orte)?:", low)
        and re.search(r"\bganancias\s*:?\s*€?\s*\d", low)
        and "potencial" not in low
        and not re.search(r"cerrar apuesta|añadir selecci[oó]n", low)
    ):
        return True
    return bool(_SETTLED_PAYOUT_PATTERN.search(text)) and not bool(
        _OPEN_SLIP_PATTERN.search(text)
    )


async def extract_pick(
    text: str,
    api_key: str,
    informante: Optional[str] = None,
    fecha_referencia: Optional[datetime] = None,
) -> Optional[ExtractedPick]:
    """Extractor híbrido.

    `fecha_referencia` es la fecha real del mensaje de Telegram: sirve de
    ancla para resolver fechas sin año ("15 sep 21:00") tanto en reglas
    como en el LLM.
    """
    if _is_settled_ticket(text):
        return ExtractedPick(
            es_apuesta=False, informante=informante, metodo="rejected", confianza=0.95
        )

    if not _looks_like_bet(text):
        return ExtractedPick(
            es_apuesta=False, informante=informante, metodo="rejected", confianza=0.95
        )

    # Combinadas: si hay señal de combinada se intenta partir por
    # reglas y, si no se puede, se va directo al LLM. `_rule_extract`
    # NO se ejecuta aquí: cogería la primera pata como si fuera un pick
    # simple y perderíamos el resto del boleto.
    if _COMBINADA_PATTERN.search(text):
        rule_comb = _rule_extract_combinada(
            text, informante=informante, fecha_referencia=fecha_referencia
        )
        if rule_comb is not None:
            rule_comb.fecha_evento = _sanitize_event_date(
                rule_comb.fecha_evento, fecha_referencia
            )
            return _normalize_pick(
                _ensure_combinada_shape(rule_comb, fecha_referencia), text
            )
        llm_comb = await _llm_extract(
            text, api_key, informante=informante, fecha_referencia=fecha_referencia
        )
        if llm_comb is None:
            return None
        llm_comb.fecha_evento = _sanitize_event_date(
            llm_comb.fecha_evento, fecha_referencia
        )
        return _normalize_pick(
            _ensure_combinada_shape(llm_comb, fecha_referencia), text
        )

    rule_result = _rule_extract(
        text, informante=informante, fecha_referencia=fecha_referencia
    )
    if rule_result:
        if rule_result.es_apuesta and not rule_result.evento:
            # Las reglas no ven el enfrentamiento cuando solo aparece en
            # la prosa del análisis ("el H2H favorece a Mayot" en Bet
            # Fran): una segunda pasada LLM intenta rellenarlo. Solo se
            # gasta la llamada en este caso — los picks ya completos por
            # reglas siguen ahorrando el LLM como antes.
            llm_result = await _llm_extract(
                text,
                api_key,
                informante=informante,
                fecha_referencia=fecha_referencia,
            )
            if llm_result is not None and llm_result.es_apuesta:
                # Merge conservador: el pick de reglas es la base (su
                # seleccion/cuota/stake son fiables) y el LLM solo
                # aporta los campos que las reglas no rellenan.
                for field in ("evento", "deporte", "mercado", "casa", "explicacion"):
                    if not getattr(rule_result, field):
                        setattr(rule_result, field, getattr(llm_result, field))
                if llm_result.evento:
                    rule_result.metodo = "rule+llm"
        rule_result.fecha_evento = _sanitize_event_date(
            rule_result.fecha_evento, fecha_referencia
        )
        return _normalize_pick(rule_result, text)

    llm_result = await _llm_extract(
        text, api_key, informante=informante, fecha_referencia=fecha_referencia
    )
    if llm_result is not None:
        llm_result.fecha_evento = _sanitize_event_date(
            llm_result.fecha_evento, fecha_referencia
        )
        # El LLM puede devolver patas aunque el mensaje no tuviera la
        # señal léxica de combinada (p. ej. un slip solo con viñetas).
        llm_result = _ensure_combinada_shape(llm_result, fecha_referencia)
        return _normalize_pick(llm_result, text)
    return llm_result
