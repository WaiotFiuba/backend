# Documentación: Modelo de Generación de Residuos por Densidad y Radios Censales

Este documento detalla la arquitectura, fórmulas matemáticas, unidades empleadas, mitigaciones de error y el registro exhaustivo de funciones alteradas para la integración del modelo de generación de basura basado en polígonos de radios censales de CABA y el factor de demanda global del frontend.

---

## 1. Resumen de los Cambios

Se reemplazó el cálculo anterior de demanda de generación basado en factores estáticos macro por barrio por un **motor espacial de precisión territorial a nivel de radio censal**:
1. **Asignación Espacial Point-in-Polygon**: Cada contenedor se ubica dentro de su correspondiente polígono de radio censal de CABA mediante indexación espacial `shapely.strtree.STRtree`.
2. **Agrupación y Distribución Per Cápita**: Se determina cuántos y cuáles contenedores pertenecen a cada radio censal, repartiendo la generación de basura de la población total del radio entre los contenedores disponibles.
3. **Multiplicador Dinámico Global**: La generación base se modula mediante el factor `global_demand_multiplier` enviado en tiempo real desde el frontend.
4. **Autonomía en Backend y Docker**: La lógica y los datos residen en `backend/app/` y se ejecutan automáticamente en el worker del gemelo digital al iniciar una simulación con `docker compose`.

---

## 2. Unidades de Medida Empleadas

Para garantizar la coherencia en todo el backend, base de datos y simulador:

| Variable / Parámetro | Unidad | Descripción |
| :--- | :--- | :--- |
| **Generación per cápita** | $\text{kg} / \text{persona} / 24\text{ h}$ | Constante `PROMEDIO_GENERACION_BASURA_PERSONAS_24H = 1.5` |
| **Densidad aparente de residuo** | $\text{kg} / \text{m}^3$ | Constante `DENSIDAD_BASURA_KG_M3 = 150.0` (residuo suelto en contenedor) |
| **Volumen de contenedor** | $\text{m}^3$ | Propiedad `Container.volume_m3` (ej. $3.2\text{ m}^3$ o $2.4\text{ m}^3$) |
| **Nivel de llenado** | Porcentaje ($\%$) | Rango `0.0` a `100.0` en telemetría y base de datos |
| **Paso de simulación ($\Delta t$)** | Minutos | `ScenarioConfig.frequency_minutes` (típico $15$ o $60\text{ min}$) |
| **Tasa horaria (`demand_base`)** | $\% / \text{hora}$ | Porcentaje base de llenado por hora por contenedor |
| **Multiplicador global** | Adimensional (`float`) | `global_demand_multiplier` enviado desde el Frontend |

### Fórmulas de Conversión:
1. **Demanda Diaria por Contenedor (kg/24h)**:
   $$\text{Demanda}_{24\text{h}} = \left(\frac{\text{Población Total del Radio} \times 1.5\text{ kg}}{\text{Cant. Contenedores en el Radio}}\right) \times \text{global\_demand\_multiplier}$$

2. **Capacidad Máxima del Contenedor (kg)**:
   $$\text{Capacidad}_{\text{kg}} = \text{volume\_m3} \times 150.0\text{ kg/m}^3$$

3. **Tasa de Llenado Horaria (`demand_base`)**:
   $$\text{Llenado Horario (\%)} = \left(\frac{\text{Demanda}_{24\text{h}}}{24 \times \text{Capacidad}_{\text{kg}}}\right) \times 100$$

4. **Incremento por Tick de Simulación**:
   $$\Delta \text{Nivel (\%)} = \text{demand\_base} \times \left(\frac{\text{frequency\_minutes}}{60.0}\right) \times f_{\text{hora}} \times f_{\text{dia}} \times f_{\text{tipo\_residuo}} \times \text{ruido}$$

---

## 3. Cosas a Tener en Cuenta

1. **Montaje de Volúmenes en Docker**:
   - `docker-compose.yml` monta `./app:/app/app`. Por este motivo, el archivo de polígonos [`radios_caba_filtrado.csv`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/datos/radios_caba_filtrado.csv) se almacena dentro de `backend/app/datos/` para estar disponible inmediatamente en el contenedor.
2. **Semilla y Reproducibilidad**:
   - Las variaciones estocásticas (`ruido lognormal`) son reproducibles a través de la semilla `seed` del escenario.
3. **Escala Temporal**:
   - Modificar `frequency_minutes` en el frontend (ej. de 60 a 15 min) ajusta automáticamente la fracción horaria sin alterar la acumulación diaria total de residuos.

