"""Pruebas de ingeniería de características."""

from __future__ import annotations

import numpy as np
import pandas as pd

from solar_ai.config import AppConfig
from solar_ai.features import FEATURES, build_features, feature_matrix


def _madrid_config() -> AppConfig:
    """Configuración explícita acorde al clima sintético de estas pruebas."""
    config = AppConfig()
    config.site.name = "Madrid"
    config.site.latitude = 40.4168
    config.site.longitude = -3.7038
    config.site.timezone = "Europe/Madrid"
    return config


def _synthetic_weather(days: int = 5) -> pd.DataFrame:
    times = pd.date_range("2025-06-01", periods=days * 24, freq="h", tz="UTC")
    hour = times.hour.to_numpy()
    sun = np.maximum(0.0, np.sin(np.pi * (hour - 6) / 12.0))
    df = pd.DataFrame(
        {
            "time_utc": times,
            "global_tilted_irradiance": sun * 900.0,
            "shortwave_radiation": sun * 850.0,
            "direct_normal_irradiance": sun * 700.0,
            "diffuse_radiation": sun * 150.0,
            "temperature_2m": 20.0 + 8.0 * sun,
            "relative_humidity_2m": 45.0,
            "cloud_cover": 20.0,
            "wind_speed_10m": 10.0,
            "snow_depth": 0.0,
        }
    )
    df.attrs["timezone"] = "Europe/Madrid"
    return df


def test_features_are_complete_and_finite():
    df = build_features(_synthetic_weather(), _madrid_config())
    assert set(FEATURES).issubset(df.columns)
    X = feature_matrix(df)
    assert list(X.columns) == FEATURES
    assert np.isfinite(X.to_numpy()).all()
    assert df["time_local"].dt.tz is not None
    assert df["is_day"].any()


def test_night_features_are_zero():
    df = build_features(_synthetic_weather(2), _madrid_config())
    night = df[~df["is_day"]]
    assert not night.empty
    assert (night["gti_temp"] == 0.0).all()


def test_missing_tilted_irradiance_falls_back_to_shortwave():
    weather = _synthetic_weather(2)
    weather["global_tilted_irradiance"] = np.nan
    df = build_features(weather, _madrid_config())
    assert df["global_tilted_irradiance"].notna().all()
    assert (df["global_tilted_irradiance"] >= 0).all()


def test_short_gaps_are_interpolated():
    weather = _synthetic_weather(2)
    weather.loc[10, "temperature_2m"] = np.nan
    df = build_features(weather, _madrid_config())
    assert df["temperature_2m"].notna().all()
