# ruff: noqa: E402
"""Reprocesa mensajes crudos ya guardados con el extractor híbrido.

La lógica vive en `app/services/maintenance/rescue.py` — el loop de
`lifecycle.py` corre la misma función periódicamente; este script es
el wrapper manual.

Solo se consideran raws dentro de `rescue_max_age_days` (30 días por
defecto); `--max-age-days 0` desactiva el límite. Los que el extractor
resuelve como "no es pick" y los que superan la ventana se cierran con
`processed=True` — la fila queda en BD pero no se vuelve a intentar.

Uso:
    .venv\\Scripts\\python.exe scripts\\reprocess_raw.py
    .venv\\Scripts\\python.exe scripts\\reprocess_raw.py --max-age-days 0

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
    max_age = None
    if "--max-age-days" in sys.argv:
        max_age = float(sys.argv[sys.argv.index("--max-age-days") + 1])

    resumen = await reprocess_pending_raws(max_age_days=max_age)
    print(f"Mensajes por reprocesar: {resumen['pendientes']}")
    print(
        f"Reproceso completado: {resumen['procesados']} procesados, "
        f"{resumen['picks']} picks creados, {resumen['duplicados']} duplicados, "
        f"{resumen['sin_pick']} resueltos sin pick, "
        f"{resumen['antiguos']} antiguos descartados."
    )


if __name__ == "__main__":
    asyncio.run(main())
