#!/usr/bin/env python3
"""
Inspector Visual de Radios Censales y Contenedores para WaiotFiuba.
Dibuja los polígonos reales de los radios censales de CABA y el 100% de los
contenedores correspondientes para verificar visualmente que cada contenedor
está correctamente conectado a sus radios censales (relación muchos a muchos).
Incluye rankings de comunas, hotspots de sobredemanda y métricas demográficas avanzadas.
"""

from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path
import sys
import time
from shapely import wkt
from shapely.geometry import mapping
from app.digital_twin.synthetic_data.density_processor import get_density_processor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WaiotFiuba - Inspector de Radios Censales y Métricas de Demanda</title>

    <!-- Leaflet CSS & JS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>

    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">

    <style>
        :root {
            --bg-dark: #080c14;
            --surface-dark: rgba(15, 23, 42, 0.94);
            --border-dark: rgba(255, 255, 255, 0.12);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.35);
            --highlight: #f59e0b;
            --color-critical: #ef4444;
            --color-high: #f97316;
            --color-medium: #eab308;
            --color-normal: #06b6d4;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            overflow: hidden;
            display: flex;
            height: 100vh;
            width: 100vw;
        }

        #map {
            flex: 1;
            height: 100%;
            background: #090d16;
        }

        .sidebar {
            width: 480px;
            background: var(--surface-dark);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-left: 1px solid var(--border-dark);
            display: flex;
            flex-direction: column;
            z-index: 1000;
            box-shadow: -10px 0 40px rgba(0, 0, 0, 0.6);
            overflow: hidden;
        }

        .header {
            padding: 20px 24px 16px 24px;
            border-bottom: 1px solid var(--border-dark);
            background: linear-gradient(180deg, rgba(56, 189, 248, 0.1) 0%, transparent 100%);
            flex-shrink: 0;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 9999px;
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: #38bdf8;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }

        .badge::before {
            content: '';
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #38bdf8;
            box-shadow: 0 0 8px #38bdf8;
        }

        h1 {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 2px;
        }

        .subtitle {
            font-size: 12px;
            color: var(--text-muted);
            line-height: 1.4;
        }

        /* Tabs Navigation */
        .tabs-nav {
            display: flex;
            border-bottom: 1px solid var(--border-dark);
            background: rgba(0, 0, 0, 0.25);
            flex-shrink: 0;
        }

        .tab-btn {
            flex: 1;
            padding: 12px 8px;
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: var(--text-muted);
            font-family: inherit;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: color 0.2s, border-color 0.2s, background 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }

        .tab-btn:hover {
            color: #fff;
            background: rgba(255, 255, 255, 0.03);
        }

        .tab-btn.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            background: rgba(56, 189, 248, 0.05);
        }

        .tab-content {
            flex: 1;
            overflow-y: auto;
            display: none;
        }

        .tab-content.active {
            display: block;
        }

        /* Forms & Selectors */
        .selector-box {
            padding: 16px 24px;
            border-bottom: 1px solid var(--border-dark);
            background: rgba(0, 0, 0, 0.15);
        }

        label {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            display: block;
            margin-bottom: 6px;
        }

        select {
            width: 100%;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-dark);
            color: #fff;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            font-family: inherit;
            outline: none;
            cursor: pointer;
            transition: border-color 0.2s;
        }

        select:focus {
            border-color: var(--accent);
        }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            padding: 16px 24px;
            border-bottom: 1px solid var(--border-dark);
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-dark);
            border-radius: 10px;
            padding: 10px 12px;
        }

        .stat-label {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 3px;
        }

        .stat-val {
            font-size: 17px;
            font-weight: 700;
            color: #fff;
            font-family: 'JetBrains Mono', monospace;
        }

        .stat-sub {
            font-size: 11px;
            color: #10b981;
            margin-top: 2px;
        }

        .panel-body {
            padding: 20px 24px;
        }

        /* Card Active Radio */
        .card-active-radio {
            background: rgba(56, 189, 248, 0.06);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
        }

        .card-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .radio-details {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-top: 10px;
        }

        .detail-item {
            background: rgba(0, 0, 0, 0.3);
            padding: 8px 10px;
            border-radius: 6px;
        }

        .detail-label {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        .detail-val {
            font-size: 13px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
            color: #fff;
            margin-top: 2px;
        }

        .instruction-box {
            background: rgba(255, 255, 255, 0.02);
            border: 1px dashed var(--border-dark);
            border-radius: 10px;
            padding: 14px;
            font-size: 12px;
            color: var(--text-muted);
            line-height: 1.5;
            margin-top: 14px;
        }

        .instruction-box strong {
            color: #fff;
        }

        /* Top Comunas Table */
        .comunas-table {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .comuna-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-dark);
            cursor: pointer;
            transition: background 0.15s, border-color 0.15s, transform 0.15s;
        }

        .comuna-row:hover {
            background: rgba(56, 189, 248, 0.08);
            border-color: rgba(56, 189, 248, 0.3);
            transform: translateX(2px);
        }

        .comuna-rank {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 700;
            color: var(--text-muted);
            width: 24px;
        }

        .comuna-info {
            flex: 1;
            margin-left: 8px;
        }

        .comuna-name {
            font-size: 13px;
            font-weight: 600;
            color: #fff;
        }

        .comuna-sub {
            font-size: 11px;
            color: var(--text-muted);
        }

        .comuna-stat {
            text-align: right;
        }

        .comuna-rate {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            font-weight: 700;
        }

        .bar-container {
            width: 90px;
            height: 5px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 4px;
        }

        .bar-fill {
            height: 100%;
            border-radius: 3px;
        }

        /* Hotspot Cards */
        .hotspot-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-dark);
            border-radius: 10px;
            padding: 12px 14px;
            margin-bottom: 10px;
            transition: border-color 0.2s, background 0.2s;
        }

        .hotspot-card:hover {
            border-color: rgba(245, 158, 11, 0.4);
            background: rgba(245, 158, 11, 0.05);
        }

        .hotspot-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 6px;
        }

        .hotspot-code {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 700;
            color: #fff;
        }

        .hotspot-btn {
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: #38bdf8;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
        }

        .hotspot-btn:hover {
            background: rgba(56, 189, 248, 0.3);
        }

        .hotspot-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 6px;
            font-size: 11px;
            margin-top: 8px;
        }

        .hs-stat {
            background: rgba(0, 0, 0, 0.25);
            padding: 6px;
            border-radius: 6px;
            text-align: center;
        }

        .hs-label {
            font-size: 9px;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        .hs-val {
            font-size: 12px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
            margin-top: 2px;
        }

        /* Leaflet Popups */
        .leaflet-popup-content-wrapper {
            background: rgba(15, 23, 42, 0.95) !important;
            backdrop-filter: blur(12px) !important;
            color: #fff !important;
            border: 1px solid var(--border-dark) !important;
            border-radius: 10px !important;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.6) !important;
            font-family: 'Outfit', sans-serif !important;
        }

        .leaflet-popup-tip {
            background: rgba(15, 23, 42, 0.95) !important;
        }

        .popup-header {
            font-size: 13px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 2px;
        }

        .popup-sub {
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 10px;
            padding-bottom: 6px;
            border-bottom: 1px solid var(--border-dark);
        }
    </style>
