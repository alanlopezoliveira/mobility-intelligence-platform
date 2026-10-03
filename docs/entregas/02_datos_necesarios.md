# 02. Selección de idea y datos necesarios

## 1. Idea seleccionada

**Problema.** Los viajes de BiciMAD cambian según la hora, el día y la estación. Una persona que estudia la movilidad necesita entender estos patrones y valorar cuánto puede anticiparse la actividad, sin revisar miles de registros por separado.

**Solución.** MobilityLab reúne los viajes históricos y el tiempo meteorológico de Madrid en una aplicación visual. Permite explorar la actividad, comparar periodos y estudiar si un modelo estima las salidas mejor que una regla sencilla basada en el pasado.

**MVP.** El producto mínimo será una web con gráficos de actividad, una comparación entre horas secas y lluviosas y un simulador histórico de salidas a 60 y 120 minutos. Mostrará predicción y resultado observado. Su alcance es académico: no ofrece disponibilidad actual de bicicletas ni decisiones automáticas de redistribución.

## 2. Datos necesarios

| Datos | Detalle necesario | Prioridad |
|---|---|---|
| Viajes | Estaciones y fechas de salida y llegada | Imprescindible |
| Estaciones | Identificador y coordenadas; nombre para facilitar la consulta | Imprescindible; nombre deseable |
| Calendario | Hora, día de la semana y mes, calculados desde la fecha | Imprescindible |
| Tiempo meteorológico | Lluvia, temperatura y viento por hora para Madrid | Deseable para ampliar el análisis |

La unidad principal será **una estación durante una hora**. Un año completo permite comparar meses y días de la semana. Se elige 2022 por disponer de viajes con fechas precisas y evitar mezclar formatos de otros años. El volumen local verificado ronda 4,1 millones de salidas y 1,6 millones de registros estación-hora observados: suficiente para un proyecto acotado.

## 3. Fuentes previstas

| Fuente y acceso | Formato e histórico | Riesgos |
|---|---|---|
| [Históricos oficiales BiciMAD](https://datos.madrid.es/dataset/900034-0-bicimad-viajes-estaciones), mediante la [distribución 2022](https://media.emtmadrid.es/-uHaW6iZkhG) | Descarga pública; ZIP con CSV mensuales de viajes | Enlaces cambiantes, duplicados, fechas incompletas y cambios de identificador |
| [Open-Meteo, API de tiempo histórico](https://open-meteo.com/en/docs/historical-weather-api) | JSON horario; estimaciones históricas ERA5 para 2022 | Una celda representa Madrid; no mide la lluvia en cada estación |

Son las fuentes utilizadas por el proyecto y tienen documentación pública. Se guardarán copias locales y referencias de origen; su disponibilidad futura deberá comprobarse al descargar. El MVP no necesita datos en directo ni una cuenta de pago.

## 4. Privacidad y protección de datos

El análisis necesita recuentos, no identificar personas. Se excluirán identificadores de usuarios y trayectos individuales de la web y de los resultados compartidos. Las coordenadas publicadas describirán estaciones. Los archivos originales se mantendrán fuera del repositorio.

Antes de redistribuir datos se revisarán sus condiciones de uso y atribución. El carácter académico no elimina estas obligaciones ni el riesgo de identificar hábitos mediante trayectos detallados.

## 5. Viabilidad inicial

La obtención y preparación de los datos ya están demostradas localmente. El detalle horario y un año de histórico permiten un MVP realista, aunque no garantizan representar todos los usos o años. El principal riesgo es confundir ausencia de registros con falta de actividad o disponibilidad de bicicletas.

Si falla la descarga, se utilizarán las copias locales con su procedencia registrada. Si falta meteorología, se mantendrá el análisis de viajes; si los datos no permiten validar predicciones, se entregará el explorador descriptivo.
