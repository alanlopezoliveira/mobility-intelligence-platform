# 04. Diseño del análisis y estrategia de modelado

## 1. Problema y utilidad

Una persona que analiza movilidad necesita distinguir patrones habituales de cambios puntuales y saber cuánto puede anticiparse la actividad. El resultado útil será explorar esos patrones y estimar salidas por estación para una hora que comienza 60 o 120 minutos después de emitir la predicción. Servirá para estudiar la demanda registrada, no para decidir dónde habrá bicicletas disponibles.

## 2. Análisis planteado

| Pregunta o hipótesis | Análisis y utilidad |
|---|---|
| ¿Se repiten horas punta y patrones semanales? | Comparar actividad por hora, día y mes; orientar las variables del modelo |
| ¿Todas las estaciones se comportan igual? | Comparar actividad y cobertura; localizar casos poco representados |
| ¿La lluvia se asocia con menos viajes? | Comparar horas secas y lluviosas del mismo mes, hora y tipo de día; evitar atribuir diferencias solo al calendario |
| ¿Cuándo falla más el modelo? | Revisar errores por estación, periodo, horizonte y horas punta; mostrar sus límites |

Antes de modelar se revisará la cobertura; durante la comparación, los errores; después, los casos extremos. El MVP mostrará tendencias, comparaciones y predicciones frente a observaciones. La relación con la lluvia será descriptiva: no demuestra causalidad.

## 3. Modelos previstos

La tarea es predecir un recuento horario a partir del pasado.

| Alternativa | Ventaja | Limitación |
|---|---|---|
| Referencias: última hora terminada, misma hora del día o semana anterior | Fáciles de entender; permiten medir una mejora real | Responden mal a cambios de patrón |
| Regresión Ridge | Sencilla, rápida y relativamente interpretable | Capta peor relaciones complejas |
| Árbol y conjuntos de árboles, como Random Forest y boosting | Captan diferencias entre estaciones y patrones no lineales | Más coste y mayor riesgo de ajustarse demasiado al pasado |

El repositorio ya contiene una [comparación de siete familias y tres referencias](../model-comparison.md). Esta entrega resume el criterio, sin asumir que un algoritmo será siempre el mejor.

## 4. Datos de entrada

Se parte de `data/rebuilt/station-hourly.csv.gz` y de las estaciones seleccionadas según la [entrega 3](03_modelo_datos.md). Cada ejemplo identifica una estación, un instante de emisión y una hora objetivo; su clave es estación, emisión y horizonte.

Las entradas son estación, calendario de la hora objetivo, recuentos de horas ya terminadas, misma hora del día y semana anteriores, y resúmenes de las últimas 24 horas y siete días. Solo se usan valores disponibles al emitir la predicción. Se excluyen identificadores personales, información de la hora futura y meteorología histórica reconstruida después de los hechos.

## 5. Salidas y consumo

| Salida | Tipo y significado |
|---|---|
| Estación, emisión, hora objetivo y horizonte | Identificador, fechas y minutos; sitúan cada estimación |
| Predicción | Decimal no negativo; salidas esperadas durante la hora objetivo |
| Observación y error | Recuento real y diferencia; permiten revisar el resultado histórico |
| Modelo y fecha de generación | Texto y fecha; permiten rastrear la ejecución |

Las predicciones se publican como JSON para la web, con el informe de ejecución como referencia. La persona puede comparar estaciones y periodos y valorar dónde funciona mejor el modelo. Se mostrarán el periodo evaluado, número de casos y error medio; no se presentará un intervalo de confianza sin haberlo validado.

## 6. Selección del modelo

Se preparan ejemplos con historia suficiente y se comparan las alternativas sobre las mismas filas. Las transformaciones, como convertir categorías en entradas numéricas o ajustar escalas, se aprenden solo con entrenamiento. Las ventanas incompletas se excluyen.

Se prioriza el menor error absoluto medio en validación. Entre candidatos a menos del 1% del mejor, se prefiere la familia más sencilla y después el archivo de modelo más pequeño. También se revisan estabilidad, tiempo de ejecución y utilidad. La prueba final no decide el ganador.

## 7. Validación y aceptación

| Elemento | Decisión |
|---|---|
| Separación temporal de 2022 | Enero–agosto para aprender; septiembre–octubre para elegir; noviembre–diciembre para evaluar |
| Evitar información futura | Dejar separación en los límites y excluir ejemplos cuyo objetivo cruce al siguiente bloque |
| Métricas | MAE: error medio en salidas por estación-hora; RMSE: da más peso a errores grandes; cobertura: cuántos casos se evalúan |
| Comparación | Mismas filas para modelos y referencias, por separado para +60 y +120 minutos |
| Aceptación propuesta | Mejorar al menos un 5% el MAE de la mejor referencia en validación y revisar que la ventaja se mantiene en prueba; si falla, conservar la referencia y el análisis |

El 5% es un criterio propuesto para el MVP, distinto de la regla de empate del 1%. Se revisarán errores por estación y periodo, sin ocultar los casos difíciles. Las mismas estaciones pueden aparecer en distintos bloques: se evalúa su futuro, no estaciones nuevas. La prueba histórica ya se ha consultado en iteraciones anteriores; no equivale a una validación nueva en uso real.

## 8. Riesgos y alternativas

Las salidas están disponibles, pero representan viajes registrados y no toda la demanda potencial. Un año aporta suficientes ejemplos para experimentar, aunque limita el estudio de cambios entre años. Seleccionar estaciones presentes todo el año introduce un sesgo y reduce la generalización.

La principal incertidumbre es trasladar los resultados a otros periodos. Si ningún modelo mejora la referencia o la cobertura es insuficiente, el producto mantendrá comparaciones históricas y explicará la limitación, sin presentar predicciones operativas.
