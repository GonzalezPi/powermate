"""Entrenamiento con histórico de Open-Meteo y gemelo digital físico."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .features import build_features, daylight_rows, feature_matrix
from .model import save_artifact, train_model
from .physics import simulate_power
from .utils import get_logger
from .weather import OpenMeteoClient, default_archive_range


def build_training_dataset(
    weather: pd.DataFrame,
    config: AppConfig,
    *,
    rng: np.random.Generator | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Construye X, objetivo sintético y objetivo físico nominal.

    El objetivo sintético incluye ruido y, opcionalmente, aleatorización de
    parámetros (suciedad, coeficiente térmico, eficiencia del inversor) para
    que el modelo no aprenda una curva artificialmente perfecta.
    """
    features = build_features(weather, config)
    day = daylight_rows(features)
    if len(day) < 200:
        raise RuntimeError(
            f"Solo hay {len(day)} horas diurnas válidas. Se necesitan al menos 200. "
            "Prueba con más años de histórico o revisa las coordenadas."
        )

    if rng is None:
        rng = np.random.default_rng(config.training.random_state)

    target = simulate_power(
        day,
        config.system,
        rng=rng,
        randomized=config.training.domain_randomization,
        noise=0.02,
    )
    nominal = simulate_power(
        day,
        config.system,
        rng=None,
        randomized=False,
        noise=0.0,
    )
    return day, target, nominal


def train_from_weather(
    weather: pd.DataFrame,
    config: AppConfig,
    *,
    start: date | None = None,
    end: date | None = None,
    source: str = "physics",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entrena el modelo a partir de un DataFrame meteorológico."""
    logger = get_logger()
    logger.info("Construyendo características físicas…")
    day, target, nominal = build_training_dataset(weather, config)
    X = feature_matrix(day)

    data_range = None
    if start and end:
        data_range = f"{start.isoformat()} → {end.isoformat()}"

    logger.info(
        "Entrenando regresión lineal con %d muestras diurnas…", len(X)
    )
    artifact = train_model(
        X,
        target,
        config,
        source=source,
        data_range=data_range,
        nominal_y=nominal,
        baseline_X=day[["global_tilted_irradiance", "temperature_2m"]],
        extra={
            "weather_source": "Open-Meteo",
            "weather_rows": int(len(weather)),
            "training_rows": int(len(day)),
            "mean_gti_wm2": round(float(day["global_tilted_irradiance"].mean()), 1),
            "peak_target_w": round(float(target.max()), 1),
            "domain_randomization": bool(config.training.domain_randomization),
            **(extra or {}),
        },
    )
    return artifact


def train(
    config: AppConfig,
    *,
    client: OpenMeteoClient | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Entrena con los últimos años de histórico disponible en Open-Meteo."""
    config.validate()
    logger = get_logger()
    client = client or OpenMeteoClient()
    start, end = default_archive_range(config.training.years)
    logger.info(
        "Descargando histórico de Open-Meteo: %s → %s (%.1f años)…",
        start.isoformat(),
        end.isoformat(),
        config.training.years,
    )
    weather = client.fetch_archive(config, start, end)
    artifact = train_from_weather(
        weather,
        config,
        start=start,
        end=end,
        source="physics",
    )
    if save:
        path = save_artifact(artifact)
        logger.info("Modelo guardado en %s", path)
    return artifact
