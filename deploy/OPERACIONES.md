# ControlPick — Operaciones en producción (Oracle VM)

Estado real al **2026-09-25**. Documento de referencia para operar el
sistema desplegado: qué corre dónde, cómo se despliega y cómo se
recupera. Para arquitectura de código ver `AGENTS.md` y
`ROADMAP_MIGRACION.md`.

## Infraestructura

| Pieza | Valor |
|---|---|
| VM | Oracle Cloud Always Free, ARM Neoverse-N1, Ubuntu 22.04, 1 OCPU / 6 GB |
| IP pública | `130.110.233.198` |
| Dominio | `https://controlpick.duckdns.org` (DuckDNS, cuenta del usuario) |
| TLS | Terminada por `checkcoast-caddy` (Caddy existente, compartido) |
| Puerto host | `8000` → backend. En el Security List de Oracle solo 80/443 abiertos |
| Disco | ~45 GB totales, ~39 libres. Media de Telegram = mayor crecimiento |

Contenedores (en la VM, `docker ps`):

- `controlpick-backend` — FastAPI + Telethon + verifier + snapshotter,
  1 worker uvicorn (los loops viven en proceso; MÁS workers = loops
  duplicados).
- `controlpick-db` — Postgres 16 alpine, SIN puerto expuesto al host.
- `checkcoast-*` — proyecto vecino que comparte VM y Caddy. NO TOCAR:
  `controlpick-backend` está en la red `checkcoast_default` solo para
  que caddy lo resuelva por nombre; los contenedores checkcoast no se
  modifican.

## Layout en la VM

```
/home/ubuntu/controlpick/
├── docker-compose.prod.yml   # stack
├── .env                      # solo DB_PASSWORD (env var de compose)
├── backend/
│   ├── .env.docker           # config completa (env_file del servicio)
│   ├── controlpick_telegram_new.session  # sesión ACTIVA (cuenta Ramón)
│   ├── media/telegram/       # evidencia OCR (montada a /app/media)
│   ├── provider_state.json   # cuotas/misses diarios (montado)
│   ├── provider_cache.json   # respuestas cacheadas (montado)
│   └── backups/              # dumps diarios (montado a /app/backups)
└── deploy/
    ├── Caddyfile.snippet     # bloque ya aplicado en checkcoast/Caddyfile
    ├── backup_cron.sh        # instalado en cron diario 04:00
    └── verify_backlog_cron.sh  # cron diario 00:30 (backlog >14d)
```

## Cuenta de Telegram en producción

- **Cuenta dedicada "Ramón"** (eSIM Lebara en el Vivo del usuario),
  con 2FA activado. Nombre neutro a propósito: los tipsters ven la
  lista de miembros — nada que delate auditoría ni al dueño.
- Telethon usa `TELEGRAM_SESSION_NAME=controlpick_telegram_new`
  (en `backend/.env.docker`). El archivo `.session` = credencial viva:
  tratar como contraseña, nunca commitear.
- La sesión VIEJA (`controlpick_telegram.session`, cuenta personal) se
  eliminó 2026-09-25: archivo borrado y línea de `volumes:` quitada
  del compose. Si algún día hiciera falta esa cuenta, requeriría
  re-login con código al móvil personal.
- La app de Telegram del usuario NO se ve afectada por borrar
  `.session` — esos archivos solo sirven para la API de Telethon.

## Canales monitorizados

Fuente de verdad: tabla `channels`. Los 6 canales son privados; el
`target` guarda el **invite link** (verificado con
`CheckChatInviteRequest` contra el `channel_id` real) para que una
cuenta nueva pueda re-entrar sola con `scripts/join_all_channels.py`.

| channel_id | Canal | target |
|---|---|---|
| -1001125596067 | Dm7 \|\| GRATUITO | t.me/+Ud417RcBtH8zNGE0 |
| -1001914772235 | CopetePicks | t.me/+I1LYc_fKWWQwMzM8 |
| -1002491428353 | Bet Fran | t.me/+BmxEZtceE5liNjlk |
| -1002077450014 | Lady Bets | t.me/joinchat/EKOmtQsAR2IwM2Nk |
| -1002463340479 | AllSportsPicks | t.me/+zdT1vtI692IxOGZk |
| -1001472388026 | Dm7 Allsports | t.me/+pdqCqEEmHIBlMmE8 |

Ojo: un admin puede revocar un link. Si una cuenta nueva no entra por
el link, hay que pedir uno nuevo (comportamiento normal de suscriptor:
DM al contacto del canal, o link en su web/redes — dm7.es funcionó).

## Deploy de cambios de código

El backend NO monta el código: va baked en la imagen. Subir archivos
cambiados y rebuild:

```bash
# desde el PC (PowerShell/Git Bash), llave en Downloads:
scp -i ~/Downloads/ssh-key-2026-09-20.key <archivos> \
  ubuntu@130.110.233.198:/home/ubuntu/controlpick/<ruta>

ssh -i ~/Downloads/ssh-key-2026-09-20.key ubuntu@130.110.233.198 \
  "cd /home/ubuntu/controlpick && docker compose -f docker-compose.prod.yml up -d --build backend"
```

El contenedor ejecuta `alembic upgrade head` al arrancar — las
migraciones se aplican solas.

## Operaciones comunes

```bash
# Logs
docker logs controlpick-backend --tail 100 -f

# BD (psql dentro del contenedor; sin password al ser exec local)
docker exec controlpick-db psql -U postgres -d controlpick

# Backup manual
docker exec controlpick-db pg_dump -U postgres -Fc controlpick \
  > backups/manual-$(date +%F).dump

# Barrido diario de backlog (cron 00:30 UTC, cuotas recién renovadas):
# verifica pendientes >14 días que el ciclo de 3h ya no reintenta.
cat verify_backlog.log

# Restaurar un dump
cat backups/XXX.dump | docker exec -i controlpick-db \
  pg_restore -U postgres -d controlpick --clean --if-exists

# Estado providers (cuota diaria agotada, misses, llamadas)
cat backend/provider_state.json | python -m json.tool
# o en la app: pantalla debug → "Sistema · providers" / "Sistema · verificación"

# Añadir la cuenta a un canal nuevo: meter el invite link como target
# del canal en la tabla channels y correr join_all_channels.py
# (o unirse a mano desde la app de Telegram de la cuenta dedicada).
```

## Cuotas de providers (cascada)

Cada provider gratuito tiene cuota diaria. `provider_state.json` marca
`rate_limited` al que la agotó hoy y se salta al siguiente de la
cascada; los "missed" cachean lookups fallidos para no re-quemar
llamadas. Reset diario. Días anómalos (migraciones, catch-up masivo)
agotan todo — es esperado; si ocurre a diario, espaciar el verifier o
añadir provider.

## Datos sensibles — NUNCA en git

`.env`, `.env.docker`, `*.session`, `*.dump`, `provider_*.json`,
`media/`, número de teléfono de la eSIM, tokens DuckDNS.

## Pendientes conocidos (2026-09-25)

- [ ] Borrar sesión vieja de la VM tras estabilidad confirmada
- [ ] Push notifications: Firebase sin configurar (dev-client avisa)
- [ ] Retención/purga de media (margen ~años, decidir política)
- [ ] Credenciales biométricas: AsyncStorage → expo-secure-store
- [ ] Vigilar ritmo real de cuotas tras el catch-up de la migración
- [ ] Build APK producción con eas.json (ya apunta al dominio bueno)
