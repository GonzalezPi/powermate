# SolarPredict AI

Predicción de la generación de una instalación solar fotovoltaica a partir del
clima, usando **Open-Meteo** y **regresión lineal** en Python.

El proyecto descarga el pronóstico horario, construye variables físicas
(irradiancia sobre el plano del panel, temperatura de célula, geometría solar,
índice de claridad, viento, nubes…) y aplica un modelo de regresión lineal
entrenado para estimar la potencia y la energía que producirán los paneles.

```
Open-Meteo ──► variables meteorológicas ──► características físicas ──► regresión lineal ──► W / kWh
                                                                              │
                                                       gemelo digital físico ┘ (entrenamiento)
```

---

## 1. Puesta en marcha

Requisitos: Python 3.10 o superior.

```bash
cd ClarolabPaneles
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1) Entrenar con 2 años de histórico real de Open-Meteo (Madrid, 3 kWp por defecto)
python main.py train

# 2) Predecir los próximos 7 días
python main.py predict

# 3) Generar un informe HTML visual y exportar CSV/JSON
python main.py predict --days 7 --html reports/forecast.html \
  --csv reports/forecast.csv --json reports/forecast.json
```

La primera ejecución de `predict` entrena automáticamente el modelo si no
existe. También puedes usar el paquete instalado:

```bash
pip install -e .
solar-ai train
solar-ai predict --html
```

### Ejemplo de salida

```
Pronóstico: 10/11/2026 00:00 → 16/11/2026 23:00 (7 días)
  Total:          30.93 kWh · pico 1.55 kW (12/11 15:00) · factor de capacidad 6.1%
  Hoy:            2.63 kWh (día completo) · restante 1.14 kWh · mañana 0.82 kWh
  Modelo:         R² 0.99579 · MAE 34.19 W (1.14% de la pico) · incertidumbre ±97 W

Resumen diario:
Fecha        kWh  Pico kW  Hora   GTI máx  T máx  Nubes
----------  ----  -------  -----  -------  -----  -----
2026-11-10  2.63     0.76  15:00      282   17.6   100%
2026-11-11  0.82     0.13  14:00       45   14.9   100%
2026-11-12  8.92     1.55  15:00      587   15.6    68%
...
```

El informe HTML (`reports/forecast.html`) incluye tarjetas de resumen, gráfico
SVG de potencia, tabla diaria y detalle horario. No necesita Internet ni
dependencias externas para visualizarse.

---

## 2. Cómo funciona

### 2.1 Datos de Open-Meteo

Se solicitan por hora:

| Variable | Uso |
|---|---|
| `global_tilted_irradiance` | Irradiancia sobre el plano inclinado del panel (variable principal) |
| `shortwave_radiation` | Radiación global horizontal |
| `direct_normal_irradiance`, `diffuse_radiation` | Reparto directo/difuso |
| `temperature_2m` | Temperatura ambiente |
| `wind_speed_10m` | Refrigeración del módulo |
| `cloud_cover` | Nubosidad |
| `relative_humidity_2m` | Humedad |
| `snow_depth` | Pérdida por nieve |
| `precipitation`, `weather_code`, `is_day` | Contexto y filtrado |

Open-Meteo calcula `global_tilted_irradiance` para la inclinación y el azimut
del panel, lo que evita tener que estimar la transposición de forma casera.

### 2.2 Características físicas

Un modelo lineal solo es potente si las variables ya contienen la física. Por
eso se construyen, entre otras:

- `gti_temp = (GTI/1000) · (1 + γ·(T_célula − 25))`: el término físico central.
- `gti_temp_sq`: curvatura por saturación y recorte del inversor.
- `gti_cloud`, `gti_wind`, `gti_hum`, `gti_cosz`: interacciones con nubes,
  viento, humedad y geometría solar.
- `clearness`, `diffuse_fraction`, `direct_fraction`: índices de claridad.
- `cos_zenith`, `elevation_norm`, `hour_sin/cos`, `doy_sin/cos`: geometría y
  estacionalidad.
- `snow_depth_cm`.

La temperatura de célula usa el modelo **SAM de pvlib**, escalado por el NOCT
del módulo:

```
T_célula = T_ambiente + GTI · exp(−3.56 − 0.075 · viento) · escala
```

donde la escala hace que, con 800 W/m² y 1 m/s, el incremento sea `NOCT − 20`.

La potencia DC incorpora después el coeficiente de temperatura, la suciedad, el
cableado, el desacoplamiento y el recorte (clipping) del inversor.

### 2.3 Entrenamiento

Para poder funcionar sin datos históricos de tu tejado, el proyecto entrena con
un **gemelo digital físico** alimentado por el clima histórico real de
Open-Meteo:

1. Descarga 2 años de clima horario (API de archivo).
2. Calcula la potencia que produciría la instalación configurada.
3. Añade ruido (2 %) y aleatoriza parámetros reales (suciedad, coeficiente
   térmico, eficiencia del inversor) para no aprender una curva artificialmente
   perfecta.
