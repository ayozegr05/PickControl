# ControlPick — guía rápida para agentes

Auditoría de tipsters: ingiere picks de canales de Telegram (texto + foto + OCR + LLM),
los deduplica, verifica resultados contra APIs deportivas y compara la rentabilidad
publicada del tipster con la real del usuario.

Ver `ROADMAP_MIGRACION.md` para estado de fases y próximos hitos.
Ver `deploy/OPERACIONES.md` para el runbook de producción (VM Oracle,
cuenta Telegram dedicada, deploy, backups, cuotas de providers).

## Arquitectura

- **Backend** `backend/` — FastAPI + SQLModel + PostgreSQL + Alembic.
  - `app/services/telegram/` — Telethon (cuenta de usuario): `client.py`,
    `handlers.py` (NewMessage + Album), `catchup.py` (rellena huecos al arrancar),
    `processor.py` (persistencia + dedup), `pick_extractor.py` (pre-filtro →
    reglas → cascada LLM gratis `compat_llm`), `ocr.py` (Gemini vision →
    visión OpenAI-compatible OpenRouter).
  - `app/services/results/` — verificador de resultados por deporte:
    ESPN (`espn.py`, gratis sin key) va PRIMERO en los tres deportes —
    fútbol (~46 ligas: marcador FT/HT + stats de equipo + props de
    jugador vía `summary.rosters`), tenis (ATP/WTA, sets por
    `linescores`; sin aces/dobles faltas ni retiradas), basket
    (NBA/WNBA/NBL/FIBA; sin ACB ni Euroliga). Después: fútbol
    (football-data.org → API-Football → footapi7/Sofascore vía
    RapidAPI, sin ventana de fechas), tenis (TheSportsDB →
    RapidAPI ATP-WTA-ITF → tennisapi1). Tras 365scores va
    `football24h.py`: HTML estático sin key — escanea listados de
    liga/día (`/{pais}/{liga}/`, `/yesterday-matches`...), casa
    fixtures por slug `DD-MM-YYYY-equipo-vs-equipo` y lee marcador +
    tabla de stats (Corners→"Corner Kicks"...) de la página del
    partido. Cubre la cola larga (reservas DK, AFC Cup, ligas
    menores); `_LEAGUE_PAGES` se extiende al aparecer ligas nuevas y
    `_CLUB_ALIASES` mapea renombrados (Lion City=Home United,
    BG Pathum=Bangkok Glass). Cuando el listado ya no muestra el
    fixture, `_probe_slug` prueba la URL determinista
    `{DD-MM-YYYY}-{home}-vs-{away}.html` en cada liga configurada
    (la página del partido sobrevive semanas; los soft-404 se
    rechazan validando los nombres del marcador). Después va
    `transfermarkt.py`: `/live/index?datum=` lista todos los
    partidos del día (marcador inline + match_id) y
    `/statistik/index/spielbericht/{id}` trae córners/disparos/
    faltas/fueras de juego; fetch directo con fallback FlareSolverr
    (TM usa Cloudflare). En footapi7, los equipos femeninos/filiales
    comparten nombre con el senior ("Real Madrid"): si `/api/search`
    no devuelve entidad `team`, `_team_id_from_events` sigue los
    resultados `event` → `/api/match/{id}` para sacar el team-id
    real; `_TEAM_ALIASES` expande acrónimos del tipster
    ("psg"→"paris saint germain") antes del match_score — cubre
    UWCL y similares (caso: pick 2951, evento 17018497).
  - `app/services/results/espn_f1.py` — Fórmula 1 (deporte
    `automovilismo`): matching de GPs por tokens de localización
    (`_GP_LOCATION_ALIASES`: "GP ITALIA"→"Italian Grand Prix") sobre
    hooks `_fetch_races`/`_race_*` que implementa cada provider.
    "Menos de X coches" = `Classified Cars`; ganador del GP modelado
    como local 1-0. PRIMERO va `jolpica_f1.py` (Ergast: calendario
    `/f1/{año}/races` + resultados oficiales FIA por `positionText`
    numérico — R/W/D/N/E no clasifican). ESPN (`racing/f1`) es
    respaldo: su conteo por `competitors[].status.$ref` puede
    desviarse del oficial (Baku 2025: 15 ESPN vs 19 FIA). Al final de
    la cascada,
    Hockey (`nhl.py`): api-web.nhle.com `/v1/score/{día}` — API
    pública oficial de nhl.com, gratis sin key; solo NHL. Detrás va
    `Scores365Provider("hockey")` (sportId 4: NHL+KHL+ligas EU en el
    feed diario) y los mirrors Sofascore con slug `ice-hockey`.
    Al final de la cascada,
    `gemini_research.py` (Gemini, free tier): salto 1 = DDG Lite por
    fetch propio (cae a FlareSolverr ante challenge) + parseo de
    enlaces — `url_context` solo queda como plan B cuando la página de
    resultados es ilegible. Los saltos 2/3 descargan esas páginas
    nosotros y la extracción texto→JSON va PRIMERO por `compat_llm.py`
    (cascada OpenAI-compatible gratis: Groq `gpt-oss-120b` (+ pool de
    ingesta propio `qwen3.8-27b`) → OpenRouter `:free`, keys
    `GROQ_API_KEY`/`OPENROUTER_API_KEY`, cooldown por endpoint con
    hint del 429) —
    Gemini solo extrae cuando toda la cascada está seca. Fases:
    `find_postponed_match` devuelve SOLO estados no jugados
    (cancelled/postponed/walkover) con cita obligatoria; `find_match`
    liquida marcadores solo con fuente leída + doble lectura que debe
    coincidir; `find_match_stats` extrae aces/córners/etc. validando
    que cada número aparece literalmente junto al nombre de la
    estadística en la página. IMPORTANTE cuota: los providers de
    investigación (`NAME` gemini*) se excluyen de TODOS los barridos
    de aplazados rutinarios (`_is_research_provider`) — solo se les
    pregunta por aplazado cuando la resolución completa ya falló
    (último recurso en `verify_pick`), porque cada consulta les
    cuesta una búsqueda real. Queries de búsqueda sin día exacto ni
    conectores ("X vs Y" empobrece DDG). Config: `GOOGLE_API_KEY`.
    Fetch de páginas con fallback a **FlareSolverr** (contenedor
    `controlpick-flaresolverr`, Chromium anti-Cloudflare, solo red
    interna, `FLARESOLVERR_URL`) para webs que devuelven 403/challenge
    (totalcorner, sofascore web...). Rescate manual de stats:
    `scripts/backfill_stats_csv.py` (CSVs football-data.co.uk, dry-run por
    defecto). Mercados de 1ª parte/descanso: `MatchResult.ht_*` +
    `_ht_view()` en el verifier (stats 1H vía `find_match_stats_1h` de
    footapi7); sin dato HT el pick queda pendiente, nunca se resuelve
    con el marcador final.
  - `app/services/notifications/` — push vía Expo Push API (`push.py`,
    best-effort: nunca rompe ingesta/verificación; `DeviceNotRegistered`
    desactiva el token). Tokens en `device_tokens` (CRUD `/devices`);
    hooks en `processor.py` (pick nuevo) y `verifier.py` (liquidación
    solo a usuarios con "Yo también la jugué"). Kill-switch:
    `PUSH_NOTIFICATIONS_ENABLED`.
  - `app/services/odds/` — snapshots de cuotas de mercado (auditoría del
    tipster): ESPN (`espn_odds.py`, pickcenter/DraftKings gratis) primero
    para fútbol/basket (solo ganador/total/hándicap), luego Sofascore
    (allsportsapi2 dedicado, tennisapi1 scavenger). Al final de la
    cadena, `apihub.py` (key `APIHUB_KEY`): 1xBet vía APIHub con
    mercados etiquetados profundos — "Corners → 3Way Total" es el
    mercado EXACTO del tipster (líneas enteras Más/Exactamente/Menos,
    ~1.50) — más FlashScore de respaldo vivo (~7 días); enriquece
    eventos ya enlazados vía `_capture_apihub_odds` (cuota
    100/mes/servicio, cooldown al 402). Espacios de ids por
    familia (`ID_PREFIX` "espn:"/"sofascore:"): el snapshotter solo pasa
    a capturar ids al provider que los emitió. Job periódico
    `snapshotter.py`, mapeo pick→mercado y comparación en `compare.py`.
    Backfill histórico en `historical_backfill.py` — cascada:
    `football_csv.py` (football-data.co.uk, CSVs GRATIS por
    liga/temporada: 1X2+O-U2.5+AH en 22 ligas, 1X2 en 16 extra; su
    listado de temporada entera hace de sondeo de fecha — casa picks
    cuya `fecha_evento` es la del repost y el backfill les escribe la
    fecha real del fixture) → `tennisdata.py` (tennis-data.co.uk
    gratis) → `oddspapi` → `oddsfeed` → `gemini-odds` (último
    recurso). OddsPapi corre por el canal DIRECTO api.oddspapi.io
    (`ODDSPAPI_DIRECT_KEY`, `direct=True` en `OddsPapiClient`):
    `/v4/historical-odds` es GRATIS ilimitado (curva completa bet365,
    ~244 mercados/partido); solo `/v4/fixtures`+`/v4/markets` gastan
    las ~250 req/mes del plan free, con cooldown por endpoint (5 s
    histórico, 2 s resto) — si la cuota billable llega a 0 el gratis
    también da 429. RapidAPI bet36528 queda de respaldo latente.
    Catálogo sin mercado "Exactly" 3-way (corners/tarjetas solo 2-way).
    Props de jugador: el payload histórico trae las cuotas por jugador
    bajo `outcomes.<id>.players["<playerId>"]` (sin nombre — el equipo
    vive en `players["0"]`); `/v4/players` es el diccionario
    id→nombre (billable, cacheable). En vivo, `/v4/odds` sí incluye
    `playerName` por punto — vía futura de apoyo al snapshotter si
    algún día sobra cuota (hoy las props en vivo ya las cubren
    betsapi2 + theoddsapi + apihub). Además `fivedollar.py` (5DollarFootballAPI, key
    `FIVEDOLLAR_KEY`, plan Free 60 req/h): apertura+cierre REALES de
    bet365 en las 5 grandes ligas, ~3 meses de histórico — payload en
    `fivedollar_data/` para re-traducción offline. Ver ROADMAP §5.
