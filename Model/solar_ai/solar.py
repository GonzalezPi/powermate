"""Posición solar y radiación de cielo claro.

Implementación vectorizada del algoritmo NOAA (fracción de año, ecuación
del tiempo, declinación y ángulo horario). La misma formulación que usa
pvlib para la posición solar aproximada, sin dependencias externas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def solar_position(
    times_utc: pd.DatetimeIndex | pd.Series,
    latitude: float,
    longitude: float,
) -> pd.DataFrame:
    """Calcula elevación, azimut, coseno del cenit y masa de aire.

    Parámetros
    ----------
    times_utc:
        Instantes en UTC. Si no tienen zona horaria se asumen UTC.
    latitude, longitude:
        Coordenadas en grados (WGS84).

    Devuelve
    --------
    DataFrame con índice alineado a ``times_utc`` y columnas:
    ``solar_elevation``, ``solar_azimuth``, ``solar_zenith``,
    ``cos_zenith``, ``airmass``, ``clearsky_ghi``.
    """
    idx = pd.DatetimeIndex(times_utc)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    doy = idx.day_of_year.to_numpy(dtype=float)
    hour = (
        idx.hour.to_numpy(dtype=float)
        + idx.minute.to_numpy(dtype=float) / 60.0
        + idx.second.to_numpy(dtype=float) / 3600.0
    )

    # Fracción de año (radianes) y ecuación del tiempo (minutos).
    gamma = 2.0 * np.pi / 365.0 * (doy - 1.0 + (hour - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2.0 * gamma)
        - 0.040849 * np.sin(2.0 * gamma)
    )

    # Declinación solar (radianes).
    decl = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2.0 * gamma)
        + 0.000907 * np.sin(2.0 * gamma)
        - 0.002697 * np.cos(3.0 * gamma)
        + 0.00148 * np.sin(3.0 * gamma)
    )

    # Tiempo solar verdadero y ángulo horario.
    time_offset = eqtime + 4.0 * longitude  # UTC
    tst = hour * 60.0 + time_offset
    hour_angle = np.deg2rad(tst / 4.0 - 180.0)

    lat_rad = np.deg2rad(latitude)
    cos_zenith = (
        np.sin(lat_rad) * np.sin(decl)
        + np.cos(lat_rad) * np.cos(decl) * np.cos(hour_angle)
    )
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    zenith = np.arccos(cos_zenith)
    elevation = 90.0 - np.rad2deg(zenith)

    # Azimut desde el norte, en sentido horario (0-360).
    sin_zenith = np.sin(zenith)
    safe_sin = np.where(np.abs(sin_zenith) < 1e-8, np.nan, sin_zenith)
    cos_azimuth = (
        np.sin(decl) - np.sin(lat_rad) * cos_zenith
    ) / (np.cos(lat_rad) * safe_sin)
    cos_azimuth = np.clip(cos_azimuth, -1.0, 1.0)
    azimuth = np.rad2deg(np.arccos(cos_azimuth))
    azimuth = np.where(hour_angle > 0.0, 360.0 - azimuth, azimuth)
    azimuth = np.where(elevation > -0.5, azimuth, np.nan)

    # Masa de aire (Kasten-Young, 1989).
    zenith_deg = np.rad2deg(zenith)
    airmass = 1.0 / (
        np.maximum(cos_zenith, 0.001)
        + 0.50572 * np.power(np.maximum(96.07995 - zenith_deg, 0.1), -1.6364)
    )
    airmass = np.where(elevation > 0.0, np.clip(airmass, 1.0, 40.0), 40.0)

    # GHI de cielo claro (modelo de Haurwitz), útil como referencia.
    cos_z = np.clip(cos_zenith, 0.0, 1.0)
    clearsky_ghi = 1098.0 * cos_z * np.exp(-0.059 / np.maximum(cos_z, 0.01))
    clearsky_ghi = np.where(cos_z > 0.01, clearsky_ghi, 0.0)

    return pd.DataFrame(
        {
            "solar_elevation": elevation,
            "solar_azimuth": azimuth,
            "solar_zenith": zenith_deg,
            "cos_zenith": cos_zenith,
            "airmass": airmass,
            "clearsky_ghi": clearsky_ghi,
        },
        index=idx,
    )


def daylight_mask(elevation: np.ndarray | pd.Series, gti: np.ndarray | pd.Series) -> np.ndarray:
    """Verdadero en horas con sol y radiación útil sobre el plano del panel."""
    return (np.asarray(elevation) > 0.0) & (np.asarray(gti) > 1.0)
