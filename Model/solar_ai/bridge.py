"""Puente JSON entre la aplicación PHP y el modelo de IA.

La aplicación web no ejecuta Python directamente: le envía un documento JSON
por la entrada estándar y recibe otro documento JSON por la salida estándar.
Así el modelo queda desacoplado del lenguaje de la web y puede probarse por
separado.

Acciones disponibles:

``predict``
    Recibe las horas de clima ya guardadas en MySQL y devuelve la potencia y
    la energía previstas (horaria y diaria).

``train``
    Reentrena el modelo. Si hay mediciones reales suficientes se calibra con
    ellas; si no, usa el gemelo digital físico sobre el clima almacenado; y
    como último recurso descarga el histórico de Open-Meteo.

``info``
    Devuelve la configuración y las métricas del modelo activo.

Formato de entrada (ejemplo)::

    {
      "action": "predict",
      "config": {
        "site":   {"name": "Junín", "latitude": -34.5833,
                   "longitude": -60.95,
                   "timezone": "America/Argentina/Buenos_Aires"},
        "system": {"kwp": 3.0, "tilt": 30.0, "azimuth": 0.0, "ac_kwp": 3.0},
        "training": {"years": 2.0, "test_fraction": 0.2}
      },
      "weather": [
        {"time_utc": "2026-11-12T03:00:00Z", "temperature_2m": 18.2,
         "relative_humidity_2m": 70, "cloud_cover": 20,
         "wind_speed_10m": 9.0, "shortwave_radiation": 0,
         "direct_normal_irradiance": 0, "diffuse_radiation": 0,
         "global_tilted_irradiance": 0, "snow_depth": 0,
         "precipitation": 0}
      ],
      "auto_train": true
    }
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .features import build_features, daylight_rows, feature_matrix
from .model import (
    load_artifact,
    predict_power,
    save_artifact,
    train_model,
)
from .physics import simulate_power
from .predict import aggregate_daily, build_summary
from .train import train, train_from_weather
from .utils import get_logger, now_utc, setup_logging

# Columnas meteorológicas que puede aportar la base de datos. Las que falten
# se rellenan con valores conservadores dentro de ``build_features``.
WEATHER_COLUMNS = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_10m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
    "snow_depth",
    "precipitation",
    "weather_code",
    "is_day",
]

# Mínimo de horas diurnas para que el gemelo digital tenga sentido.
MIN_PHYSICS_ROWS = 200
# Mínimo de horas totales para un primer modelo con el clima recién guardado.
MIN_TOTAL_ROWS = 100


# ----------------------------------------------------------------------
# Normalización de la entrada
# ----------------------------------------------------------------------
def build_config(payload: dict[str, Any]) -> AppConfig:
    """Combina ``config.json`` con los valores enviados por la web."""
    base = AppConfig.load()
    data = base.to_dict()
    overrides = payload.get("config") or {}
    for section in ("site", "system", "training"):
        values = overrides.get(section)
        if isinstance(values, dict):
            data[section].update(
                {k: v for k, v in values.items() if v is not None}
            )
    config = AppConfig.from_dict(data)
    config.validate()
    return config


def weather_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Convierte las filas de MySQL en un DataFrame UTC normalizado."""
    if not rows:
        raise ValueError("No se recibieron datos meteorológicos.")

    frame = pd.DataFrame(rows)
    if "time_utc" not in frame.columns:
        raise ValueError("Los datos meteorológicos no incluyen 'time_utc'.")

    frame["time_utc"] = pd.to_datetime(
        frame["time_utc"], utc=True, errors="coerce"
    )
    frame = frame[frame["time_utc"].notna()].copy()
    if frame.empty:
        raise ValueError("Ninguna fila tiene una fecha válida.")

    for column in WEATHER_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = (
        frame.drop_duplicates(subset="time_utc")
        .sort_values("time_utc")
        .reset_index(drop=True)
    )
    return frame


