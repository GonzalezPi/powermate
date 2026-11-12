"""Ingeniería de características físicamente informadas.

La idea es darle a la regresión lineal variables que ya contengan la
física del problema (irradiancia sobre el plano, temperatura de célula,
índice de claridad, geometría solar, interacciones con viento/nubes). Así
un modelo lineal puede aproximar muy bien una relación no lineal sin
perder interpretabilidad.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AppConfig
from .physics import cell_temperature
from .solar import daylight_mask, solar_position
from .utils import get_logger, resolve_zoneinfo

# Orden estable de las variables que ve el modelo.
FEATURES = [
    "gti_temp",
    "gti_temp_sq",
    "gti_cloud",
    "gti_wind",
    "gti_hum",
    "gti_cosz",
    "temp_factor",
    "clearness",
    "diffuse_fraction",
    "direct_fraction",
    "cos_zenith",
    "elevation_norm",
    "cloud_fraction",
    "wind_ms",
    "humidity",
    "hour_sin",
    "hour_cos",
    "doy_sin",
    "doy_cos",
    "snow_depth_cm",
]

REQUIRED_RAW = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_10m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
    "snow_depth",
]


def _fill_column(df: pd.DataFrame, col: str, fallback: float) -> None:
    series = pd.to_numeric(df[col], errors="coerce")
    median = series.median()
    df[col] = series.fillna(median if pd.notna(median) else fallback)


def _clean_weather(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos, rellena huecos pequeños y elimina valores imposibles."""
    df = df.copy()
    if "time_utc" not in df.columns:
        raise ValueError("Los datos meteorológicos no contienen la columna 'time_utc'.")

    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True, errors="coerce")
    if df["time_utc"].isna().any():
        df = df[df["time_utc"].notna()].copy()
    if df.empty:
        raise ValueError("No hay filas con fecha válida en los datos meteorológicos.")

    for col in REQUIRED_RAW:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Si el plano inclinado no está disponible, usamos la radiación global.
    if df["global_tilted_irradiance"].isna().all():
        get_logger().warning(
            "Open-Meteo no devolvió 'global_tilted_irradiance'; se usa "
            "'shortwave_radiation' como aproximación."
        )
        df["global_tilted_irradiance"] = df["shortwave_radiation"]

    radiation = [
        "global_tilted_irradiance",
        "shortwave_radiation",
        "direct_normal_irradiance",
        "diffuse_radiation",
    ]
    fillable = radiation + [
        "temperature_2m",
        "relative_humidity_2m",
        "cloud_cover",
        "wind_speed_10m",
    ]
    # Huecos cortos (nubes, modelos) se interpolan; los largos se rellenan
    # con un valor conservador más abajo.
    df[fillable] = df[fillable].interpolate(limit=3, limit_direction="both")

    for col in radiation:
        df[col] = df[col].clip(lower=0.0).fillna(0.0)
        # Errores de redondeo del modelo meteorológico (~1e-13) se anulan.
        df.loc[df[col] < 1e-6, col] = 0.0
    _fill_column(df, "temperature_2m", 20.0)
    _fill_column(df, "relative_humidity_2m", 50.0)
    _fill_column(df, "cloud_cover", 50.0)
    _fill_column(df, "wind_speed_10m", 7.2)
    df["snow_depth"] = pd.to_numeric(df["snow_depth"], errors="coerce").fillna(0.0)
    df["snow_depth"] = df["snow_depth"].clip(lower=0.0)

    # Límites físicos razonables.
    df["cloud_cover"] = df["cloud_cover"].clip(0.0, 100.0)
    df["relative_humidity_2m"] = df["relative_humidity_2m"].clip(0.0, 100.0)
    df["wind_speed_10m"] = df["wind_speed_10m"].clip(lower=0.0)
    df["temperature_2m"] = df["temperature_2m"].clip(-60.0, 60.0)
    for col in radiation:
        df[col] = df[col].clip(0.0, 1500.0)

    df = (
        df.drop_duplicates(subset="time_utc")
        .sort_values("time_utc")
        .reset_index(drop=True)
    )
    return df