---

## 4. Posibles Casos de Error y Manejo de Fallbacks

| Caso de Error | Causa Posible | Mitigación Implementada |
| :--- | :--- | :--- |
| **Contenedor en límite/calle** | La coordenada cae en la arista entre dos polígonos | Fallback automático a `STRtree.nearest(Point)` para asignar el radio más próximo. |
| **Contenedor fuera de CABA** | Coordenadas sintéticas o de prueba fuera del mapa | Se asigna demand fallback neutral de $150\text{ kg/24h} \times \text{global\_demand\_multiplier}$. |
| **Radio con población 0** | Zonas industriales o plazas sin habitantes censados | Fallback a demanda mínima proporcional para evitar división por cero o contenedores inertes. |
| **Contenedor sin `volume_m3`** | Registro con tipo de contenedor sin volumetría cargada | Fallback seguro a $1.0\text{ m}^3$ para el cálculo de capacidad. |
| **Falta del CSV en entorno** | Archivo eliminado o ruta cambiada | `DensityProcessor` detecta ausencia y conmuta a fallback sin interrumpir la simulación. |

---

## 5. Posibles Mejoras Futuras

1. **Diferenciación por Tipo de Residuo según Fracción**:
   - Calibrar la generación per cápita diferenciando fracción seca (reciclables $\approx 0.35\text{ kg}$) y fracción húmeda ($\approx 1.15\text{ kg}$).
2. **Generación Comercial / Puntos de Alto Tránsito**:
   - Incorporar capas de puntos de interés (gastronomía, comercios) que sumen demanda adicional a los radios censales correspondientes.
3. **Caché Espacial Precalculada en Base de Datos**:
   - Guardar la relación `container_id <-> radio_id` en una tabla o columna espacial (`ST_Contains` en PostGIS) para acelerar arranques en flotas de más de 50.000 contenedores.

---

## 6. Registro Detallado de Funciones Alteradas y Nuevas

### A. Nuevos Módulos
* **[`backend/app/digital_twin/synthetic_data/density_processor.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/digital_twin/synthetic_data/density_processor.py)**
  * `class DensityProcessor`: Clase principal para cargar el CSV censal y construir el índice `STRtree`.
  * `find_radio(lat, lon)`: Encuentra el polígono censal exacto o más cercano.
  * `process_containers(containers, ..., global_demand_multiplier)`: Asigna contenedores a radios, agrupa la cantidad por radio, calcula kg diarios por habitante y determina `hourly_fill_pct`.
  * `get_density_processor()`: Retorna la instancia singleton en memoria.

### B. Módulos Modificados
* **[`backend/app/core/config.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/core/config.py)**
  * Se agregaron a `Settings`: `promedio_generacion_basura_personas_24h: float = 1.5` y `densidad_basura_kg_m3: float = 150.0`.
* **[`backend/app/digital_twin/synthetic_data/topology.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/digital_twin/synthetic_data/topology.py)**
  * `topology_from_backend_records(...)`: Se modificó para enviar en lote las coordenadas de los contenedores a `get_density_processor().process_containers(...)` y asignar el `demand_base` resultante a cada sitio/contenedor.
* **[`backend/app/digital_twin/synthetic_data/simulation/engine.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/digital_twin/synthetic_data/simulation/engine.py)**
  * `run_tick(...)`: Se integró el factor temporal `time_ratio = (frequency_minutes / 60.0)` en la multiplicación de incrementos, vinculando `demand_base` con el reloj del escenario y el `global_mult`.
* **[`backend/app/digital_twin/synthetic_data/generators/filling.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/app/digital_twin/synthetic_data/generators/filling.py)**
  * `filling_increment(...)`: Se incorporó el escalado por `time_ratio`.
* **[`ProcesadorDeDensidad/procesador.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/ProcesadorDeDensidad/procesador.py)**
  * `class IndiceEspacialRadios`: Búsqueda espacial optimizada con `STRtree`.
  * `agrupar_y_calcular_demanda_contenedores(...)`: Agrupa contenedores por radio censal y calcula demanda con `global_demand_multiplier`.
  * Soporte nativo de `csv` y compatibilidad multiplataforma de caracteres.
* **[`backend/tests/test_density_processor.py`](file:///c:/Users/isaia/Documents/Mis_archivos/Programacion/Proyectos/Waiot/backend/tests/test_density_processor.py)**
  * Nuevos tests unitarios verificando carga de datos, Point-in-Polygon, reparto poblacional equitativo por radio y acumulación en simulación.
