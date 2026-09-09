"""Router agregador de la API v1.

Los routers concretos (auth.py, picks.py, informantes.py) se añadirán en
la Fase 2 (migración de endpoints). Este archivo solo deja preparado el
punto de entrada para que `main.py` pueda importar `api_router`.
"""
from fastapi import APIRouter

api_router = APIRouter()
