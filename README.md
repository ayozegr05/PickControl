# ControlPick

![Backend](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)
![Database](https://img.shields.io/badge/database-PostgreSQL%2016-4169E1?logo=postgresql&logoColor=white)
![Frontend](https://img.shields.io/badge/frontend-Expo%20%2F%20React%20Native-000020?logo=expo&logoColor=white)
![Language](https://img.shields.io/badge/backend%20lang-Python%203.12-3776AB?logo=python&logoColor=white)
![Language](https://img.shields.io/badge/frontend%20lang-TypeScript-3178C6?logo=typescript&logoColor=white)
![Status](https://img.shields.io/badge/status-en%20desarrollo%20activo-yellow)

> Plataforma inteligente de auditoría y análisis de rentabilidad para tipsters de apuestas deportivas.

## 1. Visión del proyecto

**ControlPick** nace para responder una pregunta muy simple que casi nadie audita con rigor: *¿este tipster/canal de pronósticos realmente gana dinero, o solo lo parece?*

La visión completa del producto —a la que este repositorio todavía se está acercando pieza a pieza— es un pipeline end-to-end que:

1. 🚧 **Extrae** pronósticos automáticamente desde canales de origen (empezando por **Telegram**, con Instagram como integración futura pausada). *Hoy: el listener de Telegram ya escucha el canal y recibe los mensajes en tiempo real (✅), pero aún no extrae nada de ellos — ver punto 2.*
2. 🚧 **Normaliza y procesa** ese texto libre y desestructurado mediante **modelos de lenguaje (LLMs)**, convirtiéndolo en datos estructurados (evento, mercado, cuota, stake, casa de apuestas...). *Sin implementar todavía: el procesador de mensajes es hoy un stub que solo loguea el texto recibido, no lo interpreta.*
3. 🚧 **Contrasta la efectividad real** de cada pronóstico frente a las **cuotas de mercado** obtenidas de APIs de casas de apuestas, calculando métricas objetivas de rentabilidad (ROI, yield, % de acierto, tendencia) por tipster y por casa. *Sin implementar todavía: no hay integración con ninguna API de cuotas externa.*
4. ✅ **Expone todo esto** en una app móvil (Android/iOS vía Expo) para que el usuario audite de un vistazo en quién merece la pena confiar su dinero. *Ya implementado para los datos introducidos manualmente (ver tabla de abajo); en cuanto los puntos 2 y 3 generen datos automáticamente, se mostrarán con la misma app sin cambios de fondo.*

> **En resumen:** hoy ControlPick funciona como una app de registro y auditoría **manual** de picks (tú introduces la apuesta, la app calcula rentabilidad/ranking/yield). Los pasos 1-3 de arriba son la hoja de ruta para automatizar por completo esa entrada de datos. La tabla de la siguiente sección es la fuente de verdad de qué está implementado y qué no.

Este repositorio es el resultado de migrar un PoC inicial (Node.js + Express + MongoDB + bot de Telegram acoplado) a una arquitectura moderna, tipada y desacoplada en Python/PostgreSQL + TypeScript/Expo. El detalle fase a fase de esa migración vive en [`ROADMAP_MIGRACION.md`](./ROADMAP_MIGRACION.md).

## 2. Estado del proyecto

### ✅ Implementado

| Módulo | Descripción |
| --- | --- |
| **Backend core** | API REST en FastAPI sobre PostgreSQL 16, con modelos `users`, `informantes` y `picks` gestionados vía SQLModel + Alembic. |
| **Autenticación** | Registro/login con JWT (`python-jose` + `passlib`/`bcrypt`). |
| **Gestión de picks** | CRUD de pronósticos, cálculo de ganancia/yield/% de acierto por informante (`app/services/pick_service.py`). |
| **Listener de Telegram** | Módulo `app/services/telegram/` basado en **Telethon** (cliente de usuario, no Bot API) que escucha en tiempo real un canal objetivo y loguea cada mensaje entrante, arrancando/parando junto al ciclo de vida de FastAPI. |
| **App móvil** | Cliente Expo (React Native + TypeScript) con capa de API tipada (`src/api/`), auth context, y pantallas de login/registro, listado de picks, detalle por informante, análisis y ganancias. Probada en dispositivo físico Android (Vivo X300 Pro) vía Expo Go. |

### 🚧 Pendiente / en el roadmap

| Módulo | Descripción |
| --- | --- |
| **Pipeline de LLM** | `app/services/telegram/processor.py` es hoy un *stub*: recibe el mensaje del canal pero aún no lo interpreta. La extracción real de pronósticos (evento, cuota, stake...) vía LLM está por implementar. |
| **Comparación con APIs de cuotas** | Contraste automático del pronóstico contra cuotas de mercado en tiempo real (odds APIs) para calcular valor esperado real. |
| **Scrapers de resultados** | Los scrapers legacy (LaLiga/Marca/AS) y el verificador automático de aciertos se retiraron junto con el backend Node.js; su reintroducción en Python está pendiente de decisión. |
| **Integración con Instagram** | Pausada temporalmente. |
| **Despliegue a producción** | Fase 6 del roadmap: contenedorización, hardening, CI/CD. |

Consulta [`ROADMAP_MIGRACION.md`](./ROADMAP_MIGRACION.md) para el detalle fase a fase, notas de arranque y *gotchas* conocidos (p. ej. el throttling de Telegram al pedir códigos de login).

## 3. Arquitectura tecnológica

### Backend — `backend/`

- **Lenguaje/runtime:** Python 3.12
- **Framework API:** [FastAPI](https://fastapi.tiangolo.com/)
- **Base de datos:** PostgreSQL 16
- **ORM / capa de datos:** [SQLModel](https://sqlmodel.tiangolo.com/) (SQLAlchemy + Pydantic) sobre `asyncpg`
- **Migraciones:** Alembic
- **Telegram:** [Telethon](https://docs.telethon.dev/) (cliente de usuario MTProto, permite leer canales sin necesidad de que un bot sea administrador)
- **Auth:** JWT (`python-jose`) + `passlib`/`bcrypt`

### Frontend — `Frontend/PickControl/`

- **Framework:** React Native vía [Expo](https://expo.dev/) (Expo Router para la navegación)
- **Lenguaje:** TypeScript (`strict` activado)
- **Gestión de safe areas:** `react-native-safe-area-context`
- **Probado en:** Expo Go sobre dispositivo Android físico (Vivo X300 Pro)

## 4. Estructura del proyecto

```
ControlPick/
├── ROADMAP_MIGRACION.md      # Hoja de ruta fase a fase de la migración
├── backend/
│   ├── app/
│   │   ├── api/v1/           # Endpoints REST (auth, picks, informantes)
│   │   ├── core/             # Config (.env), seguridad JWT, lifecycle (arranque/parada de Telegram)
│   │   ├── db/               # Conexión a Postgres + migraciones Alembic
│   │   ├── models/           # Modelos SQLModel (User, Pick, Informante)
│   │   ├── schemas/          # Esquemas Pydantic de entrada/salida de la API
│   │   └── services/
│   │       ├── pick_service.py   # Lógica de negocio: ganancia, % aciertos, yield
│   │       └── telegram/         # Listener de Telegram (Telethon): client, handlers, processor (stub)
│   ├── scripts/               # Scripts puntuales de diagnóstico (p. ej. login de Telethon)
│   ├── requirements.txt
│   └── .env.example
└── Frontend/PickControl/
    ├── app/                   # Rutas de Expo Router (pantallas) únicamente
    │   ├── screens/           # login, register, add-pick, analysis, earns
    │   └── dynamic-routes/    # detalle por informante
    ├── src/
    │   ├── api/               # Cliente HTTP tipado hacia el backend
    │   ├── components/        # Componentes de UI reutilizables (TopBar, BottomBar...)
    │   ├── context/           # AuthContext
    │   └── types/             # Tipos TypeScript compartidos
    └── .env.example
```

## 5. Guía de arranque rápido

### Requisitos previos

- Python 3.12+
- Node.js LTS y `npm`
- PostgreSQL 16 corriendo localmente (o accesible por red)
- Una cuenta de Telegram + credenciales de API (`api_id`/`api_hash`) de [my.telegram.org](https://my.telegram.org) si quieres probar el listener de Telegram

### 5.1. Backend

```powershell
cd backend

# Entorno virtual y dependencias
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt

# Configura tus variables de entorno
copy .env.example .env
# Edita .env: DATABASE_URL, JWT_SECRET, y (opcional) TELEGRAM_API_ID/API_HASH/PHONE/TARGET_CHANNEL

# Aplica el esquema a la base de datos
.\.venv\Scripts\python.exe -m alembic upgrade head

# Arranca el servidor
.\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app
```

La API queda disponible en `http://localhost:8000/api/v1`, con documentación interactiva autogenerada en `http://localhost:8000/docs`.

> **Nota:** `--reload-dir app` es importante — sin él, `--reload` vigila toda la carpeta `backend/` (incluido `.venv/`), lo que puede reiniciar el servidor en mitad del login interactivo de Telegram. Ver detalles en el roadmap.

### 5.2. Frontend

```powershell
cd Frontend/PickControl

npm install

# Configura tus variables de entorno
copy .env.example .env
# Edita .env: EXPO_PUBLIC_API_BASE_URL apuntando a tu backend (IP de red local, no localhost, si pruebas en móvil físico)

npx expo start
```

Escanea el QR con la app **Expo Go** desde tu dispositivo Android/iOS (misma red local que el backend), o pulsa `a` para abrir un emulador Android / `w` para la versión web.

## 6. Documentación relacionada

- [`ROADMAP_MIGRACION.md`](./ROADMAP_MIGRACION.md) — estado detallado fase a fase de la migración, comandos de referencia y notas de troubleshooting (incluye el throttling de Telegram al solicitar códigos de login repetidamente).
