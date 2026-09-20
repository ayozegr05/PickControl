# ControlPick — guía rápida para agentes

Auditoría de tipsters: ingiere picks de canales de Telegram (texto + foto + OCR + LLM),
los deduplica, verifica resultados contra APIs deportivas y compara la rentabilidad
publicada del tipster con la real del usuario.

Ver `ROADMAP_MIGRACION.md` para estado de fases y próximos hitos.

## Arquitectura

- **Backend** `backend/` — FastAPI + SQLModel + PostgreSQL + Alembic.
  - `app/services/telegram/` — Telethon (cuenta de usuario): `client.py`,
    `handlers.py` (NewMessage + Album), `catchup.py` (rellena huecos al arrancar),
    `processor.py` (persistencia + dedup), `pick_extractor.py` (pre-filtro →
    reglas → LLM `gpt-4o-mini`), `ocr.py` (OpenAI vision).
  - `app/services/results/` — verificador de resultados por deporte:
    fútbol (football-data.org → API-Football → footapi7/Sofascore vía
    RapidAPI, sin ventana de fechas), tenis (TheSportsDB →
    RapidAPI ATP-WTA-ITF → tennisapi1). Rescate manual de stats:
    `scripts/backfill_stats_csv.py` (CSVs football-data.co.uk, dry-run por
    defecto).
  - `app/services/odds/` — snapshots de cuotas de mercado (auditoría del
    tipster): provider Sofascore (allsportsapi2 dedicado, tennisapi1
    scavenger), job periódico `snapshotter.py`, mapeo pick→mercado y
    comparación en `compare.py`. Ver ROADMAP §5.
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
