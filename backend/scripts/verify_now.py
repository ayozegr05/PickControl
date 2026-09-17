# ruff: noqa: E402
"""Ejecuta una pasada de verificación de resultados bajo demanda.

Útil tras cambiar la lógica del verificador o de los proveedores, sin
esperar al ciclo automático del backend. Solo toca picks pendientes
(`acierto IS NULL`, `anulada=False`) con `fecha_evento` ya pasada y
dentro de la ventana de verificación (14 días).

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_now.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.results.verifier import verify_pending_picks


async def main() -> None:
    resolved = await verify_pending_picks()
    print(f"Picks resueltos en esta pasada: {resolved}")


if __name__ == "__main__":
    asyncio.run(main())
