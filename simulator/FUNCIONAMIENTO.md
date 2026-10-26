# Funcionamiento del simulador

Este documento explica en términos generales cómo el simulador genera los datos.
Para levantarlo y usarlo, ver el [README](README.md).

## Datos de entrada

### Lo que recibe del backend

El simulador no tiene topología propia. Al iniciar cada simulación la pide al
endpoint `GET /digital-twin/worker/containers`, que devuelve por cada contenedor:

- su sitio (id, nombre y dirección) y su ubicación (latitud y longitud).
- el IMEI del dispositivo.
- el tipo de contenedor: altura, volumen y tipo de residuo.
- el nivel actual, que se usa como nivel inicial de la simulación.

También toma del backend la configuración de la sesión (período, horas y días de
recolección) y los controles en vivo (velocidad, multiplicador global de demanda
y ajustes por barrio). Los consulta antes de cada tick.

### Lo que aporta el simulador

| Dato | Archivo | Para qué se usa |
| --- | --- | --- |
| Radios censales de CABA | `datos/simulator/demography/radios_caba_filtrado.csv` | Población y polígono de cada radio|
| Uso del suelo por radio y por barrio | `datos/simulator/land_use/land_use_by_radio.csv`, `land_use_by_barrio.csv` | Tipo de zona (residencial, comercial, oficinas, industrial) y su curva horaria y semanal. |
| Rutas de recolección | `datos/simulator/routes/rutas_recoleccion_residuos_humedos.csv`, `rutas_recoleccion_residuos_secos.csv` | Recorrido de cada camión, por tramos de calle. |
| Bases de camiones | `simulator/trucks/truck_depots.py` | Punto de partida de la flota de cada zona. |
| Valores por defecto del escenario | `simulator/config/scenario_defaults.yaml` | Horas y días de recolección, factores por tipo de residuo, etc. |
| Perfiles de zona | `simulator/config/zone_profiles.yaml` | Calibración global, umbrales para clasificar zonas, multiplicadores y curvas por tipo de zona. |

Los archivos de uso del suelo se generan con
`simulator/demography/commands/process_land_use.py` a partir del relevamiento de
usos del suelo de BA Data (`db/datos/relevamiento-usos-del-suelo-2022-2024.csv`),
la traza de calles y los radios censales. El worker lo corre al arrancar, pero
solo si `land_use_by_radio.csv` todavía no existe. Si se cambian los umbrales o
los pesos por categoría de `zone_profiles.yaml`, hay que regenerarlo a mano:

```bash
uv run python -m simulator.demography.commands.process_land_use --force
```

### Fuera de CABA

Todos los datos propios cubren solo los límites de la ciudad. Un contenedor
ubicado fuera de CABA no toca ningún radio censal, así que:

- recibe una demanda fija (`DENSITY_FALLBACK_DAILY_WASTE_KG`, 150 kg/día).
- usa un perfil de zona genérico, sin curva propia.
- queda asignado a la ruta de CABA más cercana.

La simulación no falla, pero los datos de esos contenedores no son representativos.

## Qué pasa en cada tick

Cada tick representa `frequency_minutes` de tiempo simulado. En cada uno, el
motor hace tres pasos para todos los contenedores a la vez:

