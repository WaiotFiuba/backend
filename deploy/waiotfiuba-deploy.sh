#!/usr/bin/env bash
# Pasos del deploy en la VM de Oracle. Lo ejecuta deploy/bootstrap.sh con el
# código ya en origin/main.
set -euo pipefail
cd /data/app/backend
git log --oneline -1

echo "== datos semilla"
python3 scripts/download_seed_data.py

echo "== contenedores"
docker compose up -d --build --remove-orphans
docker image prune -f > /dev/null
docker compose ps
