#!/usr/bin/env bash
# Barrido diario de backlog de ControlPick en la VM Oracle.
# Verifica picks pendientes FUERA de la ventana de 14 días que el
# verificador automático (cada 3h) ya no reintenta. Corre DENTRO del
# contenedor backend (mismo código y misma BD que el ciclo normal).
#
# Autolimitado: los providers sin cuota diaria se saltan solos
# (provider_state.json) y los "missed" definitivos quedan cacheados
# 15 días — cuando el backlog resoluble se agote, el coste es ~0 y la
# tarea converge a un no-op. Se programa a las 00:30 UTC para pillar
# las cuotas recién renovadas, antes de las pasadas del verifier.
#
# Instalar en la VM:
#   chmod +x verify_backlog_cron.sh
#   crontab -e  ->  30 0 * * * /home/ubuntu/controlpick/deploy/verify_backlog_cron.sh >> /home/ubuntu/controlpick/verify_backlog.log 2>&1
set -euo pipefail

echo "[verify_backlog] $(date -u '+%F %T UTC') inicio"
docker exec controlpick-backend python /app/scripts/verify_backlog.py --apply
echo "[verify_backlog] $(date -u '+%F %T UTC') fin"
