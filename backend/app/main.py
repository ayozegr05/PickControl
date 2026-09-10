"""Punto de entrada de la aplicación FastAPI.

Equivalente a `backend/app.js` + `backend/index.js` del backend Node.

El esquema de la base de datos ya NO se crea automáticamente al
arrancar (a diferencia del PoC inicial). Antes de levantar el
servidor hay que aplicar las migraciones de Alembic:

    alembic upgrade head

Ver `app/db/migrations/` y `app/db/postgres.py` (`init_db` se conserva
solo para los tests, que usan una base de datos SQLite efímera).
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.lifecycle import lifespan


def _parse_cors_origins(origins: str) -> list[str]:
    """Convierte una lista separada por comas en una lista de strings.

    El valor "*" (como string único) se deja tal cual para CORS.
    """
    stripped = [o.strip() for o in origins.split(",") if o.strip()]
    return stripped if stripped else ["*"]


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger("app.main")

app = FastAPI(
    title="Tipster Auditor API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Errores de validación de Pydantic (422) con el mismo formato que FastAPI.

    Se loguean para depuración sin exponer detalles extraños al cliente.
    """
    logger.warning("Validation error on %s: %s", request.url, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException se loguea y se devuelve con el `detail` original."""
    logger.warning("HTTP %s on %s: %s", exc.status_code, request.url, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Cualquier error inesperado: se loguea y se responde con un mensaje genérico.

    Importante: NO devolvemos la traza ni el mensaje original para evitar
    filtrar información interna del servidor en producción.
    """
    logger.exception("Unhandled error on %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


settings = get_settings()

# CORS configurable por entorno. Ver la variable CORS_ORIGINS en .env.
# En desarrollo suele ser "*"; en producción, lista de dominios autorizados.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    """Equivalente a `app.get("/", ...)` en app.js."""
    return {"message": "Servidor funcionando"}
