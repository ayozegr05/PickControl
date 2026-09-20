# Hoja de Ruta - ControlPick

Estado del proyecto: migración del backend Node/Mongo a FastAPI/PostgreSQL y del
frontend a Expo/React Native **completada en lo esencial**. El sistema ya
ingiere picks de Telegram (texto + foto + OCR + LLM), los deduplica, verifica
resultados de fútbol contra APIs de mercado y permite corrección manual.

## Fases completadas

- **Fase 0: Sanitización de historial Git y entorno.** (COMPLETADA)
- **Fase 1: Verificación de lógica de backend en FastAPI.** (COMPLETADA)
- **Fase 2: Definición y validación de endpoints y modelos de BD.** (COMPLETADA)
  - `users`, `informantes`, `picks` migradas y validadas con SQLModel/Alembic.
  - `informantes.es_canal_telegram` distingue canales reales de informantes manuales.
- **Fase 3: Integración de servicios API en Frontend Expo.** (COMPLETADA)
  - Cliente API (`src/api/*`), AuthContext y pantallas principales funcionando.
- **Fase 4: Validación End-to-End en dispositivo móvil.** (COMPLETADA)
  - Registro, login, creación/listado de picks y navegación validados en Android físico.
- **Fase 5: Pipeline de Telegram completo.** (COMPLETADA)
  - Backend legacy Node.js retirado por completo; todo vive en FastAPI/PostgreSQL.
  - `app/services/telegram/`: Telethon (cuenta de usuario), listener en vivo,
    descarga de fotos, OCR con OpenAI, extractor híbrido (pre-filtro regex →
    reglas → LLM `gpt-4o-mini`) y persistencia de `telegram_raw_messages` +
    `parsed_picks`.
  - **Deduplicación** foto+texto del mismo pick: similitud de texto, Jaccard,
    subconjunto de palabras significativas (ignora stopwords) y umbral rebajado
    si la cuota coincide; la fusión conserva campos que falten (stake, cuota…).
  - **Catch-up con detección de huecos** (verificado en producción): al
    arrancar procesa lo posterior a la marca de agua Y escanea los últimos
    mensajes del canal buscando ids que falten en BD o raws guardados
    vacíos. Límites: máx. 300 mensajes Y tope de 7 días de antigüedad (lo
    que ocurra primero, en ambas fases). Orden nuevo→viejo para priorizar
    los picks de hoy y try/except por mensaje (un fallo no aborta el canal).
  - **Deduplicación por fecha del mensaje**: la ventana de candidatos usa
    `received_at` del raw (±6 h), no `created_at` — el catch-up importa
    todo "hoy" y sin esto un pick antiguo absorbía al nuevo con la misma
    selección genérica. Además, si ambos picks tienen `linea` y difieren
    ("Más de 7.0" vs "Más de 8.0 córners") NO se fusionan.
  - **Filtro de boletos liquidados**: `_is_settled_ticket` rechaza slips
    cobrados — sello `GANADOR`/`GANADA` en mayúsculas, o línea de premio
    pagado `<importe>€ Ganancias` al final de línea sin marcadores de slip
    abierto (`Cerrar apuesta`, `Añadir selección`, `potenciales`…). Los
    canales repostean slips ganadores como marketing y contaminaban stats.
  - **Retos (`es_reto`)**: los mensajes con "reto" se clasifican aparte —
    excluidos de la tabla diaria y de las stats, con sección propia en la
    vista del tipster y en la pantalla de depuración.
  - **Verificación automática de resultados**: fútbol (football-data.org +
    API-Football) y tenis (cadena TheSportsDB → ATP-WTA-ITF → tennisapi1).
    Detalle de mercados en "Próximos hitos" → "Mercados".
  - **Corrección manual** de resultados vía `PATCH /telegram/parsed-picks/{id}`.
- **Fase 6: Despliegue y Hardening a producción.** (PENDIENTE — ver hitos abajo)

## Próximos hitos

### Vista completa (resumen)

| # | Hito | Estado | Esfuerzo |
|---|---|---|---|
| 1 | Combinadas como sección propia | Hecho | Alto — modelo, extractor, verificador, UI |
| 2 | Fútbol: primera parte/descanso | Hecho — `MatchResult.ht_*` en los 3 providers de fútbol + stats 1ST en footapi7; el verifier reusa los resolutores con `_ht_view` | Medio (reusa providers) |
| 3 | Fútbol: partidos parados (SUSP/ABD/INT) | Manual a propósito | — |
| 4 | Cuota tipster vs cuota real de mercado | Hecho y backend probado en vivo (endpoints + snapshots reales OK) — falta solo revisar la UI en móvil (ver §5) | Alto, ya implementado con API gratuita |
| 5 | Baloncesto | Aparcado | Medio |
| 6 | Álbumes Telegram | Implementado — falta probar con álbum real | Trivial |
| 7 | Pantalla de análisis global | Hecho | — |
| 8 | "Yo también la jugué" | Hecho | — |
| 9 | Notificaciones push | Pendiente | Medio |
| 10 | CRUD de canales desde la app | Hecho (tabla `channels`, handlers globales dinámicos, picker desde `get_dialogs`, pantalla `canales`) — falta probar en vivo | Medio |
| 11 | Deploy real (servidor + PostgreSQL + HTTPS) | Pendiente | Medio-alto |
| 12 | Backups: script `backup_db.py` | Hecho | — |
| 13 | Backups programados diarios | Pendiente (va con el deploy) | Trivial |
| 14 | Tests del pipeline Telegram | Hecho | — |
| 15 | Limpieza de usuarios (test@a.com) | Pendiente | Trivial |

Aparcados fuera de la lista: verificación live/en juego, torneos sin
cobertura de proveedor, hándicap sin sujeto en zona ambigua |línea| 2-3.

### Bugs detectados en pruebas reales (sept-2026)

Diagnosticados tras probar las combinadas en el móvil — pendientes de
tenis de canales Challenger (Lady Bets, Bet Fran) sin resolver:

