# ruff: noqa: E402
"""CLI del backfill de cuotas históricas vía OddsPapi (bet36528).

La lógica vive en `app/services/odds/historical_backfill.py` — el job
diario de `lifecycle.py` llama a la misma función `run()`.

    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py --apply    # escribe
    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py --limit 20 # prueba acotada
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.odds.historical_backfill import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="escribe en BD")
    parser.add_argument("--limit", type=int, default=None, help="acota nº de picks")
    args = parser.parse_args()

    report = asyncio.run(run(apply=args.apply, limit=args.limit))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode} backfill cuotas históricas ===")
    print(f"picks revisados: {report.picks}")
    print(f"con fixture:     {report.con_fixture}")
    print(f"sin fixture:     {report.sin_fixture}")
    print(f"sin historial:   {report.sin_historial}")
    print(f"mapeados:        {report.mapeados}")
    print(f"sin opción:      {report.sin_opcion}")
    print(f"filas {'insertadas' if args.apply else 'a insertar'}: {report.filas}")
    for line in report.detalles[:80]:
        print(line)
    if len(report.detalles) > 80:
        print(f"  ... y {len(report.detalles) - 80} líneas más")
    if not args.apply:
        print("\n(dry-run: nada escrito — repite con --apply para persistir)")


if __name__ == "__main__":
    main()