4. Ajusta `StandardScaler + LinearRegression` con una **partición cronológica**
   (80 % pasado / 20 % futuro) y validación cruzada temporal.

El resultado es un modelo que aprende la relación clima → potencia y puede
predecir en milisegundos.

### 2.4 Métricas del modelo incluido (Madrid, 3 kWp, 30°, sur)

| Métrica | Valor |
|---|---|
| R² (test cronológico) | **0.996** |
| MAE | **34.2 W** (1.14 % de la potencia pico) |
| RMSE | 49.5 W |
| MAPE | 2.6 % |
| CV temporal (5 folds) | R² 0.9963 · MAE 29.4 W |
| Línea base (solo GTI + temperatura) | R² 0.9910 · MAE 53.3 W |
| Error frente al gemelo físico nominal | **2.13 W** (0.07 % de la pico) |

El modelo reduce el error de la línea base naive un **36 %** y reproduce el
gemelo físico con un error medio de 2 W. La diferencia restante (34 W) es el
ruido y la variabilidad de parámetros introducidos a propósito.

> **Importante:** estas métricas son frente al gemelo digital, no frente a un
> tejado real. En una instalación real, el error dominante suele ser el del
> pronóstico meteorológico (especialmente las nubes). Para medir y mejorar la
> precisión real, calibra con tus datos de producción (sección 4).

---

## 3. Configuración

El archivo `config.json` controla ubicación, sistema y entrenamiento:

```json
{
  "site": {
    "name": "Madrid",
    "latitude": 40.4168,
    "longitude": -3.7038,
    "timezone": "auto"
  },
  "system": {
    "kwp": 3.0,
    "tilt": 30.0,
    "azimuth": 0.0,
    "ac_kwp": 3.0,
    "inverter_efficiency": 0.96,
    "temp_coefficient": -0.0035,
    "noct": 45.0,
    "soiling_loss": 0.03,
    "wiring_loss": 0.02,
    "mismatch_loss": 0.02,
    "snow_loss": true,
    "snow_zero_cm": 2.0
  },
  "training": {
    "years": 2.0,
    "test_fraction": 0.2,
    "random_state": 42,
    "domain_randomization": true,
    "poly": false
  }
}
```

Convención de azimut (la de Open-Meteo):

- `0` = sur
- `-90` = este
- `90` = oeste
- `±180` = norte

`tilt` es la inclinación respecto a la horizontal (0–90°). `ac_kwp` es el límite
del inversor; usa `null` o `--no-ac-limit` si no quieres recortar.

Cualquier parámetro se puede sobrescribir por CLI. Con `--save-config` se
persiste en `config.json`:

```bash
python main.py train --name Valencia --lat 39.4699 --lon -0.3763 \
  --kwp 5.5 --tilt 35 --azimuth 10 --ac-kwp 5 --save-config
```

El modelo se guarda en `data/models/model_<clave>.joblib`; la clave depende de
latitud, longitud, kWp, inclinación, azimut y potencia AC. Cambiar cualquiera de
ellos entrena/usa un modelo distinto.

---

## 4. Calibración con datos reales (recomendado)

Si tienes un CSV con la producción de tu instalación, `calibrate` sustituye el
objetivo sintético por tus mediciones reales:

```bash
python main.py calibrate --csv produccion.csv \
  --kwp 3.5 --tilt 30 --azimuth 0 --ac-kwp 3.5 \
  --power-unit kW --tz Europe/Madrid
```

El CSV debe tener una columna de fecha/hora y otra de potencia. Se autodetectan
nombres como `timestamp`, `datetime`, `fecha`, `hora`, `ac_power`, `power`,
`potencia`, `generacion`… También puedes indicarlas:

```bash
python main.py calibrate --csv produccion.csv \
  --time-col "Fecha y hora" --power-col "Potencia AC (kW)" --power-unit kW
```

Ejemplo mínimo:

```csv
timestamp,ac_power_w
2025-06-01T10:00:00Z,1250
2025-06-01T11:00:00Z,2100
```

o en formato español:

```csv
fecha,potencia
2025-06-01 12:00,1.25
2025-06-01 13:00,2.10
```

Notas:

- Si las fechas no llevan zona horaria, indica `--tz` (por defecto UTC).
- Los datos de 15 minutos se agregan a media horaria automáticamente.
- La unidad se detecta sola (`auto`): valores ≤ 100 se interpretan como kW.
- Se necesitan al menos **100 horas diurnas** coincidentes con el archivo de
  Open-Meteo. Lo ideal son varias semanas o meses.
- El modelo calibrado se guarda con la misma configuración de sistema; úsalo
  con `predict` sin cambiar kWp/tilt/azimut.

Tras calibrar, `info` muestra las métricas **reales** (R², MAE, RMSE, MAPE) y la
comparación con la línea base.

---

## 5. Comandos

