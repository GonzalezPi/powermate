"""Pruebas de carga de CSV de producción (sin red)."""

from __future__ import annotations

import pandas as pd
import pytest

from solar_ai.calibrate import load_production_csv


def test_detects_spanish_columns_and_kw_unit(tmp_path):
    path = tmp_path / "produccion.csv"
    path.write_text(
        "fecha,potencia\n"
        "2025-06-01 12:00,1.5\n"
        "2025-06-01 12:15,2.0\n"
        "2025-06-01 12:30,2.5\n"
        "2025-06-01 12:45,3.0\n"
        "2025-06-01 13:00,3.5\n",
        encoding="utf-8",
    )
    hourly, info = load_production_csv(
        path, timezone="Europe/Madrid", power_unit="auto"
    )
    assert info["time_col"] == "fecha"
    assert info["power_col"] == "potencia"
    assert info["power_unit_detected"] == "kW"
    assert len(hourly) == 2
    # 12:00 local en verano (CEST) = 10:00 UTC.
    assert hourly["time_utc"].iloc[0] == pd.Timestamp("2025-06-01T10:00:00Z")
    assert hourly["power_w"].iloc[0] == pytest.approx(2250.0)
    assert hourly["power_w"].iloc[1] == pytest.approx(3500.0)


def test_explicit_columns_and_watts(tmp_path):
    path = tmp_path / "produccion.csv"
    path.write_text(
        "ts,p_ac\n2025-01-01T00:00:00Z,0\n2025-01-01T01:00:00Z,500\n",
        encoding="utf-8",
    )
    hourly, info = load_production_csv(
        path, time_col="ts", power_col="p_ac", power_unit="W"
    )
    assert info["power_unit_detected"] == "W"
    assert hourly["power_w"].tolist() == [0.0, 500.0]


def test_missing_power_column_raises(tmp_path):
    path = tmp_path / "produccion.csv"
    path.write_text("fecha,valor\n2025-01-01 00:00,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columna de potencia"):
        load_production_csv(path)


def test_invalid_dates_raise(tmp_path):
    path = tmp_path / "produccion.csv"
    path.write_text("fecha,power\nno-es-fecha,10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fecha"):
        load_production_csv(path)
