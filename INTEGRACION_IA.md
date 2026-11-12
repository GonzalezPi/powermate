# Integración de IA solar

Esta guía explica cómo la aplicación PHP de PowerMate predice la energía
diaria de la instalación fotovoltaica usando el modelo de Python de
`Model/`, y cómo ese modelo va aprendiendo con los datos que la web acumula
en MySQL.

## Qué hace

- Predice la **energía diaria (kWh)** y la **potencia horaria (W)** a partir
  del clima que ya está guardado en `Predicciones_Meteorologicas`.
- Guarda las predicciones en `Predicciones_Energia` (resumen diario) y
  `Predicciones_Energia_Horaria` (detalle por hora).
- Reentrena solo cuando aparecen suficientes mediciones nuevas de
  `Mediciones.Panel_solar`.
- Si todavía no hay mediciones reales, arranca con un **gemelo digital
  físico** entrenado sobre el clima almacenado. Open-Meteo histórico queda
  solo como último recurso.

## Arquitectura

```
Navegador / cron
      │
      ▼
PHP (endpoints y servicios)
      │  1. lee clima de MySQL
      │  2. ejecuta Model/bridge.py y le pasa JSON por stdin
      ▼
Python (solar_ai)
      │  - construye características físicas
      │  - entrena o predice
      ▼
JSON por stdout
      │
      ▼
PHP guarda la predicción en MySQL
```

PHP es el único que habla con MySQL. Python no necesita el driver de MySQL:
recibe y devuelve documentos JSON. Esto mantiene el modelo desacoplado y
permite probarlo por separado.

### Archivos principales

| Archivo | Función |
| --- | --- |
| `Model/bridge.py` | Punto de entrada que PHP ejecuta. |
| `Model/solar_ai/bridge.py` | Acciones `predict`, `train` e `info`. |
| `Model/solar_ai/predict.py` | Agregación diaria y resumen del pronóstico. |
| `services/SolarAI.php` | Lanza Python y traduce la configuración. |
| `services/RepositorioClima.php` | Lee/escribe clima y convierte local → UTC. |
| `services/RepositorioEnergia.php` | Persiste las predicciones. |
| `services/Aprendizaje.php` | Decide cuándo reentrenar y guarda el historial. |
| `services/Prediccion.php` | Orquesta reentrenamiento + predicción + guardado. |
| `config/ai.php` | Configuración del sitio, del sistema y de la IA. |
| `config/migracion_ai.php` | Migración idempotente de la base de datos. |

## Requisitos

- PHP 8.2 con `pdo_mysql` y `curl` (el de XAMPP sirve).
- Python 3.10 o superior.
- El entorno virtual del modelo:

