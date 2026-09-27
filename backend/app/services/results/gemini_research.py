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
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.results.base import (
    MatchResult,
    MatchState,
    MatchStats,
    is_rate_limited,
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
# Cooldown propio tras un 429: el free tier de Gemini limita por ritmo
# (RPM), no por día — aparcar hasta mañana (comportamiento estándar de
# `mark_rate_limited`) dejaría al investigador muerto por una ráfaga.
_429_COOLDOWN = timedelta(minutes=20)

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
    # Agregadores de resultados de segunda línea: menos fiables que los
    # de arriba pero válidos cuando la doble lectura coincide.
    "aiscore.com",
    "tennisexplorer.com",
    "tennis24.com",
    "soccer24.com",
    "basketball24.com",
    "scores24.live",
    "scorebar.com",
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

_STATS_PROMPT = """Del siguiente texto extraído de una página de
resultados deportivos, extrae las estadísticas del partido {hint}
({sport}, {date}).

TEXTO:
\"\"\"
{text}
\"\"\"

Devuelve SOLO JSON válido, sin markdown:
{{"team1": "<nombre tal como aparece primero en la tabla>",
 "team2": "<el otro>",
 "stats": {{"<nombre de la estadística>": [<valor_eq1>, <valor_eq2>]}}}}

Reglas:
- Solo estadísticas VISIBLES en el texto con un número por equipo.
- Los valores se copian EXACTOS, en el orden en que aparecen los
  equipos en la tabla. Si solo hay un total sin desglose, no lo
  incluyas.
- Si el texto no contiene estadísticas del partido, {{"stats": {{}}}}."""

# Estadística canónica (la clave que espera el verificador en
# MatchStats.values) -> regex de cómo se llama en las webs (ES/EN).
_STAT_KEYWORDS: tuple[tuple[str, re.Pattern], ...] = (
    ("Aces", re.compile(r"\baces?\b", re.I)),
    (
        "Double Faults",
        re.compile(r"double\s*fault|doble\s*falta", re.I),
    ),
    ("Corner Kicks", re.compile(r"corner|c[oó]rner|esquina", re.I)),
    (
        "Yellow Cards",
        re.compile(r"yellow\s*card|tarjeta\s*amarilla|amarilla", re.I),
    ),
    (
        "Red Cards",
        re.compile(r"red\s*card|tarjeta\s*roja|roja", re.I),
    ),
    (
        "Shots on Goal",
        re.compile(
            r"shots?\s*on\s*(?:goal|target)|tiros?\s*a\s*puerta|"
            r"remates?\s*a\s*puerta",
            re.I,
        ),
    ),
    (
        "Total Shots",
        re.compile(r"total\s*shots|tiros?\s*totales|remates?", re.I),
    ),
    ("Fouls", re.compile(r"fouls?|faltas", re.I)),
    (
        "Offsides",
        re.compile(r"offsides?|fuera\s*de\s*juego|fueras?\s*de\s*juego", re.I),
    ),
)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}
_MAX_PAGE_CHARS = 30000
_PAGE_CHARS_PER_URL = 15000
_STATS_VALIDATION_WINDOW = 160

_SEARCH_PROMPT = """Lee esta página de resultados de búsqueda: {search_url}

Busco información sobre este partido concreto:
- Deporte: {sport}
- Partido: {hint}
- Fecha: {date}

Devuelve SOLO JSON válido, sin markdown:
{{"urls": ["<url1>", "<url2>"]}}

con las URLs de resultados de búsqueda que hablen de ESE partido (máx.
5), o una lista vacía si ninguno trata de ese partido."""

