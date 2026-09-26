# ruff: noqa: E402
"""Auditoría de picks basura / slips republicados (DRY-RUN por defecto).

Recorre los picks abiertos y propone, SIN escribir nada:

- `flag`  -> es_apuesta=False (slip republicado post-partido, evento
  vacío, marketing colado como selección). Nunca borra filas.
- `fix_date` -> corrige `fecha_evento` con la fecha impresa en el slip
  cuando el mensaje se publicó antes del partido.
- `review` -> mismo día pero tras el posible inicio (¿live-bet?): solo
  se lista, nunca se aplica.

Uso:
    .venv\\Scripts\\python.exe scripts\\reextract_garbage.py
    .venv\\Scripts\\python.exe scripts\\reextract_garbage.py --apply
    .venv\\Scripts\\python.exe scripts\\reextract_garbage.py --fix-incomplete

`--apply` escribe SOLO las acciones flag/fix_date del informe.
`--fix-incomplete` reintenta con el LLM los picks `rule` sin `evento`
(la misma función que corre el ciclo de rescate cada 6h).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.services.maintenance.garbage import analyze_garbage, apply_garbage
from app.services.maintenance.rescue import retry_incomplete_picks


async def main() -> None:
    apply = "--apply" in sys.argv

    if "--fix-incomplete" in sys.argv:
        # Rescate de picks 'rule' sin evento: SÍ escribe (no es dry-run).
        resumen = await retry_incomplete_picks()
        print(
            f"Picks incompletos: {resumen['candidatos']} candidatos -> "
            f"{resumen['rellenados']} rellenados, "
            f"{resumen['descartados']} descartados (no apuesta), "
            f"{resumen['fallos']} fallos de LLM (reintento luego)."
        )
        return

    report = await analyze_garbage()

    flags = report.by_action("flag")
    fixes = report.by_action("fix_date")
    reviews = report.by_action("review")

    print(f"Picks a descartar (es_apuesta=False): {len(flags)}")
    for a in flags:
        print(f"  #{a.pick_id} [{a.reason}] {a.detail}")
    print(f"\nFechas corregibles desde el slip: {len(fixes)}")
    for a in fixes:
        print(f"  #{a.pick_id} {a.detail}")
    print(f"\nPara revisión manual (no se aplican): {len(reviews)}")
    for a in reviews:
        print(f"  #{a.pick_id} [{a.reason}] {a.detail}")

    # Contexto de los picks marcados para revisar el informe.
    ids = [a.pick_id for a in report.actions]
    if ids:
        async with AsyncSessionLocal() as session:
            picks = (
                (await session.exec(select(ParsedPick).where(ParsedPick.id.in_(ids))))
                .scalars()
                .all()
            )
            print("\nDetalle de picks afectados:")
            for p in sorted(picks, key=lambda x: x.id or 0):
                print(
                    f"  #{p.id} {p.evento!r} | {p.seleccion!r} | "
                    f"fecha_evento={p.fecha_evento} | combinada={p.es_combinada}"
                )

    if not apply:
        print("\nDRY-RUN: nada escrito. Revisa la lista y relanza con --apply.")
        return
    applied = await apply_garbage(report)
    print(
        f"\nAPLICADO: {applied['flagged']} descartados, "
        f"{applied['fixed_dates']} fechas corregidas."
    )


if __name__ == "__main__":
    asyncio.run(main())