- [x] **A. `missed` prematuro en providers RapidAPI** (arreglado): el
      verificador intenta en cuanto `fecha_evento < ahora`, o sea antes
      o durante el partido — y el "no encontrado" quedaba cacheado 15
      días, matando toda la cobertura Challenger. Ahora: evento <48 h →
      fallo provisional (TTL 6 h, clave `|prov`); >48 h → definitivo.
      Además, un error de API (no-cuota) ya no marca `missed`, y
      tennisapi1 no marca si ve el evento en el feed aún en vivo.
      Limpieza puntual de `provider_state.json` aplicada.
- [x] **B. Higiene de extracción** (arreglado): `_normalize_pick` limpia
      markdown/emojis de `seleccion`/`evento` (también en patas) e
      infiere `deporte` de señales léxicas del mensaje cuando el path de
      reglas no lo rellena (un pick de tenis ya no quema llamadas de
      fútbol). En el verificador, `_tennis_lookup_hint` usa `evento`
      solo si trae enfrentamiento ("A vs B" o "A - B"); un torneo tipo
      "Tenis - Chall. Szczecin" cede el hint al jugador de `seleccion`
      (sigue mostrándose en la UI como contexto).
- [x] **C.1. Evento perdido en picks de tenis por reglas** (arreglado):
      `_rule_extract` ahora rellena `evento` gratis si el mensaje trae
      un enfrentamiento ("A - B" / "A vs B" vía `_extract_eventos`).
      Cuando el rival solo aparece en la prosa del análisis (formato
      Bet Fran: "Chidek gana" + "el H2H favorece a Mayot"), `evento`
      queda a None y se hace **una segunda pasada LLM solo en ese caso**
      (merge conservador: el pick de reglas es la base; el LLM aporta
      evento/deporte/mercado/casa → `metodo="rule+llm"`). Los picks ya
      completos por reglas siguen ahorrando la llamada como antes.
- [x] **C.2. Búsqueda por jugador en tennisapi1** (arreglado): el caso
      Cecchinato destapó que el partido del Challenger de Szczecin SÍ
      estaba en Sofascore/tennisapi1 pero no se resolvía — RapidAPI
      ATP/WTA/ITF devolvía 429 (cuota diaria agotada) y el barrido por
      categorías costaba hasta 9 req con fecha exacta. Ahora
      `find_match` hace `/api/tennis/search/{nombre}` → mejor entidad
      tenista (excluye parejas de dobles "X / Y") →
      `team/{id}/events/near`, filtra por fecha (±1 día), parsea
      `previousEvent`/`nextEvent` con el mismo `_parse_event` y aplica
      el matcher de similitud habitual. Verificado en vivo:
      `search/cecchinato` → id 44549 y `events/near` devolvió
      **Cecchinato vs Möller 2-1** (7-6, 6-7, 6-2); también resuelve
      nombres parciales en servidor ("chidek" → "Clement Chidekh").
      Coste típico: **2 llamadas/pick** frente a hasta 9, con caché de
      search y near. El barrido por categorías queda como fallback
      (jugador sin resultados o eventos near fuera de fecha). Si el
      partido está en vivo no se marca missed ni se gasta el barrido.
      Pendiente: revisar por qué "Marco Cecchinato" no apareció en
      `matches-played` de RapidAPI ATP/WTA/ITF cuando haya cuota
      (¿Challenger sin cobertura? ¿formato de perfil?).
- [x] **D. Limpieza puntual de datos** (hecho):
      - **Automovilismo**: borrados los picks 367 (GP Países Bajos) y
        902 (GP España) — deporte sin proveedor, quedaban pendientes
        para siempre. Además `_normalize_pick` ahora marca
        `es_apuesta=False`/`metodo="rejected"` para deportes sin
        soporte (`_UNSUPPORTED_SPORTS` + señal `_MOTOR_SIGNAL`), así
        futuras picks de F1 se guardan auditables sin ensuciar
        pendientes; un reproceso del raw las recupera cuando haya
        soporte.
      - **Combinada id=1201**: borrada con sus patas 1336/1337 — el OCR
        había extraído nombres de partido como "patas" sin selección
        (irrecuperables como apuestas; el raw de imagen se conserva).
      - **Lady Bets**: las ~123 filas vacías son `metodo="rejected"`
        (saludos/marketing rechazados a propósito — son el rastro de
        auditoría, no se tocan). Los ~22 con datos pendientes:
        `scripts/verify_backlog.py` barre picks fuera de la ventana de
        14 días con el mismo `verify_pick` del ciclo normal. Primera
        ejecución: TODOS los proveedores en rate-limit (429 en
        API-Football, football-data.org, TheSportsDB y RapidAPI) → 0
        resueltos, sin marcar misses (los errores no contaminan).
        Re-ejecutar cuando renueve la cuota diaria.
- [ ] **ACCIÓN PENDIENTE (mañana, con cuota renovada)**: ejecutar
      `backend/scripts/verify_backlog.py --apply` para resolver los
      ~22 pendientes con datos de Lady Bets (+ resto de canales). De
      paso, comprobar `matches-played` de RapidAPI con "cecchinato"
      para cerrar la duda documentada en C.2.
- [x] **E. Emparejado foto-boleto + texto del pick** (arreglado): los
      tipsters publican DOS mensajes por pick — la foto del slip (rival
      + cuota en el OCR) y el texto (stake + título). Se procesaban
      independientes: picks sin rival/cuota, `evento`=competición
      ("TENIS - Copa davis") y duplicados. Ahora `processor.py`
      empareja en ventana de 10 min (`_PAIR_WINDOW`): el texto busca
      el slip previo (`_find_pair_slip`, filtro `_looks_like_slip` con
      marcadores de boleto y rechazo de liquidados) y extrae sobre
      `texto + OCR` combinados; si la foto ya creó pick, se enriquece
      en vez de duplicar (`_enrich_paired_pick`, `metodo="+par"`). En
      catch-up (orden nuevo→viejo) la foto enriquece el pick del texto
      mirando hacia adelante (`_find_pair_text_pick`). Si la extracción
      combinada no ve apuesta, reintenta con el mensaje solo.