- **Frontend** `Frontend/PickControl/` — Expo + React Native + TypeScript,
  Expo Router (`app/screens/`, `app/dynamic-routes/`), cliente en `src/api/`.

## Comandos

```powershell
# Backend (desde backend/) — usar SIEMPRE uno de estos dos:
.\scripts\run_dev.ps1
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --reload --reload-dir app
.\.venv\Scripts\python.exe -m pytest tests/ -x -q
.\.venv\Scripts\python.exe -m black app/ --check
.\.venv\Scripts\python.exe -m ruff check app/
.\.venv\Scripts\python.exe -m alembic upgrade head

# Frontend (desde Frontend/PickControl/)
npx tsc --noEmit
```

> `--reload-dir app` es OBLIGATORIO: sin él uvicorn vigila `.venv/` y reinicia
> en cada escritura de Telethon, matando el login y el catch-up.

Scripts útiles en `backend/scripts/` (`inspect_picks.py`, `reprocess_raw.py`,
`reclassify_rejected.py`, `telegram_login_complete.py`, `backup_db.py` — dump
diario pg_dump a `backend/backups/` con retención 14 días, ignorado por git).

## Reglas de negocio clave (no romper)

- **`created_at` ≠ `fecha_evento`**: la primera es cuándo se importó; la segunda
  es la fecha real del mensaje/partido. La UI muestra `fecha_evento`.