```bash
cd Model
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Comprueba que la ruta configurada en `config/ai.php` apunta al intérprete
correcto:

```php
"python" => __DIR__ . "/../Model/.venv/bin/python",
"puente" => __DIR__ . "/../Model/bridge.py",
```

## Migración de la base de datos

Ejecuta una sola vez (es idempotente, se puede repetir sin riesgo):

```bash
/opt/lampp/bin/php config/migracion_ai.php
```

La migración:

1. Añade a `Predicciones_Meteorologicas` las columnas que necesita el modelo:
   humedad, nubosidad, viento, radiación global/directa/difusa/inclinada,
   nieve, probabilidad de precipitación, código de tiempo, indicador de día,
   origen y fecha de actualización.
2. Elimina duplicados por `Fecha_hora` y crea el índice único
   `uq_predicciones_fecha_hora`, necesario para el `UPSERT` del pronóstico.
3. Añade `idx_mediciones_fecha` a `Mediciones`.
4. Crea `Predicciones_Energia`.
5. Crea `Predicciones_Energia_Horaria`.
6. Crea `Historial_Entrenamiento`.

También se puede abrir `config/migracion_ai.php` desde el navegador.

### Tablas nuevas

| Tabla | Contenido |
| --- | --- |
| `Predicciones_Energia` | Una fila por día: energía, pico, confianza, modelo y métricas. |
| `Predicciones_Energia_Horaria` | Potencia y energía hora a hora. |
| `Historial_Entrenamiento` | Cada reentrenamiento con su origen, muestras y métricas. |

## Configuración

Toda la configuración está en `config/ai.php`. Para no modificar el archivo
versionado, crea `config/ai.local.php`; se fusiona con la configuración base:

```php
<?php
return [
    "sitio" => [
        "nombre" => "Junín",
        "latitud" => -34.5833,
        "longitud" => -60.95,
        "zona_horaria" => "America/Argentina/Buenos_Aires",
    ],
    "sistema" => [
        "kwp" => 3.0,
        "inclinacion" => 30.0,
        "azimut" => 0.0,
        "ac_kwp" => 3.0,
    ],
    "ia" => [
        "auto_entrenar" => true,
        "auto_reentrenar" => true,
        "min_nuevas_mediciones" => 48,
        "min_muestras_reales" => 100,
    ],
];
```

`azimut` sigue la convención de Open-Meteo: `0` = sur, `-90` = este,
`90` = oeste, `±180` = norte.

El modelo se guarda por combinación de coordenadas, potencia pico,
inclinación, azimut y potencia del inversor. Si cambias alguno de esos
valores, se genera una clave nueva y el modelo se entrena otra vez.

## Flujo de una predicción

`Prediccion::generar()` hace lo siguiente:

1. Comprueba si hay mediciones nuevas suficientes y, si las hay, reentrena.
   Un fallo al reentrenar no impide predecir con el modelo actual.
2. Lee de `Predicciones_Meteorologicas` las horas desde hoy que tengan
   radiación inclinada y global.
3. Ejecuta `Model/bridge.py` con `action = "predict"`.
4. Guarda el resultado diario y horario en MySQL, reemplazando predicciones
   futuras obsoletas.
5. Registra en `Historial_Entrenamiento` el modelo que se usó, si todavía no
   había ningún registro.

Las fechas de MySQL son **hora local** de la instalación. PHP las convierte a
UTC antes de enviarlas a Python, y vuelve a hora local al guardar el
resultado.

## Aprendizaje continuo

El puente elige la mejor fuente de entrenamiento disponible en este orden:

| Prioridad | Condición | Origen devuelto |
| --- | --- | --- |
| 1 | Hay al menos `min_muestras_reales` (100) horas diurnas con clima y producción real coincidentes. | `real_data` |
| 2 | El clima almacenado tiene al menos 200 horas diurnas. | `physics_db` |
| 3 | Hay al menos 100 horas totales (arranque con el primer pronóstico de 7 días). | `physics_db` con `bootstrap = true` |
| 4 | No hay clima útil en la base de datos. | `physics_archive` (Open-Meteo histórico) |

- El reentrenamiento automático se lanza cuando hay al menos
  `min_nuevas_mediciones` (48 por defecto) filas nuevas con
  `Mediciones.Panel_solar > 0` desde el último entrenamiento registrado.
- La calibración real necesita además 100 horas **diurnas** que coincidan en
  fecha/hora con el clima almacenado.
- `Mediciones.Panel_solar` se interpreta según
  `mediciones.unidad`: `auto` considera kW si el máximo es menor o igual a
  100, y W en caso contrario. La agregación por hora se configura con
  `mediciones.agregacion` (`sum`, `mean` o `max`).

El histórico completo de cada entrenamiento queda en
`Historial_Entrenamiento`, y el dashboard muestra las métricas del modelo
activo.

## Endpoints

Todos los endpoints responden JSON si se añade `?formato=json` o se envía la
cabecera `Accept: application/json`.

### Actualizar clima y predecir

```bash
curl -X POST "http://localhost/powermate-main/guardar_predicciones.php?formato=json"
```

Descarga el pronóstico de Open-Meteo, lo guarda con `UPSERT` y, si
`pronostico.predecir_tras_actualizar` está activo, genera la predicción.

### Generar la predicción

```bash
curl -X POST "http://localhost/powermate-main/predecir_energia.php?formato=json"
```

Usa únicamente el clima ya almacenado. Si no hay clima con radiación,
devuelve un error explicando que primero hay que actualizar el pronóstico.

### Reentrenar manualmente

```bash
curl -X POST "http://localhost/powermate-main/reentrenar_modelo.php?formato=json"
```

Fuerza un reentrenamiento y, si es posible, regenera la predicción.

### Estado del modelo

El dashboard (`index.php`) muestra el modelo activo, sus métricas y el
historial de entrenamientos.

## Tarea programada

`cron_actualizar.php` solo se puede ejecutar por consola. Actualiza el clima,
reentrena si corresponde y predice:

```bash
/opt/lampp/bin/php cron_actualizar.php
/opt/lampp/bin/php cron_actualizar.php --solo-predecir
/opt/lampp/bin/php cron_actualizar.php --sin-predecir
/opt/lampp/bin/php cron_actualizar.php --reentrenar --json
```

Ejemplo de cron cada hora:

```cron
0 * * * * /opt/lampp/bin/php /ruta/powermate-main/cron_actualizar.php >> /tmp/powermate.log 2>&1
```

El script usa un bloqueo en `/tmp/powermate_ia.lock` para evitar dos
ejecuciones simultáneas.

## Pruebas

Las pruebas del modelo no usan red ni tocan los modelos reales:

```bash
cd Model
env -u LD_LIBRARY_PATH -u LD_PRELOAD .venv/bin/python -m pytest -q
```

Incluyen pruebas del puente JSON en `Model/tests/test_bridge.py`:
entrenamiento inicial, calibración con mediciones reales, caída al gemelo
digital cuando faltan mediciones y validación de errores.

También puedes probar el puente directamente:

```bash
cd Model
echo '{"action":"info"}' | env -u LD_LIBRARY_PATH -u LD_PRELOAD .venv/bin/python bridge.py
```

## Diagnóstico

### Error de `libstdc++` o `CXXABI_1.3.9`

XAMPP exporta `/opt/lampp/lib` en `LD_LIBRARY_PATH`, y esa librería es
demasiado antigua para numpy/scikit-learn. `services/SolarAI.php` elimina
`LD_LIBRARY_PATH` y `LD_PRELOAD` antes de lanzar Python. Si ejecutas Python
desde la terminal, haz lo mismo:

```bash
env -u LD_LIBRARY_PATH -u LD_PRELOAD Model/.venv/bin/python ...
```

### El proceso web no puede escribir el modelo

El usuario de Apache necesita permiso sobre:

```bash
chmod -R a+rwX Model/data/cache Model/data/models
```

### `json_encode` devuelve decimales con 100 cifras

`config/bootstrap.php` ya aplica `ini_set("serialize_precision", "-1")`.
Mantén ese ajuste si agregas nuevos puntos de entrada.

### `php` del sistema no conecta a MySQL

Usa siempre el PHP de XAMPP:

```bash
/opt/lampp/bin/php ...
```

### Open-Meteo histórico devuelve HTTP 429 o tarda demasiado

El histórico solo se usa como último recurso. Con clima ya almacenado, el
modelo se entrena con `physics_db` sin salir a Internet. Si necesitas
reconstruir el histórico, vuelve a intentarlo más tarde o deja que la web
acumule pronósticos durante varios días.

### No hay mediciones reales

`Mediciones` puede estar vacía al principio. En ese caso el modelo funciona
con el gemelo digital y no se calibra con producción real hasta acumular al
menos 100 horas diurnas coincidentes. Es el comportamiento esperado.

## Operación en producción

- Protege los endpoints de actualización y reentrenamiento si la aplicación
  queda expuesta a Internet (autenticación o restricción por IP).
- Ejecuta la migración antes de desplegar los endpoints nuevos.
- Programa `cron_actualizar.php` cada hora para mantener el pronóstico y el
  aprendizaje al día.
- Revisa `/tmp/powermate.log` y el historial de entrenamientos para detectar
  fallos de Open-Meteo o cambios en las métricas.
