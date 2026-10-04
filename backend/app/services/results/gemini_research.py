"""Investigador de último recurso vía Gemini API + url_context.

Cuando TODA la cascada determinista falla, este provider usa Gemini
gratis (free tier, sin grounding de pago) en dos saltos:

1. BÚSQUEDA: nosotros fetcheamos la página de resultados de
   DuckDuckGo Lite (con render de FlareSolverr si responde challenge)
   y parseamos los enlaces; solo si esa página es ilegible de plano
   el modelo la lee vía `url_context` como plan B — la única llamada
   con tool que queda.
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
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape as html_unescape
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.results import compat_llm
from app.services.results.base import (
    MatchResult,
    MatchState,
    MatchStats,
    count_provider_call,
    fold_name,
    is_rate_limited,
    mark_cooldown,
    rate_limit_from,
    retry_hint_seconds,
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
# FlareSolverr: resolver un challenge de Cloudflare tarda 5-60 s; el
# timeout del POST tiene que cubrir el maxTimeout que le pedimos.
_FLARESOLVERR_MAX_MS = 60000
_FLARESOLVERR_TIMEOUT = 75.0
# Cooldown propio tras un 429: el free tier de Gemini limita por ritmo
# (RPM), no por día — aparcar hasta mañana (comportamiento estándar de
# `mark_rate_limited`) dejaría al investigador muerto por una ráfaga.
_429_COOLDOWN = timedelta(minutes=20)
# Cuota del free tier por MODELO (peticiones/día): el 429 trae el
# hint "Please retry in 3h45m" con la hora real del reset. Cada
# modelo agotado se aparca solo hasta ese instante — sin gastar ni
# una llamada redescubriendo que sigue seco — mientras los demás
# siguen trabajando con su propia cuota independiente.
_MODEL_QUOTA_UNTIL: dict[str, datetime] = {}


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
    # El País: ticker de eventos estático tipo Opta (córners, tarjetas,
    # faltas contables) para ligas grandes — el total lo computa
    # `_ticker_corner_summary`, no el modelo.
    "elpais.com",
    # Agregadores de resultados de segunda línea: menos fiables que los
    # de arriba pero válidos cuando la doble lectura coincide. Los de
    # nicho (stats de córners, fútbol femenino, Challenger) son los que
    # dan cobertura donde los grandes no llegan — la validación literal
    # del dato contra el HTML hace irrelevante su menor reputación.
    "aiscore.com",
    "tennisexplorer.com",
    "tennis24.com",
    "soccer24.com",
    "basketball24.com",
    "scores24.live",
    "scorebar.com",
    "fotmob.com",
    "globalsportsarchive.com",
    "soccerdonna.de",
    "worldfootball.net",
    "betexplorer.com",
    "oddsportal.com",
    "footystats.org",
    "soccerstats.com",
    "totalcorner.com",
    "corner-stats.com",
    "fctables.com",
    "footballant.com",
    "777score.com",
    "xscores.com",
    "diretta.it",
    "livesport.com",
    "matchstat.com",
    "live-tennis.eu",
    "coretennis.net",
    "scorebing.com",
    "azscore.com",
    # Nicho: ligas menores/reservas — oddspedia trae tabla de stats
    # ("Corners 3 3"), betsapi eventos ordinales, bsportsfan resultados.
    "oddspedia.com",
    # Comparadores de cuotas: páginas de partido con marcador, stats y
    # tabla de cuotas de cierre por casa (bet365, Stake, DraftKings).
    # El fetcher de Google las lee aunque a nosotros nos den 403.
    "oddschecker.com",
    "betsapi.com",
    "bsportsfan.com",
    "ceroacero.es",
    # FotMob sirve directo (sin Cloudflare) y embebe __NEXT_DATA__ con
    # marcador, estado y eventos — cubre ligas que ningún provider toca.
    "fotmob.com",
    # HTML estático con tabla de stats (Corners/Shots/Possession) de
    # reservas y ligas menores — la única fuente estática hallada con
    # córners de la Danish Reserve League.
    "football24hours.com",
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

# Tickers estilo Opta (El País): no publican "Total córners: N" — cada
# saque es un evento suelto "87 Corner, Real Madrid Femenino.". El
# conteo se hace aquí de forma DETERMINISTA y se añade como resumen al
# texto: la validación literal ve los números y el modelo no cuenta.
_TICKER_CORNER = re.compile(r"corner\s*,\s*([^.]{3,60}?)\.", re.IGNORECASE)
# Formato betsapi: "19' - 2nd Corner - Sonderjyske Reserves" — el
# equipo termina donde arranca el siguiente evento (minuto NN' o fin).
_TICKER_CORNER_ORDINAL = re.compile(
    r"\d+\s*(?:st|nd|rd|th)\s+corner\s*-\s*([^.]*?)(?=\s*\d+'|\s*\.|$)",
    re.IGNORECASE,
)


def _ticker_corner_summary(text: str) -> str:
    """Cuenta los eventos "Corner,<equipo>." del ticker y devuelve un
    resumen citable, o "" si la página no trae ese formato.

    Se emiten las dos ordenaciones (local-visitante / visitante-local)
    porque la ventana de `_values_in_text` exige los valores en el orden
    reportado por el modelo, que no sabemos de antemano.
    """
    counts: dict[str, int] = {}
    for pattern in (_TICKER_CORNER, _TICKER_CORNER_ORDINAL):
        for m in pattern.finditer(text):
            team = m.group(1).strip().rstrip(".").strip("()").strip()
            if team:
                counts[team] = counts.get(team, 0) + 1
        if len(counts) >= 2:
            break
    if len(counts) < 2:
        return ""
    (t1, c1), (t2, c2) = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
    return f" Corner Kicks {t1} {c1} {t2} {c2}." f" Corner Kicks {t2} {c2} {t1} {c1}."


# Por debajo de este tamaño el HTML casi seguro es un esqueleto JS
# (las páginas de comparadores con tabla real pesan centenares de KB
# renderizadas): se intenta el render de FlareSolverr y se conserva
# la versión más larga.
_MIN_RICH_HTML = 30_000

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

_FIXTURE_PROMPT = """Texto extraído de una página de resultados de {sport}:

