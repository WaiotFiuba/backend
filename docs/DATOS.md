# Datos y red de contenedores

Este documento explica cómo el backend arma la red de contenedores y sitios a
partir de datos abiertos de CABA, y cómo guarda la telemetría que reciben. Para
levantarlo, ver el [README](../README.md). Los datos propios del simulador
(radios censales, uso del suelo, rutas) están en
[simulator/FUNCIONAMIENTO.md](../simulator/FUNCIONAMIENTO.md).

## Datos de entrada

Todos están en `datos/digital_twin/` y se descargan con los datos semilla.

| Dato | Archivo | Para qué se usa |
| --- | --- | --- |
| Contenedores de residuos húmedos (negros) | `contenedores_negros.json` | Ubicación, dirección y tipo de carga de cada contenedor. |
| Contenedores de residuos secos (verdes) | `contenedores_verdes.json` | Ubicación y dirección de cada contenedor. |
| Tipos de residuo | `waste_types.json` | Fracciones húmeda y seca. |
| Traza de calles | `calles.geojson` | Capa geográfica en PostGIS y armado de sitios por cuadra y lado. |
| Barrios | `barrios.geojson` | Capa geográfica en PostGIS. |
| Tramos de calle con su normativa de estacionamiento | `estacionamiento_via_publica.csv` | Base del cálculo de capacidad por tramo. |
| Ciclovías, garajes comerciales, cajones de carga y descarga, mobiliario urbano | `ciclovias.csv`, `garajes-comerciales.csv`, `cajones-para-carga-y-descarga.csv`, `mobiliario-urbano.csv` | Zonas donde no se puede ubicar un contenedor. |
| Capacidad por tramo | `restricciones_contenedores.csv` | Resultado del cálculo de capacidad. Lo usa la redistribución. |

## Carga inicial

La carga sigue este orden, porque cada paso usa lo anterior:

1. **Base de datos.** Al crearse, PostGIS habilita la extensión e importa con
   `ogr2ogr` todos los `.geojson` de `datos/digital_twin/` como tablas
   geográficas: `calles` y `barrios` (`db/import_geojson.sh`).
