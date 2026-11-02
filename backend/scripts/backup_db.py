"""Backup diario de PostgreSQL con pg_dump.

Uso (desde backend/):

    .\\.venv\\Scripts\\python.exe scripts/backup_db.py

Genera `backups/controlpick_YYYYMMDD_HHMMSS.dump` (formato custom
comprimido de pg_dump, restaurable con `pg_restore`) y borra los dumps
más antiguos que `BACKUP_RETENTION_DAYS` (por defecto 14).

Para programarlo diario en Windows (Task Scheduler, como el usuario
actual, una vez al día a las 04:00):

    schtasks /create /tn "ControlPick Backup" /sc DAILY /st 04:00 /tr ^
        "C:\\ruta\\backend\\.venv\\Scripts\\python.exe C:\\ruta\\backend\\scripts\\backup_db.py"

En Linux/cron equivalente:

    0 4 * * * cd /ruta/backend && .venv/bin/python scripts/backup_db.py
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from glob import glob
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BACKEND_DIR / "backups"
RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "14"))

sys.path.insert(0, str(BACKEND_DIR))


def _find_pg_dump() -> str:
    """Localiza pg_dump: PATH primero, luego instalaciones típicas."""
    found = shutil.which("pg_dump")
    if found:
        return found
    for pattern in (
        r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe",
        r"C:\Program Files (x86)\PostgreSQL\*\bin\pg_dump.exe",
    ):
        matches = sorted(glob(pattern))
        if matches:
            return matches[-1]  # la versión más alta
    raise RuntimeError(
        "No se encontró pg_dump. Instala PostgreSQL o añade su bin/ al PATH."
    )


def _database_url() -> str:
    """DATABASE_URL del entorno, con el driver asyncpg quitado:
    pg_dump no entiende `postgresql+asyncpg://`."""
    from app.core.config import get_settings

    url = get_settings().database_url
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _prune_old_dumps() -> list[Path]:
    """Borra dumps más viejos que RETENTION_DAYS. Devuelve los borrados."""
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = []
    for dump in BACKUP_DIR.glob("controlpick_*.dump"):
        try:
            # El timestamp va en el propio nombre: controlpick_YYYYMMDD_HHMMSS
            stamp = dump.stem.removeprefix("controlpick_")
            created = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        if created < cutoff:
            dump.unlink()
            removed.append(dump)
    return removed


def main() -> int:
    BACKUP_DIR.mkdir(exist_ok=True)
    pg_dump = _find_pg_dump()
    url = _database_url()

    dest = BACKUP_DIR / f"controlpick_{datetime.now():%Y%m%d_%H%M%S}.dump"
    result = subprocess.run(
        [pg_dump, "-Fc", "-f", str(dest), url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        dest.unlink(missing_ok=True)
        print(f"ERROR pg_dump: {result.stderr.strip()}")
        return 1

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"Backup creado: {dest} ({size_mb:.1f} MB)")

    removed = _prune_old_dumps()
    for old in removed:
        print(f"Eliminado dump antiguo: {old.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
