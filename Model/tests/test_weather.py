"""Pruebas del cliente de Open-Meteo (sin red)."""

from __future__ import annotations

import pandas as pd
import pytest

from solar_ai.weather import OpenMeteoClient


def _payload() -> dict:
    return {
        "latitude": 40.42,
        "longitude": -3.70,
        "timezone": "Europe/Madrid",
        "utc_offset_seconds": 3600,
        "hourly": {
            "time": ["2026-11-10T00:00", "2026-11-10T01:00"],
            "temperature_2m": [10.0, 11.0],
            "global_tilted_irradiance": [0.0, 5.0],
            "cloud_cover": [100, 90],
        },
    }


def test_frame_uses_real_utc_and_keeps_timezone():
    df = OpenMeteoClient._to_frame(_payload())
    assert len(df) == 2
    assert df["time_utc"].iloc[0] == pd.Timestamp("2026-11-09T23:00:00Z")
    assert df["time_utc"].iloc[1] == pd.Timestamp("2026-11-10T00:00:00Z")
    assert df["temperature_2m"].tolist() == [10.0, 11.0]
    assert df.attrs["timezone"] == "Europe/Madrid"
    assert df.attrs["utc_offset_seconds"] == 3600


def test_missing_variables_are_created_as_nan():
    df = OpenMeteoClient._to_frame(_payload())
    for col in ("relative_humidity_2m", "wind_speed_10m", "snow_depth"):
        assert col in df.columns
        assert df[col].isna().all()


def test_unix_timestamps_are_true_utc():
    payload = _payload()
    # La API con timeformat=unixtime devuelve segundos Unix reales.
    payload["hourly"]["time"] = [
        int(pd.Timestamp("2026-11-10T00:00", tz="Europe/Madrid").timestamp()),
        int(pd.Timestamp("2026-11-10T01:00", tz="Europe/Madrid").timestamp()),
    ]
    df = OpenMeteoClient._to_frame(payload)
    assert df["time_utc"].iloc[0] == pd.Timestamp("2026-11-09T23:00:00Z")
    assert df["time_utc"].iloc[1] == pd.Timestamp("2026-11-10T00:00:00Z")


def test_invalid_payload_raises():
    with pytest.raises(RuntimeError, match="datos horarios"):
        OpenMeteoClient._to_frame({"hourly": {"temperature_2m": [1.0]}})
