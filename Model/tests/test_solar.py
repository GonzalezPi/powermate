"""Pruebas de posición solar y radiación de cielo claro."""

from __future__ import annotations

import numpy as np
import pandas as pd

from solar_ai.solar import daylight_mask, solar_position


def test_solar_noon_summer_madrid():
    times = pd.date_range("2025-06-21 12:00", periods=3, freq="h", tz="UTC")
    pos = solar_position(times, 40.4168, -3.7038)
    assert pos["solar_elevation"].max() > 70.0
    # Cerca del mediodía solar el azimut debe apuntar al sur (~180°).
    azimuth = pos.loc[times[1], "solar_azimuth"]
    assert 150.0 < azimuth < 230.0
    assert (pos["clearsky_ghi"] >= 0).all()


def test_night_has_negative_elevation_and_no_daylight():
    times = pd.date_range("2025-12-21 00:00", periods=4, freq="h", tz="UTC")
    pos = solar_position(times, 40.4168, -3.7038)
    assert (pos["solar_elevation"] < 0).all()
    assert pos["solar_azimuth"].isna().all()
    assert not daylight_mask(pos["solar_elevation"], np.zeros(4)).any()


def test_equinox_noon_elevation_matches_latitude():
    # En equinoccio, elevación al mediodía ≈ 90 - latitud.
    times = pd.date_range("2025-03-20 11:00", periods=5, freq="h", tz="UTC")
    pos = solar_position(times, 40.4168, -3.7038)
    expected = 90.0 - 40.4168
    assert abs(pos["solar_elevation"].max() - expected) < 3.0


def test_airmass_and_clearsky_are_physical():
    times = pd.date_range("2025-06-21 10:00", periods=4, freq="h", tz="UTC")
    pos = solar_position(times, 40.4168, -3.7038)
    day = pos[pos["solar_elevation"] > 0]
    assert (day["airmass"] >= 1.0).all()
    assert (day["airmass"] <= 40.0).all()
    assert (day["clearsky_ghi"] <= 1200.0).all()
