#!/usr/bin/env bash
# Backup diario de la BD ControlPick en la VM Oracle.
# pg_dump corre DENTRO del contenedor (el host no necesita Postgres).
#
# Instalar en la VM:
#   mkdir -p ~/controlpick/backups
#   chmod +x backup_cron.sh
#   crontab -e  ->  0 4 * * * /home/ubuntu/controlpick/backup_cron.sh
set -euo pipefail

BACKUP_DIR="${HOME}/controlpick/backups"
KEEP_DAYS=14
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/controlpick-${STAMP}.dump"

mkdir -p "${BACKUP_DIR}"
docker exec controlpick-db pg_dump -Fc -U postgres controlpick > "${OUT}"
find "${BACKUP_DIR}" -name 'controlpick-*.dump' -mtime "+${KEEP_DAYS}" -delete
echo "[backup] ${OUT} ($(du -h "${OUT}" | cut -f1))"

# Restaurar:  docker exec -i controlpick-db pg_restore -U postgres \
#             -d controlpick --clean < backups/controlpick-XXX.dump
