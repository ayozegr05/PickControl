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

# Copia offsite a Oracle Object Storage: si la VM muere, los dumps
# mueren con ella — la copia en el bucket sobrevive. La URL PAR vive
# en ~/controlpick/.env (BACKUP_PAR_URL=...), fuera de git: quien la
# tenga puede escribir/leer el bucket.
# shellcheck source=/dev/null
if [ -f "${HOME}/controlpick/.env" ]; then
  set -a; source "${HOME}/controlpick/.env"; set +a
fi
if [ -n "${BACKUP_PAR_URL:-}" ]; then
  if curl -fsS -X PUT --upload-file "${OUT}" \
      "${BACKUP_PAR_URL}$(basename "${OUT}")"; then
    echo "[backup] copiado a Object Storage: $(basename "${OUT}")"
  else
    echo "[backup] ERROR: la copia a Object Storage fallo" >&2
  fi
fi

# Restaurar:  docker exec -i controlpick-db pg_restore -U postgres \
#             -d controlpick --clean < backups/controlpick-XXX.dump
# Desde el bucket: curl -fsS "<BACKUP_PAR_URL>controlpick-XXX.dump" \
#             > controlpick-XXX.dump  (y el pg_restore igual)