{text}

Localiza el partido que jugó {player} en "{hint}" (fecha: {date}).

Responde SOLO con JSON válido, sin markdown:
{{"fixture": "<participante A> vs <participante B>" o null}}

Reglas:
- "fixture" son los DOS participantes del partido de {player}; en
  dobles, las dos parejas ("A / B vs C / D"). Usa los nombres tal como
  aparecen en la página.
- null si la página no muestra ese partido — nunca inventes el cruce."""

# Vocabulario canónico de `odds.compare` (map_pick_choices): el modelo
# SOLO puede devolver estos nombres — en el backfill la propuesta se
# revalida con map_pick_choices, así que una opción que no casa con el
# pick se descarta sola.
_ODDS_MARKET_NAMES = (
    "Full time",
    "Match goals",
    "Total games won",
    "Total sets",
    "Asian handicap",
    "Game handicap",
    "Set handicap",
    "Both teams to score",
    "Corners 2-Way",
    "Cards in match",
    "Double chance",
    "Draw no bet",
    "First set winner",
)

# Búsqueda dirigida por tipo de mercado: con el nombre del mercado en
# la query, DDG suele devolver directamente la pestaña del comparador
# donde vive esa línea en vez de la portada 1X2 del partido.
_MARKET_SEARCH_TERMS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"c[oó]rner|corner", re.I),
        ("oddspedia corners", "oddsportal corners", "betexplorer corners"),
    ),
    (
        re.compile(r"tarjeta|card|booking", re.I),
        ("oddspedia cards", "oddsportal cards", "betexplorer cards"),
    ),
    (
        re.compile(r"h[aá]ndicap|handicap|spread", re.I),
        ("oddsportal handicap", "betexplorer asian handicap"),
    ),
    (
        re.compile(r"ambos|btts", re.I),
        ("oddsportal btts", "oddspedia ambos marcan"),
    ),
    (
        re.compile(r"gol|over|under|m[aá]s de|menos de|total", re.I),
        ("oddsportal over under", "oddspedia goles", "betexplorer over under"),
    ),
)

# Mercados de 1ª parte: la pestaña de totals de oddsportal es la de
# tiempo completo — pedir "#over-under" para "1ª parte over 1.5"
# traería la línea equivocada (y validaría contra el pick mal).
_FIRST_HALF = re.compile(r"1ª|1er|primera|descanso|half|\bht\b|\b1h\b", re.I)


def _market_terms(descs: list[str]) -> list[str]:
    """Términos de búsqueda dirigidos por los mercados del grupo."""
    terms: list[str] = []
    for desc in descs:
        for pattern, extras in _MARKET_SEARCH_TERMS:
            if pattern.search(desc):
                terms += [t for t in extras if t not in terms]
    return terms


def _market_line(desc: str) -> Optional[str]:
    """Primera cifra del mercado ("más de 2.5 (línea 2.5)" -> "2.5")."""
    m = re.search(r"[-+]?(\d+(?:[.,]\d+)?)", desc)
    return m.group(1).replace(",", ".") if m else None


def _comparator_variants(urls: list[str], descs: list[str]) -> list[str]:
    """Subpáginas de mercado de comparadores conocidos.

    La página principal del partido suele listar solo 1X2: en
    oddsportal las demás líneas viven en pestañas que el propio JS
    selecciona por fragmento (`#over-under;2.5`, `#ah;-1.5`). El
    servidor devuelve el mismo HTML base para todas — el fragmento lo
    interpreta el navegador — así que estas variantes solo difieren
    renderizadas: el fetch directo trae la misma shell y cae a
    FlareSolverr, que es gratis. Se devuelven para ir AL FINAL de la
    cola: solo se gastan si la página base no resolvió el mercado."""
    want_ou = any(
        re.search(r"m[aá]s de|menos de|over|under|gol", d, re.I)
        and not _FIRST_HALF.search(d)
        and _market_line(d)
        for d in descs
    )
    want_ah = any(
        re.search(r"h[aá]ndicap|handicap", d, re.I)
        and not _FIRST_HALF.search(d)
        and _market_line(d)
        for d in descs
    )
    if not (want_ou or want_ah):
        return []
    variants: list[str] = []
    for url in urls:
        if not any("oddsportal" in d for d in _domains_of([url])):
            continue
        # Solo páginas de partido: en /h2h/ (historial entre equipos)
        # o /team/ el fragmento no selecciona ninguna tabla de
        # mercado — sería un render regalado.
        if "/h2h/" in url or "/team/" in url:
            continue
        base = url.split("#")[0].rstrip("/")
        if want_ou:
            line = next(
                _market_line(d)
                for d in descs
                if re.search(r"m[aá]s de|menos de|over|under|gol", d, re.I)
                and not _FIRST_HALF.search(d)
                and _market_line(d)
            )
            variants.append(f"{base}/#over-under;{line}")
        if want_ah:
            line = next(
                _market_line(d)
                for d in descs
                if re.search(r"h[aá]ndicap|handicap", d, re.I)
                and not _FIRST_HALF.search(d)
                and _market_line(d)
            )
            variants.append(f"{base}/#ah;{line}")
    return [v for v in dict.fromkeys(variants) if v not in urls]


def _literal_anchors(choice: str, group: Optional[str]) -> list[str]:
    """Anclas de proximidad para la cuota literal: el nombre de la
    selección ("Under", "Over", nombre de equipo) y/o la línea
    ("4.0"). Las elecciones "1"/"X"/"2" no son ancla útil por sí
    solas — sin línea caen al chequeo global de siempre."""
    anchors: list[str] = []
    if len(choice) > 2:
        anchors.append(choice)
    if group:
        try:
            anchors.append(f"{float(group):g}")
        except (TypeError, ValueError):
            anchors.append(group)
    return anchors


_ODDS_PROMPT = """Texto extraído de una página web de apuestas
deportivas (comparador de cuotas o casa):

