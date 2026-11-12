"""Pruebas del modelo físico (gemelo digital)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solar_ai.config import SystemConfig
from solar_ai.physics import cell_temperature, energy_kwh, simulate_power


def _weather(gti: float, *, temp: float = 25.0, wind: float = 7.2, snow: float = 0.0, n: int = 3):
    return pd.DataFrame(
        {
            "global_tilted_irradiance": [gti] * n,
            "temperature_2m": [temp] * n,
            "wind_speed_10m": [wind] * n,
            "snow_depth": [snow] * n,
        }
    )


def test_night_generates_zero():
    cfg = SystemConfig()
    power = simulate_power(_weather(0.0), cfg)
    assert (power == 0.0).all()


def test_peak_power_is_in_expected_range():
    cfg = SystemConfig()
    power = simulate_power(_weather(1000.0, temp=25.0, wind=3.6), cfg)
    # 3 kWp, ~96 % inversor, ~7 % de pérdidas y célula caliente ⇒ ~2,4 kW.
    assert 2250.0 < float(power.iloc[0]) < 2800.0


def test_cell_temperature_increases_with_irradiance_and_drops_with_wind():
    low = cell_temperature(pd.Series([20.0]), pd.Series([200.0]), pd.Series([2.0])).iloc[0]
    high = cell_temperature(pd.Series([20.0]), pd.Series([1000.0]), pd.Series([2.0])).iloc[0]
    windy = cell_temperature(pd.Series([20.0]), pd.Series([1000.0]), pd.Series([10.0])).iloc[0]
    assert high > low
    assert windy < high


def test_inverter_clipping():
    cfg = SystemConfig(kwp=10.0, ac_kwp=3.0)
    power = simulate_power(_weather(1200.0), cfg)
    assert (power <= 3000.0 + 1e-6).all()


def test_snow_cover_can_zero_output():
    cfg = SystemConfig(snow_loss=True, snow_zero_cm=2.0)
    power = simulate_power(_weather(800.0, snow=0.02), cfg)  # 2 cm
    assert (power == 0.0).all()


def test_randomized_simulation_is_reproducible_and_centered():
    cfg = SystemConfig()
    weather = _weather(800.0, n=2000)
    a = simulate_power(weather, cfg, rng=np.random.default_rng(7), randomized=True, noise=0.0)
    b = simulate_power(weather, cfg, rng=np.random.default_rng(7), randomized=True, noise=0.0)
    assert np.allclose(a, b)
    nominal = simulate_power(weather, cfg).mean()
    assert abs(a.mean() - nominal) / nominal < 0.05


def test_energy_integration():
    power = pd.Series([0.0, 1000.0, 2000.0, 500.0])
    assert energy_kwh(power) == pytest.approx(3.5)
