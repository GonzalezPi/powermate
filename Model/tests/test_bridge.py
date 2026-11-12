"""Pruebas offline del puente JSON que usa la aplicación PHP.

No se toca la red ni el modelo real: ``project_root`` se redirige a una
carpeta temporal para que los artefactos de prueba no contaminen
``Model/data/models``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solar_ai import bridge

CONFIG = {
    "site": {
        "name": "Junín",
        "latitude": -34.5833,
        "longitude": -60.95,
        "timezone": "America/Argentina/Buenos_Aires",
    },
    "system": {"kwp": 3.0, "tilt": 30.0, "azimuth": 0.0, "ac_kwp": 3.0},
    "training": {"years": 2.0, "test_fraction": 0.2},
}


def _weather(start: str | pd.Timestamp, periods: int) -> pd.DataFrame:
    """Clima sintético con ciclo diurno y radiación suficiente para entrenar."""
    times = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    hour = times.hour.to_numpy()
    sun = np.maximum(0.0, np.sin(np.pi * (hour - 6) / 12.0))
    variation = 0.65 + 0.35 * np.sin(np.arange(periods) / 50.0) ** 2
    gti = sun * 950.0 * variation
    return pd.DataFrame(
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


def _weather_rows(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "time_utc": row.time_utc.isoformat(),
                "global_tilted_irradiance": float(
                    row.global_tilted_irradiance
                ),
                "shortwave_radiation": float(row.shortwave_radiation),
                "direct_normal_irradiance": float(
                    row.direct_normal_irradiance
                ),
                "diffuse_radiation": float(row.diffuse_radiation),
                "temperature_2m": float(row.temperature_2m),
                "relative_humidity_2m": float(row.relative_humidity_2m),
                "cloud_cover": float(row.cloud_cover),
                "wind_speed_10m": float(row.wind_speed_10m),
                "snow_depth": float(row.snow_depth),
            }
        )
    return rows


def _measurements(frame: pd.DataFrame) -> list[dict]:
    """Producción medida sintética (W) para todas las horas del clima."""
    power = np.clip(frame["global_tilted_irradiance"].to_numpy() * 0.0028, 0.0, 3000.0)
    return [
        {"time_utc": ts.isoformat(), "power_w": float(p)}
        for ts, p in zip(frame["time_utc"], power)
    ]


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Evita que las pruebas escriban modelos en ``Model/data``."""
    monkeypatch.setattr("solar_ai.config.project_root", lambda: tmp_path)
    return tmp_path


def test_bootstrap_train_then_predict(isolated_root):
    weather = _weather("2026-11-12", 24 * 7)

    trained = bridge.train_action(
        {"config": CONFIG, "weather": _weather_rows(weather)}
    )
    assert trained["ok"] is True
    assert trained["origin"] == "physics_db"
    assert trained["extra"]["bootstrap"] is True
    metrics = trained["metrics"]
    assert metrics["train_samples"] + metrics["test_samples"] >= 100
    assert (isolated_root / "data" / "models").is_dir()

    predicted = bridge.predict(
        {"config": CONFIG, "weather": _weather_rows(weather.head(48))}
    )
    assert predicted["ok"] is True
    assert predicted["action"] == "predict"
    assert len(predicted["hours"]) == 48
    assert len(predicted["daily"]) >= 2
    assert predicted["summary"]["total_kwh"] > 0
    assert predicted["summary"]["peak_w"] > 0
    # De noche la potencia debe ser exactamente cero.
    for hour in predicted["hours"]:
        if hour["gti_wm2"] <= 0.5:
            assert hour["power_w"] == 0.0


def test_train_calibrates_with_real_measurements(isolated_root):
    weather = _weather("2026-01-01", 24 * 40)

    result = bridge.train_action(
        {
            "config": CONFIG,
            "weather": _weather_rows(weather),
            "measurements": _measurements(weather),
        }
    )
    assert result["ok"] is True
    assert result["origin"] == "real_data"
    assert result["source"] == "real_data"
    assert result["extra"]["real_samples"] >= 100
    assert result["extra"]["weather_source"] == "database"
    assert result["metrics"]["r2"] > 0.9


def test_train_falls_back_to_physics_when_measurements_are_scarce(isolated_root):
    weather = _weather("2026-03-01", 24 * 30)

    result = bridge.train_action(
        {
            "config": CONFIG,
            "weather": _weather_rows(weather),
            "measurements": _measurements(weather)[:10],
        }
    )
    assert result["ok"] is True
    assert result["origin"] == "physics_db"


def test_predict_without_radiation_reports_clear_error(isolated_root):
    rows = [
        {"time_utc": "2026-11-12T12:00:00Z", "temperature_2m": 20.0}
    ]
    result = bridge.handle(
        {"action": "predict", "config": CONFIG, "weather": rows}
    )
    assert result["ok"] is False
    assert "radiación" in result["error"]


def test_unknown_action_is_rejected(isolated_root):
    result = bridge.handle({"action": "no_existe"})
    assert result["ok"] is False
    assert "Acción desconocida" in result["error"]


def test_info_reports_missing_model(isolated_root):
    result = bridge.info({"config": CONFIG})
    assert result["ok"] is True
    assert result["exists"] is False
    assert result["model_key"]
