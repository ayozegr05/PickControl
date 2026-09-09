"""Punto de entrada de la aplicación FastAPI.

Equivalente a `backend/app.js` + `backend/index.js` del backend Node.

El esquema de la base de datos ya NO se crea automáticamente al
arrancar (a diferencia del PoC inicial). Antes de levantar el
servidor hay que aplicar las migraciones de Alembic:

    alembic upgrade head

Ver `app/db/migrations/` y `app/db/postgres.py` (`init_db` se conserva
solo para los tests, que usan una base de datos SQLite efímera).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router

app = FastAPI(
    title="Tipster Auditor API",
    version="0.1.0",
)

# CORS abierto para desarrollo, igual que `app.use(cors())` en app.js.
# En producción, restringir `allow_origins` al dominio del frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    """Equivalente a `app.get("/", ...)` en app.js."""
    return {"message": "Servidor funcionando"}