{text}

Busco la cuota de cierre pre-partido de esta apuesta concreta:
- Partido: {hint} ({sport}, {date})
- Apuesta del tipster: {market_desc}

Responde SOLO con JSON válido, sin markdown:
{{"market_name": "<uno exacto de: {markets}>",
 "choice_name": "<1|X|2|Over|Under|Yes|No o '(<línea>) <equipo>'>",
 "choice_group": "<línea numérica como 9.5, o null>",
 "cuota_decimal": <número como 2.05>,
 "cuota_texto": "<la cuota EXACTAMENTE como aparece en el texto
   (2.05, 2,05 o 21/20)>",
 "casa": "<casa de apuestas o cadena vacía>"}}

Reglas:
- La cuota tiene que aparecer LITERALMENTE en el texto — nunca la
  inventes ni la calcules de memoria.
- Prefiere la cuota de bet365 si hay varias casas; si no, la de la
  primera casa que muestre esa apuesta.
- "1"/"X"/"2" = gana local/empate/visitante; "Over"/"Under" para
  totales; "Yes"/"No" para ambos marcan; "(<línea>) <equipo>" para
  hándicaps.
- Si la página no muestra esa apuesta: {{"market_name": null}}."""

# Variante multi-apuesta: mismo partido, varias apuestas del tipster —
# una página de comparador suele traer TODOS los mercados del evento,
# así que una extracción resuelve el grupo entero (una búsqueda por
# evento, no por pick).
_ODDS_MULTI_PROMPT = """Texto extraído de una página web de apuestas
deportivas (comparador de cuotas o casa):

{text}

Busco la cuota de cierre pre-partido de VARIAS apuestas del MISMO
partido {hint} ({sport}, {date}). Las apuestas son:

{apuestas}

Responde SOLO con JSON válido (array), sin markdown — un objeto por
cada apuesta que SÍ aparece en el texto (omite las que no):
[{{"n": <número de la apuesta en la lista>,
 "market_name": "<uno exacto de: {markets}>",
 "choice_name": "<1|X|2|Over|Under|Yes|No o '(<línea>) <equipo>'>",
 "choice_group": "<línea numérica como 9.5, o null>",
 "cuota_decimal": <número como 2.05>,
 "cuota_texto": "<la cuota EXACTAMENTE como aparece en el texto
   (2.05, 2,05 o 21/20)>",
 "casa": "<casa de apuestas o cadena vacía>"}}]

