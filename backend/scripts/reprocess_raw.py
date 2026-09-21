# ruff: noqa: E402
"""Reprocesa mensajes crudos ya guardados con el extractor híbrido.

La lógica vive en `app/services/maintenance/rescue.py` — el loop de
`lifecycle.py` corre la misma función periódicamente; este script es
el wrapper manual.

Uso:
    .venv\\Scripts\\python.exe scripts\\reprocess_raw.py

Crea un ParsedPick para cada mensaje `processed=False` que lo permita.
Si ya existe un ParsedPick para ese raw_message_id, lo salta.
"""

import asyncio
import os
import sys

# Permite importar `app` cuando se ejecuta desde scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.maintenance.rescue import reprocess_pending_raws


async def main() -> None:
    resumen = await reprocess_pending_raws()
    print(f"Mensajes por reprocesar: {resumen['pendientes']}")
    print(
        f"Reproceso completado: {resumen['procesados']} procesados, "
        f"{resumen['picks']} picks creados, {resumen['duplicados']} duplicados."
    )


if __name__ == "__main__":
    asyncio.run(main())