2. **Migraciones.** Alembic crea el esquema.
3. **Seed** (`app.commands.seed`). La API lo corre al arrancar:
   1. carga los tipos de residuo.
   2. carga los tipos de contenedor (de `container_types.json`, o los
      predeterminados si no está).
   3. carga los contenedores de los dos GeoJSON, sin duplicar los que ya existen.
   4. si hay contenedores sin sitio y todavía no hay sitios, arma los sitios
      (ver [Armado de sitios](#armado-de-sitios)).

La capacidad por tramo no se calcula en el arranque: el CSV ya viene calculado, y
se regenera a mano con `scripts/generar_restricciones_contenedores.py`.

## Modelo de la red

- **Contenedor.** Tiene ubicación (punto en PostGIS), dirección, tipo, sitio,
  nivel actual (%), fecha de la última lectura y de la última recolección, y el
  IMEI de su dispositivo. `serie_id` guarda el identificador del dataset de
  origen (por ejemplo, `contenedores_negros|1`).
- **Tipo de contenedor.** Define la altura, el volumen, la zona de desborde y la
  fracción que recibe. Si no hay `container_types.json`, el seed carga estos:

  | Tipo | Altura | Volumen |
  | --- | --- | --- |
  | RSU Fracción Húmeda - Carga Lateral | 145 cm | 3,2 m³ |
  | RSU Fracción Húmeda - Carga Bilateral | 165 cm | 4,0 m³ |
  | RSU Fracción Húmeda - Semi Soterrado | 120 cm | 5,0 m³ |
  | RSU Fracción Seca - Carga Lateral | 145 cm | 3,2 m³ |

  Los contenedores verdes son de fracción seca. Los negros son de fracción
  húmeda, y su tipo sale del campo `CodEquipa` del dataset (lateral, bilateral o
  semi soterrado).
- **Sitio.** Es un punto de recolección: agrupa contenedores de una sola fracción
  sobre una misma cuadra y un mismo lado de la calle. Tiene ubicación, dirección,
  fracción y lado de carga.

## Armado de sitios

El sitio es una abstracción del modelo de Waiot: una ubicación de disposición de
residuos que agrupa uno o más contenedores. Es la unidad con la que trabajan la
proyección y la redistribución. Su definición está en el artículo del proyecto
(sección III.B, tabla I) y en el Informe Final.

Los datasets de CABA publican contenedores sueltos, sin sitios. Para este proyecto
se decidió agruparlos con el criterio de abajo, de modo que un sitio represente un
punto de recolección: contenedores de un mismo tipo, sobre una misma cuadra y un
mismo lado de la calle. Se agrupa por cuadra, y no por tramos más cortos, para
obtener sitios con más contenedores: la redistribución trabaja moviendo
contenedores entre sitios, y con sitios de uno o dos contenedores tendría poco
margen para equilibrar la capacidad. No es necesariamente la forma en que se
agrupan los sitios en otros modelos.

Los sitios se arman a partir de los contenedores (`site_clustering_service.py`):

1. Los contenedores se separan por fracción y tipo de contenedor: un sitio nunca
   mezcla tipos.
2. Con la traza de calles, cada contenedor se asigna a su cuadra (el tramo de
   calle más cercano) y a su lado de la calle, según el sentido de circulación.
   Los contenedores bilaterales que no están sobre una avenida se cargan desde
   los dos lados, así que su lado queda como `BILATERAL`.
3. Dentro de cada cuadra y lado, el grupo se corta si dos contenedores están a
   más de 100 m, o si sus alturas difieren en 100 o más.
4. Sin traza de calles, los contenedores se agrupan solo por cercanía, con un
   máximo de 20 m entre ellos.

La ubicación del sitio es el promedio de las coordenadas de sus contenedores, y su
dirección es la del primero. Para volver a armarlos: `seed --recluster` (ver el
[README](../README.md#comandos-útiles)).

## Capacidad por tramo de calle

`restricciones_contenedores.csv` tiene, para cada tramo de calle, la cantidad
máxima de contenedores que admite según la Resolución Conjunta 1/SSHU/19. Lo
genera `scripts/generar_restricciones_contenedores.py`:

1. Un contenedor puede ir en la calzada solo si el tramo permite estacionar, no
   tiene una ciclovía de ese lado y no es peatonal. Si no, la vereda se admite
   como excepción en avenidas y calles anchas.
2. Al largo del tramo se le restan los accesos a garajes, los cajones de carga y
   descarga, las paradas, los puestos y postes del mobiliario urbano, y una
   reserva de 10 m en cada esquina (ochava).
3. El espacio libre que queda se divide por el largo de un contenedor (1,87 m)
   más 0,20 m de separación, dejando un margen libre a cada lado: 1 m en calzada y
   6 m en vereda.

La API lee el CSV al arrancar (`site_capacity_service.py`). Para cada sitio busca
los tramos a 35 m o menos: en una esquina, donde hay varios tramos a 25 m o menos,
toma el de mayor capacidad, si no, el más cercano. Si no encuentra ningún tramo,
el sitio no puede recibir más contenedores de los que ya tiene. Si el CSV no está,
no se limita la capacidad.

El módulo de redistribución usa esta capacidad como tope de los sitios que
reciben contenedores.

## Telemetría

### Ingesta

La telemetría llega por `POST /digital-twin/telemetry` (una medición) y
`POST /digital-twin/telemetry/batch` (un lote). Para cada medición, la ingesta
busca el contenedor (por su id, su `serie_id` o el IMEI del dispositivo), guarda
la lectura en `data_level` y actualiza el nivel actual del contenedor y, si hubo
recolección, la fecha de la última. Las lecturas que llegan fuera de orden se
guardan, pero no cambian el estado del contenedor. Si hay un plan de
redistribución activo, la ingesta también actualiza el What-If (ver
[REDISTRIBUCION.md](REDISTRIBUCION.md)).

### Particiones y retención

Cada tick de una simulación genera una lectura por contenedor. Con toda la ciudad,
una simulación muy larga hacía crecer `data_level` sin límite: ocupaba cada vez
más disco y la ingesta se volvía más lenta. Para corregirlo:

- **Particiones.** `data_level` se particiona por día de lectura. Cada lote escribe
  solo en la partición de su día, con índices chicos, y los días viejos se borran
  enteros.
- **Retención.** Después de cada ingesta, en segundo plano, se borran las lecturas
  que quedan fuera de los últimos `DATA_LEVEL_RETENTION_PERIODS` períodos (un
  período es una lectura por contenedor). Los logs avisan si la ventana cubre
  menos de los 14 días de historia que mira la proyección, o si `data_level`
  supera `DATA_LEVEL_SIZE_WARNING_GB`.

### Reloj de referencia

La proyección, la redistribución y la evaluación toman como "ahora" la última
lectura de `data_level`, y no el reloj del servidor. Así funcionan igual con
sensores reales y con el simulador.

### Inicio de una simulación

Al crear una simulación, el backend borra las lecturas de `data_level` y pone en 0
el nivel de todos los contenedores: cada simulación arranca con la red vacía.

## Mapa de módulos

| Módulo | Qué hace |
| --- | --- |
| `app/core/map_migrations.py` | El seed: tipos de residuo y de contenedor, contenedores y sitios. |
| `app/services/map/site_clustering_service.py` | Armado de sitios. |
| `app/services/map/site_capacity_service.py` | Capacidad de cada sitio según su tramo de calle. |
| `app/services/digital_twin_ingest_service.py` | Ingesta de telemetría. |
| `app/services/data_level_partitions.py` | Particiones diarias de `data_level`. |
| `app/services/data_level_retention.py` | Retención de `data_level`. |
| `app/services/map/reference_time_service.py` | Reloj de referencia. |
| `db/import_geojson.sh` | Importación de las capas geográficas a PostGIS. |
| `scripts/generar_restricciones_contenedores.py` | Cálculo de la capacidad por tramo de calle. |

## Fuentes de datos

| Dato | Fuente |
| --- | --- |
| Contenedores | [Infraestructura de gestión de residuos](https://data.buenosaires.gob.ar/dataset/infraestructura-gestion-residuos) (BA Data) |
| Calles | [Calles](https://data.buenosaires.gob.ar/dataset/calles) (BA Data) |
| Barrios | [Barrios](https://data.buenosaires.gob.ar/dataset/barrios) (BA Data) |
| Estacionamiento en la vía pública | [Estacionamiento en la vía pública](https://data.buenosaires.gob.ar/dataset/estacionamiento-en-la-via-publica) (BA Data) |
| Ciclovías | [Ciclovías](https://data.buenosaires.gob.ar/dataset/ciclovias) (BA Data) |
| Garajes comerciales | [Garajes comerciales](https://data.buenosaires.gob.ar/dataset/garajes-comerciales) (BA Data) |
| Cajones de carga y descarga | [Cajones para carga y descarga](https://data.buenosaires.gob.ar/dataset/cajones-para-carga-descarga) (BA Data) |
| Mobiliario urbano | [Mobiliario urbano](https://data.buenosaires.gob.ar/dataset/mobiliario-urbano) (BA Data) |
| Criterios de ubicación de contenedores | [Resolución Conjunta 1/SSHU/19, anexo](https://documentosboletinoficial.buenosaires.gob.ar/publico/PE-RES-MAYEPGC-MDUYTGC-SSHU-1-19-ANX.pdf) |