- [x] **F. Selección-prosa y evento-competición** (arreglado):
      `_SELECCION_KEYWORD` con `\b` + español ("menos de", "más de",
      "córners"...) — "mover" ya no casa con "over" ni "ganar" con
      "gana" — y guarda de longitud (`_SELECCION_MAX_LEN`=120) para que
      el análisis nunca sea la selección. `_extract_eventos`: patrón
      "A v B" de slips, conectores minúsculas en nombres ("Celta de
      Vigo"), y filtros `_COMPETITION_WORD`/`_MARKET_WORD` (cabeceras
      tipo "TENIS - Copa davis" o mercados tipo "Bergs - Ganará el
      encuentro" ya no se confunden con cruces). `_extract_linea` ya no
      saca "-0" de marcadores "0-0". `_rule_extract` rellena `mercado`
      con `_classify_leg_market` ("resultado sin empate" → "empate no
      válido"). `_normalize_pick` recorta la cola de competición en
      mayúsculas de `seleccion` ("...goles ESPAÑA").
- [x] **G. Boletos liquidados y celebraciones** (arreglado): el prompt
      OCR pide `SELLO: GANADOR` si ve el tick/sello de cobrado de
      cualquier diseño (visión, no solo el texto "GANADOR"). Negativos
      nuevos para captions de verde reposteado ("otro verde", "seguimos
      sumando", "total ganado") — solo filtran si el mensaje no trae
      cuota/stake propios. Footer "Apuesta con responsabilidad" ya no
      cuela como pata de combinada.
- [x] **H. Cuota/stake NULL ya no se inventan como 1.00** (arreglado):
      `_parsed_to_read` pasaba `cuota or 1.0`/`stake or 1.0` — la UI
      mostraba "1.00" ficticio. `PickRead.cuota`/`cantidad_apostada`
      ahora son `Optional` y la vista de canal muestra "—".
- [x] **Backfill aplicado** (`scripts/fix_reported_picks.py`): los 7
      casos reportados re-extraídos con el pipeline nuevo — Lady Bets/
      Bet Fran ahora tienen `Bergs vs Rodionov` + cuota 1.5, Mallorca
      `Mallorca - Real Sociedad B` + mercado "resultado sin empate" +
      cuota 1.5, dobles `Dominko/Sesko vs Cukierman/Shimanov` + 1.53,
      Dm7 títulos limpios ("Menos de 3,5 goles", "Menos de 11.0
      corners"), duplicado foto+texto de Dm7 Gratuito fusionado (pick
      1463 eliminado), y la combinada de CopetePicks con patas limpias.
      Además `_clean_text_field` aplicado a los 19 picks con markdown
      residual en `seleccion`/`apuesta`/`evento`.

### Pendientes detectados tras el fix de emparejado (sept-2026)

- [x] **P.1. Backfill masivo de cuota/stake NULL** — RESUELTO
      (sept-2026): `scripts/backfill_pairs.py` recorre todos los
      textos con pick simple incompleto (cuota/stake NULL o evento
      que no es un cruce real), busca el slip pareja en la ventana
      `_PAIR_WINDOW`, re-extrae sobre `texto + OCR` combinados y
      enriquece con `_enrich_paired_pick`. Guardas anti-regresión:
      `_patas_son_reales` (descarta combinadas fantasma con patas
      tipo "CHALL SZCZECIN"/"Siempre con cabeza"), `_era_multi`
      (no degrada una selección múltiple a simple) y "mejora neta"
      (solo escribe si el evento pasa a ser cruce real o se
      rellenan cuota/stake). Elimina el pick duplicado de la foto
      (y sus patas) cuando ambos mensajes crearon pick. Dry-run
      por defecto, `--apply` para escribir; idempotente.
      Resultado: 55 incompletos con slip → 29 enriquecidos +
      1 duplicado eliminado; quedan ~29 con cuota NULL sin
      pareja aprovechable (fotos sin OCR → P.8, o picks sin
      boleto).
- [x] **P.2. Promos coladas como picks** — RESUELTO (sept-2026):
      nuevos negativos en `_NEGATIVE_PATTERNS` — escalera dinero→dinero
      ("30€ - 96€", "30€ ➜ 10.000€"), `reto del año`, `empieza a
      ganar`, `premiumpay`, `accede`, `plaza` singular. Además
      `_LEG_NOISE_PATTERN` rechaza patas que son URL/link markdown o
      escalera €→€. Bug corregido: `_looks_like_bet` quitaba markdown
      ANTES de evaluar `\b` ("__Cuota 1.50__" no casaba y un pick real
      se rechazaba). Limpieza: 20 picks marcados `es_apuesta=False` /
      `metodo='rejected'` — incluye 2 promos verificadas como acierto
      que inflaban stats (supercuota MARCA 692, repost cobrado 753).
- [x] **P.3. Anuncios de reto como "picks"** — RESUELTO (sept-2026):
      los anuncios de reto/escalera ahora se rechazan directamente
      (`es_apuesta=False`) por los negativos anteriores; no solo van
      a `es_reto`. Los picks reales DENTRO de un reto ("PASO 2: X gana
      STAKE 3") siguen entrando porque las señales fuertes
      (cuota/stake+num) tienen precedencia.
- [ ] **P.4. Verificación pendiente**: ~22 picks de Lady Bets (+ resto)
      siguen pendientes → `scripts/verify_backlog.py --apply` cuando
      renueven cuotas. Y duda abierta de C.2: por qué "Marco
      Cecchinato" no salía en `matches-played` de RapidAPI.
- [x] **P.5. Cobertura de Segunda División** — RESUELTO (sept-2026):
      el pick 1528 `Mallorca - Real Sociedad B` se verificó con
      `acierto=True` tras el backfill de P.1 — la cadena de
      proveedores cubre la competición.
- [ ] **P.6. Residuos de cabecera en `seleccion`**: el patrón "pick +
      competición en la misma línea" puede dejar restos en formatos que
      aún no hemos visto — vigilar nuevas extracciones.
- [x] **P.7. Retry de OCR tras 429** — RESUELTO (sept-2026):
      `scripts/retry_ocr.py` recuperó los 296+35 OCR pendientes
      (0 fallos; solo queda 1 foto sin fichero, msg 78734). Después:
      `reclassify_rejected.py --apply` recuperó 35+2 picks de slips
      rechazados, `reprocess_raw.py` creó picks para las fotos sin
      pick previo y `backfill_pairs.py --apply` enriqueció 29 textos
      más con el OCR nuevo. Incidencia: la cuenta de OpenAI se quedó
      sin créditos a mitad del proceso (`credit_balance_exhausted`) —
      `call_with_retry` ya NO reintenta ese error (es permanente, no
      rate limit) y los scripts cuentan esos candidatos como
      "pendientes por cuota" en vez de abortar. Sellos de boleto
      cobrado en inglés (`WON`/`Returned`) añadidos a
      `_SETTLED_TICKET_PATTERN`.
- [ ] **P.8. UI móvil sin revisar**: la sección "Cuota de mercado" del
      hito 4 aún no se ha visto en Expo.

### 1. Core: verificación de resultados

- [x] **Proveedor de resultados para tenis.** Implementado con cadena
      de 3 niveles gratuita (api-sports.io NO tiene tenis y
      api-tennis.com solo da trial de 14 días):
      1) `ApiTennisProvider` → **TheSportsDB** (gratis sin registro, key
         pública "3"; ATP/WTA Tour y Grand Slams);
      2) `RapidApiTennisProvider` → **Tennis API - ATP WTA ITF**
         (RapidAPI, historial por jugador; Challenger/ITF);
      3) `TennisApi1Provider` → **tennisapi1** (RapidAPI, datos de
         Sofascore por categoría+fecha: ATP/WTA/Challenger/ITF/Copas).
      Cada proveedor solo se consulta si el anterior falla o agotó su
      cuota (~50 req/día por suscripción): un 403/429 lo marca como sin
      cuota hasta mañana y el siguiente pick ni lo intenta (ver
      `base.py`). Enrutado por deporte y solo mercado "ganador".
      Ambos fallbacks ya probados en vivo (Alcaraz/Shelton y
      Bucsa/Udvardy resueltos). Baloncesto: pendiente de empezar cuando
      se confirme (api-sports.io sí tiene basketball — API-Basketball).
- [x] **Mercados de sets/juegos en tenis** (sin coste extra de API: los 3
      proveedores ya devolvían el desglose por sets — `strResult`,
      `result`, `homeScore.periodN` — parseado a `MatchResult.sets`):
      - ~~Correct score en sets ("gana 2-0")~~ — **hecho**: "X gana A-B"
        exige el marcador exacto de sets (un 2-1 real ya no cuenta como
        acierto de un "gana 2-0"); también "jugador" + mercado
        "resultado exacto" con orientación por el orden del evento.
      - ~~Over/under y hándicap de juegos~~ — **hecho**: sumando juegos
        por set; total del partido y por jugador, por set concreto,
        líneas de cuarto y push incluidos. Over/under de sets también.
      - ~~RET/W-O~~ — **hecho**: si el proveedor reporta retirada o
        walkover (`MatchResult.status`) el pick se anula en cualquier
        mercado; si la casa difiere, corrección manual.
      - ~~Ganador de set individual y tiebreak~~ — **hecho**: "X gana
        el 1er set" se resuelve con los juegos de ese set; "habrá
        tiebreak" se deduce de un 7-6 en el desglose.
      - ~~Dobles~~ — **hecho**: matching por parejas (`_pair_similar`);
        un individual no casa con dobles ni al revés.
      - ~~Hándicap ambiguo / "gana un set" / líneas "N+"~~ — **hecho**:
        "-1.5" sin sujeto usa la convención de casas (≤1.5 → sets,
        ≥3.5 → juegos, en medio pendiente); "gana un set" y líneas
        "21+"/"20 o más" (= over N-0.5) soportadas en verificador y
        extractor.
      - ~~Correct score en juegos~~ — **hecho**: "gana 6-4 6-2" compara
        el desglose set a set (orientado al jugador o al orden del
        evento); exige mismo número de sets.
- [x] **Amortizar el límite de API-Football** (implementado): caché de
      fixtures por fecha + caché de stats/events/players por fixture,
      pendientes ordenados por `fecha_evento` DESC (los picks que caducan
      van primero), ventana de abandono de 14 días con gracia de 6 h para
      recuperados tarde, salto de fechas fuera de la ventana gratis ±1 día
      y detección de cuota agotada devuelta como HTTP 200 con `errors`
      (persistente en `provider_state.json`).
- [ ] **Mercados de fútbol** (casi completo). Resueltos automáticamente:
      - Con marcador (football-data, histórico ilimitado): ganador,
        empate no válido, doble oportunidad (1X/X2/12/"equipo y empate"),
        hándicap asiático **incluidos cuartos de línea** (±0.25/±0.75 →
        dos medias apuestas), over/under goles (total y por equipo),
        ambos marcan sí/no, resultado exacto ("2-1", con corrección de
        orientación si el evento va al revés).
      - Con `/fixtures/statistics` (API-Football, solo ventana ±1 día):
        córners, tarjetas, tiros (total y a puerta), faltas, fueras de
        juego — total del partido y por equipo.
      - Con `/fixtures/events` + `/fixtures/players` (misma ventana):
        "X marca", "X marca o asiste", "X recibe tarjeta" (propia puerta
        y penalti fallado no cuentan; no jugó → `anulada`) y props de
        jugador con número ("X más de 1.5 tiros a puerta").
      Pendiente a propósito: partidos parados a mitad
      (`SUSP`/`ABD` — hay mercados ya decididos que la casa paga;
      quedan manuales).
- [x] **Combinadas como sección propia** (self-FK sobre `parsed_picks`;
      ADR completo en el docstring de la migración `f1a2b3c4d5e6`):
      - Modelo: padre `es_combinada=True` con cuota/stake/texto unidos y
        N patas como `parsed_picks` normales con `combinada_id`+`orden`.
        Se eligió self-FK sobre tabla hija o JSON por reutilizar TODO el
        pipeline (verificador, PATCH manual, dedup, ventanas de edad)
        sin duplicar lógica; el coste asumido es filtrar patas/padres en
        las queries de simples (centralizado en `_es_pick_simple`).
      - Extractor: señales "crea tu apuesta"/"combinada"/"N pronósticos"
        + split por viñetas por reglas, `patas` estructuradas en el
        prompt del LLM y degradación a simple con <2 patas reales.
      - Liquidación (`verifier.settle_combinada`): una pata roja tumba
        la combinada, las anuladas se excluyen, todas verdes → acierto,
        todas anuladas → anulada. `cuota_efectiva` = producto de las
        cuotas de patas activas SOLO si todas las patas tienen cuota;
        si no, NULL (no se inventa la ganancia). Los padres nunca se
        consultan a APIs deportivas. Override manual del padre siempre
        respetado; una combinada auto-liquidada vuelve a pendiente si
        una pata se reabre.
      - API/UI: `PATCH` sobre una pata re-liquida el padre;
        "Yo también la jugué" opera sobre el padre (una pata redirige);
        sección "Combinadas" propia en `[informante]` y `parsed-picks`
        con patas expandibles, fuera de las stats de simples
        (`calcular_stats_combinadas` para las métricas propias).
      - Backfill: `scripts/backfill_combinadas.py` reprocesa el RAW de
        cada registro etiquetado como combinada (dry-run por defecto,
        `--apply` para escribir) — los falsos positivos se reclasifican
        solos y los raws no se tocan.
- [x] **Partidos aplazados/cancelados → anulada**: implementado. Si el
      fixture consta `POSTPONED`/`CANCELLED` (football-data) o
      `PST`/`CANC` (API-Football) y la `fecha_evento` lleva más de 72 h
      pasada, el pick se marca `anulada` (la casa devuelve fuera de su
      ventana de reprogramación). Reusa las listas de fixtures ya
      cacheadas — no cuesta llamadas extra. Quedan fuera a propósito
      los parados a mitad (`SUSP`/`ABD`/`INT`): con marcador parcial
      hay mercados ya decididos que la casa paga — siguen pendientes.
- [x] **Comparar cuota del tipster vs cuota real de mercado**: contrastar la
      cuota publicada por el informante contra la cuota disponible en APIs de
      apuestas (The Odds API, Pinnacle, Betfair...) en el momento de la
      publicación. Detecta cuotas infladas que el usuario nunca pudo coger de
      verdad — clave para comparar el yield publicado del tipster con el yield
      real alcanzable. Implementación completa en §5.

### 2. Robustez de la extracción

- [x] **Fecha de referencia al LLM**: `extract_pick`/`_llm_extract`/
      `_extract_event_date` reciben `fecha_referencia` (la fecha real del
      mensaje); el prompt la usa para resolver fechas sin año y las reglas
      la usan como año por defecto en vez de `datetime.now()`.
- [ ] **Álbumes de Telegram** (implementado, pendiente de probar con un
      álbum real): handler `events.Album` en `handlers.py` — cada foto del
      álbum genera su propio raw+pick y hereda la caption compartida cuando
      no lleva texto propio. `NewMessage` ignora mensajes con `grouped_id`
      para no duplicar.
- [x] **Dedup entre canales: NO se hace** (decisión de diseño). El mismo pick
      reenviado a dos canales cuenta dos veces a propósito: cada canal se
      audita de forma independiente y el pick suma a las estadísticas de cada
      uno. El dedup actual es por canal y así se queda.
- [ ] ~~**Auto-resolver picks con mensaje "VERDE"**~~ (DESCARTADO por el
      usuario): usar un mensaje "✅ VERDE" posterior para marcar el pick
      anterior como acertado daría falsos positivos — los canales celebran
      resultados ambiguos o de otros picks. Se sigue verificando solo por
      APIs deportivas, que son la fuente fiable.
- [x] **Reintentos de media y OCR** (hecho): el catch-up detecta y repara
      raws incompletos (`processed=False`, foto sin OCR, vacíos) en cada
      arranque — no solo los vacíos — y las llamadas a OpenAI llevan
      backoff ante 429 (`openai_retry.call_with_retry`: espera guiada por
      el hint del error, 3 reintentos de 20-90 s) en vez de los ~0.2 s del
      SDK que dejaban raws huérfanos en masa.

### 3. App / producto

- [x] **Pantalla de análisis global** (hecho): `GET /api/v1/analisis`
      agrega por canal y por deporte las stats del tipster (picks de
      Telegram) contra las apuestas del usuario, con ranking por yield y
      comparativa de cuota media publicada vs conseguida sobre los picks
      jugados ("Yo también la jugué"). Pantalla `app/screens/auditoria.tsx`
      (icono "Análisis" de la bottom-bar); la calculadora de proyecciones
      queda en `/screens/analysis`, enlazada desde la auditoría.
- [x] **"Yo también la jugué"** (hecho): `POST /telegram/parsed-picks/{id}/jugar`
      crea una apuesta del usuario (`picks`) copiando selección/mercado/cuota/
      stake/casa del pick, enlazada con `picks.parsed_pick_id` (FK nueva,
      migración `d4e5f6a7b8c9`). El modal del pick del tipster tiene botón +
      mini-formulario: cantidad obligatoria, cuota y casa pre-rellenadas y
      editables — la cuota real conseguida puede diferir de la publicada, que
      es justo lo que habilita comparar "yield tipster vs yield tuyo".
      Idempotente: un doble tap devuelve la apuesta ya creada, no duplica.
      Si ya existe, el modal muestra "✓ Ya la tienes registrada".
- [ ] **Notificaciones push** cuando llega un pick nuevo.
- [x] **CRUD de canales en BD** (hecho): tabla `channels` como fuente de
      verdad; `TELEGRAM_TARGET_CHANNEL` queda como bootstrap
      (`seed_channels_from_env` si la tabla está vacía). Handlers globales
      de Telethon filtran por caché `active_channel_ids()` refrescado tras
      cada cambio — sin reiniciar. Endpoints `/channels` + `/disponibles`
      (picker con `iter_dialogs` de la cuenta Telethon) + alta por
      enlace/@user/id con resolución `get_entity`. Pantalla
      `app/screens/canales.tsx` (toggle activo, borrado lógico que conserva
      historial, alta manual, picker). Pendiente: probar en vivo.

### 4. Infra / calidad

- [ ] **Deploy real**: backend en servidor + PostgreSQL gestionada + HTTPS
      (hoy todo corre en local: Uvicorn + Expo en red local).
- [x] **Backups de PostgreSQL — script** (hecho): `backend/scripts/backup_db.py`
      genera dumps comprimidos (`pg_dump -Fc`) en `backend/backups/` con
      timestamp, retención de 14 días (`BACKUP_RETENTION_DAYS`) y
      localiza `pg_dump` en PATH o en `Program Files\PostgreSQL`. El
      directorio está en `.gitignore`. Restaurar:
      `pg_restore -d controlpick backups/<dump>.dump`. En local basta
      ejecución manual antes de migraciones o reprocesos.
- [ ] **Backups programados diarios** (pendiente): crear la tarea en Task
      Scheduler / cron para que `backup_db.py` corra solo cada día
      (comandos en la docstring del script). Se activa junto al deploy.
- [x] **Tests del pipeline de Telegram** (hecho): `catchup.py` (marca de
      agua, huecos por debajo de la marca, raw vacío/`processed=False`
      reprocesado, raw con pick vinculado no se toca, corte por 7 días,
      error aislado por mensaje y por canal), `handlers.py` (parsing de
      canales/ids, foto sin texto → OCR, foto con caption sin OCR,
      descarga fallida → raw vacío, grouped_id ignorado, álbum hereda
      caption), `ocr.py` (sin key/archivo/error → None, éxito) y
      `client.py` (credenciales obligatorias, singleton, reset).
- [ ] **Limpieza de usuarios**: decidir si `test@a.com` se mantiene; la
      contraseña actual `123456` es estrictamente temporal.

### 5. Auditoría de cuotas (hito 4: tipster vs mercado)

Implementado con **snapshots propios** (diseño B), no histórico de API:
no existe histórico gratis con cobertura Challenger/ITF, y cada respuesta
de la API regala ya la cuota de apertura (`initialFractionalValue`).

Proveedor dedicado: **allsportsapi2** (RapidAPI BASIC $0, misma key, solo
cambia el host; backend Sofascore → comparte ids de evento con
tennisapi1, que queda como scavenger). La cuota de resultados no se
toca: resultados siempre tienen prioridad.

- [x] **Modelo** (migraciones `b2c3d4e5f6a7` + `c2d3e4f5a6b7`):
      `odds_events` (1 fila/evento: home/away — necesarios para casar la
      selección con la opción "1"/"2"), `odds_snapshots` (append-only por
      evento: mercado, opción, línea, cuota, cuota_apertura, flags
      live/suspended) y `parsed_picks.odds_event_id`.
- [x] **Provider `app/services/odds/`**: protocolo + impl Sofascore
      (allsportsapi2 tenis+fútbol, tennisapi1 scavenger). Reutiliza
      `provider_state.json` (rate_limited/missed) y los extractores del
      verificador.
- [x] **Job de snapshots** (`odds/snapshotter.py`, loop en
      `lifecycle.py`): dedup por evento (N canales = 1 llamada), captura
      al importar (≈ cuota al publicar), captura de cierre (~3 h
      pre-inicio) y recuperación post-partido (la API sigue dando
      apertura+cierre con el partido acabado — cubre caídas cortas).
      Guarda TODOS los mercados: el mapeo se hace al comparar, así un
      mapeo futuro no necesita nuevas llamadas.
- [x] **Mapeo pick→mercado** (`odds/compare.py`): Full time 1/X/2,
      Match goals / Total games won / Corners 2-Way / Cards con
      `choice_group` = `linea`, Asian handicap "(N) Equipo", BTTS,
      Double chance (1X/X2/12 y "Equipo o empate"), Draw no bet, 1er set.
      Sin equivalente → `mapeado=false`, NULL (no se inventa).
- [x] **Endpoint** `GET /telegram/parsed-picks/{id}/odds`:
      cuota_tipster vs apertura / publicación / cierre + derivadas
      `cuota_disponible` (¿existía la cuota anunciada? → detector de
      cuotas infladas) y `clv_pct` (% sobre el cierre → el tipster bate
      al mercado). Tests en `tests/test_odds.py` (29).

- [x] **Agregados por canal** (`GET /informante/{nombre}/odds-stats`):
      % de picks con cuota inflada, CLV medio del canal, % que bate el
      cierre, % mapeado — el veredicto global del tipster (un pick
      aislado no dice nada, el patrón sí). `compare_picks` hace la
      comparación por lotes (1 query de eventos + 1 de snapshots, sin
      N+1) y `aggregate_odds_stats` agrega solo sobre los picks donde
      cada métrica era calculable. El mismo bloque `odds` llega por
      canal en `GET /analisis` (`AnalisisCanal.odds`) y agregado global
      (`totales_odds`).
- [x] **UI**: sección "Cuota de mercado" en el detalle del pick
      (`[informante].tsx`: mercado/opción de la API, tipster vs
      apertura/publicación/cierre + badges "Cuota inflada"/"Cuota
      real"/CLV) y resumen por canal en la pantalla de análisis
      (`auditoria.tsx`, `OddsLine`: mapeados, CLV medio, % bate cierre,
      % cuotas infladas — también en el resumen global).

Pendiente:

- [~] **Probar en vivo**: backend verificado en vivo (2026-09-19):
      snapshotter capturando (6 eventos / 96 snapshots / 8 picks
      enlazados), `GET /parsed-picks/{id}/odds` y
      `/informante/{nombre}/odds-stats` responden con datos reales
      (CLV y detección de cuotas infladas funcionando — ej. pick 753
      cuota tipster 1.53 vs mercado 2.1 → inflada detectada).
      **Falta solo revisar la UI en el móvil.**
      Nota: al arrancar se detectó y corrigió un bug de truncamiento
      — el extractor a veces vuelca el análisis completo en
      `seleccion`/`apuesta` y el INSERT en `parsed_picks` reventaba por
      varchar(255/500), haciendo rollback del raw entero y reintentos
      infinitos del catch-up. `processor._fit` ahora trunca cada campo
      a su límite real de columna. PENDIENTE de calidad: el LLM sigue
      metiendo el análisis largo en `seleccion` (no revienta, pero el
      dato es feo) — revisar prompt/normalización del extractor.
- [ ] **Backfill histórico** (opcional, decidir con datos reales):
      OddsPapi (`bet36528` en RapidAPI) regala `/v4/historical-odds`
      ilimitado en el free tier — serviría para auditar picks antiguos
      vía script tipo `verify_backlog.py`. Cobertura Challenger sin
      verificar; si no la tiene, solo rellenaría fútbol/top → valor
      marginal. NO es necesario para producción (los snapshots ya se
      auto-mantienen y las caídas cortas las cubre el fetch post-partido).

## Estado actual del entorno

- **Backend:** FastAPI en `http://0.0.0.0:8000` con `DATABASE_URL=postgresql+asyncpg://<usuario>:<contraseña>@localhost:5432/controlpick`
- **Frontend:** Expo web/móvil con `EXPO_PUBLIC_API_BASE_URL=http://192.168.1.71:8000/api/v1`
- **Base de datos:** PostgreSQL `controlpick` en `localhost:5432`

## Comando backend para red local

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app
```

> **Importante (Telegram/Telethon):** `--reload-dir app` restringe el auto-reload a la
> carpeta `app/`. Sin esto, `--reload` vigila TODO `backend/` (incluido `.venv/`), y cada
> vez que Telethon escribe/compila sus propios archivos internos, uvicorn reinicia el
> servidor entero — relanzando `client.start(phone=...)` a mitad del login interactivo y
> haciendo que el código de verificación de Telegram no llegue o quede invalidado.
> Para el primer login de Telethon, mejor aún: arrancar **sin** `--reload` una única vez
> hasta completar el login (así se genera `<TELEGRAM_SESSION_NAME>.session`); a partir de
> ahí ya se puede usar `--reload --reload-dir app` con normalidad.
>
> **Si Telegram deja de enviar el código de login** (`SentCodeTypeApp` pero no llega nada
> al chat "Telegram" de la app): es un throttle/anti-abuso de Telegram por pedir el código
> demasiadas veces seguidas. Reintentar no ayuda — hay que esperar (30-60 min o más) y
> reintentar UNA sola vez con `scripts/telegram_login_diagnostic.py`.

## Migraciones pendientes de ejecutar

Aplicar el esquema a la nueva base PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Optimizaciones futuras de costes (OCR y LLM)

- **Filtrar media**: solo aplicar OCR a fotos (`MessageMediaPhoto`). Descartar GIFs, vídeos y documentos para no gastar créditos de OpenAI en contenido que no es procesable.
- **Extractor híbrido ya implementado**: pre-filtro de alta confianza, regex para patrones claros y LLM (`gpt-4o-mini`) solo como fallback.
- **Pre-filtro anti-spam**: no llamar al extractor cuando el mensaje es un saludo, promo, enlace o sorteo.
- **OCR local**: evaluar `pytesseract` o `easyocr` para imágenes simples y reservar OpenAI solo cuando el local falle.
- **LLM local**: considerar `ollama` o modelos cuantizados una vez el volumen justifique la infraestructura.
- **Batching**: agrupar varios mensajes en una sola llamada al LLM para reducir overhead.
- **Caché por canal**: no reprocesar mensajes duplicados o plantillas de promoción repetidas.

## Verificación automática de resultados

Ver `app/services/results/`. Cadena por deporte: **fútbol** (football-data.org
→ API-Football) y **tenis** (TheSportsDB → ATP-WTA-ITF → tennisapi1). El
verificador corre cada 3 h, procesa pendientes recientes primero y deja de
reintentar picks con `fecha_evento` > 14 días (gracia de 6 h para recién
importados). "Push" (líneas enteras) y aplazados/cancelados >72 h se marcan
`anulada`; un jugador que no disputó minutos también → `anulada`.

**Limitaciones conocidas:**
- El plan gratuito de **API-Football solo permite consultar fechas dentro de
  una ventana de ±1 día respecto a "hoy"**: los mercados de estadísticas
  (córners, tarjetas, tiros...), jugador y props se resuelven al día siguiente
  o se pierden; los mercados de marcador no la necesitan (football-data tiene
  histórico ilimitado en sus ~12 ligas top).
- **Ligas menores / competiciones fuera de football-data** (Süper Lig,
  amistosos, Primera Federación...): solo resolubles dentro de la ventana de
  API-Football; fuera de ella, corrección manual.
- **Tenis** resuelve ganador, marcador exacto en sets, "gana un set",
  over/under y hándicap de juegos y de sets — también por set concreto —,
  ganador de set individual, tiebreak sí/no (vía `MatchResult.sets`) y
  **dobles** (matching por parejas: exige todos los miembros en la pista, y
  una pista de dobles no casa con un individual). RET/W-O → anulada
  automática, correct score en juegos ("gana 6-4 6-2"). Líneas
  "21+"/"N o más" y hándicap sin sujeto (convención: ≤1.5 sets,
  ≥3.5 juegos, en medio pendiente) soportadas.
  Pendiente a propósito: hándicap sin sujeto en la zona ambigua
  |línea| 2-3.
- **Baloncesto** sin proveedor (API-Basketball de api-sports.io sería el
  candidato).
- Futuro: plan de pago de API-Football eliminaría la restricción de fechas.

## Investigación: mercados de primera parte/descanso (hito 2)

Investigado el 20-sep-2026. **Cobertura confirmada gratis**, sin APIs nuevas:

- **HT score** (1X2 descanso, over/under goles 1H, HT/FT, ambos marcan 1H,
  equipo marca 1H): `score.halfTime` en football-data.org (12 ligas del
  tier gratis) y `score.halftime` en API-Football (todas las ligas, 100
  req/día). API-Football `fixtures/events` da goles/tarjetas con minuto →
  "primer equipo en marcar", "minuto del gol".
- **Stats por mitad** (córners/tarjetas/tiros 1H): Sofascore
  `GET /api/v1/event/{id}/statistics` devuelve `period: ALL|1ST|2ND` con
  `cornerKicks`, `yellowCards`, `totalShotsOnGoal`, `expectedGoals`...
  gratis y sin key. Ya integramos Sofascore para odds → el event-matching
  está escrito. Sirve también para stats de **partido completo**
  (tarjetas/córners actuales que se pierden por la ventana ±1 día).
- **Histórico/auditoría**: CSVs gratis de football-data.co.uk
  (`HTHG/HTAG/HTR` + stats + odds, 22 ligas incl. Segunda, 2 actualiz./sem).

**Implementado (22-sep-2026)**: `MatchResult` lleva `ht_home_score`/
`ht_away_score`; football-data (`score.halfTime`), API-Football
(`score.halftime`) y footapi7 (`homeScore.period1`) los rellenan. El
verifier resuelve 1ª parte con `_ht_view()` — el mismo partido visto
al descanso — reusando los resolutores existentes: ganador 1H, empate
al descanso, over/under goles 1H, doble oportunidad 1H, BTTS 1H,
marcador exacto al descanso, hándicap 1H y HT/FT ("gana 1ª parte y el
partido"). Stats 1H (córners/tarjetas 1ª parte) vía
`find_match_stats_1h` de footapi7 (periodo `1ST`). Sin dato HT el pick
queda pendiente — nunca se verifica con el marcador final. Bonus:
"Empate"/"X" a tiempo completo también se resuelve ya (antes quedaba
pendiente).

## Provider de stats footapi7 + rescate CSV (implementado 21-sep-2026)

El punto 1 de la lista anterior quedó implementado así:

- **`FootApiStatsProvider`** (`app/services/results/footapi_stats.py`):
  fútbol vía `footapi7.p.rapidapi.com` (dato de Sofascore servido por
  RapidAPI — `api.sofascore.com` directo está bloqueado por Cloudflare,
  403 confirmado). Reutiliza `RAPIDAPI_TENNIS_KEY` (la key es de cuenta
  RapidAPI; footapi7 tiene cuota BASIC diaria propia, independiente de
  tennisapi1/allsportsapi2). Sin ventana de fechas: un partido terminado
  devuelve marcador y stats semanas después.
- Flujo: `/api/search/{equipo}` → mejor entidad `sport.slug=football` →
  `/api/team/{id}/matches/previous/{page}` (~30 eventos/página, máx 2
  páginas) → match por `match_score` + tolerancia ±1 día →
  `/api/match/{id}/statistics` (periodos `ALL` y `1ST` — este último
  habilita mercados de córners/tarjetas de 1ª parte). Stats traducidas
  a las claves canónicas de API-Football ("Corner kicks"→"Corner
  Kicks"...) para que el verifier no cambie. Además `/incidents`
  (goles/tarjetas con jugador y minuto) y `/lineups` (minutos + stats
  por jugador) implementan `find_match_events`/`find_match_players`:
  los mercados de jugador ("X marca", "X marca o asiste", "X recibe
  tarjeta") y los props con número ("X más de 1.5 tiros a puerta")
  también se resuelven fuera de la ventana ±1 día. Cachés de
  búsqueda/eventos/stats por pasada;
  403/429 marcan cuota agotada hasta mañana (no cuentan como miss).
- **Cadena de fútbol**: football-data → API-Football → **footapi7** →
  (tenis después). Cubre stats fuera de ventana y marcadores de ligas
  menores.
- **`scripts/backfill_stats_csv.py`**: rescate semanal/manual con los
  CSVs gratis de football-data.co.uk (22 ligas; URL apex sin www, que
  redirige). Dry-run por defecto, `--apply` para escribir, `--days`,
  `--leagues`. Excluye padres/patas de combinada y mercado "combinada"
  legado. Tras aplicar reliquida combinadas con `_settle_combinadas`.
- Tests: `tests/test_footapi_stats.py` (11: marcador, no-terminado,
  equipo inexistente, stats canónicas, incidents→MatchEvents con
  propia puerta excluida, lineups→MatchPlayers con minutos, e
  integración con `verify_pick` para córners, "X marca", "no jugó →
  anulada" y prop de tiros a puerta).

## Decisiones pendientes para la próxima sesión

1. ~~Mercados de stats completos~~ → **hecho** (sección anterior).
2. **Probar en vivo el CRUD de canales**: arrancar backend y abrir
   `app/screens/canales.tsx` en Expo — verificar picker de `iter_dialogs`,
   alta/baja dinámica y catch-up post-alta.
3. **Notificaciones push (hito 9)**: pospuesto por decisión — se implementa
   cuando la base (extracción + verificación) esté fina. Diseño apuntado:
   notificar pick nuevo (`es_apuesta`, combinadas solo el padre) y
   liquidación solo de picks marcados "Yo también la jugué".