def measurement_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Convierte las mediciones reales (potencia en W) en un DataFrame."""
    if not rows:
        return pd.DataFrame(columns=["time_utc", "power_w"])

    frame = pd.DataFrame(rows)
    if "time_utc" not in frame.columns or "power_w" not in frame.columns:
        raise ValueError(
            "Las mediciones deben incluir 'time_utc' y 'power_w'."
        )
    frame["time_utc"] = pd.to_datetime(
        frame["time_utc"], utc=True, errors="coerce"
    )
    frame["power_w"] = pd.to_numeric(frame["power_w"], errors="coerce")
    frame = frame.dropna(subset=["time_utc", "power_w"])
    frame = frame[frame["power_w"] >= 0.0]
    return (
        frame.drop_duplicates(subset="time_utc")
        .sort_values("time_utc")
        .reset_index(drop=True)
    )


def _ensure_radiation(frame: pd.DataFrame) -> None:
    """Comprueba que el clima almacenado sirva para alimentar al modelo."""
    columns = [
        c
        for c in ("global_tilted_irradiance", "shortwave_radiation")
        if c in frame.columns
    ]
    if not columns:
        raise ValueError(
            "El clima almacenado no incluye radiación solar. "
            "Actualiza los datos meteorológicos con la versión nueva."
        )
    if not frame[columns].notna().any().any():
        raise ValueError(
            "El clima almacenado no tiene valores de radiación solar. "
            "Actualiza los datos meteorológicos con la versión nueva."
        )


# ----------------------------------------------------------------------
# Acciones
# ----------------------------------------------------------------------
def predict(payload: dict[str, Any]) -> dict[str, Any]:
    """Genera el pronóstico horario y diario a partir del clima recibido."""
    config = build_config(payload)
    weather = weather_frame(payload.get("weather") or [])
    _ensure_radiation(weather)
    weather.attrs["timezone"] = config.site.timezone

    logger = get_logger()
    auto_train = bool(payload.get("auto_train", True))
    try:
        artifact = load_artifact(config=config)
    except FileNotFoundError:
        if not auto_train:
            raise
        logger.info(
            "No hay modelo para %s; entrenando con el clima almacenado…",
            config.site.name,
        )
        # Se reutiliza el flujo de entrenamiento: primero intenta el clima
        # de la base de datos y solo descarga Open-Meteo como último recurso.
        train_action(payload)
        artifact = load_artifact(config=config)

    features = build_features(weather, config)
    if features.empty:
        raise ValueError("No quedaron horas válidas tras preparar el clima.")

    X = feature_matrix(features)
    power = predict_power(
        artifact, X, ac_limit_w=config.system.ac_limit_w
    )

    # De noche no hay generación, aunque el modelo devuelva un residuo.
    power = np.where(features["is_day"].to_numpy(), power, 0.0)
    power = np.where(
        features["global_tilted_irradiance"].to_numpy() > 0.5, power, 0.0
    )
    features["power_w"] = np.clip(
        power, 0.0, config.system.ac_limit_w
    )
    features["energy_kwh"] = features["power_w"] * 1.0 / 1000.0

    daily = aggregate_daily(features)
    summary = build_summary(features, daily, artifact, config)

    hours = []
    for row in features.itertuples(index=False):
        hours.append(
            {
                "time": row.time_local.isoformat(),
                "power_w": round(float(row.power_w), 1),
                "energy_kwh": round(float(row.energy_kwh), 3),
                "gti_wm2": round(
                    float(row.global_tilted_irradiance), 1
                ),
                "temperature_c": round(float(row.temperature_2m), 2),
                "cloud_cover_pct": round(float(row.cloud_cover), 1),
                "wind_kmh": round(float(row.wind_speed_10m), 1),
            }
        )

    return {
        "ok": True,
        "action": "predict",
        "model_key": config.model_key(),
        "model_path": str(config.model_path()),
        "source": artifact.get("source"),
        "created_at": artifact.get("created_at"),
        "data_range": artifact.get("data_range"),
        "metrics": artifact.get("metrics", {}),
        "generated_at": now_utc().isoformat(),
        "site": {
            "name": config.site.name,
            "latitude": config.site.latitude,
            "longitude": config.site.longitude,
            "timezone": config.site.timezone,
        },
        "system": {
            "kwp": config.system.kwp,
            "tilt": config.system.tilt,
            "azimuth": config.system.azimuth,
            "ac_kwp": config.system.ac_kwp,
        },
        "summary": summary,
        "daily": daily.to_dict(orient="records"),
        "hours": hours,
    }


def train_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Reentrena el modelo con la mejor fuente de datos disponible."""
    config = build_config(payload)
    logger = get_logger()
    weather_rows = payload.get("weather") or []
    measurements = measurement_frame(payload.get("measurements") or [])
    min_real = int(payload.get("min_real_samples", 100))
    min_real = max(100, min_real)

    # 1) Calibración con producción real medida (la mejor opción).
    if not measurements.empty and weather_rows:
        weather = weather_frame(weather_rows)
        weather.attrs["timezone"] = config.site.timezone
        features = build_features(weather, config)
        merged = features.merge(measurements, on="time_utc", how="inner")
        day = daylight_rows(merged)
        day = day[day["power_w"].notna()].copy()

        if len(day) >= min_real:
            logger.info(
                "Calibrando con %d horas reales de producción…", len(day)
            )
            start = day["time_utc"].min().date()
            end = day["time_utc"].max().date()
            artifact = train_model(
                feature_matrix(day),
                day["power_w"].astype(float),
                config,
                source="real_data",
                data_range=f"{start.isoformat()} → {end.isoformat()}",
                baseline_X=day[
                    ["global_tilted_irradiance", "temperature_2m"]
                ],
                extra={
                    "weather_source": "database",
                    "measurements_rows": int(len(measurements)),
                    "real_samples": int(len(day)),
                    "calibrated_at": now_utc().isoformat(),
                },
            )
            path = save_artifact(artifact)
            return _train_response(artifact, config, path, "real_data")

        logger.info(
            "Solo hay %d horas reales coincidentes; se necesitan %d. "
            "Se usará el gemelo digital.",
            len(day),
            min_real,
        )

    # 2) Gemelo digital sobre el clima ya almacenado en la base de datos.
    if weather_rows:
        weather = weather_frame(weather_rows)
        weather.attrs["timezone"] = config.site.timezone
        features = build_features(weather, config)
        day = daylight_rows(features)
        start = features["time_utc"].min().date()
        end = features["time_utc"].max().date()

        if len(day) >= MIN_PHYSICS_ROWS:
            logger.info(
                "Entrenando gemelo digital con %d horas del clima "
                "almacenado…",
                len(day),
            )
            artifact = train_from_weather(
                weather,
                config,
                start=start,
                end=end,
                source="physics_db",
                extra={
                    "weather_source": "database",
                    "measurements_rows": int(len(measurements)),
                },
            )
            path = save_artifact(artifact)
            return _train_response(artifact, config, path, "physics_db")

        # Con pocas horas (por ejemplo, el primer pronóstico de 7 días) se
        # entrena con todas las horas: las nocturnas aportan objetivo 0 y
        # permiten alcanzar el mínimo de muestras. El modelo mejora solo
        # cuando la web acumula más clima y mediciones reales.
        if len(features) >= MIN_TOTAL_ROWS:
            logger.info(
                "Entrenando gemelo digital con %d horas del clima "
                "almacenado (%d diurnas; conjunto inicial pequeño)…",
                len(features),
                len(day),
            )
            rng = np.random.default_rng(config.training.random_state)
            target = simulate_power(
                features,
                config.system,
                rng=rng,
                randomized=config.training.domain_randomization,
                noise=0.02,
            )
            nominal = simulate_power(
                features,
                config.system,
                rng=None,
                randomized=False,
                noise=0.0,
            )
            artifact = train_model(
                feature_matrix(features),
                target,
                config,
                source="physics_db",
                data_range=f"{start.isoformat()} → {end.isoformat()}",
                nominal_y=nominal,
                baseline_X=features[
                    ["global_tilted_irradiance", "temperature_2m"]
                ],
                extra={
                    "weather_source": "database",
                    "measurements_rows": int(len(measurements)),
                    "training_rows": int(len(features)),
                    "daylight_rows": int(len(day)),
                    "bootstrap": True,
                },
            )
            path = save_artifact(artifact)
            return _train_response(artifact, config, path, "physics_db")

        logger.info(
            "El clima almacenado solo tiene %d horas; se necesitan %d. "
            "Se descargará el histórico de Open-Meteo.",
            len(features),
            MIN_TOTAL_ROWS,
        )

    # 3) Último recurso: histórico completo de Open-Meteo.
    logger.info("Entrenando con el histórico de Open-Meteo…")
    artifact = train(config)
    return _train_response(
        artifact, config, config.model_path(), "physics_archive"
    )