1. **Llenado.** Cada contenedor sube según su demanda (ver [Demanda de cada contenedor](#demanda-de-cada-contenedor)). Lo que no
   entra en un contenedor lleno pasa al sitio de enfrente. El nivel nunca supera
   el 100 %.
2. **Recolección.** Si es hora y día de recolección, los camiones vacían los
   contenedores de las paradas que les tocan en esa hora.
3. **Lectura.** Cada sensor reporta el nivel resultante como una medición, con la
   hora del tick más el desfase propio del dispositivo.

## Demanda de cada contenedor

La demanda dice cuánto sube un contenedor por hora, en % de su capacidad. Se
calcula una vez, al iniciar la simulación, a partir de los radios censales.

### De la población a los contenedores

1. Cada radio censal genera `población × PROMEDIO_GENERACION_BASURA_PERSONAS_24H`
   kg de basura por día (1,5 kg por persona).
2. El polígono de cada radio se agranda `DENSITY_STREET_BUFFER_M` metros (18 m),
   para alcanzar los contenedores de la vereda de enfrente. Un contenedor queda
   conectado a todos los radios que lo alcanzan, y un radio a todos los
   contenedores que alcanza.
3. Cada radio reparte su basura entre sus contenedores según la distancia: le
   da más al que está adentro del radio y menos cuanto más cerca está del borde
   del buffer. El total del radio se conserva.
4. Cada contenedor suma lo que le aportan todos sus radios. El radio que más le
   aporta es su **radio dominante**, y define su perfil de zona.

### De kilos a porcentaje

La capacidad de un contenedor es `volumen × DENSIDAD_BASURA_KG_M3` (150 kg/m³).
Su llenado diario es lo que recibe dividido su capacidad, con un piso de
`DENSITY_MIN_DAILY_FILL_PCT_FLOOR` (20 % por día) para las zonas de baja
población. La demanda por hora es ese llenado dividido 24.

Todos los contenedores de un sitio comparten la demanda base: la del primer
contenedor del sitio.

### En cada tick

La subida de cada contenedor es:

```text
demanda por hora × horas del tick
  × factor del tipo de residuo   (waste_type_factors)
  × multiplicador global         (control de la sesión × high_demand_multiplier)
  × multiplicador de zona        (perfil de zona × calibración × ajuste por barrio)
  × overflow_stress_multiplier
  × ruido                        (lognormal, alrededor de 1)
```

El multiplicador de zona cambia según la hora y el día de la semana, se explica
en [Perfiles de zona](#perfiles-de-zona).

## Perfiles de zona

El perfil de zona dice cuánta basura genera una zona en relación con el resto, y
cómo se reparte esa basura a lo largo del día y de la semana. Cada contenedor usa
el perfil de su radio dominante.

### Uso del suelo de cada radio

`process_land_use` ubica cada parcela del relevamiento de BA Data en su radio
censal y calcula qué porcentaje de las parcelas de cada radio es residencial
multifamiliar, residencial unifamiliar, comercial, de oficinas o industrial. Con
`classification_thresholds` le asigna además un tipo de zona predominante, o
`mixed` si no hay uno claro. El resultado queda en `land_use_by_radio.csv`.

### Volumen

El volumen de un radio es el promedio de los `category_demand_weights`
ponderado por sus porcentajes de parcelas. Las parcelas que no son de ningún
tipo cuentan como `other`. Por ejemplo, un radio muy comercial (1,40) genera más
que uno de oficinas (0,85). Este valor se calcula al generar el uso del suelo y
queda guardado en el CSV.

### Curva horaria y semanal

Cada tipo de uso del suelo tiene su curva en `profiles`:

- `hour_weights`: peso de cada hora del día. Las horas que no aparecen se
  interpolan entre las definidas.
- `weekday_factors`: peso de cada día (`0` = lunes). Los días que no aparecen
  valen 1.

La curva de un radio es la mezcla de las curvas de sus tipos de uso, cada una con
el peso de su porcentaje de parcelas y su `category_demand_weights`. La curva se
normaliza para tener media 1 en la semana: solo mueve la basura en el tiempo, no
cambia cuánta se genera.

El multiplicador de zona de un contenedor en un instante es el volumen de su
radio por el valor de la curva en ese día y esa hora.

Un radio sin porcentajes usa la curva de su tipo de zona. Un contenedor sin
radio usa un perfil residencial genérico.

### Calibración global

Al iniciar la simulación se calcula la media de los volúmenes de todos los
contenedores. Si esa media sale de `target_zone_mult ± tolerance_pct` (1,05 ±
3 %), se aplica a todos un factor fijo que la lleva a `target_zone_mult`. Así el
total de la ciudad queda cerca de 1,5 × 1,05 ≈ 1,58 kg por persona por día, sin
cambiar las diferencias entre zonas.

### Ajuste por barrio

El multiplicador por barrio de la sesión se aplica encima del multiplicador de
zona, a todos los contenedores cuyo radio dominante está en ese barrio.

## Desborde al sitio de enfrente

Al iniciar la simulación, el motor empareja los sitios enfrentados. Dos sitios
quedan emparejados cuando:

- están sobre la misma calle.
- uno tiene altura par y el otro impar.
- sus alturas difieren en 60 o menos.
- están a 60 m o menos.

Cada sitio tiene a lo sumo un sitio de enfrente.

En cada tick, lo que no entra en un contenedor lleno se reparte en partes iguales
entre los contenedores del sitio de enfrente que tienen su mismo tipo de residuo,
o entre todos los del sitio si no hay ninguno de ese tipo. Los que reciben
también tienen tope en 100 %, y lo que les sobra se pierde: no hay desborde en
cadena. Si el sitio no tiene uno enfrente, el excedente se pierde.

## Recolección

### Rutas y flota

Al iniciar la simulación, el motor arma las rutas con los sitios de la topología:

- Los sitios con contenedores reciclables van a las rutas de residuos secos y el
  resto a las de húmedos. Cada CSV de `datos/simulator/routes/` define sus rutas
  como una lista de tramos de calle con su rango de alturas.
- Cada sitio se asigna a la ruta del tramo que contiene su dirección (calle y
  altura, con 50 de tolerancia). Si no matchea ninguno, va a la ruta más cercana.
- Dentro de cada ruta, los sitios se ordenan según el orden de sus tramos.
- El barrio Rodrigo Bueno tiene una ruta propia, ordenada por cercanía desde su
  acceso.

Hay un camión por ruta.

### En cada hora de recolección

Las horas de recolección se ordenan a partir de las 18 h, así que una noche de
21 a 6 es un único turno. En cada día con recolección:

- Las paradas de cada ruta se reparten en partes iguales entre las horas del
  turno, y en cada hora el camión recorre su tramo.
- En cada parada, el camión vacía los contenedores de su tipo de residuo que
  tengan algo de basura, y los deja entre 0 y 3 %.

La carga del camión se registra, pero no limita la recolección: el camión nunca
se llena.

### Método secundario

Si las rutas no se pueden armar, la simulación sigue sin camiones:

- Cada sitio tiene un turno fijo, una de las horas de recolección.
- En su turno, con probabilidad `collection_probability`, se vacían todos sus
  contenedores con basura, que quedan entre 0 y 8 %.
- Con probabilidad `partial_collection_probability`, la recolección es parcial:
  el nivel baja entre 25 y 55 puntos.

## Lectura

Cada medición lleva el nivel de llenado y la distancia que mediría el sensor
ultrasónico: la altura del contenedor por la fracción vacía, con un ruido de
alrededor de 1 cm y un mínimo de 2 cm.

La medición marca una recolección cuando el contenedor fue recolectado en el tick
y su nivel bajó 20 puntos o más.

## Ciclo de una simulación

1. El worker marca la sesión como tomada.
2. Relee `scenario_defaults.yaml` y `zone_profiles.yaml`, carga la topología
   (hasta `SIMULATOR_CONTAINER_LIMIT` contenedores) e inicializa el motor: la
   demanda de cada contenedor y la flota de camiones.
3. Publica la capa de perfiles de zona con la que corre la sesión.
4. Marca la sesión como `running` y reporta el escenario efectivo (con los
   defaults aplicados) y la cantidad de ticks.
5. En cada tick lee los controles vigentes, genera las lecturas, las envía en
   lotes de `SIMULATOR_BATCH_SIZE`, reporta el progreso y espera hasta el
   próximo tick.
6. Cuando termina el último tick, cierra la sesión como `completed`.

El multiplicador global de la sesión se multiplica por el
`high_demand_multiplier` del escenario. El de cada barrio se aplica encima.

### Velocidad

La espera entre ticks es la duración del tick dividida por `speedup`, menos lo
que tardó el worker en generarlo y enviarlo. Si un tick tarda más que eso, el
siguiente arranca enseguida, pero la velocidad pedida no se sostiene.

### Pausa y detención

El worker revisa el estado de la sesión antes de cada tick y durante la espera.
Al pausar, termina el tick que estaba enviando y no genera más hasta que se
reanude, el reloj simulado no avanza. Al detener, cierra la sesión como
`completed`.

### Fallos

- Si un tick falla, la sesión se cierra como `failed`, con el error.
- Si no logra consultar el estado de la sesión `SIMULATOR_CONTROL_MISS_TOLERANCE`
  veces seguidas, deja de ejecutarla. Si la sesión sigue activa, la vuelve a
  tomar desde el primer tick.
- Al arrancar, el worker marca como `failed` las sesiones que quedaron a medias
  por un reinicio. No se reanudan.

## La CLI

La CLI usa el mismo motor que el worker: misma demanda, perfiles de zona,
desborde, camiones y sensores. Cambia todo lo que lo rodea:

- No hay sesión: no tiene pausa, controles en vivo ni progreso. La demanda global
  es el `high_demand_multiplier` del escenario durante todo el período, sin
  ajustes por barrio.
- Corre todos los ticks seguidos, sin esperas, y guarda el resultado en memoria.
  Después lo valida (niveles entre 0 y 100, que haya mediciones) y recién ahí lo
  exporta o lo envía.
- No publica la capa de perfiles de zona ni genera el uso del suelo: usa los CSV
  que ya estén en `datos/simulator/land_use/`.
- La topología sintética (sin `--from-backend`) son unos pocos sitios alrededor
  de cinco barrios, con demanda puesta a mano. No pasa por los radios censales,
  y como los sitios no tienen dirección, no hay desborde al sitio de enfrente.

## Mapa de módulos

| Módulo | Qué hace |
| --- | --- |
| `worker.py` | Toma las sesiones del backend y corre el ciclo de ticks. |
| `__main__.py` | La CLI. |
| `simulation/engine.py` | El motor: arma el estado y ejecuta cada tick. |
| `simulation/state.py` | Estado de la simulación y datos fijos de cada contenedor. |
| `simulation/scenario.py` | Lee el escenario y le aplica los defaults. |
| `simulation/controls.py` | Controles en vivo de la sesión. |
| `simulation/opposing_sites.py` | Empareja los sitios enfrentados. |
| `topology.py` | Arma la topología a partir de lo que manda el backend. |
| `density_processor.py` | Demanda de cada contenedor a partir de los radios censales. |
| `zone_classifier.py` | Perfil de zona de cada radio. |
| `demography/commands/process_land_use.py` | Genera el uso del suelo por radio y por barrio. |
| `generators/filling.py` | Llenado y desborde. |
| `generators/collections.py` | Recolección con camiones y método secundario. |
| `generators/sensors.py` | Lecturas del sensor. |
| `generators/topology.py` | Topología sintética de la CLI. |
| `trucks/` | Rutas, bases y flota de camiones. |
| `transport/` | Comunicación HTTP con el backend: sesión, topología y telemetría. |
| `exporters/` | Exportación a archivos y capa de perfiles de zona. |
| `validation/checks.py` | Validación del resultado de la CLI. |
| `scripts/generate_census_radio_inspector.py` | Genera un HTML para revisar qué radios alimentan a cada contenedor. |

## Fuentes de datos

| Dato | Archivo | Fuente | Link |
| --- | --- | --- | --- |
| Radios censales: población, hogares y polígonos | `datos/simulator/demography/radios_caba_filtrado.csv` | Censo nacional (INDEC), radios de CABA | [Poblaciones](https://mapa.poblaciones.org/map/257701/#/@-34.631711,-58.463745,12z&r79070/l=12201!v3!a2!w0,0,0,0,0) |
| Relevamiento de usos del suelo 2022-2024 | `db/datos/relevamiento-usos-del-suelo-2022-2024.csv` | BA Data (GCBA) | [Documentación](https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/relevamiento-usos-suelo/documentacion-relevamiento-usos-suelo-2022-2024.pdf) |
| Traza de calles con alturas | `datos/digital_twin/calles.geojson` | BA Data (GCBA) | [Calles](https://data.buenosaires.gob.ar/dataset/calles) |
| Barrios | `datos/digital_twin/barrios.geojson` | BA Data (GCBA) | [Barrios](https://data.buenosaires.gob.ar/dataset/barrios) |
| Rutas de recolección de residuos húmedos | `datos/simulator/routes/rutas_recoleccion_residuos_humedos.csv` | BA Data (GCBA) | [Infraestructura de gestión de residuos](https://data.buenosaires.gob.ar/dataset/infraestructura-gestion-residuos) |
| Rutas de recolección de residuos secos (contenedores verdes) | `datos/simulator/routes/rutas_recoleccion_residuos_secos.csv` | BA Data (GCBA) | [Infraestructura de gestión de residuos](https://data.buenosaires.gob.ar/dataset/infraestructura-gestion-residuos) |
| Bases de camiones y estaciones de transferencia | `simulator/trucks/truck_depots.py` | Coordenadas cargadas a mano | |
| Generación de 1,5 kg por persona por día | `PROMEDIO_GENERACION_BASURA_PERSONAS_24H` | APrA (GCBA) | [Ficha RSU](https://buenosaires.gob.ar/areas/med_ambiente/apra/educ_com/archivos/fichas/ficha_rsu.pdf) |

Los multiplicadores por tipo de uso del suelo (`category_demand_weights`) y las
curvas horarias y semanales (`profiles`) de `zone_profiles.yaml` no salen de un
estudio de generación de residuos por uso del suelo en CABA. Son valores dados
por el equipo, no realistas.
