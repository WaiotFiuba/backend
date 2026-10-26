# Proyección de llenado

El módulo de proyección calcula cómo evolucionaría el llenado de cada sitio sin
recolecciones, para anticipar qué sitios alcanzarían un nivel crítico dentro
de la ventana elegida. No genera ni modifica rutas de recolección.

Hay dos modelos con una misma interfaz: un modelo base (Baseline), construido a
partir de los patrones históricos del sitio, y Chronos-2 Small, un modelo
preentrenado de series temporales. Los dos reciben el historial del sitio y su
nivel actual, y devuelven una trayectoria de niveles.

Los datos que usa (sitios, telemetría y reloj de referencia) se explican en
[DATOS.md](DATOS.md).

## Cómo se proyecta un sitio

1. **Instante de partida.** Por defecto es el reloj de referencia: la última
   lectura registrada (ver [Reloj de referencia](DATOS.md#reloj-de-referencia)).
   Con `as_of` se puede proyectar desde un instante del pasado.
2. **Nivel actual.** Es el promedio del nivel de los contenedores del sitio, o el
   máximo si se pide `level_aggregation=max`, para mirar el contenedor más
   exigido.
3. **Historial.** Las lecturas de los últimos `lookback_days` días (14 por
   defecto), agrupadas en intervalos de `interval_minutes` (60 por defecto).
4. **Proyección.** El modelo devuelve un punto por intervalo hasta
   `horizon_hours` (24 por defecto, 168 como máximo): nivel previsto, banda
   inferior y superior, y una confianza. Si `stop_at_full` está activo, la
   trayectoria se corta al llegar al 100 %.

La respuesta agrega el volumen previsto en metros cúbicos, si cada punto alcanza
el nivelcrítico o el 100 %, y cuándo y en cuántas horas se llegaría a cada uno.
El nivel crítico es `LEVEL_THRESHOLD_CRITICAL` (80 % por defecto).

## Historial de un sitio

- **Nivel por intervalo.** El nivel del sitio en cada intervalo combina a todos
  sus contenedores. Un contenedor que no reportó en ese intervalo aporta su
  último nivel conocido, si no tiene más de 3 horas. Sin esto, si los contenedores
  reportan en momentos distintos, el promedio salta según quién reportó, y esos
  saltos se confunden con recolecciones o con generación de residuos.
- **Recolecciones.** Un intervalo cuenta como recolección si alguna lectura trae
  la marca de recolección del sensor (`garbage_collection_alarm`), o si el nivel
  baja 10 puntos o más respecto del intervalo anterior.
- **Generación.** Lo que se generó en un intervalo es la subida de nivel respecto
  del anterior. Cuando hay un vaciado usa la mediana de lo que suele subir el sitio
  a esa hora. Así una recolección no se interpreta como generación negativa.

## Modelo base operativo (Baseline)

Clave: `baseline_operational`. Es el modelo por defecto.

1. **Incrementos.** Toma las subidas de nivel entre intervalos consecutivos del
   historial, sin las recolecciones y sin las bajadas.
2. **Incremento de cada punto futuro.** Usa el patrón más específico que tenga al
   menos dos casos:
   1. la mediana de los incrementos del mismo día de la semana y la misma hora.
   2. si no, la de la misma hora, cualquier día.
   3. si no, la de todo el historial.
   4. si no hay historial, 1 punto por intervalo.

   Se usa la mediana para que los picos aislados no pesen.
3. **Nivel previsto.** Es el nivel actual más los incrementos acumulados, con
   tope en 100 %.
4. **Ajuste por densidad.** Si el sitio tiene menos de 6 incrementos y tiene un
   factor de densidad cargado, los incrementos se multiplican por ese factor,
   acotado entre 0,75 y 1,5. Con historial suficiente, prevalece la serie del
   sitio.
5. **Confianza y banda.** La confianza es operativa: mide cuánta evidencia hubo
   para armar el perfil, no es un intervalo estadístico.

Es interpretable y no necesita entrenamiento, pero supone que los patrones
recientes del sitio se repiten.

## Chronos-2 Small

Clave: `chronos_2_small`. Usa el modelo preentrenado `autogluon/chronos-2-small`
(28 millones de parámetros), sin entrenamiento propio (zero-shot). Corre en local y
se carga la primera vez que se usa.

1. **Entrada.** No recibe el nivel, sino la serie de generación por intervalo
   (ver [Historial de un sitio](#historial-de-un-sitio)), sin las bajadas por
   recolección.
2. **Salida.** Chronos pronostica la generación de cada intervalo futuro en tres
   cuantiles: 0,1, `CHRONOS_FORECAST_QUANTILE` (0,6 por defecto) y 0,9.
3. **Nivel previsto.** Es el nivel actual más la generación acumulada del cuantil
   configurado, con tope en 100 %. Los incrementos negativos se toman como cero,
   para que el nivel no baje en un escenario sin recolección. La banda se arma
   igual con los cuantiles 0,1 y 0,9: es una variabilidad orientativa, no un
   intervalo de cobertura validado para el nivel final.
4. **Confianza.** Es 0,85 con 24 intervalos de historial o más, y 0,70 con menos.
   Baja 0,10 si la banda supera los 15 puntos, y 0,25 si supera los 30.

Cuando se proyectan varios sitios, todos van en una sola llamada al modelo.

Si el sitio tiene menos de 4 intervalos de historial, o si la inferencia falla, se
usa un respaldo: 1 punto por intervalo, con confianza 0,35 y banda de ±15 puntos.
Si la biblioteca `chronos` no está instalada, el modelo no aparece en la lista.

## Evaluación

La evaluación es retrospectiva, con cortes temporales sucesivos (backtesting):
proyecta desde instantes del pasado y compara con lo que pasó después.

1. **Sitios.** Una lista de sitios (`site_ids`) o una muestra aleatoria
   (`site_sample_size`) entre los que tienen mediciones.
2. **Cortes.** Se toman `cutoff_count` cortes (14 por defecto), separados por
   `cutoff_spacing_hours` (13 h por defecto, así cada corte cae a otra hora del
   día). El último corte deja `horizon_hours` de datos posteriores para comparar.
   Los cortes se anclan a una grilla fija, para que se repitan con los mismos
   datos. Se descartan los que tienen menos de `min_history_days` días de
   historia (7 por defecto), si no queda ninguno, se evalúa solo el último.
3. **Proyección.** En cada corte, el modelo solo ve la historia hasta ese
   instante, con intervalos de 60 minutos.
4. **Referencia.** Se compara contra el nivel que habría tenido el sitio sin
   recolecciones: el nivel del corte más lo que se generó después, con tope en
   100 %. Es lo mismo que proyecta el modelo.

Métricas:

- **Error de nivel:** MAE y RMSE, en puntos porcentuales.
- **Anticipación de cruces críticos:** un caso por sitio y corte, según si el
  sitio cruza el nivel crítico dentro del horizonte. Se calculan precisión, recall
  y F1 con dos criterios:
  - **Por horizonte:** acierta si anticipa un cruce que ocurre dentro del
    horizonte, sin mirar el horario.
  - **Por ventana:** acierta si el cruce pronosticado cae entre
    `critical_early_hours` antes del cruce de referencia (3 h por defecto) y
    `critical_late_hours` después (1 h por defecto). Un aviso más temprano cuenta
    como falsa alarma, porque implica una visita de más. Uno más tardío cuenta
    como cruce perdido, porque el desborde no se anticipó. Con las dos
    tolerancias iguales (por ejemplo, 1 y 1) es una ventana simétrica.

  También se informan el error absoluto y el sesgo del instante de cruce, en
  horas. Los sitios que ya estaban críticos en el corte no se cuentan, porque
  cualquier modelo los acierta.

La evaluación corre en segundo plano, de a una por vez. El reporte trae las
métricas globales, las de cada sitio y los sitios mejor y peor proyectados.

## Configuración

| Variable | Default | Uso |
| --- | --- | --- |
| `CHRONOS_FORECAST_QUANTILE` | `0.6` | Cuantil de Chronos-2 que se toma como nivel previsto (entre 0,1 y 0,9). |
| `LEVEL_THRESHOLD_CRITICAL` | `80` | Nivel (%) a partir del cual un sitio se considera crítico. |

## Endpoints

Todos requieren token. El detalle está en `/docs`.

| Método | Ruta | Qué hace |
| --- | --- | --- |
| `GET` | `/map/site-projections/models` | Lista los modelos de proyección. |
| `POST` | `/map/site-projections` | Proyecta hasta 100 sitios, sin guardar la corrida. |
| `GET` | `/map/sites/{site_id}/projection` | Proyecta un sitio. |
| `POST` | `/map/site-projections/evaluations` | Lanza una evaluación, que corre en segundo plano. |
| `GET` | `/map/site-projections/evaluations/{evaluation_id}/status` | Estado de una evaluación. |
| `GET` | `/map/site-projections/evaluations/{evaluation_id}` | Resultado de una evaluación. |
| `GET` | `/map/site-projections/evaluations/{evaluation_id}/report` | Reporte de una evaluación. |

`POST /map/site-projections` acepta, además de los parámetros de [Cómo se proyecta un sitio](#cómo-se-proyecta-un-sitio), `as_of`
y `target_at`: proyecta desde `as_of` y devuelve el nivel de cada sitio en
`target_at`, hasta 168 horas después. Es lo que usa la vista proyectada del mapa.

## Mapa de módulos

| Módulo | Qué hace |
| --- | --- |
| `app/services/site_projection/service.py` | Punto de entrada: nivel actual, historial y llamada al modelo de cada sitio. |
| `app/services/site_projection/history.py` | Historial de nivel de cada sitio, agrupado por intervalo. |
| `app/services/site_projection/collection_cycle.py` | Detección de recolecciones. |
| `app/services/site_projection/generation.py` | Generación de residuos, sin las bajadas por recolección. |
| `app/services/site_projection/baseline.py` | Modelo Baseline. |
| `app/services/site_projection/chronos.py` | Modelo Chronos-2 Small. |
| `app/services/site_projection/evaluation.py` | Evaluación y métricas. |
| `app/services/site_projection/models.py` | Interfaz común y registro de modelos. |
| `app/routers/map/site_projections.py` | Endpoints. |
