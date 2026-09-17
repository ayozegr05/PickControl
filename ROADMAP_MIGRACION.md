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
  - **Verificación automática de resultados** (fútbol): football-data.org con
    fallback a API-Football; mercados ganador / hándicap asiático / over-under.
    Solo `deporte=fútbol` — otros deportes quedan pendientes por diseño.
  - **Corrección manual** de resultados vía `PATCH /telegram/parsed-picks/{id}`.
- **Fase 6: Despliegue y Hardening a producción.** (PENDIENTE — ver hitos abajo)

## Próximos hitos

### 1. Core: verificación de resultados

- [ ] **Proveedor de resultados para tenis y baloncesto.** Tenis
      implementado con cadena de 3 niveles gratuita (api-sports.io NO
      tiene tenis y api-tennis.com solo da trial de 14 días):
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
      **Pendiente de verificar en producción**: ambos fallbacks ya
      probados en vivo (Alcaraz/Shelton y Bucsa/Udvardy resueltos).
      Baloncesto: pendiente de empezar cuando se confirme
      (api-sports.io sí tiene basketball).
- [ ] **Mercados de sets/juegos en tenis** (mismo coste de API: los 3
      proveedores ya devuelven el desglose por sets — `strResult`,
      `result`, `homeScore.periodN` — solo falta parsearlo):
      - Correct score en sets ("gana 2-0"): hoy un pick así se etiqueta
        mercado "ganador" y se marca acierto aunque gane 2-1 — bug de
        precisión: la apuesta real se perdió. Verificación estricta.
      - Over/under y hándicap de juegos: sumando juegos por set.
      - Decidir política ante RET/W-O (bookies suelen anular).
- [ ] **Amortizar el límite de API-Football** (implementado, pendiente de
      verificar que ya no saltan 429): caché de fixtures por fecha en ambos
      proveedores (una llamada por día en vez de por pick) y ventana de
      abandono de 14 días en el verificador: los picks irresolubles dejan de
      reintentarse en cada ciclo y quedan para corrección manual. Si sigue
      corto: bajar la frecuencia del verificador (hoy cada 3 h) o plan de pago
      que elimine la restricción de fechas.
- [ ] **Verificar mercados complejos** (parcial): ya se resuelven con el
      marcador — ganador, hándicap asiático, over/under goles (total del
      partido y por equipo), doble oportunidad (1X/X2/12/"equipo y
      empate"), ambos marcan (sí/no) y empate no válido. Además se
      resuelven con `/fixtures/statistics` de API-Football: **córners,
      tarjetas (amarilla+roja), tiros (totales y a puerta), faltas y
      fueras de juego**, tanto total del partido como por equipo —
      **pero solo para partidos dentro de la ventana ±1 día del plan
      gratis**: los picks de stats se verifican al día siguiente o se
      pierden (el plan de pago lo arreglaría). También los **mercados de
      jugador** con `/fixtures/events` ("X marca", "X marca o asiste",
      "X recibe tarjeta"; gol en propia y penalti fallado no cuentan;
      y si el jugador no consta en ningún evento se consulta además
      `/fixtures/players`: consta que no disputó minutos → `anulada`
      (la casa devuelve), sin datos fiables → pendiente). También los
      **props de jugador con número** ("X más de 1.5 tiros a puerta",
      "2 o más tiros - Jugador") con las stats por jugador del mismo
      endpoint, el **resultado exacto** ("2-1", con corrección de
      orientación si el evento va al revés) y los **cuartos de
      hándicap** (±0.25/±0.75 → dos medias apuestas). Pendiente aún:
      **primera parte/descanso** y **combinadas** (hay que modelar
      cada selección por separado; hasta entonces quedan manuales —
      nunca se verifican contra una sola selección, eso sería un falso
      resultado).
- [x] **Partidos aplazados/cancelados → anulada**: implementado. Si el
      fixture consta `POSTPONED`/`CANCELLED` (football-data) o
      `PST`/`CANC` (API-Football) y la `fecha_evento` lleva más de 72 h
      pasada, el pick se marca `anulada` (la casa devuelve fuera de su
      ventana de reprogramación). Reusa las listas de fixtures ya
      cacheadas — no cuesta llamadas extra. Quedan fuera a propósito
      los parados a mitad (`SUSP`/`ABD`/`INT`): con marcador parcial
      hay mercados ya decididos que la casa paga — siguen pendientes.
- [ ] **Comparar cuota del tipster vs cuota real de mercado**: contrastar la
      cuota publicada por el informante contra la cuota disponible en APIs de
      apuestas (The Odds API, Pinnacle, Betfair...) en el momento de la
      publicación. Detecta cuotas infladas que el usuario nunca pudo coger de
      verdad — clave para comparar el yield publicado del tipster con el yield
      real alcanzable.

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

- [ ] **Pantalla de análisis global**: ROI/yield por canal y por deporte,
      comparativa "tipster vs tú" agregada — es el valor real del proyecto
      (auditar si un tipster es rentable de verdad).
- [ ] **"Yo también la jugué"**: registrar apuesta manual desde un pick de
      Telegram con un tap, copiando cuota/stake/mercado.
- [ ] **Notificaciones push** cuando llega un pick nuevo.
- [ ] **CRUD de canales en BD**: añadir/quitar canales monitorizados desde la
      app en vez de editar `TELEGRAM_TARGET_CHANNEL` y reiniciar.

### 4. Infra / calidad

- [ ] **Deploy real**: backend en servidor + PostgreSQL gestionada + HTTPS
      (hoy todo corre en local: Uvicorn + Expo en red local).
- [ ] **Backups de PostgreSQL** (`pg_dump` diario): la BD es la fuente de
      verdad de la auditoría.
- [ ] **Tests del pipeline de Telegram**: catch-up, dedup, OCR y verifier tienen
      la lógica más frágil y la menor cobertura.
- [ ] **Limpieza de usuarios**: decidir si `test@a.com` se mantiene; la
      contraseña actual `123456` es estrictamente temporal.

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

Implementada para los mercados "ganador" (incl. "resultado sin empate"), hándicap asiático y over/under, usando football-data.org (ligas top) con fallback a API-Football (más cobertura, límite más bajo). Con líneas enteras puede haber "push" (empate técnico), que se marca como `anulada` en vez de acierto/fallo.

**Limitaciones conocidas:**
- El plan gratuito de **API-Football solo permite consultar fechas dentro de una ventana de ±1 día respecto a "hoy"**. Para partidos de hace varios días esta fuente prácticamente no aporta cobertura salvo verificación casi en tiempo real.
- **Tenis y otros deportes no de fútbol** no están cubiertos: quedan siempre en verificación manual.
- **Ligas menores** (Primera Federación/Segunda RFEF) no están cubiertas por football-data.org (~12 competiciones top solamente).
- Futuro: plan de pago de API-Football (elimina la restricción de fechas) o API de tenis.
