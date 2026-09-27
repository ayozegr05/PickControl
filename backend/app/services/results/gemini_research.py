"""Investigador de último recurso vía Gemini API + url_context.

Cuando TODA la cascada determinista falla, este provider usa Gemini
gratis (free tier, sin grounding de pago) en dos saltos:

1. BÚSQUEDA: el modelo lee una página de resultados de DuckDuckGo Lite
   vía `url_context` y devuelve las URLs que hablan del partido.
2. VERIFICACIÓN: filtramos esas URLs por la allowlist de dominios de
   resultados y el modelo lee SOLO esas páginas concretas, devolviendo
   el estado del fixture con las fuentes consultadas.

Fase 1 — solo estados "no jugado": `find_postponed_match` devuelve
MatchState (cancelled/postponed/walkover) y SOLO si la respuesta cita
al menos una de las URLs fiables que le dimos — sin cita de dominio
conocido la respuesta se descarta (anti-alucinación). Nunca liquida
resultados: `find_match` devuelve siempre None, así que es inofensivo
en la cascada de marcadores y solo se consulta en el camino de
aplazados/anuladas.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    MatchResult,
    MatchState,
    is_rate_limited,
    mark_rate_limited,
    rate_limit_from,
)

logger = get_logger("app.results.gemini_research")

_PROVIDER_NAME = "gemini-search"
# En orden de preferencia; los 503 de "high demand" son transitorios y
# saltamos al siguiente modelo. Todos soportan url_context en free tier.
_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
)
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/" "{model}:generateContent"
)
_TIMEOUT = 90.0
_MAX_VERIFIED_URLS = 3

# Dominios de resultados que validan una respuesta (allowlist anti-
# alucinación): la cita tiene que caer en alguno de estos — un blog
# cualquiera no verifica nada. También filtra qué URLs de la búsqueda
# se dejan leer en el salto 2.
_ALLOWED_DOMAINS = (
    "livescore.com",
    "sofascore.com",
    "flashscore.",
    "365scores.com",
    "tennis.com",
    "atptour.com",
    "wtatennis.com",
    "itftennis.com",
    "daviscup.com",
    "espn.com",
    "espn.",
    "bbc.",
    "skysports.com",
    "uefa.com",
    "fifa.com",
    "nba.com",
    "acb.com",
    "euroleague.net",
    "olympics.com",
    "resultados-futbol.com",
    "besoccer.com",
    "soccerway.com",
    "marca.com",
    "as.com",
    "sport.es",
    "mundodeportivo.com",
)

# Estados "no jugado" que el modelo puede devolver -> normalizado.
_VOID_ALIASES = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "cancelado": "cancelled",
    "postponed": "postponed",
    "aplazado": "postponed",
    "walkover": "cancelled",
    "walk over": "cancelled",
    "w/o": "cancelled",
    "wo": "cancelled",
    "retired": "cancelled",
    "retirada": "cancelled",
    "abandoned": "cancelled",
    "not played": "cancelled",
    "no disputado": "cancelled",
    "suspended": "postponed",
    "suspendido": "postponed",
}

_SPORT_ES = {
    "futbol": "fútbol",
    "tenis": "tenis",
    "baloncesto": "baloncesto",
}

_SEARCH_PROMPT = """Lee esta página de resultados de búsqueda: {search_url}

Busco información sobre este partido concreto:
- Deporte: {sport}
- Partido: {hint}
- Fecha: {date}

Devuelve SOLO JSON válido, sin markdown:
{{"urls": ["<url1>", "<url2>"]}}

con las URLs de resultados de búsqueda que hablen de ESE partido (máx.
5), o una lista vacía si ninguno trata de ese partido."""

_VERIFY_PROMPT = """Lee estas páginas de resultados deportivos:
{urls}

Comprueba si este partido se disputó:
- Deporte: {sport}
- Partido/pista: {hint}
- Fecha: {date}

Responde SOLO con JSON válido, sin markdown:
{{"status": "played|cancelled|postponed|walkover|unknown",
 "home_team": "<equipo/jugador 1>", "away_team": "<equipo/jugador 2>",
 "sources": ["<url1>"]}}

Reglas:
- "cancelled"/"postponed"/"walkover" SOLO si alguna de las páginas lo
  muestra explícitamente (Canc., PPD, Walkover, Retired...).
