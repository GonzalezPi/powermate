"""Modelo físico de una instalación fotovoltaica.

Este modelo actúa como "gemelo digital" para generar el objetivo de
entrenamiento y como referencia física. Sigue ideas de pvlib-python:

* temperatura de célula modelo SAM (fuentes et al.),
* coeficiente de temperatura,
* pérdidas por suciedad, cableado y desacoplamiento,
* recorte (clipping) del inversor,
* pérdida por nieve.

La regresión lineal aprende después la relación entre las variables
meteorológicas y esta potencia, de forma que puede predecir en tiempo real
sin ejecutar el modelo físico.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SystemConfig


def _as_series(values, index) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype(float)
    return pd.Series(np.asarray(values, dtype=float), index=index)


def cell_temperature(
    ambient_c: pd.Series | np.ndarray,
    gti_wm2: pd.Series | np.ndarray,
    wind_ms: pd.Series | np.ndarray,
    noct: float = 45.0,
) -> pd.Series:
    """Temperatura de célula con el modelo SAM de pvlib, escalado por NOCT.

    ``Tcell = Tamb + GTI * exp(a + b * viento) * escala``. La escala hace
    que, con 800 W/m² y 1 m/s, el incremento sea ``NOCT - 20`` grados.
    """
    index = getattr(ambient_c, "index", None)
    tamb = _as_series(ambient_c, index)
    gti = _as_series(gti_wm2, index).clip(lower=0.0)
    wind = _as_series(wind_ms, index).clip(lower=0.0, upper=30.0)
    heating = gti * np.exp(-3.56 - 0.075 * wind)
    reference = 800.0 * np.exp(-3.635)
    scale = max((float(noct) - 20.0) / reference, 0.1)
    tcell = tamb + heating * scale
    return tcell.clip(lower=-40.0, upper=90.0)


def nominal_loss_factor(config: SystemConfig) -> float:
    """Fracción de potencia DC que llega al inversor."""
    return (
        (1.0 - config.soiling_loss)
        * (1.0 - config.wiring_loss)
        * (1.0 - config.mismatch_loss)
    )


def _snow_factor(
    snow_depth_m: pd.Series,
    config: SystemConfig,
) -> pd.Series:
    if not config.snow_loss or config.snow_zero_cm <= 0:
        return pd.Series(1.0, index=snow_depth_m.index)
    depth_cm = snow_depth_m.clip(lower=0.0) * 100.0
    return (1.0 - depth_cm / config.snow_zero_cm).clip(lower=0.0, upper=1.0)


def simulate_power(
    weather: pd.DataFrame,
    config: SystemConfig,
    *,
    rng: np.random.Generator | None = None,
    randomized: bool = False,
    noise: float = 0.0,
) -> pd.Series:
    """Simula la potencia AC (W) para cada fila de ``weather``.

    ``randomized`` introduce incertidumbre de parámetros (suciedad,
    coeficiente de temperatura, rendimiento del inversor…) para que el
    modelo aprenda el comportamiento medio y no memorice una curva perfecta.
    """
    n = len(weather)
    index = weather.index
    gti = weather["global_tilted_irradiance"].astype(float).fillna(0.0).clip(lower=0.0)
    tamb = weather["temperature_2m"].astype(float)
    if tamb.isna().any():
        median = tamb.median()
        tamb = tamb.fillna(20.0 if pd.isna(median) else median)
    wind_kmh = weather["wind_speed_10m"].astype(float).fillna(2.0).clip(lower=0.0)
    wind_ms = wind_kmh / 3.6
    snow = weather.get("snow_depth", pd.Series(0.0, index=index))
    snow = snow.astype(float).fillna(0.0).clip(lower=0.0)

    gamma = np.full(n, config.temp_coefficient, dtype=float)
    soiling = np.full(n, config.soiling_loss, dtype=float)
    wiring = np.full(n, config.wiring_loss, dtype=float)
    mismatch = np.full(n, config.mismatch_loss, dtype=float)
    inv_eff = np.full(n, config.inverter_efficiency, dtype=float)
    ac_limit = np.full(n, config.ac_limit_w, dtype=float)

    if randomized:
        if rng is None:
            rng = np.random.default_rng(42)
        gamma = np.clip(
            rng.normal(config.temp_coefficient, 0.0004, n), -0.006, -0.002
        )
        soiling = np.clip(rng.normal(config.soiling_loss, 0.015, n), 0.0, 0.12)
        wiring = np.clip(rng.normal(config.wiring_loss, 0.008, n), 0.0, 0.06)
        mismatch = np.clip(rng.normal(config.mismatch_loss, 0.008, n), 0.0, 0.06)
        inv_eff = np.clip(rng.normal(config.inverter_efficiency, 0.01, n), 0.88, 0.995)
        ac_limit = ac_limit * np.clip(rng.normal(1.0, 0.01, n), 0.95, 1.05)

    tcell = cell_temperature(tamb, gti, wind_ms, noct=config.noct)
    temp_factor = 1.0 + gamma * (tcell.to_numpy() - 25.0)
    temp_factor = np.clip(temp_factor, 0.0, 1.5)

    loss_factor = (1.0 - soiling) * (1.0 - wiring) * (1.0 - mismatch)
    p_dc = config.dc_capacity_w * (gti.to_numpy() / 1000.0) * temp_factor * loss_factor
    p_ac = np.minimum(p_dc * inv_eff, ac_limit)
    p_ac = np.clip(p_ac, 0.0, None)

    snow_factor = _snow_factor(snow, config).to_numpy()
    p_ac = p_ac * snow_factor

    if noise > 0.0:
        if rng is None:
            rng = np.random.default_rng(42)
        p_ac = p_ac * np.clip(rng.normal(1.0, noise, n), 0.4, 1.6)

    return pd.Series(np.clip(p_ac, 0.0, None), index=index, name="power_w")


def energy_kwh(power_w: pd.Series, interval_hours: float = 1.0) -> float:
    """Integra potencia (W) a energía (kWh)."""
    return float(power_w.fillna(0.0).sum() * interval_hours / 1000.0)