| Comando | Descripción |
|---|---|
| `python main.py train` | Entrena con histórico de Open-Meteo |
| `python main.py predict` | Pronóstico de 7 días (1–16) |
| `python main.py predict --days 3 --html` | Pronóstico corto + informe HTML |
| `python main.py predict --csv out.csv --json out.json` | Exportaciones |
| `python main.py calibrate --csv produccion.csv` | Recalibra con datos reales |
| `python main.py info` | Configuración, métricas y ruta del modelo |

Opciones útiles de `predict`:

```bash
--days 7              # horizonte (1-16)
--model ruta.joblib   # usar un modelo concreto
--no-auto-train       # fallar si no hay modelo en vez de entrenar
--html [ruta]         # informe visual autocontenido
```

Opciones comunes: `--lat`, `--lon`, `--kwp`, `--tilt`, `--azimuth`,
`--ac-kwp`, `--no-ac-limit`, `--timezone`, `--name`, `--config`,
`--save-config`, `--verbose`.

---

## 6. Estructura del proyecto

```
ClarolabPaneles/
├── main.py                  # punto de entrada
├── config.json              # configuración de la instalación
├── requirements.txt
├── pyproject.toml
├── solar_ai/
│   ├── config.py            # dataclasses y validación
│   ├── solar.py             # posición solar NOAA, masa de aire, cielo claro
│   ├── physics.py           # gemelo digital (temperatura, pérdidas, clipping)
│   ├── weather.py           # cliente Open-Meteo + caché
│   ├── features.py          # características físicas
│   ├── model.py             # regresión, métricas, persistencia
│   ├── train.py             # dataset de entrenamiento
│   ├── calibrate.py         # CSV real + clima histórico
│   ├── predict.py           # pronóstico y agregados
│   ├── report.py            # informe HTML/SVG
│   └── cli.py               # interfaz de comandos
├── tests/                   # 32 pruebas (pytest)
├── data/
│   ├── cache/               # respuestas de Open-Meteo comprimidas
│   └── models/              # modelos y métricas
└── reports/                 # informes y exportaciones
```

---

## 7. Proyectos de GitHub que inspiraron la precisión

Este proyecto es autocontenido (solo `numpy`, `pandas`, `scikit-learn`,
`requests`, `joblib`), pero reutiliza ideas y fórmulas de proyectos abiertos:

- **[pvlib-python](https://github.com/pvlib/pvlib-python)** — posición solar,
  temperatura de célula (modelo SAM), masa de aire y conceptos de modelado FV.
  Es la referencia de facto para simulación solar en Python.
- **[rany2/open-meteo-solar-forecast](https://github.com/rany2/open-meteo-solar-forecast)**
  — uso de `global_tilted_irradiance` de Open-Meteo con inclinación/azimut,
  recorte del inversor y pérdida por nieve. Confirmó que pedir la irradiancia
  ya transpuesta mejora mucho la precisión frente a calcularla a mano.
- **[openclimatefix/quartz-solar-forecast](https://github.com/openclimatefix/quartz-solar-forecast)**
  — enfoque de forecasting FV con geometría solar, índice de claridad y
  evaluación temporal.
- **[Open-Meteo](https://open-meteo.com/en/docs)** — API meteorológica e
  histórica, variables de radiación y documentación de `tilt`/`azimuth`.

Ideas aplicadas de ellos: irradiancia en el plano inclinado, temperatura de
célula dependiente del viento, recorte del inversor, índice de claridad,
validación cronológica y calibración con producción real.

---

## 8. Pruebas

```bash
pip install pytest
pytest
```

Cubren geometría solar, física, características, modelo, persistencia, parseo
de Open-Meteo, carga de CSV y un flujo completo offline de entrenamiento,
predicción y exportación.

---

## 9. Limitaciones y siguientes pasos

- El objetivo de entrenamiento por defecto es un gemelo digital, no mediciones
  de tu tejado. **Calibra con datos reales** para conocer el error real.
- El error del pronóstico de nubes de Open-Meteo es, con diferencia, la mayor
  fuente de incertidumbre en un día real.
- No se modelan sombras de horizonte, árboles o edificios. Se puede añadir un
  mapa de horizonte como en `open-meteo-solar-forecast`.
- Para producción comercial, revisa la licencia de Open-Meteo y usa su API de
  pago si corresponde.
- Mejoras posibles: usar la API de radiación por satélite para el pasado,
  ensembles para bandas de incertidumbre, datos de un sensor de irradiancia
  local, o un modelo híbrido (física + ML) para corregir sesgos.

---

## 10. Licencia y atribución

Código del proyecto: úsalo y modifícalo libremente dentro de tu organización.

Datos meteorológicos: [Open-Meteo](https://open-meteo.com/) (licencia
CC BY 4.0 para uso no comercial; consulta sus términos para uso comercial).
Las ideas y fórmulas citadas pertenecen a sus respectivos proyectos (pvlib,
rany2/open-meteo-solar-forecast, openclimatefix/quartz-solar-forecast), todos
con licencias abiertas.