- **Dedup solo dentro del mismo canal** — el mismo pick en dos canales cuenta
  dos veces a propósito (stats independientes por canal).
- Ventana de duplicados por **`received_at` del mensaje** (±6 h), no `created_at`;
  si `linea` difiere, nunca se fusionan.
- **Boletos liquidados** (sello GANADOR / línea de premio pagado) se rechazan:
  son marketing del tipster, no picks abiertos.
- **Par foto-boleto + texto**: los tipsters publican el slip (rival + cuota
  en OCR) y el pick (stake + título) como mensajes separados a ~2-5 min.
  `processor.py` los empareja en ventana de 10 min (`_PAIR_WINDOW`,
  `_looks_like_slip`): el texto se extrae junto al OCR del slip; si la foto
  ya creó pick se enriquece (nunca duplicar), y en catch-up la foto busca el
  pick del texto hacia adelante. Los picks emparejados llevan `metodo` con
  sufijo `+par`.
- **Retos** (`parsed_picks.es_reto`): tabla y stats aparte.
- **`sospecha_mercado='3way'`**: flag de auditoría (no cambia la
  liquidación) en picks anulados `motivo_anulada='push'` con línea
  entera de córners/tarjetas — el tipster probablemente juega el
  mercado a 3 vías de 1xBet (línea exacta pierde, no devuelve). El
  verifier lo marca solo al liquidar push; con snapshots 1xBet se
  confirma por huella de precio (~1.5 vs ~1.25 del 2-way).
