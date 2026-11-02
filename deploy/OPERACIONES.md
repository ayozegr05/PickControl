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

## Build APK local (sin EAS)

```bash
cd Frontend/PickControl/android
# local.properties: sdk.dir=C:\\Users\\asert\\AppData\\Local\\Android\\Sdk
EXPO_PUBLIC_API_BASE_URL=https://controlpick.duckdns.org/api/v1 \
  ./gradlew assembleRelease
# APK: android/app/build/outputs/apk/release/app-release.apk
```

- OJO firma: va firmado con `debug.keystore` (default de prebuild) —
  distinto al keystore de EAS. Sobre una app instalada por EAS,
  Android exige DESINSTALAR primero (pierde login guardado).
- Si `ninja: error: manifest 'build.ninja' still dirty after 100
  tries`: es el ninja 1.10.2 bundled con cmake 3.22.1 del SDK —
  sustituir `%LOCALAPPDATA%\Android\Sdk\cmake\3.22.1\bin\ninja.exe`
  por ninja >= 1.12 (github.com/ninja-build/ninja/releases). El
  original queda en `ninja-1.10.2.bak.exe` en ese dir.

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

## OddsPapi — estado de cuota y plan noviembre (2026-10-26)

- Plan free: **250 req/mes billables** (`/fixtures`, `/participants`,
  fichas por participante); `/v4/historical-odds` y `/v4/account`
  son **gratis ilimitados**. `/v4/account` es la fuente de verdad del
  gasto (`subscriptions[0].request_count`).
- Freno local `_DIRECT_MONTHLY_CAP = 235` para no llegar al corte
  duro del proveedor. Reconciliación diaria automática contra
  `/v4/account` (hook `oddspapi_quota_sync` en el loop del backfill).
- Fin de octubre 2026: contador real **232/250** — freno alcanzable,
  backlog de ~39 picks congelado hasta renovación mensual.
- Caché de descubrimiento: `backend/oddspapi_data/participants/`
  (catálogo por deporte) y `fixtures_participant/` (lista de
  fixtures por equipo, 1 billable por ficha, persistente).
- **Lección selecciones**: el proveedor duplica cada equipo por
  competición ("England" tiene ~20 fichas). Con
  `_PARTICIPANT_MAX_IDS=3` las selecciones quedaban "sin fixture" en
  las fichas top — NO confirmado como gap real: el partido puede
  vivir en otra variante (amistosos/clasificación FIFA).
  → En noviembre: sonda sobre un partido de selecciones (p. ej.
  Portugal–Noruega) descargando 2-3 variantes más de cada lado;
  si aparece, valorar subir `_PARTICIPANT_MAX_IDS` para picks de
  selecciones o elegir la variante por competición.
- Catálogo completo: fútbol ~21k + tenis ~25k participantes —
  descargar TODO costaría ~46k llamadas (~15 años de cuota). No es
  el objetivo: solo se fichan los equipos que aparecen en picks
  reales; el resto nunca se necesita.

## OddsPapi — checklist de mercados sin traducir (2026-10-26)

Inventario offline sobre los 97 payloads cacheados (`payloads/`):
mercados presentes en fixtures reales que `_MARKET_TRANSLATION` aún
no cubre. Cada punto se verifica GRATIS: traducir el payload guardado
con `translate_history` + probar `map_pick_choices` con pick sintético
(solo el descubrimiento de fixtures nuevos cuesta billables).
Los conteos son nº de opciones (líneas) en los payloads, no fixtures.

### Tenis — 22 sin traducir

- [x] `Second Set Winner` [p2] / `Third Set Winner` [p3] (40x) →
  "Second/Third set winner". Pick: "X gana el 2º set". HECHO
  2026-10-26 (`_SET_WINNER_MARKETS`, guarda `linea is None`).
- [x] `Participant 1/2 Total Games` [result] (213x) →
  "Player 1/2 games". Pick: "Alcaraz más de 12.5 juegos". HECHO
  2026-10-26 (`_event_player_in_text` con apellido; Participant N =
  home/away del fixture). Ojo: "jugador +N juegos EN SET" no tiene
  mercado → miss honesto a propósito.
