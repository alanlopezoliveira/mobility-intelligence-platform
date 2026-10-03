# 03. Modelo de datos y capa gold

## 1. Resumen

MobilityLab estudia viajes históricos de BiciMAD y estima salidas por estación. Los viajes oficiales aportan estaciones, horas y movimientos; Open-Meteo aporta el contexto meteorológico. El alcance es 2022.

## 2. Almacenamiento elegido

Se conservarán los ZIP originales y se trabajará con CSV, comprimidos cuando sean grandes. Son formatos fáciles de revisar y suficientes para este volumen. La web recibirá resúmenes en JSON para funcionar sin consultar una base de datos. PostgreSQL permanece en la vía de investigación anterior, pero no es necesario para el MVP actual.

## 3. Capas de datos

| Capa | Ubicación desde la raíz | Contenido |
|---|---|---|
| Originales, equivalente a raw | `data/bronze/` | Archivos de viajes y respuestas meteorológicas sin alterar |
| Preparación, equivalente a processed | `data/rebuilt/cache/` | Resultados mensuales limpios y reutilizables |
| Final, equivalente a gold | `data/rebuilt/` | Tablas descritas abajo, listas para análisis y modelado |
| Publicación | `frontend/public/project-data/` | Resúmenes y predicciones que consume la web |

Se mantienen las rutas de la implementación actual para no duplicar datos. Estos archivos se generan localmente y están excluidos de Git. La documentación anterior sobre `data/gold/` corresponde a otra vía del proyecto; el contrato de esta entrega sigue la [reconstrucción actual](../rebuilt-project.md).

## 4. Definición de la capa gold

Todos los ficheros de esta tabla se encuentran en `data/rebuilt/`. Los volúmenes son aproximaciones de la ejecución local revisada.

| Fichero | Una fila representa / clave | Tamaño | Campos principales y uso |
|---|---|---|---|
| `station-hourly.csv.gz` | Estación y hora observada / `station` + `time` | 1,64 millones | `departures`, `arrivals`; análisis y base del modelo |
| `stations.csv` | Un identificador de estación / `station` | 278 | `name`, `latitude`, `longitude`, `latitude_span`, `longitude_span`; etiquetas y selección de estaciones |
| `network-weather-hourly.csv` | Una hora de la red / `time` | 8.754 | Salidas, llegadas, temperatura, precipitación, lluvia, viento y calendario; análisis meteorológico |

El objetivo del modelo será el número de salidas durante una hora futura. Su conjunto de trabajo se deriva de la primera tabla: 83 estaciones presentes en los doce meses y con coordenadas suficientemente estables, unas 727.000 filas antes de preparar las variables históricas.

## 5. Relaciones

Una estación tiene muchas horas de actividad (1:N). Los viajes se agrupan por estación y hora; después se suman por hora para obtener el total de la red. Este total se cruza con meteorología por `time` (1:1). Una misma hora meteorológica puede corresponder a muchas estaciones (1:N), aunque el análisis de lluvia utiliza el total de la red.

Los cruces usan UTC. La lluvia se alinea con el intervalo que representa, y las comparaciones de calendario usan la hora de Madrid. Un identificador repetido no demuestra que una estación conserve siempre la misma ubicación.

## 6. Diccionario inicial

| Campo | Significado y tipo | Fuente | Obligatorio / observación |
|---|---|---|---|
| `station` | Identificador; entero | Viajes | Sí; no identifica a personas |
| `time` | Inicio de hora; fecha y hora UTC | Viajes / meteorología | Sí; clave temporal |
| `departures`, `arrivals` | Salidas y llegadas; enteros | Viajes agregados | Sí; valores no negativos |
| `name` | Etiqueta; texto | Metadatos de estación | No; se puede mostrar el identificador |
| `latitude`, `longitude` | Coordenadas; decimales | Orígenes de viajes | Sí para seleccionar estaciones estables; grados |
| `latitude_span`, `longitude_span` | Variación de coordenadas; decimales | Cálculo del proyecto | Sí para esa selección; grados |
| `temperature_2m` | Temperatura; decimal | ERA5 | Para análisis meteorológico; °C |
| `precipitation`, `rain` | Precipitación total y lluvia; decimales | ERA5 | Para análisis meteorológico; mm |
| `wind_speed_10m` | Viento; decimal | ERA5 | Complementario; km/h |
| `month`, `hour`, `weekend`, `wet` | Mes y hora; enteros. Fin de semana y lluvia; booleanos | Fecha / lluvia | Para comparaciones; `wet` significa lluvia ≥ 0,1 mm |

## 7. Calidad esperada

Los riesgos concretos son viajes duplicados, estaciones sin coordenadas, cambios de ubicación, fechas inválidas y horas ambiguas por el cambio de hora. Faltan seis horas de cobertura de red en 2022. La meteorología representa una zona amplia y los viajes no permiten distinguir siempre clientes de movimientos del operador.

## 8. Limpieza y transformación

Se eliminan duplicados exactos y se registran los descartes. Cada salida y llegada se valida por separado; una llegada válida puede conservarse aunque falte la salida. Se rechazan fechas irresolubles y llegadas anteriores a la salida. No se inventan coordenadas ni recuentos ausentes.

Para el modelo, solo en horas con salidas registradas en la red se completan combinaciones estación-hora con cero: significa **ningún viaje registrado**, sin confirmar que la estación estuviera operativa. Las horas sin cobertura siguen siendo desconocidas. Se calculan calendario y resúmenes de horas ya terminadas; las ventanas incompletas se excluyen.

## 9. Riesgos y alternativa

Los recuentos horarios están bien definidos; la mayor incertidumbre es la continuidad de las estaciones y la cobertura real de los archivos. Si no puede construirse un conjunto fiable por estación, se simplificará a totales de red y análisis descriptivo, conservando las limitaciones visibles.