_VERIFY_PROMPT = """Textos extraídos de páginas de resultados
deportivos (cada bloque empieza por la URL de la página):

{pages}

Comprueba el estado y marcador de este partido:
- Deporte: {sport}
- Partido/pista: {hint}
- Fecha: {date}

Responde SOLO con JSON válido, sin markdown:
{{"status": "played|cancelled|postponed|walkover|unknown",
 "home_team": "<equipo/jugador 1>", "away_team": "<equipo/jugador 2>",
 "home_score": <entero o null>, "away_score": <entero o null>,
 "sets": [[<juegos_home>,<juegos_away>], ...] o null,
 "sources": ["<url1>"]}}

Reglas:
- "played" SOLO si alguna página muestra el partido terminado con
  marcador: copia el marcador EXACTO tal como aparece (en tenis
  home_score/away_score son sets ganados y "sets" los juegos de cada
  set; en fútbol/baloncesto son goles/puntos finales). null si la
  página no muestra el número — nunca inventes un marcador.
- "cancelled"/"postponed"/"walkover" SOLO si alguna página lo muestra
  explícitamente (Canc., PPD, Walkover, Retired...).
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
        self._cooldown_until: Optional[datetime] = None

    def _in_cooldown(self) -> bool:
        """429 reciente (ritmo del free tier) o marca global del día."""
        if self._cooldown_until and self._cooldown_until > utc_now():
            return True
        return is_rate_limited(_PROVIDER_NAME)

    async def _call_model(
        self, model: str, prompt: str, use_tool: bool = False
    ) -> tuple[Optional[dict[str, Any]], Optional[httpx.Response]]:
        """Una llamada a generateContent; (payload, resp_429|None).

        `use_tool` añade url_context (fetcher de Google): solo para el
        salto de búsqueda, porque los buscadores bloquean la IP del
        servidor pero no la de Google. Su cuota es minuscula, así que
        verificación/stats van por fetch propio + texto plano.
        """
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        }
        if use_tool:
            body["tools"] = [{"url_context": {}}]
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

    async def _ask(
        self, prompt: str, use_tool: bool = False
    ) -> Optional[dict[str, Any]]:
        """generateContent de texto plano, con fallback de modelos."""
        quota_hits = 0
        for model in _MODELS:
            payload, quota_resp = await self._call_model(model, prompt, use_tool)
            if quota_resp is not None:
                # La cuota del free tier es por modelo: un 429 no
                # implica que los demás estén secos — se prueban.
                quota_hits += 1
                continue
            if payload is not None:
                return payload
        if quota_hits:
            self._cooldown_until = utc_now() + _429_COOLDOWN
            logger.warning(
                "[GEMINI] 429 en %d modelos — cooldown %s min",
                quota_hits,
                _429_COOLDOWN.seconds // 60,
            )
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
        """Salto 1: búsqueda -> URLs fiables del partido.

        Los buscadores bloquean la IP del servidor (DDG 403, Bing
        degradado), así que la lectura de resultados la hace el
        fetcher de Google vía url_context sobre DDG Lite — la única
        llamada con tool del pipeline; el resto va por fetch propio
        y texto plano para no quemar su cuota mínima.

        El día exacto en la query empobrece los resultados de DDG (las
        páginas no indexan la fecha como texto), así que se prueba
        primero solo hint+deporte y luego con mes/año; la fecha queda
        en el prompt para que el modelo filtre por relevancia."""
        queries = [
            f"{hint} {self._sport_es()}",
            f"{hint} {self._sport_es()} {date.strftime('%B %Y')}",
        ]
        urls: list[str] = []
        seen: set[str] = set()
        for query in queries:
            search_url = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
            payload = await self._ask(
                _SEARCH_PROMPT.format(
                    search_url=search_url,
                    sport=self._sport_es(),
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                ),
                use_tool=True,
            )
            if payload is None:
                continue
            data = _extract_json(self._payload_text(payload))
            if not data:
                continue
            for u in data.get("urls") or []:
                u = str(u)
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)
            if urls:
                break
        return _filter_trusted_urls(urls)[:_MAX_VERIFIED_URLS]

    async def _verify_data(
        self, date: datetime, hint: str, urls: list[str]
    ) -> Optional[dict[str, Any]]:
        """Salto 2: descargamos las páginas y el modelo reporta
        estado + marcador SOLO sobre su texto. None si no hay cita
        de una página realmente leída."""
        pages: list[str] = []
        fetched: list[str] = []
        for url in urls:
            text = await self._fetch_page_text(url)
            if not text:
                continue
            fetched.append(url)
            pages.append(f"PÁGINA: {url}\n{text[:_PAGE_CHARS_PER_URL]}")
        if not fetched:
            return None
        payload = await self._ask(
            _VERIFY_PROMPT.format(
                pages="\n\n".join(pages),
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
        # Anti-alucinación: la cita debe ser una de las páginas que
        # descargamos (o que el modelo confirma haber leído).
        read_urls = set(fetched) | set(self._payload_urls(payload))
        sources = [str(s) for s in (data.get("sources") or []) if str(s) in read_urls]
        if not sources or not _trusted_domains(_domains_of(sources)):
            logger.info(
                "[GEMINI] '%s' status=%s descartado: sin cita fiable %s",
                hint,
                data.get("status"),
                (data.get("sources") or [])[:3],
            )
            return None
        data["sources"] = sources
        return data

    @staticmethod
    def _score_pair(data: dict[str, Any]) -> Optional[tuple[int, int]]:
        """Marcador validado: dos enteros >= 0 o None."""
        try:
            home = int(data["home_score"])
            away = int(data["away_score"])
        except (KeyError, TypeError, ValueError):
            return None
        return (home, away) if home >= 0 and away >= 0 else None

    @staticmethod
    def _sets_pair(data: dict[str, Any]) -> Optional[list[tuple[int, int]]]:
        """Sets (juegos por set) validados o None."""
        raw = data.get("sets")
        if not isinstance(raw, list) or not raw:
            return None
        sets: list[tuple[int, int]] = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return None
            try:
                h, a = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                return None
            sets.append((h, a))
        return sets

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """El fixture consta NO jugado en una fuente fiable -> MatchState.

        Anti-alucinación: exige status "no jugado" Y al menos una cita
        de las URLs fiables leídas. "played"/"unknown" o respuestas sin
        fuentes devuelven None — el pick sigue pendiente.
        """
        if self._in_cooldown():
            return None
        hint = (team_hint or "").strip()
        if not hint:
            return None
        urls = await self._search_urls(date, hint)
        if not urls:
            logger.info("[GEMINI] '%s': búsqueda sin URLs fiables", hint)
            return None
        data = await self._verify_data(date, hint, urls)
        if not data:
            return None
        status = _VOID_ALIASES.get(str(data.get("status") or "").lower().strip())
        if status is None:
            return None
        logger.info(
            "[GEMINI] '%s': fixture %s confirmado por %s",
            hint,
            status,
            data["sources"][:3],
        )
        return MatchState(
            home_team=str(data.get("home_team") or hint),
            away_team=str(data.get("away_team") or ""),
            status=status,
        )

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Fase 2: liquida "played" con marcador citado + doble lectura.

        Dos verificaciones independientes sobre las mismas URLs deben
        coincidir en marcador (y sets si los da) — si difieren, la
        respuesta se descarta y el pick sigue pendiente. Es el último
        recurso de la cascada: solo ve los fixtures que ningún
        proveedor determinista encontró.
        """
        if self._in_cooldown():
            return None
        hint = (team_hint or "").strip()
        if not hint:
            return None
        urls = await self._search_urls(date, hint)
        if not urls:
            return None
        first = await self._verify_data(date, hint, urls)
        if not first or str(first.get("status")).lower().strip() != "played":
            return None
        score = self._score_pair(first)
        if score is None:
            return None
        second = await self._verify_data(date, hint, urls)
        if not second or str(second.get("status")).lower().strip() != "played":
            logger.info("[GEMINI] '%s': doble lectura no confirma played", hint)
            return None
        score2 = self._score_pair(second)
        sets = self._sets_pair(first)
        if score2 != score or (sets is not None and self._sets_pair(second) != sets):
            logger.warning(
                "[GEMINI] '%s': lecturas discrepan %s vs %s — descartado",
                hint,
                score,
                score2,
            )
            return None
        logger.info(
            "[GEMINI] '%s': played %s-%s confirmado x2 por %s",
            hint,
            score[0],
            score[1],
            first["sources"][:3],
        )
        return MatchResult(
            home_team=str(first.get("home_team") or hint),
            away_team=str(first.get("away_team") or ""),
            home_score=score[0],
            away_score=score[1],
            sets=sets,
        )

    async def _fetch_html(self, url: str) -> Optional[str]:
        """HTML crudo de una página (búsqueda DDG o web de resultados)."""
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers=_FETCH_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return None
        except httpx.HTTPError as exc:
            logger.info("[GEMINI] Fetch %s falló: %r", url, exc)
            return None
        return resp.text

    async def _fetch_page_text(self, url: str) -> Optional[str]:
        """Descarga la página y devuelve su texto visible truncado.

        La validación anti-alucinación del salto de stats exige que
        cada número reportado exista literalmente en este texto, así
        que la fuente tiene que ser lo que nosotros descargamos — no
        lo que el modelo "recuerda" de la URL.
        """
        html = await self._fetch_html(url)
        if not html:
            return None
        # Scripts/estilos fuera; tags fuera; espacios colapsados.
        html = re.sub(
            r"<(script|style|noscript)\b[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;?", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&#\d+;|&\w+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:_MAX_PAGE_CHARS] or None

    def _stats_from_text(self, text: str, data: dict[str, Any]) -> Optional[MatchStats]:
        """Stats normalizadas y VERIFICADAS contra el texto de la página.

        Cada par de valores reportado por el modelo tiene que aparecer
        literalmente cerca del nombre de la estadística en el HTML —
        así un número alucinado nunca pasa a MatchStats.
        """
        raw_stats = data.get("stats")
        if not isinstance(raw_stats, dict):
            return None
        values: dict[str, tuple[int, int]] = {}
        for reported_name, pair in raw_stats.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            try:
                v1, v2 = int(pair[0]), int(pair[1])
            except (TypeError, ValueError):
                continue
            name = str(reported_name)
            canonical = next(
                (key for key, pattern in _STAT_KEYWORDS if pattern.search(name)),
                None,
            )
            if canonical is None or canonical in values:
                continue
            if not self._values_in_text(text, canonical, v1, v2):
                continue
            values[canonical] = (v1, v2)
        if not values:
            return None
        return MatchStats(
            home_team=str(data.get("team1") or ""),
            away_team=str(data.get("team2") or ""),
            values=values,
        )

    @staticmethod
    def _values_in_text(text: str, canonical: str, v1: int, v2: int) -> bool:
        """Los dos valores aparecen en el texto junto al nombre de la
        estadística (en ese orden, dentro de una ventana corta)."""
        pattern = next(p for k, p in _STAT_KEYWORDS if k == canonical)
        for m in pattern.finditer(text):
            window = text[m.start() : m.start() + _STATS_VALIDATION_WINDOW]
            i1 = re.search(rf"(?<!\d){re.escape(str(v1))}(?!\d)", window)
            if not i1:
                continue
            i2 = re.search(rf"(?<!\d){re.escape(str(v2))}(?!\d)", window[i1.end() :])
            if i2:
                return True
        return False

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Fase 3: estadísticas del partido leyendo la página citada.

        url_context no renderiza el JS donde viven las tablas de
        stats, así que la página se descarga nosotros mismos y el
        modelo solo la interpreta; cada valor se verifica contra el
        texto real antes de aceptarse.
        """
        if self._in_cooldown():
            return None
        hint = (team_hint or "").strip()
        if not hint:
            return None
        urls = await self._search_urls(date, hint)
        for url in urls:
            text = await self._fetch_page_text(url)
            if not text:
                continue
            payload = await self._ask(
                _STATS_PROMPT.format(
                    text=text,
                    sport=self._sport_es(),
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                )
            )
            if payload is None:
                continue
            data = _extract_json(self._payload_text(payload))
            if not data:
                continue
            stats = self._stats_from_text(text, data)
            if stats:
                logger.info(
                    "[GEMINI] '%s': stats %s verificadas en %s",
                    hint,
                    list(stats.values),
                    url,
                )
                return stats
        return None
