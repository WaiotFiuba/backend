#!/usr/bin/env bash
# Instalado en la VM como /usr/local/bin/waiotfiuba-deploy (comando forzado de la
# clave SSH del usuario deploy). Trae origin/main y corre el deploy versionado.
set -euo pipefail
cd /data/app/backend
git fetch --prune origin main
git checkout -q -f -B main origin/main
exec ./deploy/waiotfiuba-deploy.sh