def _train_response(
    artifact: dict[str, Any],
    config: AppConfig,
    path: Any,
    origin: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": "train",
        "origin": origin,
        "model_key": config.model_key(),
        "model_path": str(path),
        "created_at": artifact.get("created_at"),
        "source": artifact.get("source"),
        "data_range": artifact.get("data_range"),
        "metrics": artifact.get("metrics", {}),
        "extra": artifact.get("extra", {}),
    }


def info(payload: dict[str, Any]) -> dict[str, Any]:
    """Devuelve el estado del modelo activo para la configuración dada."""
    config = build_config(payload)
    try:
        artifact = load_artifact(config=config)
    except FileNotFoundError:
        return {
            "ok": True,
            "action": "info",
            "exists": False,
            "model_key": config.model_key(),
            "model_path": str(config.model_path()),
            "config": config.to_dict(),
        }
    return {
        "ok": True,
        "action": "info",
        "exists": True,
        "model_key": config.model_key(),
        "model_path": str(config.model_path()),
        "created_at": artifact.get("created_at"),
        "source": artifact.get("source"),
        "data_range": artifact.get("data_range"),
        "metrics": artifact.get("metrics", {}),
        "config": config.to_dict(),
    }


ACTIONS = {
    "predict": predict,
    "train": train_action,
    "info": info,
}


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la acción solicitada y captura cualquier error."""
    action = str(payload.get("action", "")).strip().lower()
    if action not in ACTIONS:
        return {
            "ok": False,
            "error": (
                f"Acción desconocida: '{action}'. "
                f"Usa una de: {', '.join(sorted(ACTIONS))}."
            ),
        }
    try:
        return ACTIONS[action](payload)
    except Exception as exc:  # noqa: BLE001
        get_logger().error("%s", exc)
        if payload.get("verbose"):
            traceback.print_exc()
        return {
            "ok": False,
            "action": action,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def main(argv: list[str] | None = None) -> int:
    """Lee JSON de stdin, ejecuta la acción y escribe JSON en stdout."""
    setup_logging(bool(argv and "--verbose" in argv))
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"JSON de entrada inválido: {exc}"},
                ensure_ascii=False,
            )
        )
        return 0

    result = handle(payload)
    # Solo el JSON va a stdout; los logs van a stderr.
    print(json.dumps(result, ensure_ascii=False, default=_json_default))
    return 0


def _json_default(value: Any) -> Any:
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    raise TypeError(f"Tipo no serializable: {type(value)!r}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