- **Combinadas** (self-FK `parsed_picks.combinada_id`, ADR en la migración
  `f1a2b3c4d5e6`): padre `es_combinada=True` + patas como filas normales.
  Las patas se verifican solas pero NUNCA cuentan en stats/listados de
  simples ni en dedup; el padre se liquida en conjunto
  (`verifier.settle_combinada`) y no se consulta a APIs. `cuota_efectiva`
  NULL si faltan cuotas por pata (no se inventa la ganancia).
- Los raws (`telegram_raw_messages`) NUNCA se borran: son la auditoría.
- Catch-up: máx. 300 mensajes Y tope 7 días, orden nuevo→viejo.

## Canales monitorizados

Fuente de verdad: tabla `channels` (CRUD en `/api/v1/channels`, pantalla
`app/screens/canales.tsx`). `TELEGRAM_TARGET_CHANNEL` (`.env`) solo se usa
como bootstrap: `seed_channels_from_env` puebla la tabla si está vacía.
Los handlers de Telethon son globales y filtran por el caché
`active_channel_ids()` (`services/telegram/channels.py`), recargado tras
cada cambio por API — añadir/quitar canales no requiere reiniciar.
Borrar un canal NO borra sus raws ni picks (auditoría).
Sesión Telethon: `TELEGRAM_SESSION_NAME=controlpick_telegram`;
media en `backend/media/telegram/`.

## Convenciones del proyecto

- Tipado estricto: Pydantic/SQLModel en backend, TypeScript en frontend.
- Explicar brevemente en español antes de tocar archivos; resumen por archivo
  al terminar (ver `.windsurfrules`).
- Windows: cuidado con CP1252 al imprimir emojis en consola
  (`sys.stdout.reconfigure(encoding='utf-8')`).
- Los 429 de los providers OCR/LLM dejan raws sin leer — cubierto por
  la cascada con cooldown por endpoint (`compat_llm`, visión de `ocr.py`)
  y el ciclo de rescate (`maintenance/rescue.py`: `retry_pending_ocr` →
  `reprocess_pending_raws` → `retry_incomplete_picks`, cada 6h en
  `lifecycle.py`, ventana `rescue_max_age_days`; media sin fichero:
  `scripts/rescue_missing_media.py`).