- [x] `Game Handicap First/Second/Third Set` [p1/p2/p3] (137x) →
  "Nth set game handicap". Pick: "Alcaraz -1.5 juegos 1er set".
  HECHO 2026-10-26 (`_SET_GAME_HANDICAP`; comparación de línea CON
  signo — "Hurkacz +2.5" ≠ "(-2.5) Hurkacz"; pick de hándicap sin
  mercado ya no cae al 1X2 del partido).
- [x] `Participant 1/2 To Win a Set` (38x) / `Both Participants To
  Win a Set` (9x) → "Player N to win a set" / "Both to win a set"
  (Yes/No). HECHO 2026-10-26. Ojo: "Both..." solo existe en 9/21
  fixtures (bet365 no siempre lo publica → miss honesto).
- [x] `Exact Sets` [result] (21x) → "Exact sets" (nº total de sets,
  choices "2".."5"). HECHO 2026-10-26 — el dígito exige lookbehind
  para no capturar "2.5 sets" de un over.
- [x] `Correct Score` [result] (~20x) → "Correct score" (es el
  marcador EN SETS: 2-0/2-1) + `Correct Score First/Second/Third
  Set` → "Nth set correct score" (marcador EN JUEGOS: 6-4...).
  HECHO 2026-10-26. Los `After Four/Six Games` (pNg4/pNg6, ~110x)
  quedan SIN traducir a propósito: son mercados en vivo que ningún
  pick pide.
- [x] `Odd Even Games` [result] (21x) → "Games odd/even".
  HECHO 2026-10-26.

**Tenis: COMPLETO** — los 22 mercados pendientes están traducidos o
deliberadamente omitidos (variantes live g4/g6).

### Fútbol — 80 sin traducir

- [x] Primera parte: `Over Under First Half` (848x), `Corners - Over
  Under First Half` (1014x), `Bookings - Over Under First Half`
  (287x), `Asian Handicap First Half` (284x), `First Half Result`
  (104x), `BTTS First Half` (13x). Pick: "+1.5 goles 1ª parte",
  "+4 córners 1T". HECHO 2026-10-26 → "1st half goals/corners/
  bookings", "1st half" (1X2), "1st half btts", "1st half handicap".
  `_detect_half` cubre "1ª parte/primer tiempo/1T/descanso/first
  half". Cobertura real (76 fixtures): goals 76, corners 75,
  bookings 40, 1X2 76, hándicap 69, btts 13 — donde bet365 no
  publica, miss honesto. Quedan sin traducir `Double Chance/DNB/
  Correct Score/Odd Even First Half` (~205x) — picks raros.
- [x] Segunda parte: `Over Under Second Half` (485x), `Corners -
  Over Under Second Half` (932x), `Bookings - Over Under Second
  Half` (431x), `Second Half Result` (90x). HECHO 2026-10-26 →
  "2nd half goals/corners/bookings", "2nd half result".
  Cobertura real: goals 67, corners 75, bookings 40, 1X2 76.
  `Asian Handicap Second Half` NO existe en los payloads (entrada
  retirada a propósito — no afirmar cobertura inventada). Queda
  `Exact Score Second Half` (60x) sin traducir.
- [x] Totales por equipo y mitad: `Over Under Team 1/2 First/Second
  Half` (1.169x), `Corners - Over Under Team 1/2` ambas partes
  (2.048x), `Bookings - Over Under Team 1/2` ambas partes (920x).
  Pick: "+5 córners del Betis en la 2ª parte". HECHO 2026-10-26 →
  "Nth half Team N goals/corners/cards" (la convención Team usa
  "cards", no "bookings" — mismo nombre que "Team N cards" del
  fulltime). Cobertura real (76 fixtures): Team goals 66-70, Team
  corners 53-54, Team cards 39-40 por mitad. Además: `_event_team_
  in_text` ahora casa por token distintivo ("Betis" → "Real Betis",
  "Rovers" → "Shamrock Rovers") y devuelve -1 (ambiguo) cuando un
  mismo token casa con los dos nombres ("Madrid" en Real vs
  Atlético) → miss honesto. Veto "en/del partido" para totales del
  encuentro aunque nombren equipo.