- "played" si se disputó con marcador conocido.
- "unknown" si las páginas no hablan del partido o no son claras.
- "sources" lista SOLO las URLs de las páginas anteriores que lo
  confirman (nunca inventes URLs)."""


def _domains_of(urls: list[str]) -> set[str]:
    domains: set[str] = set()
    for url in urls:
        host = (urlparse(str(url)).hostname or "").lower()
        if host:
            domains.add(host.removeprefix("www."))
    return domains


def _trusted_domains(domains: set[str]) -> bool:
    """Alguna cita cae en la allowlist de webs de resultados."""
    return any(
        any(
            domain == allowed
            or domain.endswith("." + allowed)
            or allowed.endswith("." + domain)
            for allowed in _ALLOWED_DOMAINS
        )
        for domain in domains
    )


def _filter_trusted_urls(urls: list[str]) -> list[str]:
    """URLs citables: solo dominios de la allowlist."""
    return [
        u
        for u in urls
        if _trusted_domains(_domains_of([u])) and str(u).startswith("http")
    ]


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Primer objeto JSON del texto (tolera code fences)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class GeminiResearchProvider:
    """Provider-investigador: búsqueda con citas como último recurso.

    Una instancia sirve los tres deportes (`SUPPORTED_SPORTS`); el
    deporte llega por constructor igual que en `Scores365Provider`
    porque `find_*` no lo recibe como parámetro.
    """

    NAME = _PROVIDER_NAME
    SUPPORTED_SPORTS = frozenset({"futbol", "tenis", "baloncesto"})

    def __init__(self, sport: str, api_key: str) -> None:
        self._sport = sport
        self._api_key = api_key

    async def _call_model(
        self, model: str, prompt: str
    ) -> tuple[Optional[dict[str, Any]], Optional[httpx.Response]]:
        """Una llamada a generateContent; (payload, resp_429|None)."""
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"url_context": {}}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _ENDPOINT.format(model=model),
                    json=body,
                    headers={"x-goog-api-key": self._api_key},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if rate_limit_from(exc):
                return None, exc.response
            logger.warning(
                "[GEMINI] %s HTTP %s: %s",
                model,
                exc.response.status_code,
                exc.response.text[:150],
            )
            return None, None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("[GEMINI] %s error: %r", model, exc)
            return None, None
        return resp.json(), None

    async def _ask(self, prompt: str) -> Optional[dict[str, Any]]:
        """generateContent con url_context, con fallback de modelos."""
        for model in _MODELS:
            payload, quota_resp = await self._call_model(model, prompt)
            if quota_resp is not None:
                mark_rate_limited(_PROVIDER_NAME, quota_resp)
                logger.warning("[GEMINI] Cuota agotada; se omite")
                return None
            if payload is not None:
                return payload
        return None

    def _payload_text(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        return "".join(p.get("text") or "" for p in parts)

    def _payload_urls(self, payload: dict[str, Any]) -> list[str]:
        """URLs realmente leídas por url_context (auditoría)."""
        candidates = payload.get("candidates") or []
        if not candidates:
            return []
        meta = candidates[0].get("urlContextMetadata") or {}
        urls: list[str] = []
        for entry in meta.get("urlMetadata") or []:
            url = entry.get("retrievedUrl")
            if url:
                urls.append(url)
        return urls

    def _sport_es(self) -> str:
        return _SPORT_ES.get(self._sport, self._sport)

    async def _search_urls(self, date: datetime, hint: str) -> list[str]:
        """Salto 1: DDG Lite vía url_context -> URLs fiables del partido."""
        query = f"{hint} {self._sport_es()} {date.strftime('%d %B %Y')}"
        search_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
        payload = await self._ask(
            _SEARCH_PROMPT.format(
                search_url=search_url,
                sport=self._sport_es(),
                hint=hint,
                date=date.strftime("%d de %B de %Y"),
            )
        )
        if payload is None:
            return []
        data = _extract_json(self._payload_text(payload))
        if not data:
            return []
        urls = [str(u) for u in (data.get("urls") or []) if u]
        return _filter_trusted_urls(urls)[:_MAX_VERIFIED_URLS]

    async def _verify_on_urls(
        self, date: datetime, hint: str, urls: list[str]
    ) -> Optional[MatchState]:
        """Salto 2: el modelo lee SOLO las URLs fiables y reporta estado."""
        payload = await self._ask(
            _VERIFY_PROMPT.format(
                urls="\n".join(f"- {u}" for u in urls),
                sport=self._sport_es(),
                hint=hint,
                date=date.strftime("%d de %B de %Y"),
            )
        )
        if payload is None:
            return None
        data = _extract_json(self._payload_text(payload))
        if not data:
            return None
        status = _VOID_ALIASES.get(str(data.get("status") or "").lower().strip())
        if status is None:
            return None
        # Anti-alucinación: la cita debe ser una de las páginas fiables
        # que le dimos (o que url_context confirma haber leído).
        read_urls = set(urls) | set(self._payload_urls(payload))
        sources = [str(s) for s in (data.get("sources") or []) if str(s) in read_urls]
        if not sources or not _trusted_domains(_domains_of(sources)):
            logger.info(
                "[GEMINI] '%s' status=%s descartado: sin cita fiable %s",
                hint,
                data.get("status"),
                (data.get("sources") or [])[:3],
            )
            return None
        logger.info(
            "[GEMINI] '%s': fixture %s confirmado por %s",
            hint,
            status,
            sources[:3],
        )
        return MatchState(
            home_team=str(data.get("home_team") or hint),
            away_team=str(data.get("away_team") or ""),
            status=status,
        )

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """El fixture consta NO jugado en una fuente fiable -> MatchState.

        Anti-alucinación: exige status "no jugado" Y al menos una cita
        de las URLs fiables leídas. "played"/"unknown" o respuestas sin
        fuentes devuelven None — el pick sigue pendiente.
        """
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = (team_hint or "").strip()
        if not hint:
            return None
        urls = await self._search_urls(date, hint)
        if not urls:
            logger.info("[GEMINI] '%s': búsqueda sin URLs fiables", hint)
            return None
        return await self._verify_on_urls(date, hint, urls)

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Fase 1: nunca liquida resultados — solo estados no jugados."""
        return None
