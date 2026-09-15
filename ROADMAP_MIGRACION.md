# Hoja de Ruta - ControlPick

Estado del proyecto: migración del backend Node/Mongo a FastAPI/PostgreSQL y del frontend a Expo/React Native.

## Fases completadas

- **Fase 0: Sanitización de historial Git y entorno.** (COMPLETADA)
- **Fase 1: Verificación de lógica de backend en FastAPI.** (COMPLETADA)
- **Fase 2: Definición y validación de endpoints y modelos de BD.** (COMPLETADA)
  - `users`, `informantes`, `picks` migradas y validadas con SQLModel/Alembic.
- **Fase 3: Integración de servicios API en Frontend Expo.** (COMPLETADA)
  - Cliente API (`src/api/*`), AuthContext y pantallas principales funcionando.
  - Configuración local de `.env` para backend y frontend.
- **Fase 4: Validación End-to-End en dispositivo móvil (Auth, flujo de Picks y navegación).** (COMPLETADA)
  - Validado en dispositivo Android físico vía Expo Go: registro, login (incl. manejo de credenciales inválidas), creación/listado de picks, navegación a detalle de informante.
  - Corregido el manejo de "safe areas" (TopBar/BottomBar se superponían con la barra de estado y la barra de navegación del sistema).
  - Eliminado el botón "Atrás" redundante de la BottomBar (duplicaba el gesto/botón nativo del sistema).

- **Fase 5: Desacoplamiento de Bots de Telegram y Scrapers.** (EN CURSO)
  - Retirado por completo el backend legacy de Node.js (`app.js`, `index.js`, `telegramBot.js`,
    `DbMongo/`, `Api/`, `Firebase/`, `services/*.js`, `package.json`) y el script Python
    obsoleto basado en MongoDB (`scripts/telegram_history.py`). Todo el backend vive ya
    únicamente en FastAPI/PostgreSQL (`backend/app/`).
  - Nuevo módulo `app/services/telegram/` con Telethon (cliente de usuario, no Bot API):
    `client.py` (singleton del `TelegramClient`), `handlers.py` (listener del canal
    objetivo vía `TELEGRAM_TARGET_CHANNEL`) y `processor.py` (stub de procesamiento sin
    lógica de LLM todavía, solo loguea y estructura el mensaje).
  - `app/core/lifecycle.py` arranca/detiene el listener de Telegram de forma concurrente
    con el servidor FastAPI usando el patrón `lifespan`.
  - Pendiente dentro de esta fase: primer login interactivo de Telethon (genera el
    `.session`), scrapers de resultados (LaLiga/Marca/AS) y verificador de picks —
    de momento quedan retirados junto con el resto del Node.js, a la espera de decidir
    si se reintroducen en Python. Integración con LLM e Instagram: pospuestas a fases
    posteriores.
- **Fase 6: Despliegue y Hardening a producción.** (PENDIENTE)

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
> demasiadas veces seguidas (p. ej. por el bug de `--reload` de arriba). Reintentar no
> ayuda y probablemente alarga el bloqueo — hay que esperar (30-60 min o más) y reintentar
> UNA sola vez con `scripts/telegram_login_diagnostic.py`. Si el `phone_code_hash` que
> devuelve es igual al de un intento anterior, Telegram sigue sirviendo una respuesta en
> caché sin notificar de verdad: seguir esperando.

## Migraciones pendientes de ejecutar

Aplicar el esquema a la nueva base PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Optimizaciones futuras de costes (OCR y LLM)

- **Filtrar media**: solo aplicar OCR a fotos (`MessageMediaPhoto`). Descartar GIFs, vídeos y documentos para no gastar créditos de OpenAI en contenido que no es procesable.
- **Extractor híbrido ya implementado**: pre-filtro de alta confianza, regex para patrones claros y LLM (`gpt-4o-mini`) solo como fallback, reduciendo llamadas innecesarias.
- **Pre-filtro anti-spam**: no llamar al extractor cuando el mensaje es un saludo, promo, enlace o sorteo.
- **OCR local**: evaluar `pytesseract` o `easyocr` para imágenes simples y reservar OpenAI solo cuando el local falle.
- **LLM local**: considerar `ollama` o modelos cuantizados una vez el volumen justifique la infraestructura.
- **Batching**: agrupar varios mensajes en una sola llamada al LLM para reducir overhead.
- **Caché por canal**: no reprocesar mensajes duplicados o plantillas de promoción repetidas.

## Verificación automática de resultados

Implementada para los mercados "ganador" (incl. "resultado sin empate"), hándicap asiático y over/under, usando football-data.org (ligas top) con fallback a API-Football (más cobertura, límite más bajo). Con líneas enteras de hándicap/over-under puede haber "push" (empate técnico), que se marca como `anulada` en vez de acierto/fallo.

**Limitaciones conocidas:**
- El plan gratuito de **API-Football solo permite consultar fechas dentro de una ventana de ±1 día respecto a "hoy"** (p. ej. si hoy es 15/09, solo admite 14/09-16/09). Para partidos de hace varios días (lo habitual, ya que se verifica después de jugarse) esta fuente prácticamente no aporta cobertura adicional salvo que se verifique casi en tiempo real. Football-data.org no tiene esta restricción para sus ligas cubiertas.
- **Tenis y otros deportes no de fútbol** no están cubiertos por ninguna de las dos APIs actuales: quedan siempre en verificación manual.
- **Ligas menores** (p. ej. Primera Federación/Segunda RFEF en España) no están cubiertas por football-data.org (solo ~12 competiciones top) y, aunque API-Football tiene más cobertura, la restricción de fechas del plan gratuito limita su utilidad práctica para verificación diferida.
- Futuro: evaluar un plan de pago de API-Football (elimina la restricción de fechas) o una API de resultados de tenis, si el volumen de picks de ese tipo lo justifica.
