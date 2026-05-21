#!/bin/bash
set -e

echo "================================================"
echo "Inicializando datos de contenedores"
echo "================================================"

echo ""
echo "Cargando contenedores desde JSON..."
python3 /docker-entrypoint-initdb.d/load_contenedores.py

echo ""
echo "================================================"
echo "✓ Inicialización completada"
echo "================================================"
