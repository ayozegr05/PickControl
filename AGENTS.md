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
    reglas → LLM `gpt-4o-mini`), `ocr.py` (OpenAI vision).
  - `app/services/results/` — verificador de resultados por deporte:
    ESPN (`espn.py`, gratis sin key) va PRIMERO en los tres deportes —
    fútbol (~46 ligas: marcador FT/HT + stats de equipo + props de
    jugador vía `summary.rosters`), tenis (ATP/WTA, sets por
    `linescores`; sin aces/dobles faltas ni retiradas), basket
    (NBA/WNBA/NBL/FIBA; sin ACB ni Euroliga). Después: fútbol
    (football-data.org → API-Football → footapi7/Sofascore vía
    RapidAPI, sin ventana de fechas), tenis (TheSportsDB →
    RapidAPI ATP-WTA-ITF → tennisapi1). Al final de la cascada,
    `gemini_research.py` (Gemini + `url_context`, free tier): salto 1
    lee DDG Lite y filtra URLs por allowlist de dominios de resultados,
    salto 2 lee solo esas páginas — SOLO devuelve estados no jugados
    (cancelled/postponed/walkover) con cita obligatoria; `find_match`
    siempre None. Config: `GOOGLE_API_KEY`. Rescate manual de stats:
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
    (allsportsapi2 dedicado, tennisapi1 scavenger). Espacios de ids por
    familia (`ID_PREFIX` "espn:"/"sofascore:"): el snapshotter solo pasa
    a capturar ids al provider que los emitió. Job periódico
    `snapshotter.py`, mapeo pick→mercado y comparación en `compare.py`.
    Ver ROADMAP §5.
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
- Los errores 429 de OpenAI (TPM) dejan raws sin OCR — el retry está en roadmap.
