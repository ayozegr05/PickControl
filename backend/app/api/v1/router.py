"""Router agregador de la API v1."""

from fastapi import APIRouter

from app.api.v1 import auth, informantes, picks

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(picks.router)
api_router.include_router(informantes.router)