</head>
<body>

    <div id="map"></div>

    <div class="sidebar">
        <div class="header">
            <div class="badge">Auditoría Geoespacial</div>
            <h1>Inspector de Radios y Métricas</h1>
            <p class="subtitle">Validación de conexiones radio↔contenedor (muchos a muchos) y ranking demográfico per cápita en CABA.</p>
        </div>

        <!-- Navigation Tabs -->
        <div class="tabs-nav">
            <button class="tab-btn active" onclick="switchTab('tab-inspector')">
                <span>📍</span> Inspección
            </button>
            <button class="tab-btn" onclick="switchTab('tab-ranking')">
                <span>🏆</span> Top Comunas
            </button>
            <button class="tab-btn" onclick="switchTab('tab-hotspots')">
                <span>🔥</span> Hotspots
            </button>
            <button class="tab-btn" onclick="switchTab('tab-distribucion')">
                <span>📊</span> Distribución
            </button>
        </div>

        <!-- TAB 1: INSPECTOR DE COMUNA -->
        <div id="tab-inspector" class="tab-content active">
            <div class="selector-box">
                <label for="comuna-select">Seleccionar Comuna para Inspeccionar</label>
                <select id="comuna-select"></select>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Contenedores en Comuna</div>
                    <div class="stat-val" id="stat-containers-comuna">--</div>
                    <div class="stat-sub" id="stat-containers-total">100% de la comuna</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Radios Censales</div>
                    <div class="stat-val" id="stat-radios-count">--</div>
                    <div class="stat-sub">Polígonos trazados</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Población & Basura</div>
                    <div class="stat-val" id="stat-poblacion-comuna" style="font-size: 14px;">--</div>
                    <div class="stat-sub" id="stat-basura-comuna">-- tn/día</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Tasa Media Comuna</div>
                    <div class="stat-val" id="stat-comuna-rate" style="color: #38bdf8;">--</div>
                    <div class="stat-sub" id="stat-comuna-fill">-- %/día</div>
                </div>
            </div>

            <div class="panel-body">
                <div id="active-radio-box" class="card-active-radio" style="display: none;">
                    <div class="card-title">
                        <span>Radio Censal Seleccionado</span>
                        <span id="ar-code" style="font-family: 'JetBrains Mono'; color: #fff;"></span>
                    </div>
                    <div style="font-size: 12px; color: var(--text-muted);" id="ar-desc"></div>
                    <div class="radio-details">
                        <div class="detail-item">
                            <div class="detail-label">Población Censo</div>
                            <div class="detail-val" id="ar-pop">--</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Contenedores Conectados</div>
                            <div class="detail-val" id="ar-count" style="color: var(--highlight);">--</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Basura Generada</div>
                            <div class="detail-val" id="ar-kg">--</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Tasa de Llenado</div>
                            <div class="detail-val" id="ar-rate" style="color: #38bdf8;">--</div>
                        </div>
                    </div>
                </div>

                <div class="instruction-box">
                    <strong>¿Cómo verificar la asignación?</strong>
                    <ul style="margin-top: 8px; margin-left: 18px;">
                        <li>Hacé <strong>click en cualquier polígono</strong> (área censal delimitada) para ver sus fronteras e iluminar en color amarillo todos los contenedores conectados a ese radio (incluye los que están del otro lado de la calle, dentro del buffer).</li>
                        <li>Hacé <strong>click en cualquier contenedor</strong> (círculo) para abrir su popup: si está conectado a más de un radio censal, se listan todos.</li>
                        <li>Hacé zoom en cualquier cuadra: verás <strong>todos los contenedores reales</strong> instalados en la vereda.</li>
                    </ul>
                </div>
            </div>
        </div>

        <!-- TAB 2: RANKING TOP COMUNAS -->
        <div id="tab-ranking" class="tab-content">
            <div class="panel-body">
                <div style="margin-bottom: 16px;">
                    <label>Ranking de las 15 Comunas (por Tasa de Llenado)</label>
                    <p style="font-size: 11px; color: var(--text-muted);">Hacé click en cualquier comuna para cargarla directamente en el mapa.</p>
                </div>
                <div class="comunas-table" id="comunas-ranking-list"></div>
            </div>
        </div>

        <!-- TAB 3: HOTSPOTS CABA -->
        <div id="tab-hotspots" class="tab-content">
            <div class="panel-body">
                <div style="margin-bottom: 16px;">
                    <label style="color: var(--color-critical);">🔥 Top 5 Radios con Mayor Sobredemanda</label>
                    <p style="font-size: 11px; color: var(--text-muted);">Radios densos con altísima población por contenedor conectado (candidatos a recibir más contenedores en optimización).</p>
                </div>
                <div id="hotspots-high-list"></div>

                <div style="margin-top: 24px; margin-bottom: 16px;">
                    <label style="color: var(--color-normal);">🟢 Top 5 Radios con Menor Demanda (Capacidad Ociosa)</label>
                    <p style="font-size: 11px; color: var(--text-muted);">Zonas con baja densidad donde opera el piso mínimo del 40% (potenciales donantes de contenedores).</p>
                </div>
                <div id="hotspots-low-list"></div>
            </div>
        </div>

        <!-- TAB 4: DISTRIBUCIÓN GRANULAR -->
        <div id="tab-distribucion" class="tab-content">
            <div class="panel-body">
                <div style="margin-bottom: 16px;">
                    <label>Distribución de Tasa Horaria (%/h) — Todos los Contenedores</label>
                    <p style="font-size: 11px; color: var(--text-muted);">Percentiles y forma de la distribución sobre el total de contenedores reales (no promediado por comuna), más el detalle de los radios censales con menor cobertura de contenedores — la causa real de los valores extremos.</p>
                </div>

                <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr);">
                    <div class="stat-card">
                        <div class="stat-label">Mediana (p50)</div>
                        <div class="stat-val" id="dist-p50">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">p90</div>
                        <div class="stat-val" id="dist-p90">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">p99</div>
                        <div class="stat-val" id="dist-p99">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Media</div>
                        <div class="stat-val" id="dist-mean" style="font-size: 14px;">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Máximo</div>
                        <div class="stat-val" id="dist-max" style="font-size: 14px; color: var(--color-critical);">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Contenedores</div>
                        <div class="stat-val" id="dist-n" style="font-size: 14px;">--</div>
                    </div>
                </div>

                <div style="margin-top: 20px; margin-bottom: 10px;">
                    <label>Histograma (%/h)</label>
                </div>
                <div id="histogram-list" class="comunas-table"></div>

                <div style="margin-top: 24px; margin-bottom: 16px;">
                    <label style="color: var(--color-critical);">⚠️ Radios con Baja Cobertura (≤3 contenedores conectados)</label>
                    <p style="font-size: 11px; color: var(--text-muted);">Causa raíz de los valores extremos: muy pocos contenedores absorben toda la generación diaria del radio.</p>
                </div>
                <div id="low-coverage-list"></div>
            </div>
        </div>
    </div>

    <script>
        const DATA = __DATA_PAYLOAD__;

        const map = L.map('map', {
            center: [-34.6150, -58.4400],
            zoom: 13,
            zoomControl: false
        });

        L.control.zoom({ position: 'topleft' }).addTo(map);

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap',
            subdomains: 'abcd',
            maxZoom: 20
        }).addTo(map);

        let polygonsLayer = L.geoJSON(null).addTo(map);
        let containersLayer = L.layerGroup().addTo(map);

        let currentActiveRadioCode = null;
        let containerMarkersMap = {}; // {cid: marker}
        let polygonLayersMap = {};   // {radioCode: layer}

        function getColor(hourlyRate) {
            if (hourlyRate > 3.0) return '#ef4444';
            if (hourlyRate >= 2.2) return '#f97316';
            if (hourlyRate >= 1.8) return '#eab308';
            return '#06b6d4';
        }

        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

            const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick').includes(tabId));
            if (activeBtn) activeBtn.classList.add('active');

            const content = document.getElementById(tabId);
            if (content) content.classList.add('active');
        }

        function populateComunaSelect() {
            const select = document.getElementById('comuna-select');
            DATA.comunas_list.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.name;
                opt.textContent = `${c.name} (${c.container_count} contenedores, ${c.avg_hourly.toFixed(2)} %/h)`;
                select.appendChild(opt);
            });

            select.addEventListener('change', (e) => {
                loadComuna(e.target.value);
            });
        }

        function populateRankingTab() {
            const container = document.getElementById('comunas-ranking-list');
            const maxRate = Math.max(...DATA.comunas_list.map(c => c.avg_hourly));

            DATA.comunas_list.forEach((c, idx) => {
                const row = document.createElement('div');
                row.className = 'comuna-row';
                const color = getColor(c.avg_hourly);
                const barPct = Math.round((c.avg_hourly / maxRate) * 100);

                row.innerHTML = `
                    <div class="comuna-rank">#${idx + 1}</div>
                    <div class="comuna-info">
                        <div class="comuna-name">${c.name}</div>
                        <div class="comuna-sub">${c.container_count.toLocaleString()} cont. • ${(c.population_total || 0).toLocaleString()} hab.</div>
                    </div>
                    <div class="comuna-stat">
                        <div class="comuna-rate" style="color: ${color};">${c.avg_hourly.toFixed(3)} %/h</div>
                        <div class="bar-container">
                            <div class="bar-fill" style="width: ${barPct}%; background: ${color};"></div>
                        </div>
                    </div>
                `;

                row.addEventListener('click', () => {
                    document.getElementById('comuna-select').value = c.name;
                    loadComuna(c.name);
                    switchTab('tab-inspector');
                });

                container.appendChild(row);
            });
        }

        function populateHotspotsTab() {
            const highContainer = document.getElementById('hotspots-high-list');
            const lowContainer = document.getElementById('hotspots-low-list');

            DATA.hotspots_high.forEach(h => {
                const card = document.createElement('div');
                card.className = 'hotspot-card';
                card.innerHTML = `
                    <div class="hotspot-header">
                        <div>
                            <span class="hotspot-code">Radio ${h.code}</span>
                            <span style="font-size: 11px; color: var(--text-muted); margin-left: 6px;">${h.comuna}</span>
                        </div>
                        <button class="hotspot-btn" onclick="jumpToHotspot('${h.comuna}', '${h.code}')">Ver en Mapa</button>
                    </div>
                    <div class="hotspot-stats">
                        <div class="hs-stat">
                            <div class="hs-label">Tasa Horaria</div>
                            <div class="hs-val" style="color: var(--color-critical);">${h.rate.toFixed(1)} %/h</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Población</div>
                            <div class="hs-val">${h.pop.toLocaleString()} hab.</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Contenedores</div>
                            <div class="hs-val" style="color: var(--highlight);">${h.count} cont.</div>
                        </div>
                    </div>
                `;
                highContainer.appendChild(card);
            });

            DATA.hotspots_low.forEach(h => {
                const card = document.createElement('div');
                card.className = 'hotspot-card';
                card.innerHTML = `
                    <div class="hotspot-header">
                        <div>
                            <span class="hotspot-code">Radio ${h.code}</span>
                            <span style="font-size: 11px; color: var(--text-muted); margin-left: 6px;">${h.comuna}</span>
                        </div>
                        <button class="hotspot-btn" onclick="jumpToHotspot('${h.comuna}', '${h.code}')">Ver en Mapa</button>
                    </div>
                    <div class="hotspot-stats">
                        <div class="hs-stat">
                            <div class="hs-label">Tasa Horaria</div>
                            <div class="hs-val" style="color: var(--color-normal);">${h.rate.toFixed(2)} %/h</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Población</div>
                            <div class="hs-val">${h.pop.toLocaleString()} hab.</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Contenedores</div>
                            <div class="hs-val">${h.count} cont.</div>
                        </div>
                    </div>
                `;
                lowContainer.appendChild(card);
            });
        }

        function populateDistributionTab() {
            const dist = DATA.distribution;

            document.getElementById('dist-p50').textContent = dist.percentiles.p50.toFixed(2) + ' %/h';
            document.getElementById('dist-p90').textContent = dist.percentiles.p90.toFixed(2) + ' %/h';
            document.getElementById('dist-p99').textContent = dist.percentiles.p99.toFixed(2) + ' %/h';
            document.getElementById('dist-mean').textContent = dist.stats.mean.toFixed(2) + ' %/h';
            document.getElementById('dist-max').textContent = dist.stats.max.toFixed(2) + ' %/h';
            document.getElementById('dist-n').textContent = dist.stats.n.toLocaleString();

            const histContainer = document.getElementById('histogram-list');
            const maxCount = Math.max(...dist.histogram.counts);
            dist.histogram.labels.forEach((label, i) => {
                const count = dist.histogram.counts[i];
                const share = dist.stats.n > 0 ? (100 * count / dist.stats.n) : 0;
                const barPct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;

                const row = document.createElement('div');
                row.className = 'comuna-row';
                row.style.cursor = 'default';
                row.innerHTML = `
                    <div class="comuna-rank" style="width: 56px; font-size: 11px;">${label}</div>
                    <div class="comuna-info">
                        <div class="bar-container" style="width: 100%;">
                            <div class="bar-fill" style="width: ${barPct}%; background: #38bdf8;"></div>
                        </div>
                    </div>
                    <div class="comuna-stat">
                        <div class="comuna-rate" style="font-size: 12px;">${count.toLocaleString()}</div>
                        <div class="comuna-sub">${share.toFixed(1)}%</div>
                    </div>
                `;
                histContainer.appendChild(row);
            });

            const lowContainer = document.getElementById('low-coverage-list');
            dist.low_coverage_radios.forEach(r => {
                const card = document.createElement('div');
                card.className = 'hotspot-card';
                card.innerHTML = `
                    <div class="hotspot-header">
                        <div>
                            <span class="hotspot-code">Radio ${r.code}</span>
                            <span style="font-size: 11px; color: var(--text-muted); margin-left: 6px;">${r.comuna}</span>
                        </div>
                        <button class="hotspot-btn" onclick="jumpToHotspot('${r.comuna}', '${r.code}')">Ver en Mapa</button>
                    </div>
                    <div class="hotspot-stats">
                        <div class="hs-stat">
                            <div class="hs-label">Tasa Horaria</div>
                            <div class="hs-val" style="color: var(--color-critical);">${r.rate.toFixed(2)} %/h</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Población</div>
                            <div class="hs-val">${r.pop.toLocaleString()} hab.</div>
                        </div>
                        <div class="hs-stat">
                            <div class="hs-label">Contenedores</div>
                            <div class="hs-val" style="color: var(--highlight);">${r.count} cont.</div>
                        </div>
                    </div>
                `;
                lowContainer.appendChild(card);
            });
        }

        function jumpToHotspot(comuna, radioCode) {
            document.getElementById('comuna-select').value = comuna;
            loadComuna(comuna);
            switchTab('tab-inspector');
            setTimeout(() => {
                highlightRadio(radioCode, true);
            }, 250);
        }

        function highlightRadio(radioCode, zoomTo = false) {
            currentActiveRadioCode = radioCode;
            const rData = DATA.radios_by_code[radioCode];

            // Resetear estilos de todos los polígonos
            Object.entries(polygonLayersMap).forEach(([code, layer]) => {
                if (code === radioCode) {
                    layer.setStyle({
                        weight: 3,
                        color: '#38bdf8',
                        fillColor: '#38bdf8',
                        fillOpacity: 0.28
                    });
                    if (zoomTo) {
                        map.fitBounds(layer.getBounds(), { padding: [40, 40] });
                    }
                } else {
                    layer.setStyle({
                        weight: 1.2,
                        color: 'rgba(255, 255, 255, 0.22)',
                        fillColor: '#64748b',
                        fillOpacity: 0.08
                    });
                }
            });

            // Resaltar TODOS los contenedores conectados a este radio (relación
            // muchos a muchos: un contenedor puede aparecer conectado a más de
            // un radio, así que se chequea membresía en radio_codes, no igualdad).
            Object.values(containerMarkersMap).forEach(({ marker, data }) => {
                const conectado = Array.isArray(data.radio_codes) && data.radio_codes.includes(radioCode);
                if (conectado) {
                    marker.setStyle({
                        radius: 7,
                        fillColor: '#f59e0b',
                        color: '#ffffff',
                        weight: 2,
                        fillOpacity: 1.0
                    });
                    marker.bringToFront();
                } else {
                    marker.setStyle({
                        radius: 4,
                        fillColor: '#0ea5e9',
                        color: '#ffffff',
                        weight: 0.5,
                        fillOpacity: 0.75
                    });
                }
            });

            // Actualizar tarjeta lateral
            if (rData) {
                document.getElementById('active-radio-box').style.display = 'block';
                document.getElementById('ar-code').textContent = radioCode;
                document.getElementById('ar-desc').textContent = `${rData.department_name} • Superficie: ${rData.area_km2.toFixed(3)} km²`;
                document.getElementById('ar-pop').textContent = `${rData.population.toLocaleString()} hab.`;
                document.getElementById('ar-count').textContent = `${rData.container_count} contenedores`;
                document.getElementById('ar-kg').textContent = `${(rData.population * 1.5).toFixed(0)} kg/día`;
                document.getElementById('ar-rate').textContent = `${rData.avg_hourly_pct.toFixed(3)} %/h`;
            }
        }

        function loadComuna(comunaName) {
            polygonsLayer.clearLayers();
            containersLayer.clearLayers();
            polygonLayersMap = {};
            containerMarkersMap = {};
            document.getElementById('active-radio-box').style.display = 'none';

            const cData = DATA.comunas_data[comunaName];
            if (!cData) return;

            // Actualizar tarjetas de métricas
            document.getElementById('stat-containers-comuna').textContent = cData.containers.length.toLocaleString();
            document.getElementById('stat-radios-count').textContent = cData.features.length.toLocaleString();
            document.getElementById('stat-comuna-rate').textContent = cData.avg_hourly.toFixed(3) + ' %/h';
            document.getElementById('stat-comuna-fill').textContent = `~${(cData.avg_hourly * 24).toFixed(1)} %/día`;

            const popTotal = cData.population_total || 0;
            document.getElementById('stat-poblacion-comuna').textContent = `${popTotal.toLocaleString()} hab.`;
            document.getElementById('stat-basura-comuna').textContent = `~${((popTotal * 1.5) / 1000).toFixed(1)} tn/día`;

            // Dibujar polígonos censales
            polygonsLayer = L.geoJSON(cData.features, {
                style: function (feature) {
                    return {
                        weight: 1.2,
                        color: 'rgba(255, 255, 255, 0.22)',
                        dashArray: '2, 3',
                        fillColor: '#64748b',
                        fillOpacity: 0.08
                    };
                },
                onEachFeature: function (feature, layer) {
                    const code = feature.properties.radio_code;
                    polygonLayersMap[code] = layer;

                    layer.on('click', () => {
                        highlightRadio(code, false);
                    });

                    layer.bindTooltip(`Radio Censal ${code}<br>${feature.properties.population} hab.`, {
                        sticky: true,
                        className: 'radio-tooltip'
                    });
                }
            }).addTo(map);

            // Ajustar vista del mapa a los límites de la comuna
            map.fitBounds(polygonsLayer.getBounds(), { padding: [30, 30] });

            // Dibujar TODOS los contenedores de la comuna
            cData.containers.forEach(c => {
                const color = getColor(c.hourly_fill_pct);
                const marker = L.circleMarker([c.lat, c.lon], {
                    radius: 4,
                    fillColor: color,
                    color: '#ffffff',
                    weight: 0.5,
                    opacity: 0.9,
                    fillOpacity: 0.8
                });

                const radiosConectados = (c.radio_codes && c.radio_codes.length > 0) ? c.radio_codes : [c.radio_code];
                const radiosLabel = radiosConectados.length > 1
                    ? `${radiosConectados.length} radios: ${radiosConectados.join(', ')}`
                    : radiosConectados[0];

                const popupHtml = `
                    <div class="popup-header">Contenedor ${c.id}</div>
                    <div class="popup-sub">${c.address || 'Dirección s/d'}</div>
                    <div style="font-size: 11px; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">${radiosConectados.length > 1 ? 'Radios Censales Conectados' : 'Radio Censal Asignado'}:</span>
                        <strong style="color: #38bdf8; font-family: 'JetBrains Mono';">${radiosLabel}</strong>
                    </div>
                    <div style="font-size: 11px; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">Población del Radio Dominante:</span>
                        <strong>${c.population.toLocaleString()} personas</strong>
                    </div>
                    <div style="font-size: 11px; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">Contenedores que comparten el radio dominante:</span>
                        <strong>${c.containers_in_radio}</strong>
                    </div>
                    <div style="font-size: 11px; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">Basura diaria asignada (suma de todos los radios conectados):</span>
                        <strong>${c.daily_waste_kg.toFixed(1)} kg/día</strong>
                    </div>
                    <div style="font-size: 11px;">
                        <span style="color: var(--text-muted);">Tasa base horaria:</span>
                        <strong style="color: ${color}; font-family: 'JetBrains Mono'; font-size: 12px;">${c.hourly_fill_pct.toFixed(3)} %/h</strong>
                    </div>
                `;

                marker.bindPopup(popupHtml);

                marker.on('click', () => {
                    highlightRadio(c.radio_code, false);
                });

                marker.addTo(containersLayer);
                containerMarkersMap[c.id] = { marker, data: c };
            });
        }

        populateComunaSelect();
        populateRankingTab();
        populateHotspotsTab();
        populateDistributionTab();

        // Cargar por defecto Comuna 6 (Caballito)
        loadComuna("Comuna 6");
        document.getElementById('comuna-select').value = "Comuna 6";
    </script>
