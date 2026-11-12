"""Prueba de integración offline: entrenar, predecir y exportar."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from solar_ai.config import AppConfig
from solar_ai.predict import run_forecast
from solar_ai.report import write_html_report
from solar_ai.train import build_training_dataset, train_from_weather


def _weather(start: str | pd.Timestamp, periods: int) -> pd.DataFrame:
    times = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    hour = times.hour.to_numpy()
    sun = np.maximum(0.0, np.sin(np.pi * (hour - 6) / 12.0))
    variation = 0.65 + 0.35 * np.sin(np.arange(periods) / 50.0) ** 2
    gti = sun * 950.0 * variation
    df = pd.DataFrame(
        {
            "time_utc": times,
            "global_tilted_irradiance": gti,
            "shortwave_radiation": gti * 0.9,
            "direct_normal_irradiance": gti * 0.8,
            "diffuse_radiation": gti * 0.2,
            "temperature_2m": 18.0 + 10.0 * sun,
            "relative_humidity_2m": 50.0,
            "cloud_cover": 100.0 * (1.0 - variation),
            "wind_speed_10m": 10.0,
            "snow_depth": 0.0,
        }
    )
    df.attrs["timezone"] = "Europe/Madrid"
    return df


def _madrid_config() -> AppConfig:
    """Configuración explícita acorde al clima sintético de estas pruebas."""
    config = AppConfig()
    config.site.name = "Madrid"
    config.site.latitude = 40.4168
    config.site.longitude = -3.7038
    config.site.timezone = "Europe/Madrid"
    return config


class FakeClient:
    def __init__(self, forecast: pd.DataFrame) -> None:
        self.forecast = forecast

    def fetch_forecast(self, config: AppConfig, days: int = 7) -> pd.DataFrame:
        return self.forecast.head(days * 24).copy()


def test_offline_end_to_end(tmp_path):
    config = _madrid_config()
    weather = _weather("2025-01-01", 365 * 24)

    day, target, nominal = build_training_dataset(weather, config)
    assert len(day) > 3500
    assert (target >= 0).all()
    assert (nominal >= 0).all()

    artifact = train_from_weather(
        weather,
        config,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
    )
    assert artifact["metrics"]["r2"] > 0.98
    assert artifact["metrics"]["mae_vs_physics_pct"] < 1.0

    start = pd.Timestamp(datetime.now(timezone.utc)).floor("D")
    forecast_weather = _weather(start, 48)
    result = run_forecast(
        config,
        days=2,
        artifact=artifact,
        client=FakeClient(forecast_weather),
    )
    assert len(result.hours) == 48
    assert 2 <= len(result.daily) <= 3
    assert result.summary["total_kwh"] > 0
    assert result.summary["peak_w"] > 0

    html_path = write_html_report(result, tmp_path / "informe.html")
    text = html_path.read_text(encoding="utf-8")
    assert "<svg" in text and "Pronóstico solar" in text

    json_path = result.save_json(tmp_path / "forecast.json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload["forecast"]["hours"]) == 48
    assert payload["summary"]["total_kwh"] == result.summary["total_kwh"]

    csv_path = result.save_csv(tmp_path / "forecast.csv")
    assert csv_path.read_text(encoding="utf-8").startswith("time,power_w")