- [x] Portería a cero / win-to-nil / marcar: `Team 1/2 Clean Sheet`
  (146x), `Team 1/2 Win To Nil` (+First Half) (48x), `Team 1/2 To
  Score` (+por parte/both halves) (231x). HECHO 2026-10-26 →
  "Team N clean sheet" / "Team N win to nil" / "Team N to score" +
  variantes "Nth half" y "both halves" (Yes/No). Verificado con los
  picks reales: #6150 "Suiza - Sí" (`_event_team_in_text` ahora
  traduce países ES→EN vía `_translate_countries`) y #3662
  "Inglaterra gana sin encajar goles" → win to nil. Ojo: "marca en
  ambas partes" NO es HT/FT — la guarda `_is_halftime_fulltime` solo
  dispara con contexto de ganar ("gana ambas partes").
- [x] Hándicaps europeos y 1X2 de stats: HECHO 2026-10-26 →
  `European Handicap` (+First/Second Half) → "European handicap" /
  "Nth half european handicap" (1X2 con línea en choice_group; el
  visitante invierte la línea: "Getafe +1" = opción "2" del grupo
  "-1"). `Corners - 1X2`/`Bookings - 1X2` (+p1/p2) → "Corners 1X2" /
  "Cards 1X2" (quién saca/recibe más; "empate a córners" → "X").
  `Corners - Handicap`/`Bookings - Handicap` (+p1/p2) → spread
  "(hc) Equipo" con signo. Línea negativa en córners/tarjetas sin la
  palabra "hándicap" también enruta (un total nunca es negativo).
  Cobertura real (76 fixtures): European handicap 71 (1ª mitad 65,
  2ª mitad 0 — el proveedor la lista pero no la publicó en ninguno
  cacheado), Corners 1X2 42, Corners handicap 13 (+13 en 1H),
  Cards 1X2 29, Cards handicap 12. Guardas: un pick CON línea sin
  mercado ya no cae al 1X2 FT ("Betis -3.5 córners" sin el mercado
  de córners → None), y "Corners 1X2" ausente tampoco cae al
  "Full time".