def build_features(df: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    """Devuelve una copia con las columnas meteorológicas y de características."""
    attrs = dict(getattr(df, "attrs", {}) or {})
    out = _clean_weather(df)
    out.attrs.update(attrs)
    lat, lon = config.site.latitude, config.site.longitude

    pos = solar_position(out["time_utc"], lat, lon).reset_index(drop=True)
    for col in pos.columns:
        out[col] = pos[col].to_numpy()

    # Hora local real (con DST) para las variables cíclicas.
    tz_name = out.attrs.get("timezone") or config.site.timezone
    if isinstance(tz_name, str) and tz_name not in ("", "auto"):
        tz = resolve_zoneinfo(tz_name)
    else:
        tz = resolve_zoneinfo(config.site.timezone)
    try:
        out["time_local"] = out["time_utc"].dt.tz_convert(tz)
    except Exception:  # noqa: BLE001
        out["time_local"] = out["time_utc"]

    gti = out["global_tilted_irradiance"].to_numpy(dtype=float)
    ghi = out["shortwave_radiation"].to_numpy(dtype=float)
    dni = out["direct_normal_irradiance"].to_numpy(dtype=float)
    dhi = out["diffuse_radiation"].to_numpy(dtype=float)
    cos_zenith = np.clip(out["cos_zenith"].to_numpy(dtype=float), 0.0, 1.0)
    elevation = out["solar_elevation"].to_numpy(dtype=float)
    clearsky = out["clearsky_ghi"].to_numpy(dtype=float)

    wind_ms = out["wind_speed_10m"].to_numpy(dtype=float) / 3.6
    humidity = np.clip(out["relative_humidity_2m"].to_numpy(dtype=float) / 100.0, 0.0, 1.0)
    cloud = np.clip(out["cloud_cover"].to_numpy(dtype=float) / 100.0, 0.0, 1.0)

    tcell = cell_temperature(
        out["temperature_2m"],
        out["global_tilted_irradiance"],
        wind_ms,
        noct=config.system.noct,
    ).to_numpy(dtype=float)
    temp_factor = np.clip(
        1.0 + config.system.temp_coefficient * (tcell - 25.0), 0.0, 1.5
    )

    clearness = np.divide(
        ghi, clearsky, out=np.zeros_like(ghi), where=clearsky > 1.0
    )
    diffuse_fraction = np.divide(
        dhi, ghi, out=np.zeros_like(dhi), where=ghi > 1.0
    )
    direct_fraction = np.divide(
        dni * cos_zenith, ghi, out=np.zeros_like(dni), where=ghi > 1.0
    )

    gti_norm = gti / 1000.0
    gti_temp = gti_norm * temp_factor

    local = pd.DatetimeIndex(out["time_local"])
    hour = local.hour.to_numpy(dtype=float) + local.minute.to_numpy(dtype=float) / 60.0
    doy = local.day_of_year.to_numpy(dtype=float)

    out["gti_temp"] = gti_temp
    out["gti_temp_sq"] = gti_temp**2
    out["gti_cloud"] = gti_norm * cloud
    out["gti_wind"] = gti_norm * wind_ms
    out["gti_hum"] = gti_norm * humidity
    out["gti_cosz"] = gti_norm * cos_zenith
    out["temp_factor"] = temp_factor
    out["clearness"] = np.clip(clearness, 0.0, 1.5)
    out["diffuse_fraction"] = np.clip(diffuse_fraction, 0.0, 1.0)
    out["direct_fraction"] = np.clip(direct_fraction, 0.0, 1.0)
    out["cos_zenith"] = cos_zenith
    out["elevation_norm"] = np.clip(elevation, 0.0, 90.0) / 90.0
    out["cloud_fraction"] = cloud
    out["wind_ms"] = wind_ms
    out["humidity"] = humidity
    out["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    out["doy_sin"] = np.sin(2.0 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2.0 * np.pi * doy / 365.25)
    out["snow_depth_cm"] = out["snow_depth"].to_numpy(dtype=float) * 100.0
    out["is_day"] = daylight_mask(elevation, gti)

    # Sanidad final: sin inf y sin NaN en las variables del modelo.
    out[FEATURES] = out[FEATURES].replace([np.inf, -np.inf], np.nan)
    nan_ratio = out[FEATURES].isna().mean()
    bad = nan_ratio[nan_ratio > 0.05]
    if not bad.empty:
        get_logger().warning(
            "Huecos en características: %s. Se rellenan con 0.",
            ", ".join(f"{k}={v:.0%}" for k, v in bad.items()),
        )
    out[FEATURES] = out[FEATURES].fillna(0.0)
    return out


def feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Matriz X lista para el modelo, con el orden de columnas estable."""
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan características: {', '.join(missing)}")
    X = df[FEATURES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X


def daylight_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Filas diurnas con radiación suficiente para entrenar/evaluar."""
    return df[(df["is_day"]) & (df["global_tilted_irradiance"] > 1.0)].copy()
