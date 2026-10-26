# Redistribución de contenedores

El módulo de redistribución recomienda trasladar contenedores entre sitios
existentes, para optimizar la capacidad instalada a la utilización de cada sitio.
La cantidad total de contenedores se conserva, es decir, cada contenedor que recibe un
sitio sale de otro. Por eso cada movimiento se evalúa por lo que mejora el sitio
receptor y por lo que empeora el donante.

Los datos que usa (sitios, telemetría, capacidad por tramo y reloj de referencia)
se explican en [DATOS.md](DATOS.md).

## Enfoques y estrategias

Hay dos enfoques, cada uno con una estrategia greedy y otra que considera todas
las asignaciones posibles a la vez. Se eligen con el parámetro `algorithm` del
plan:

| Estrategia | Enfoque | Objetivo | `algorithm` | Cómo elige |
| --- | --- | --- | --- | --- |
| Score Greedy | Índice de utilización | Equilibrar la utilización | `greedy` (por defecto) | Prioridad por índice. |
| Score LP | Índice de utilización | Equilibrar la utilización | `lp` | Programación lineal. |
| Sobrecarga Greedy | Horas de sobrecarga | Reducir las horas críticas y de desborde | `overload` | Mayor beneficio neto. |
| Sobrecarga MIP | Horas de sobrecarga | Reducir las horas críticas y de desborde | `overload_mip` | Programación entera mixta. |