- [x] Goles especiales y marcadores: HECHO 2026-10-26 →
  `Half Time / Full Time` → "Half time/Full time" ("1/1".."2/2";
  `_htft_choice` parsea "gana al descanso y al final"→1/1,
  "empata al descanso y gana al final"→X/1, código literal "1/1";
  "gana ambas partes"/"gana la 1ª y la 2ª" = To Win Both Halves —
  NO es 1/1 → miss). `First/Last Goal Full Time` → "First/Last
  team to score" ("No Goal"→"X"). `Correct Score First/Second
  Half` → "Nth half correct score". `Odd Even` (FT/p1/p2/Team N)
  → "Goals odd/even" familia. `Both Teams To Score Second Half`
  → "2nd half btts". `Asian Handicap Second Half` → "2nd half
  handicap". `Double Chance`+`Draw No Bet` p1/p2 → variantes de
  mitad. `Highest Scoring Half` → "Highest scoring half"
  (1st/X/2nd). `To Win From Behind` → "To win from behind".
  Cobertura real (76 fixtures): HT/FT 76, First/Last goal 71/67,
  CS 1H 76 (2H 0), OE goles 71 (1H/Team 13, 2H 0), DC/DNB 1H 13
  (2H 0), BTTS 2H 0, AH 2H 0, HSH 67, WFB 67. Bug cerrado:
  "primera y segunda parte" no casaba HALF_1 y fugaba al
  "2nd half result" — `_is_halftime_fulltime` amplío el patrón.
- [ ] Sin traducir a propósito (sin picks que los pidan):
  `Winning Margin` ("gana de 1/2 goles"), `Exact Score`
  numérico ("exactamente 3 goles"), `Team N Exact Score`,
  `Corners/Bookings Odd Even`, `To Win Both Halves`.
- [x] Props de jugador: HECHO 2026-10-26 → `_PROP_TRANSLATION`
  cubre anytime/first/last scorer, "Player Goals" (conteo →
  líneas Over N-0.5 bajo "Goalscorer anytime"), assists, shots,
  shots on goal, fouls, carded, goalkeeper saves, tackles,
  offsides (O/U `result` + formas de conteo; también
  `fulltime+overtime` para assists/shots). El comparador casa el
  nombre del jugador por fuzzy (apellido ≥4 letras eleva a 0.8,
  umbral 0.7): "marca"/"anota"/"recibe amonestación"/"verá
  tarjeta"/"cae en fuera de juego"/"comete falta"/"portero +
  paradas"/"doblete"/"+N goles". Dirección afirmativa = Over;
  las filas "Yes" (group None) solo cuentan para Over — un "no
  marca" nunca toca el outcome Yes. Con línea pedida la línea
  debe casar ("marca 2 goles" = Over 1.5; "2+" ≡ Over N-0.5).
  Bugs cerrados en este bloque: (a) el filtro de dirección
  descartaba las filas "Yes" (group None) y el anytime no casaba
  nada; (b) a igualdad de nombre ganaba la línea MENOR — ahora
  gana la fila "Yes" (es el contrato literal "marca gol", no el
  Over 0.5); (c) empate de similitud entre DOS jugadores
  distintos ("Silva" vs Bernardo/André) casaba el primero de la
  lista — ahora es miss ambiguo; (d) "Morata +1.5 goles" fugaba
  a "Match goals" del partido — el guarda O/U detecta nombre no
  equipo y lo deja para la rama prop. Cobertura real (76
  fixtures, 49 con props): scorers ~61-65 instancias, O/U
  assists 92, saves 263, shots 224, shots-on-goal 126; conteo
  assists 33, fouls 40, goals 61, shots 33, carded 49,
  tackles 23. NO se publica "Score or Assist" ni offsides —
  "marca o asiste X" y "fuera de juego" sin mercado = miss
  honesto.
- [ ] Sin traducir a propósito (sin picks que los pidan):
  `Winning Margin` ("gana de 1/2 goles"), `Exact Score`
  numérico ("exactamente 3 goles"), `Team N Exact Score`,
  `Corners/Bookings Odd Even`, `To Win Both Halves`,
  `Next Goal Scorer`, props por periodo (p1/p2/p3, hockey),
  `Player Goals Against`, combos "Tackles+Assists"/
  "Points+Assists+Rebounds" (basket).

### Misses honestos verificados (None correctos — revisar juntos)

Casos donde el comparador devuelve None A PROPÓSITO porque el
proveedor no publica ese contrato (verificado en payloads reales
2026-10-26). Si un día aparecen en el catálogo, `revisit_done` los
re-traduce gratis:

- Juegos del JUGADOR dentro de un set ("Sinner +4.5 juegos en el
  1er set"): OddsPapi solo tiene Participant Total Games a nivel
  partido, no por set.
- Stats por set (aces/dobles faltas/breaks EN set): el catálogo
  lista "Total Aces" con periodos p1..p5 pero solo traducimos
  [result]; los picks "aces en el 1er set" quedan miss — traducible
  si aparece un pick real.
- Lado de hándicap no publicado: bet365 solo ofrece una dirección
  de cada línea (p. ej. en Djokovic-Hurkacz 2º set existe "(2.5)
  Djokovic"/"(-2.5) Hurkacz" pero NO "(2.5) Hurkacz" — un pick
  "Hurkacz +2.5" es miss honesto aunque el mercado "exista").
- Juegos por set sin mencionar set pero con jugador nombrado:
  ambiguo — se trata como jugador-partido y si la línea no existe
  para ese jugador es miss (Tien solo tenía líneas 11.5-16.5).
- "Both to win a set" solo se publica en ~la mitad de los partidos
  (9/21 cacheados): miss honesto donde falte.
- `Correct Score After Four/Six Games` (pNg4/pNg6): mercados en
  vivo ("4-0 tras 4 juegos del 1er set") — sin traducir hasta que
  un pick real los pida.
- "Marca o asiste <jugador>" (picks reales #165, #2309-2324...):
  el proveedor no lista mercado "Score Or Assist" — miss honesto.
  (Combinar anytime+assists inventaría un precio.)
- Prop de jugador no cotizado en el partido ("Morata marca gol"
  cuando bet365 no lo lista): miss — el dict de 29k jugadores
  resuelve pero la casa no ofreció su cuota.
- Apellido compartido ("Silva marca" con dos Silvas en el
  mercado): empate de similitud -> miss ambiguo, nunca casa el
  primero.
- "Yamal +0.5 goles" si solo se publicaron líneas 2+/3+ para él:
  la línea pedida no existe -> miss (no sustituye por otra línea).
- Marcador de set sin mencionar set ("gana 6-4" a secas): ambiguo
  entre marcador del partido y del set → miss honesto.
- Lado europeo no publicado: `European Handicap` solo se vende a una
  línea por partido (p. ej. "-1"): "Betis EH -2" es miss honesto.
- `European Handicap Second Half`, `Corners/Cards 1X2` por mitad y
  hándicaps de stats de 2ª parte existen en el catálogo pero el
  proveedor no los publicó en ninguno de los 76 fixtures cacheados
  (0/76): miss honesto donde falten.
- "Más tarjetas/córners de X" con mercado 1X2 ausente → None, nunca
  se compara contra el "Full time" del marcador.
- "Gana ambas partes" / "gana la 1ª y la 2ª parte" = To Win Both
  Halves: NO es el "1/1" del HT/FT (ir ganando al descanso no exige
  ganar cada mitad) → miss mientras el proveedor no lo publique.
- Par/impar de córners o tarjetas → None (mercados `Corners/Bookings
  Odd Even` del catálogo sin traducir hasta que un pick los pida).
- `Winning Margin` ("gana de 2", "por la mínima") y `Exact Score`
  numérico ("exactamente N goles") → sin traducir a propósito.
- Pick #410 "Carreño NO ganará 2 sets (Lehecka 3-0 o 3-1)": apuesta
  compuesta que cubre DOS marcadores — ninguna casa publica ese
  combo ni "sets del jugador" como total. Reconstruir el precio
  sumando "3-0"+"3-1" sería inventar, no auditar → miss honesto.
- Picks O/U/hándicap sin su mercado ya no caen al 1X2: guardas de
  `hándicap`, de mitad, de `_TEAM_AMBIGUOUS` (token compartido por
  ambos equipos), de marcador-en-mitad ("gana 1-0 la 1ª parte" no
  es "1st half" 1X2 ni "Correct score" FT), DC/DNB por mitad, y
  HT/FT ("gana al descanso y al final", "ambas partes" = contrato
  único sin traducir → None). Verificado 2026-10-26: un O/U sin
  línea ofrecida ya no llega al fallback porque
  `_extract_predicted_team` devuelve None con frases de total.
- Fútbol 1ª/2ª parte: `1st half bookings` solo en 40/76 fixtures,
  `1st half btts` en 13/76, `2nd half bookings` 40/76 — donde
  bet365 no publica es miss honesto. "Ambos marcan 2ª parte":
  `Both Teams To Score Second Half` NO existe en el catálogo
  (solo 1ª parte) → siempre miss. `Asian Handicap Second Half`
  tampoco existe en payloads → "hándicap 2ª parte" = miss.
- Mercados de mitad sin traducir → miss honesto: `Double Chance/
  Draw No Bet/Correct Score/Odd Even First Half` (~205x),
  `Exact Score Second Half` (60x), `Half Time / Full Time` (96x),
  `Team To Score in Both Halves` (~145x), `Team X Clean Sheet /
  Win To Nil / To Score` (bloque siguiente).
- Token de equipo compartido por ambos nombres ("Madrid" en Real
  Madrid vs Atlético Madrid): `_TEAM_AMBIGUOUS` → miss honesto,
  nunca adivinar el lado ni caer al total del partido.
- Team bookings por mitad solo en 39-40/76 fixtures; Team cards
  FT en ~39/76 — bet365 no siempre publica tarjetas por equipo.

### Criterio

- Prioridad = frecuencia en payloads × probabilidad de que un canal
  lo apueste. Se traduce al aparecer el vocabulario real del pick;
  `revisit_done` re-traduce payloads viejos gratis cuando se añade
  un mercado.
- NO traducir "por si acaso" sin un pick que lo pida: cada entrada
  nueva necesita también vocabulario en `compare.py`, y una mala
  traducción activa puede colar una comparación falsa (peor que un
  miss honesto).

## Backlog de cuotas — anatomía de los 35 pendientes (2026-10-27)

Estudio caso a caso del backlog oficial (`count_pending_backfill`).
Si mañana entra una fuente de cuotas nueva o con mejor cobertura,
esta tabla dice qué puede rescatar y qué es estructuralmente
inalcanzable para el feed bet365 (que es EL MISMO en oddspapi,
betsapi, betexplorer y oddsfeed).

### Grupo A — partido inexistente en bet365 (~16, terminal)

`nofixture` en TODAS las fuentes bet365-family. No es que falte el
mercado: la casa ni lista la competición.

- Tenis ITF/Challenger menor (~10): Basavareddy–Mmoh,
  Sakellaridis–Ajdukovic, Wallin–Nikles, Albot–Rossi, Kym–Blanchet,
  Djere–Glinka, Passaro–Forejtek, Herbert–Mrva. `tennisdata` no
  cubre ITF; bet365 no lista esos torneos.
- Fútbol femenino/sub-21/menores: Arsenal fem–Køge, Lyon fem–
  Chelsea, Alemania u21–Georgia u21, Sønderise–B, Barcelona–Al Ahly.
- Basket ACB (2): Tenerife–Zaragoza, CB Canarias–Baskonia — solo
  betsapi podría llegar a cubrir ACB.

### Grupo B — fixture existe, mercado no publicado (~12, terminal)

El partido casa (`nomarket` en sofascore/footballdata/betexplorer)
pero la casa no vende ese contrato en esa competición:

- Córners/tarjetas en selecciones y ligas menores (Turquía–Francia
  U11c, Inglaterra–España U7c, España–Chequia, Moldavia–Feroe,
  Georgia–Ucrania, Omonia–Benfica, Lens–Sporting, Francia–Bélgica,
  Alemania–Serbia): bet365 solo cotiza córners en ligas grandes.
- "Ambos equipos recibirán una tarjeta" (Suecia–Polonia,
  Galatasaray–Barça): contrato inexistente en bet365.
- "Suiza-Sí/portería a 0", "San Marino +4.5", "Arsenal 3-1":
  mercados traducidos pero no publicados en esos partidos.
- Tenis `juegos del set` en ITF: tennisexplorer cotiza ganador pero
  no totales de set en Challengers.

### Grupo C — `fecha_evento` corrupta por el extractor (~5, bug)

El mensaje llegó el día del partido pero `extract_pick` escribió una
fecha vieja/errónea; las fuentes buscaron fixtures el día equivocado
→ `nofixture` contaminado. La fecha BUENA está en el `OddsEvent`
enlazado (`odds_event_id` correcto):

| pick | fecha_evento (mala) | fecha real (enlace) |
|---|---|---|
| #6047 Albania–San Marino `San Marino +4.5` | 2026-08-19 | 2026-10-06 |
| #4763 Alemania–Serbia `+7 córners` | 2026-09-11 | 2026-10-01 |
| #7656 Arsenal–Lille `Arsenal 3-1` | 2026-09-12 | 2026-10-13 |
| #5779 Francia–Bélgica `+7 córners` | 2026-10-17 | 2026-10-05 |
| #6498 Irlanda–Israel `+1.5 goles` | 2026-10-08 (=received) | 2026-10-04 |

Rescate posible: el reproceso offline ya ancla por fecha/equipos del
`OddsEvent` enlazado (`_reprocess_offline_all.py`), pero estos
partidos tampoco están en los 97 payloads cacheados → requieren
cuota nueva (nov) o una fuente que busque por nombre+fecha real.
Expectativa baja: son probablemente femenino/sub-21/amistosos que
bet365 ni lista.

**Investigación del bug (2026-10-27)**: la extracción quedó
descartada como origen — `_sanitize_event_date` (±2d pasado/+30d
futuro) corre en TODOS los paths (ingesta, rescue, repair) y
rechazaría un -48d. Los escritores post-extracción de
`fecha_evento` son: `garbage.fix_date` (fecha impresa del slip,
ventana ±60d → ahora ±30d y nunca sobre patas/padres de combinada),
`backlog.probe_event_date` (±7d) y `fechas_corregidas` del backfill
(fixture casado → fecha real). Las fechas malas vinieron por una
de esas vías post-extracción (probablemente `fix_date` con una
fecha impresa de otra pata/emisión). Además #6498 resultó NO ser
un bug de fecha: su raw es una pantalla **"Mis apuestas" (MYMATCH)**
de bet365 — historial republicado. La familia #6494-6498 (padre
combinada + 4 patas) entera es contaminación; el detector nuevo
`_HISTORY_SCREEN_RE` ("en curso (N)"/"mymatch"/pestañas con
contador) la marca como liquidada y el ciclo de reparación la
rechaza solo.

**Fix desplegado (2026-10-27)**: el backfill real ya no agrupa solo
por `pick.fecha_evento`. En `_run_source`, cada pick con
`odds_event_id` valida su enlace contra `_link_consistent` (ambos
lados del hint deben compartir tokens con home/away del evento — un
apellido suelto no basta). Si el enlace es consistente, su
`OddsEvent.start` ancla el cubo de día y el `dt` de `match()`; si la
fecha anclada difiere de `fecha_evento`, las marcas `nofixture`/
`nomarket` previas se invalidan (`clear_missed_for_pick`) porque se
escribieron buscando el día equivocado. Así, en noviembre la pasada
de OddsPapi buscará Albania–San Marino el 06-oct (día real), no el
19-ago (fecha inventada por el extractor).

### Grupo D — `odds_event_id` enlazado al partido equivocado (2)

- #7146 `Djere vs Glinka` → enlazado a `Pedro Martínez vs Djere`.
- #7167 `Passaro vs Forejtek` → enlazado a `Passaro vs Milev`.

El snapshotter en vivo casó por nombre parcial ("Djere") contra otro
partido del mismo jugador. La re-extracción no lo arregla (el pick
está bien extraído; el link lo escribió el snapshotter, no el
extractor). Ambos son ITF que bet365 no lista → miss terminal igual.

**Fix desplegado (2026-10-27)**: cuando `_link_consistent` detecta
que el `OddsEvent` enlazado no contiene ambos lados del pick, el
backfill **suelta el enlace** (`odds_event_id=None`), desatribuye las
filas `OddsSnapshot.parsed_pick_id` capturadas del partido equivocado
(#7146 llevaba 18 filas del Martínez–Djere) y borra las marcas para
reintentar limpio. El enlace roto ya no se usa como ancla ni como
filtro `done` — sin esto, `_sofascore_id(pick)` podía casar por id y
escribir cuotas del partido que no es.

Cobertura: `test_link_consistent_*`, `test_enlace_ancla_dia_de_
busqueda`, `test_enlace_inconsistente_se_suelta_y_desatribuye`,
`test_ancla_invalida_marcas_de_fecha_corrupta` y
`test_ancla_no_salta_marcas_si_fecha_coincide` en
`tests/test_backfill_source.py`. Alias añadido: `oporto → fc porto`
en `TEAM_ALIASES` (el falso negativo de "Betis - Oporto" vs
"Real Betis-FC Porto").

### Conclusión operativa

- ~50% partidos fuera del feed bet365 → inalcanzable con la familia
  actual de fuentes; una fuente nueva (p. ej. otra casa o feed con
  cobertura ITF/femenino/ACB) es lo único que los rescataría.
- ~35% mercados no publicados en esas competiciones → igual.
- ~15% fechas/enlaces rotos → mismo destino final aunque el enlace
  fuera correcto.
- El goteo diario de betsapi los confirma uno a uno (hoy: 0 filas);
  en noviembre las marcas TTL caducan y se revalida con cuota nueva.
- **Deportes sin fuente (fix 2026-10-27)**: `_load_picks` filtraba a
  deportes con provider de API → picks de F1/hockey/etc. eran
  invisibles para TODO el backfill (ni contaban como pendientes).
  Ahora `run_gemini_odds_pass` los carga con `uncovered_sports=True`:
  el comparador web no distingue deportes y el LLM es agnóstico —
  tienen su intento honesto de auditar cuota. Primer caso: #1803
  "GP Italia, menos de 19,5 coches" (automovilismo). El gate
  `SUPPORTED_SPORTS` solo aplica a deportes con provider de API; el
  check de cruce "A vs B" se relaja para eventos no-partido
  (carreras, torneos). El backfill diario de APIs sigue sin verlos —
  no tienen fixture que pedirle a nadie.
- Bug real ya cerrado hoy: `repair_pick` rechazaba material-basura
  solo tras los candados pata/bloqueada (deadlock: 104 picks de
  slips `SELLO: GANADOR` inalcanzables). Ahora el chequeo de material
  va primero → 88 rechazados (es_apuesta=False, `rejected+reext`).

## Incidencia — ingesta Telegram caída 10 días (23-oct → 2-nov 2026)

**Síntoma**: cero raws nuevos desde 23-oct 09:12. `ingest_health`
avisó por push cada 6h desde 31-oct ("sin mensajes nuevos en Nh")
pero el admin no tenía la app instalada → avisos al vacío.

**Causa raíz**: migración/backup de `backend/` (23-oct) dejó fuera
`controlpick_telegram_new.session`. Al recrear el contenedor, Docker
creó el origen del bind-mount como **directorio vacío** (comportamiento
por defecto). Telethon abrió una sesión vacía → no autorizada →
`client.start()` colgado esperando el código SMS interactivo que
nadie puede escribir. Sin crash, sin log de error: silencio total.

**Fix**: sesión restaurada desde `backend.bak-20261023/` (36KB,
válida) → `rmdir` del directorio falso + `cp` + recreate. La cuenta
reconecta sin pedir código. Catch-up normal recupera ≥26-oct;
`_deep_catchup.py` (session copiada a `/tmp`, ventana 23→26 oct)
cierra el hueco de los primeros días.

**Prevención**:
- `find_repair_candidates` ahora incluye material-basura como
  señal de candidatura propia (slip liquidado/historial MYMATCH
  bien extraído también es candidato — antes solo "extracción
  rota" entraba y la familia #6494-6498 sobrevivía).
- `_DC_EXPLICIT` ya no excluye picks cuya selección lleva la
  redacción DNB ("X gana empate no cuenta" se re-etiqueta a
  "resultado sin empate"; desbloquea #7496, #7245, #8810).
- Nuevo canal de alerta por **email** (`notifications/email_alert.py`,
  SMTP stdlib vía to_thread): `ALERT_EMAIL_TO`+`SMTP_*` en .env;
  `notify_ingest_down` lo llama además del push — el email sale
  aunque `push_notifications_enabled` esté apagado.

**Lección operativa**: un bind-mount cuyo origen no existe se monta
como DIRECTORIO vacío sin avisar. Si `backend/` se re-migra, verificar
que `*.session` es fichero regular ANTES de recrear el contenedor:
`stat -c %F backend/controlpick_telegram_new.session` debe decir
`regular file`.