Reglas:
- La cuota tiene que aparecer LITERALMENTE en el texto — nunca la
  inventes ni la calcules de memoria.
- Prefiere la cuota de bet365 si hay varias casas; si no, la de la
  primera casa que muestre esa apuesta.
- "1"/"X"/"2" = gana local/empate/visitante; "Over"/"Under" para
  totales; "Yes"/"No" para ambos marcan; "(<línea>) <equipo>" para
  hándicaps."""


@dataclass
class ClosingOdds:
    """Cuota de cierre de una apuesta, validada contra la página."""

    market_name: str
    choice_name: str
    choice_group: Optional[str]
    cuota: float
    casa: str
    source_url: str


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


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Primer array JSON de objetos del texto (tolera code fences)."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


class GeminiResearchProvider:
    """Provider-investigador: búsqueda con citas como último recurso.

    Una instancia sirve los tres deportes (`SUPPORTED_SPORTS`); el
    deporte llega por constructor igual que en `Scores365Provider`
    porque `find_*` no lo recibe como parámetro.
    """

    NAME = _PROVIDER_NAME
    SUPPORTED_SPORTS = frozenset({"futbol", "tenis", "baloncesto", "automovilismo"})

    def __init__(
        self, sport: str, api_key: str, *, calls_as: str | None = None
    ) -> None:
        self._sport = sport
        self._api_key = api_key
        self._cooldown_until: Optional[datetime] = None
        # Nombre bajo el que se cuentan las llamadas en provider_state
        # ("calls" -> tarjeta de sistema). La pasada de cuotas usa
        # "gemini-odds" para que su gasto aparezca en "Sistema ·
        # cuotas" y no mezclado con la investigación de resultados
        # (es la misma suscripción free tier — el cooldown se marca
        # en ambos nombres).
        self._calls_as = calls_as or _PROVIDER_NAME

    def _in_cooldown(self) -> bool:
        """429 reciente (ritmo del free tier) o marca global del día.

        Con un endpoint compat vivo la extracción no depende de la
        cuota de Gemini: la búsqueda va por fetch propio y extraer
        sale por Groq/Cerebras/OpenRouter, así que el cooldown del
        free tier no debe parar el pipeline."""
        if compat_llm.has_live_endpoint():
            return False
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
            count_provider_call(self._calls_as)
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
        parked = 0
        for model in _MODELS:
            # Modelo aparcado por su cuota diaria concreta: se salta
            # sin gastar la llamada (el 429 trae "retry in XhYm").
            until = _MODEL_QUOTA_UNTIL.get(model)
            if until is not None and until > utc_now():
                parked += 1
                continue
            payload, quota_resp = await self._call_model(model, prompt, use_tool)
            if quota_resp is not None:
                # La cuota del free tier es por modelo: un 429 no
                # implica que los demás estén secos — se prueban.
                quota_hits += 1
                hint = retry_hint_seconds(quota_resp)
                _MODEL_QUOTA_UNTIL[model] = utc_now() + (
                    timedelta(seconds=hint) if hint else _429_COOLDOWN
                )
                continue
            if payload is not None:
                return payload
        if quota_hits or parked == len(_MODELS):
            if parked == len(_MODELS):
                # Toda la familia seca por cuota diaria: se retoma
                # cuando el primer modelo renueve — no a los 20 min.
                self._cooldown_until = min(
                    u for u in _MODEL_QUOTA_UNTIL.values() if u > utc_now()
                )
            else:
                self._cooldown_until = utc_now() + _429_COOLDOWN
            # Visible en /providers como bloqueo activo (timestamp, no
            # aparcado de día). Misma suscripción: se marca en el
            # nombre compartido y en el alias de la pasada
            # (gemini-odds) para que ambas tarjetas lo vean.
            mark_cooldown(_PROVIDER_NAME, self._cooldown_until)
            if self._calls_as != _PROVIDER_NAME:
                mark_cooldown(self._calls_as, self._cooldown_until)
            logger.warning(
                "[GEMINI] 429 en %d modelos%s — cooldown hasta %s",
                quota_hits,
                f" (+{parked} aparcados)" if parked else "",
                self._cooldown_until.strftime("%H:%M"),
            )
        return None

    async def _extract_text(self, prompt: str) -> Optional[tuple[str, list[str]]]:
        """Extracción texto→JSON: primero los endpoints OpenAI-compat
        gratuitos (Groq/Cerebras/OpenRouter — cuota diaria grande y
        modelos 70B); Gemini solo si ninguno responde — su cuota es la
        más pequeña. Devuelve (texto, urls_leídas_por_el_modelo) —
        la lista solo es no-vacía cuando respondió Gemini con tool —
        y el texto lo valida siempre quien llama contra la página."""
        text = await compat_llm.extract_text(prompt)
        if text:
            return text, []
        payload = await self._ask(prompt)
        if payload is None:
            return None
        return self._payload_text(payload), self._payload_urls(payload)

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

    async def _search_urls(
        self,
        date: datetime,
        hint: str,
        priority_terms: Optional[list[str]] = None,
    ) -> list[str]:
        """Salto 1: búsqueda -> URLs fiables del partido.

        DDG Lite lo fetcheamos nosotros (cae a FlareSolverr ante
        challenge) y los enlaces se parsean aquí — la búsqueda ya no
        quema cuota del modelo. Solo si la página de resultados no se
        puede leer de ninguna forma se usa el fetcher de Google vía
        url_context como plan B; el resto del pipeline siempre va por
        fetch propio y texto plano para no quemar su cuota mínima.

        El día exacto en la query empobrece los resultados de DDG (las
        páginas no indexan la fecha como texto), así que se prueba
        primero solo hint+deporte y luego con mes/año; la fecha queda
        en el prompt para que el modelo filtre por relevancia.

        Los conectores ("X vs Y", "X - Y") también degradan a DDG:
        "Glostrup v Ballerup" no devuelve nada y "Glostrup Ballerup"
        sí. La query usa el hint sin conectores; el original queda en
        el prompt como contexto del partido."""
        q_hint = re.sub(r"\s+(?:vs\.?|v)\s+|\s+[-—–]\s+", " ", hint).strip()
        # Queries dirigidas a dominios de nicho (totalcorner, oddspedia):
        # en la rama de stats van PRIMERO — una página genérica sin tabla
        # de córners no sirve aunque esté indexada antes.
        queries = [f"{q_hint} {t}" for t in (priority_terms or [])]
        queries += [
            f"{q_hint} {self._sport_es()}",
            f"{q_hint} {self._sport_es()} {date.strftime('%B %Y')}",
            # Con "stats" en la query DDG saca los agregadores de nicho
            # (totalcorner, footystats...) que con el nombre solo no
            # aparecen — clave para mercados de estadística.
            f"{q_hint} {self._sport_es()} stats",
        ]
        urls: list[str] = []
        seen: set[str] = set()
        for query in queries:
            # Primero gratis: DDG Lite por fetch propio (cae a
            # FlareSolverr si responde challenge). Solo si el fetch
            # en sí falla — página vacía/bloqueada — se gasta una
            # llamada del modelo; una página leída sin dominios
            # permitidos da el mismo vacío a cualquiera que la lea.
            found, page_ok = await self._search_ddg_lite(query)
            if not found and not page_ok:
                found = await self._search_urls_gemini(query, date, hint)
            for u in found:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
            if urls:
                break
        return _filter_trusted_urls(urls)[:_MAX_VERIFIED_URLS]

    async def _search_ddg_lite(self, query: str) -> tuple[list[str], bool]:
        """DDG Lite por fetch propio — cero cuota de modelo.

        Devuelve (urls de dominios permitidos, página_legible). Los
        enlaces salientes vienen envueltos como `/l/?uddg=<url>` — se
        decodifica el destino real antes de aplicar la allowlist."""
        html = await self._fetch_html(
            "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
        )
        if not html:
            return [], False
        urls: list[str] = []
        seen: set[str] = set()
        for href in re.findall(r'href="([^"]+)"', html):
            href = html_unescape(href).strip()
            if "uddg=" in href:
                href = (parse_qs(urlparse(href).query).get("uddg") or [""])[0]
            if href.startswith("http") and href not in seen:
                seen.add(href)
                urls.append(href)
        return _filter_trusted_urls(urls), True

    async def _search_urls_gemini(
        self, query: str, date: datetime, hint: str
    ) -> list[str]:
        """Fallback: el modelo lee DDG Lite vía url_context.

        Solo se usa cuando el fetch propio de la página de resultados
        falla del todo — gasta una llamada del free tier, así que es
        el plan B y no el camino normal."""
        payload = await self._ask(
            _SEARCH_PROMPT.format(
                search_url="https://lite.duckduckgo.com/lite/?q=" + quote_plus(query),
                sport=self._sport_es(),
                hint=hint,
                date=date.strftime("%d de %B de %Y"),
            ),
            use_tool=True,
        )
        if payload is None:
            return []
        data = _extract_json(self._payload_text(payload))
        if not data:
            return []
        return [str(u) for u in (data.get("urls") or []) if u]

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
        result = await self._extract_text(
            _VERIFY_PROMPT.format(
                pages="\n\n".join(pages),
                sport=self._sport_es(),
                hint=hint,
                date=date.strftime("%d de %B de %Y"),
            )
        )
        if not result:
            return None
        out, model_read = result
        data = _extract_json(out)
        if not data:
            return None
        # Anti-alucinación: la cita debe ser una de las páginas que
        # descargamos (o que el modelo confirma haber leído).
        read_urls = set(fetched) | set(model_read)
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
        """HTML crudo de una página; si el fetch directo choca contra
        Cloudflare/403 se reintenta vía FlareSolverr (Chromium propio).

        También cuando el 200 trae un esqueleto JS sin contenido: los
        comparadores (oddspedia, oddsportal, betexplorer) sirven la
        página vacía y las tablas de cuotas/stats solo existen en el
        DOM renderizado — sin render el fetch "funciona" pero no hay
        literal que validar y la búsqueda se marca 'sin cuota' con
        datos que sí estaban ahí."""
        html: Optional[str] = None
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers=_FETCH_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            if resp.status_code == 200 and "__cf_chl" not in resp.text:
                html = resp.text
        except httpx.HTTPError as exc:
            logger.info("[GEMINI] Fetch %s falló: %r", url, exc)
        if html is not None and len(html) >= _MIN_RICH_HTML:
            return html
        rendered = await self._fetch_via_flaresolverr(url)
        if rendered and len(rendered) > len(html or ""):
            return rendered
        return html

    async def _fetch_via_flaresolverr(self, url: str) -> Optional[str]:
        """HTML renderizado por el FlareSolverr de la VM.

        POST /v1 {"cmd": "request.get", url} -> solution.response con la
        página ya renderizada por un Chromium que resuelve el challenge
        de Cloudflare. Solo se usa cuando el fetch directo falla: un
        solve cuesta segundos de CPU del único núcleo de la VM."""
        base = getattr(get_settings(), "flaresolverr_url", None)
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=_FLARESOLVERR_TIMEOUT) as client:
                resp = await client.post(
                    f"{base.rstrip('/')}/v1",
                    json={
                        "cmd": "request.get",
                        "url": url,
                        "maxTimeout": _FLARESOLVERR_MAX_MS,
                    },
                )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("[GEMINI] FlareSolverr %s falló: %r", url, exc)
            return None
        solution = data.get("solution") or {}
        status = solution.get("status") or 0
        if data.get("status") != "ok" or not (200 <= status < 400):
            logger.info("[GEMINI] FlareSolverr %s no resuelto (status=%s)", url, status)
            return None
        return solution.get("response") or None

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
        summary = _ticker_corner_summary(text)
        return (text[:_MAX_PAGE_CHARS] + summary).strip() or None

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
        estadística, en ese orden, dentro de una ventana corta.

        Hay dos layouts: `nombre v1 v2` (tabla clásica) y
        `v1 nombre v2` (tennis.com pinta el valor antes de la barra).
        Se aceptan ambos, siempre preservando el orden v1 -> v2."""
        pattern = next(p for k, p in _STAT_KEYWORDS if k == canonical)
        for m in pattern.finditer(text):
            after = text[m.start() : m.start() + _STATS_VALIDATION_WINDOW]
            i1 = re.search(rf"(?<!\d){re.escape(str(v1))}(?!\d)", after)
            if i1 and re.search(
                rf"(?<!\d){re.escape(str(v2))}(?!\d)", after[i1.end() :]
            ):
                return True
            before = text[max(0, m.start() - _STATS_VALIDATION_WINDOW) : m.end()]
            i1 = re.search(rf"(?<!\d){re.escape(str(v1))}(?!\d)", before)
            if i1 and re.search(
                rf"(?<!\d){re.escape(str(v2))}(?!\d)", text[m.end() : m.end() + 60]
            ):
                return True
        return False

    async def find_fixture(
        self, date: datetime, player_hint: str, tournament: Optional[str] = None
    ) -> Optional[str]:
        """Reconstruye el evento de un pick que solo guardó el torneo.

        Patas tipo "CHALLENGER BIELLA" sin rival: el tipster no escribió
        el cruce, pero jugador+torneo+fecha suelen bastar para ubicarlo
        en un agregador. La respuesta se valida exigiendo que el fixture
        contenga el apellido del jugador — un cruce alucinado no pasa.

        Devuelve "A vs B" (o "A / B vs C / D" en dobles) o None.
        """
        if self._in_cooldown():
            return None
        player = (player_hint or "").strip()
        hint = f"{player} {tournament or ''}".strip()
        if not hint or not player:
            return None
        urls = await self._search_urls(date, hint)
        for url in urls:
            text = await self._fetch_page_text(url)
            if not text:
                continue
            result = await self._extract_text(
                _FIXTURE_PROMPT.format(
                    text=text[:_PAGE_CHARS_PER_URL],
                    sport=self._sport_es(),
                    player=player,
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                )
            )
            if not result:
                continue
            data = _extract_json(result[0])
            if not data:
                continue
            fixture = str(data.get("fixture") or "").strip()
            if not fixture:
                continue
            # Anti-alucinación: el cruce debe contener algún token del
            # jugador ("De Jong" basta con "jong"; "Brunold" entero).
            fixture_fold = fold_name(fixture)
            tokens = [t for t in fold_name(player).split() if len(t) >= 4]
            if not any(t in fixture_fold for t in tokens):
                continue
            logger.info(
                "[GEMINI] fixture de '%s' reconstruido: %s (%s)",
                hint,
                fixture,
                url,
            )
            return fixture
        return None

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
        # Queries dirigidas a los agregadores de nicho que SÍ publican
        # tabla de stats (córners incluidos) — una crónica sin tabla no
        # resuelve mercados de estadística.
        priority = (
            [
                "totalcorner corners",
                "oddspedia statistics corners",
                "footystats corners",
            ]
            if self._sport == "fútbol"
            else None
        )
        urls = await self._search_urls(date, hint, priority_terms=priority)
        for url in urls:
            text = await self._fetch_page_text(url)
            if not text:
                continue
            result = await self._extract_text(
                _STATS_PROMPT.format(
                    text=text,
                    sport=self._sport_es(),
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                )
            )
            if not result:
                continue
            data = _extract_json(result[0])
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

    async def find_closing_odds(
        self, date: datetime, team_hint: str, market_desc: str
    ) -> Optional[ClosingOdds]:
        """Cuota de cierre de una apuesta leyendo comparadores web.

        Último recurso del backfill de cuotas: para mercados que las
        APIs deterministas no cubren (córners, tarjetas, props), el
        modelo busca la página del partido en oddspedia/oddsportal y
        extrae la cuota pre-partido. Doble anti-alucinación:

        1. `cuota_texto` tiene que aparecer literalmente en el HTML
           descargado y coincidir numéricamente con `cuota_decimal`
           (fracciones UK "a/b" se traducen y comparan).
        2. La tripleta (market, choice, group) devuelta pertenece al
           vocabulario canónico y quien llama la revalida con
           `map_pick_choices` — el modelo propone, lo determinista
           decide.
        """
        if self._in_cooldown():
            return None
        hint = (team_hint or "").strip()
        desc = (market_desc or "").strip()
        if not hint or not desc:
            return None
        # Queries dirigidas a los comparadores que publican la tabla
        # de cuotas pre-partido por casa — una crónica sin tabla no
        # resuelve nada. Las del tipo de mercado van primero: con
        # "corners" en la query DDG trae la pestaña de córners.
        priority = _market_terms([desc]) + [
            "oddspedia cuotas",
            "oddsportal odds",
            "betexplorer odds",
        ]
        urls = await self._search_urls(date, hint, priority_terms=priority)
        # Al final de la cola, las pestañas de mercado del comparador
        # (la portada suele traer solo 1X2 — el fragmento selecciona
        # la pestaña/línea concreta una vez renderizado).
        urls += _comparator_variants(urls, [desc])
        for url in urls:
            text = await self._fetch_page_text(url)
            if not text:
                continue
            result = await self._extract_text(
                _ODDS_PROMPT.format(
                    text=text[:_MAX_PAGE_CHARS],
                    sport=self._sport_es(),
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                    market_desc=desc,
                    markets="|".join(_ODDS_MARKET_NAMES),
                )
            )
            if not result:
                continue
            data = _extract_json(result[0])
            if not data:
                continue
            found = self._closing_from_text(text, data, url)
            if found is not None:
                logger.info(
                    "[GEMINI] cuota cierre '%s' %s/%s = %s (%s) en %s",
                    hint,
                    found.market_name,
                    found.choice_name,
                    found.cuota,
                    found.casa or "?",
                    url,
                )
                return found
        return None

    async def find_closing_odds_multi(
        self, date: datetime, team_hint: str, market_descs: list[str]
    ) -> dict[int, ClosingOdds]:
        """Varias cuotas de cierre del MISMO partido en una pasada.

        Varias patas/picks del mismo evento comparten la página del
        comparador (oddspedia lista todos sus mercados): se hace UNA
        búsqueda web por evento y UNA extracción por página con todas
        las apuestas pendientes, en vez de un ciclo completo por pick.
        Devuelve índice en `market_descs` → cuota validada con la
        misma doble barrera anti-alucinación que `find_closing_odds`.
        """
        descs = [d.strip() for d in market_descs]
        if len(descs) == 1:
            one = await self.find_closing_odds(date, team_hint, descs[0])
            return {0: one} if one else {}
        if self._in_cooldown():
            return {}
        hint = (team_hint or "").strip()
        if not hint or not descs or any(not d for d in descs):
            return {}
        priority = _market_terms(descs) + [
            "oddspedia cuotas",
            "oddsportal odds",
            "betexplorer odds",
        ]
        urls = await self._search_urls(date, hint, priority_terms=priority)
        urls += _comparator_variants(urls, descs)
        apuestas = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(descs))
        found: dict[int, ClosingOdds] = {}
        for url in urls:
            if len(found) == len(descs):
                break
            text = await self._fetch_page_text(url)
            if not text:
                continue
            result = await self._extract_text(
                _ODDS_MULTI_PROMPT.format(
                    text=text[:_MAX_PAGE_CHARS],
                    sport=self._sport_es(),
                    hint=hint,
                    date=date.strftime("%d de %B de %Y"),
                    apuestas=apuestas,
                    markets="|".join(_ODDS_MARKET_NAMES),
                )
            )
            if not result:
                continue
            for entry in _extract_json_array(result[0]):
                try:
                    idx = int(entry.get("n")) - 1
                except (TypeError, ValueError):
                    continue
                if not (0 <= idx < len(descs)) or idx in found:
                    continue
                closing = self._closing_from_text(text, entry, url)
                if closing is None:
                    continue
                found[idx] = closing
                logger.info(
                    "[GEMINI] cuota cierre '%s' [%d] %s/%s = %s (%s) en %s",
                    hint,
                    idx + 1,
                    closing.market_name,
                    closing.choice_name,
                    closing.cuota,
                    closing.casa or "?",
                    url,
                )
        return found

    def _closing_from_text(
        self, text: str, data: dict[str, Any], url: str
    ) -> Optional[ClosingOdds]:
        """Valida la respuesta del modelo contra el texto descargado."""
        market = str(data.get("market_name") or "")
        if market not in _ODDS_MARKET_NAMES:
            return None
        choice = str(data.get("choice_name") or "").strip()
        if not choice:
            return None
        try:
            cuota = float(data["cuota_decimal"])
        except (KeyError, TypeError, ValueError):
            return None
        if not 1.01 <= cuota <= 1000:
            return None
        group = data.get("choice_group")
        group = str(group).strip() if group not in (None, "") else None
        literal = str(data.get("cuota_texto") or "").strip()
        anchors = _literal_anchors(choice, group)
        if not self._odds_literal_ok(text, literal, cuota, anchors):
            return None
        return ClosingOdds(
            market_name=market,
            choice_name=choice,
            choice_group=group,
            cuota=cuota,
            casa=str(data.get("casa") or "").strip(),
            source_url=url,
        )

    @staticmethod
    def _odds_literal_ok(
        text: str,
        literal: str,
        cuota: float,
        anchors: Optional[list[str]] = None,
    ) -> bool:
        """La cuota existe en la página: el literal copiado aparece en
        el texto y coincide numéricamente con el decimal devuelto
        (fracción UK "a/b" -> a/b + 1). Con `anchors`, además el literal
        tiene que aparecer ~al lado de la selección/línea — sin eso un
        precio de OTRO mercado de la misma tabla colaba."""
        variants = {f"{cuota:.2f}", f"{cuota:.2f}".replace(".", ",")}
        if literal:
            variants.add(literal)
        found: Optional[str] = None
        for v in variants:
            if v and re.search(rf"(?<![\d.,/]){re.escape(v)}(?![\d.,/])", text):
                found = v
                break
        if found is None:
            return False
        if anchors and not GeminiResearchProvider._literal_near_anchor(
            text, found, anchors
        ):
            return False
        frac = re.fullmatch(r"(\d+)\s*/\s*(\d+)", found)
        if frac:
            num, den = int(frac.group(1)), int(frac.group(2))
            return den > 0 and abs(num / den + 1 - cuota) <= 0.02
        try:
            return abs(float(found.replace(",", ".")) - cuota) <= 0.02
        except ValueError:
            return False

    @staticmethod
    def _literal_near_anchor(text: str, literal: str, anchors: list[str]) -> bool:
        """Alguna aparición del literal cae a ~150 chars de un ancla
        (nombre de selección o línea): la cuota es de ESA selección,
        no de otro mercado de la misma tabla.

        La aparición no puede SOLAPARSE con un ancla: si el modelo
        confunde la línea con el precio ("Under 11 córners" -> cuota
        11.0), el literal "11" caería justo dentro del ancla "11" de
        la propia línea y colaría. El ancla numérica tampoco casa
        tras "-"/"+": "-3.5/+3.5" es un hándicap, no el total 3.5."""
        anchor_res = [
            re.compile(
                (
                    rf"(?<![\d.,+\-]){re.escape(a)}(?![\d.,])"
                    if re.fullmatch(r"[\d.,]+", a)
                    else re.escape(a)
                ),
                re.I,
            )
            for a in anchors
            if a
        ]
        anchor_spans = [m.span() for ar in anchor_res for m in ar.finditer(text)]
        for m in re.finditer(re.escape(literal), text):
            if any(m.start() < e and s < m.end() for s, e in anchor_spans):
                continue
            window = text[max(0, m.start() - 150) : m.end() + 150]
            if any(ar.search(window) for ar in anchor_res):
                return True
        return False