</body>
</html>
"""


def main():
    print("=" * 70)
    print(" 🗺️  CONSTRUYENDO INSPECTOR VISUAL DE RADIOS CENSALES Y MÉTRICAS")
    print("=" * 70)

    t0 = time.time()
    processor = get_density_processor()
    print(
        f"✓ Índice espacial y {len(processor.radios)} radios listos en {time.time() - t0:.2f}s"
    )

    # 1. Cargar geometrías GeoJSON de radios agrupados por Comuna
    print("Extrayendo polígonos WKT de radios censales...")
    radios_by_comuna = defaultdict(list)
    radios_by_code = {}
    comuna_population_acc = defaultdict(int)

    csv_path = ROOT / "datos" / "radios_caba_filtrado.csv"
    with open(csv_path, mode="r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            wkt_str = row.get("Geometría en WKT")
            if not wkt_str:
                continue
            try:
                geom = wkt.loads(wkt_str)
                if not geom.is_valid:
                    geom = geom.buffer(0)
            except Exception:
                continue

            r_code = row.get("Código de radio")
            dept = row.get("Nombre de departamento") or "Sin Comuna"
            pop = int(float(row.get("Población total") or "0"))
            area = float(row.get("Superficie en km2") or "0.0")

            comuna_population_acc[dept] += pop

            geo_feat = {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    "radio_code": r_code,
                    "department_name": dept,
                    "population": pop,
                    "area_km2": area,
                },
            }
            radios_by_comuna[dept].append(geo_feat)
            radios_by_code[r_code] = {
                "department_name": dept,
                "population": pop,
                "area_km2": area,
                "container_count": 0,
                "avg_hourly_pct": 0.0,
            }

    # 2. Cargar TODOS los 28.267 contenedores reales
    contenedores_path = ROOT / "db" / "datos" / "contenedores_negros.json"
    with open(contenedores_path, "r", encoding="utf-8") as fh:
        raw_features = json.load(fh)["features"]

    print(f"Procesando los {len(raw_features)} contenedores con DensityProcessor...")
    all_inputs = []
    for f in raw_features:
        coords = f["geometry"]["coordinates"]
        props = f.get("properties", {})
        all_inputs.append(
            {
                "id": f["id"],
                "latitude": coords[1],
                "longitude": coords[0],
                "volume_m3": 3.2,
                "address": props.get("DireccionNormalizada", ""),
            }
        )

    demands, summaries = processor.process_containers(all_inputs)

    # 2.b Conteos y tasas reales por radio, tomados de los 'summaries' (que
    # reflejan TODOS los contenedores conectados a cada radio, no solo
    # aquellos para los que ese radio resultó ser el dominante).
    radio_rates_acc = defaultdict(list)
    for summary in summaries:
        if summary.radio_code in radios_by_code:
            radios_by_code[summary.radio_code]["container_count"] = (
                summary.total_containers
            )
        for cid in summary.container_ids:
            d = demands.get(cid)
            if d:
                radio_rates_acc[summary.radio_code].append(d.hourly_fill_pct)

    for r_code, rates in radio_rates_acc.items():
        if rates and r_code in radios_by_code:
            radios_by_code[r_code]["avg_hourly_pct"] = round(sum(rates) / len(rates), 3)

    # 3. Mapear contenedores por comuna (agrupados por la comuna del radio
    # dominante de cada contenedor; un contenedor puede aportar demanda a
    # radios de otras comunas si su buffer cruza el límite, pero para la
    # visualización se lo ubica en la comuna de su radio dominante).
    containers_by_comuna = defaultdict(list)

    for item in all_inputs:
        cid = item["id"]
        d = demands.get(cid)
        if not d:
            continue
        dept = d.department_name or "Sin Comuna"
        item_data = {
            "id": str(cid).split("|")[-1],
            "lat": round(item["latitude"], 5),
            "lon": round(item["longitude"], 5),
            "address": item["address"],
            "radio_code": d.radio_code,
            "radio_codes": d.radio_codes,
            "population": d.population,
            "containers_in_radio": d.containers_in_radio,
            "daily_waste_kg": round(d.daily_waste_kg, 1),
            "daily_fill_pct": round(d.daily_fill_pct, 1),
            "hourly_fill_pct": round(d.hourly_fill_pct, 3),
        }
        containers_by_comuna[dept].append(item_data)

    # 4. Compilar estructura por comuna
    comunas_data = {}
    comunas_list = []

    for dept in sorted(radios_by_comuna.keys()):
        dept_containers = containers_by_comuna.get(dept, [])
        dept_features = radios_by_comuna[dept]
        if not dept_containers or not dept_features:
            continue

        rates = [c["hourly_fill_pct"] for c in dept_containers]
        avg_h = sum(rates) / len(rates) if rates else 0.0
        pop_dept = comuna_population_acc.get(dept, 0)

        comunas_data[dept] = {
            "features": dept_features,
            "containers": dept_containers,
            "avg_hourly": round(avg_h, 3),
            "population_total": pop_dept,
        }

        comunas_list.append(
            {
                "name": dept,
                "container_count": len(dept_containers),
                "radio_count": len(dept_features),
                "avg_hourly": round(avg_h, 3),
                "population_total": pop_dept,
            }
        )

    # Ordenar comunas_list de mayor a menor tasa media
    comunas_list.sort(key=lambda x: x["avg_hourly"], reverse=True)

    # 5. Calcular Top 5 Hotspots de Sobredemanda y Capacidad Ociosa
    active_radios = [
        {"code": code, **data}
        for code, data in radios_by_code.items()
        if data["container_count"] > 0
    ]
    active_radios.sort(key=lambda x: x["avg_hourly_pct"], reverse=True)

    hotspots_high = [
        {
            "code": r["code"],
            "comuna": r["department_name"],
            "pop": r["population"],
            "count": r["container_count"],
            "rate": r["avg_hourly_pct"],
        }
        for r in active_radios[:5]
    ]

    hotspots_low = [
        {
            "code": r["code"],
            "comuna": r["department_name"],
            "pop": r["population"],
            "count": r["container_count"],
            "rate": r["avg_hourly_pct"],
        }
        for r in active_radios[-5:]
    ]

    # 6. Distribución granular de tasa horaria sobre TODOS los contenedores
    # (no promediada por comuna, para no perder la forma real de la cola) más
    # el detalle completo de los radios con poca cobertura de contenedores,
    # que es la causa real de los valores extremos.
    all_rates = sorted(d.hourly_fill_pct for d in demands.values())
    n_rates = len(all_rates)

    def _percentile(p: float) -> float:
        if n_rates == 0:
            return 0.0
        idx = min(int(n_rates * p), n_rates - 1)
        return all_rates[idx]

    bin_edges = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, float("inf")]
    bin_labels = [
        "<0.5",
        "0.5-1",
        "1-1.5",
        "1.5-2",
        "2-2.5",
        "2.5-3",
        "3-4",
        "4-5",
        "5-10",
        ">10",
    ]
    bin_counts = [0] * len(bin_labels)
    for rate in all_rates:
        for i in range(len(bin_labels)):
            if bin_edges[i] <= rate < bin_edges[i + 1]:
                bin_counts[i] += 1
                break

    low_coverage_radios = [
        {
            "code": r["code"],
            "comuna": r["department_name"],
            "pop": r["population"],
            "count": r["container_count"],
            "rate": r["avg_hourly_pct"],
        }
        for r in sorted(active_radios, key=lambda r: -r["avg_hourly_pct"])
        if r["container_count"] <= 3
    ]

    distribution = {
        "percentiles": {
            "p50": _percentile(0.50),
            "p90": _percentile(0.90),
            "p99": _percentile(0.99),
        },
        "stats": {
            "n": n_rates,
            "mean": sum(all_rates) / n_rates if n_rates else 0.0,
            "max": all_rates[-1] if all_rates else 0.0,
        },
        "histogram": {"labels": bin_labels, "counts": bin_counts},
        "low_coverage_radios": low_coverage_radios,
    }

    payload = {
        "comunas_list": comunas_list,
        "comunas_data": comunas_data,
        "radios_by_code": radios_by_code,
        "hotspots_high": hotspots_high,
        "hotspots_low": hotspots_low,
        "distribution": distribution,
    }

    html = HTML_TEMPLATE.replace("__DATA_PAYLOAD__", json.dumps(payload))
    out_file = ROOT.parent / "density_verification_map.html"
    out_file.write_text(html, encoding="utf-8")

    print("=" * 70)
    print(" ✨ INSPECTOR VISUAL GENERADO EXITOSAMENTE:")
    print(f"    {out_file.resolve()}")
    print("=" * 70)
    print(" 🏆 Top 3 Comunas por Tasa Media:")
    for idx, c in enumerate(comunas_list[:3]):
        print(
            f"   #{idx + 1} {c['name']}: {c['avg_hourly']:.3f} %/h ({c['container_count']} contenedores, {c['population_total']:,} hab.)"
        )
    print("\n 🔥 Top 3 Hotspots con Mayor Sobredemanda:")
    for h in hotspots_high[:3]:
        print(
            f"   • Radio {h['code']} ({h['comuna']}): {h['rate']:.1f} %/h ({h['pop']} hab. / {h['count']} cont.)"
        )
    print("=" * 70)


if __name__ == "__main__":
    main()