Los cuatro devuelven un plan con el mismo formato: una lista de movimientos, cada
uno con el contenedor, el sitio de origen, el de destino y la distancia entre
ellos. El plan se puede inspeccionar en el mapa y probar en el
[What-If](#what-if).

## Restricciones

Todos los planes:

- conservan la cantidad total de contenedores.
- mueven contenedores solo entre sitios compatibles: mismo tipo de residuo y de
  contenedor.
- respetan una distancia máxima entre sitios (`max_distance_km`, 2 km por
  defecto), medida en línea recta, no por calle.
- dejan al menos un contenedor en cada donante.
- con `apply_capacity_constraints` (activado por defecto), no hacen que un sitio
  supere la capacidad calculada de su tramo de calle (ver
  [Capacidad por tramo de calle](DATOS.md#capacidad-por-tramo-de-calle)).

## Enfoque por índice de utilización

### Índice

Resume la historia de cada sitio con tres indicadores, sobre los últimos
`window_days` días (todo el historial disponible si no se indica):

- **Llenado medio** ($\bar{L}_s$): el promedio de sus lecturas.
- **Proporción de lecturas elevadas** ($P_s$): la fracción de lecturas en 80 % o
  más.
- **Lecturas de saturación** ($N_{100,s}$): la cantidad de lecturas en 100 %, que
  no son necesariamente episodios independientes.

Se combinan en un índice entre 0 y 1:

$$U_s = 0{,}4 \cdot \frac{\bar{L}_s}{100} + 0{,}3 \cdot P_s + 0{,}3 \cdot \min\left(\frac{N_{100,s}}{50}, 1\right)$$

Las ponderaciones son parámetros de diseño. Según el índice, cada sitio se
clasifica como `critical` (0,85 o más), `high` (0,65 o más), `normal` (0,35 o
más), `low` (0,15 o más) o `idle`.

### Donantes, receptores, oferta y demanda

- **Donantes:** sitios con índice menor que `min_utilization_donor` (0,35 por
  defecto).
- **Receptores:** sitios con índice de `min_utilization_receiver` o más (0,65 por
  defecto).
- **Oferta de un donante:** cuántos contenedores puede ceder conservando al menos
  uno, y sin que su llenado medio estimado supere un límite de seguridad
  (`target_utilization` × 100 + 10 %, acotado entre 50 % y 75 %, con el objetivo
  por defecto, 60 %).
- **Demanda de un receptor:** cuántos contenedores necesita para acercarse a
  `target_utilization` (0,50 por defecto), limitada por el espacio libre en su
  tramo de calle.

Los umbrales tienen que cumplir donante < objetivo < receptor. Así ningún sitio
es donante y receptor a la vez, y el plan no arma cadenas.

### Estrategia greedy

Ordena los receptores por índice, de mayor a menor, y atiende primero a los más
exigidos. Para cada uno busca donantes compatibles dentro de la distancia máxima,
del más cercano al más lejano, y asigna contenedores hasta cubrir su demanda o
agotar la oferta. Es simple y rapido pero el orden puede consumir capacidad que les
serviría a otros receptores, por ende no es optimo.

### Programación lineal

Plantea un problema de transporte: cada variable es la cantidad de contenedores
que pasan de un donante a un receptor compatible. Minimiza la distancia menos un
beneficio proporcional al índice del receptor, con un peso grande para que
atender a un receptor exigido siempre convenga más que no mover. Las
restricciones limitan la oferta de cada donante y la demanda de cada receptor. A
diferencia del greedy, considera todas las asignaciones a la vez y halla el óptimo.

Las dos estrategias dependen del mismo resumen histórico, y por lo tanto de la
definición del índice y de cómo se estiman la oferta y la demanda.

## Enfoque por horas de sobrecarga

### Modelo

En lugar de resumir la historia en promedios, el enfoque reconstruye hora por
hora el residuo acumulado en cada sitio desde la última recolección, y estima qué
habría pasado con otra cantidad de contenedores. Por ejemplo, tres contenedores
con un llenado medio de 90 % equivalen a 2,7 contenedores completos. Con cuatro
contenedores y reparto parejo, el llenado medio habría sido 67,5 %.

- Se conservan los horarios reales de recolección del sitio.
- Usa los últimos `window_days` días (28 por defecto), en intervalos de una hora.
- Cuando las lecturas quedan saturadas, el sensor no muestra lo que se sigue
  generando: se estima con la velocidad de llenado de las horas anteriores.

Para cada cantidad de contenedores $c$ se cuentan las horas críticas (nivel entre
el umbral crítico y el 100 %) y las de desborde (100 % o más). El costo del sitio
es:

$$C_s(c) = H_{\text{crítico},s}(c) + \lambda \cdot H_{\text{desborde},s}(c)$$

con $\lambda = 3$, para penalizar más la saturación completa.

### Penalización por traslado

Un movimiento solo se hace si ahorra al menos `move_cost_hours_per_week` horas de
sobrecarga por semana (20 por defecto), para evitar movimientos marginales que
representan un costo innecesario. Con 0 se hacen todos los movimientos que mejoran algo.

### Estrategia greedy

Calcula el beneficio neto de cada traslado posible: lo que baja el costo del
receptor menos lo que sube el del donante. En cada paso hace el de mayor
beneficio, actualiza la cantidad de contenedores de los dos sitios y recalcula
los traslados afectados. Termina cuando ningún movimiento supera la penalización
por traslado. Ante empates, prioriza la menor distancia.

El primer movimiento de cada sitio fija su rol: un sitio que recibió no dona, y
uno que donó no recibe. Así el plan no arma cadenas (A → B → C).

### Programación entera mixta (MIP)

Minimiza el costo total de la red más la penalización por los traslados. Los
efectos de agregar o quitar contenedores se representan con una aproximación: el
beneficio de cada contenedor que recibe un sitio es decreciente, y el perjuicio
de cada uno que cede es creciente. Variables binarias impiden que un sitio sea
donante y receptor a la vez.

- Un receptor sin capacidad conocida puede recibir hasta 20 contenedores.
- Cada traslado suma al costo una fracción mínima de su distancia (0,001 por km).
  No cambia qué movimientos convienen, pero a igual mejora prefiere el donante más
  cercano, como el greedy.
- El resolvedor (HiGHS) tiene un límite de 300 segundos. Si llega al límite, se
  usa la mejor solución factible encontrada, sin garantía de que sea óptima. Si
  por alguna razon no encuentra ninguna, el plan se arma con el greedy.

## What-If

El What-If muestra la red como si el plan ya se hubiera aplicado. Solo puede haber
un plan con el What-If activo a la vez.

### Al activarlo

Cada contenedor arranca en su sitio real y con su nivel real. Los que el plan
mueve pasan al sitio receptor y llegan vacíos.

### Regla de reparto

Los sensores siguen midiendo la red real. Cada lectura real de un contenedor $r$
en su sitio real $S$ es residuo que llegó a $S$, y en el What-If se reparte entre
los contenedores que $S$ tendría con el plan. Si $K$ son los contenedores que se
quedan en $S$, $A$ los que llegan según el plan, y $\Delta$ lo que subió la
lectura:

| Caso | Qué recibe cada contenedor virtual |
| --- | --- |
| $r$ se queda en $S$ | $r$ recibe $\Delta \cdot \lvert K \rvert / (\lvert K \rvert + \lvert A \rvert)$, y cada contenedor de $A$ recibe $\Delta / (\lvert K \rvert + \lvert A \rvert)$. |
| $r$ se va a otro sitio | Su sensor sigue midiendo en $S$: $\Delta$ se reparte entre los de $K$. |
| $S$ no participa del plan | El nivel virtual sigue exactamente al real. |

Cuando el contenedor real queda lleno, el sensor no muestra todo lo que llegó: se
completa con su velocidad reciente de llenado. Cuando el camión pasa por $S$, los
contenedores virtuales de $S$ se vacían en la misma pasada.

Las lecturas llegan al What-If desde la ingesta de telemetría. Al crear una
simulación, los niveles virtuales vuelven a 0, igual que los reales.

### Indicadores

La comparación distingue el impacto estimado al generar el plan del
comportamiento registrado desde su activación:

- **Al generar el plan:** la dispersión del llenado (desvío estándar de los
  niveles medios de los sitios, ponderado por su cantidad de contenedores), antes
  y después del plan. Para el enfoque por sobrecarga, también las horas
  históricas estimadas en estado crítico y en desborde con las dos
  distribuciones.
- **Desde la activación, real contra What-If:** el promedio temporal de sitios
  críticos y de contenedores llenos, y las horas acumuladas de contenedores
  llenos. Los promedios temporales ponderan cada estado por su duración: diez
  sitios críticos durante una hora pesan menos que los mismos diez durante seis.

El What-If aplica la regla de reparto sobre las mismas lecturas. No recalcula
rutas de recolección ni modela cambios en el comportamiento de los generadores de
residuos ante la nueva distribución.

## Parámetros del plan

| Parámetro | Default | Uso |
| --- | --- | --- |
| `algorithm` | `greedy` | Enfoque y estrategia (ver [Enfoques y estrategias](#enfoques-y-estrategias)). |
| `window_days` | todo el historial (índice), 28 (sobrecarga) | Días de historia que se analizan. |
| `max_distance_km` | `2.0` | Distancia máxima entre donante y receptor. |
| `apply_capacity_constraints` | `true` | Limita la recepción a la capacidad del tramo de calle. |
| `min_utilization_donor` | `0.35` | Índice máximo para ser donante (solo índice de utilización). |
| `min_utilization_receiver` | `0.65` | Índice mínimo para ser receptor (solo índice de utilización). |
| `target_utilization` | `0.50` | Utilización objetivo (solo índice de utilización). |
| `move_cost_hours_per_week` | `20` | Horas de sobrecarga por semana que tiene que ahorrar un movimiento (solo sobrecarga). |

## Endpoints

Todos bajo `/map/optimization`, con token. El detalle está en `/docs`.

| Método | Ruta | Qué hace |
| --- | --- | --- |
| `GET` | `/metrics` | Métricas de utilización de todos los sitios. |
| `POST` | `/plan` | Genera un plan de redistribución. |
| `GET` | `/plan/active` | Plan con What-If activo, o `null`. |
| `GET` | `/plan/{plan_id}` | Un plan por id. |
| `POST` | `/plan/{plan_id}/whatif` | Activa el What-If de un plan. |
| `DELETE` | `/plan/{plan_id}/whatif` | Desactiva el What-If de un plan. |
| `GET` | `/plan/{plan_id}/comparison` | Comparación real contra What-If. |

## Mapa de módulos

| Módulo | Qué hace |
| --- | --- |
| `app/services/map/optimization_service.py` | Métricas por sitio y enfoque por índice de utilización (greedy y LP). |
| `app/services/map/redistribution_overload.py` | Modelo de sobrecarga: reconstrucción del acumulado y costo por cantidad de contenedores. |
| `app/services/map/redistribution_overload_service.py` | Arma los perfiles de sobrecarga, agrupa sitios compatibles y llama a la estrategia. |
| `app/services/map/redistribution_overload_planner.py` | Greedy del enfoque por sobrecarga. |
| `app/services/map/redistribution_overload_mip.py` | MIP del enfoque por sobrecarga. |
| `app/services/map/optimization_whatif_service.py` | Activación del What-If, niveles virtuales e indicadores. |
| `app/services/map/whatif_allocation.py` | Regla de reparto de las lecturas en el What-If. |
| `app/services/map/site_capacity_service.py` | Capacidad de cada sitio según su tramo de calle. |
| `app/routers/map/optimization.py` | Endpoints. |
