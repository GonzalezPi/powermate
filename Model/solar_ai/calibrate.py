"""Calibración del modelo con datos reales de producción.

Flujo:
1. Leer un CSV con fecha y potencia (se autodetectan las columnas).
2. Descargar el clima histórico de Open-Meteo para ese mismo periodo.
3. Entrenar la regresión lineal con la potencia medida.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .features import build_features, daylight_rows, feature_matrix
from .model import save_artifact, train_model
from .utils import get_logger, resolve_zoneinfo
from .weather import OpenMeteoClient


def _detect_column(
    df: pd.DataFrame,
    candidates: list[str],
    tokens: tuple[str, ...],
) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate in lower:
            return lower[candidate]
    for column in df.columns:
        name = str(column).strip().lower()
        if any(token in name for token in tokens):
            return column
    return None


def load_production_csv(
    path: str | Path,
    *,
    time_col: str | None = None,
    power_col: str | None = None,
    power_unit: str = "auto",
    timezone: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Carga un CSV de producción y lo agrega a resolución horaria (UTC)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el CSV: {path}")

    df = pd.read_csv(path, sep=None, engine="python")
    if df.empty:
        raise ValueError("El CSV está vacío.")
    df.columns = [str(c).strip() for c in df.columns]

    time_col = time_col or _detect_column(
        df,
        ["timestamp", "datetime", "date_time", "time", "fecha_hora", "fecha", "hora"],
        ("time", "fecha", "date", "hora"),
    )
    if not time_col or time_col not in df.columns:
        raise ValueError(
            "No se encontró la columna de fecha/hora. "
            f"Columnas disponibles: {list(df.columns)}. Usa --time-col."
        )

    power_col = power_col or _detect_column(
        df,
        [
            "ac_power",
            "power_ac",
            "p_ac",
            "potencia_ac",
            "potencia",
            "power",
            "generation",
            "generacion",
            "yield",
            "energy",
            "energia",
        ],
        ("power", "potencia", "generation", "generacion", "yield", "energia", "energy"),
    )
    if not power_col or power_col not in df.columns:
        raise ValueError(
            "No se encontró la columna de potencia. "
            f"Columnas disponibles: {list(df.columns)}. Usa --power-col."
        )

    parsed = pd.to_datetime(df[time_col], errors="coerce")
    if parsed.isna().mean() > 0.5:
        parsed = pd.to_datetime(df[time_col], errors="coerce", dayfirst=True)
    if parsed.isna().all():
        raise ValueError(f"No se pudo interpretar la columna de fecha '{time_col}'.")

    try:
        tz_naive = parsed.dt.tz is None
    except AttributeError:
        # Fechas con offsets mezclados: normalizamos directamente a UTC.
        parsed = pd.to_datetime(df[time_col], errors="coerce", utc=True)
        tz_naive = False
    if tz_naive:
        tzinfo = resolve_zoneinfo(timezone or "UTC")
        parsed = parsed.dt.tz_localize(
            tzinfo, ambiguous="infer", nonexistent="shift_forward"
        )
    parsed = parsed.dt.tz_convert("UTC")

    raw_power = df[power_col].astype(str).str.strip()
    # Formato decimal con coma (habitual en CSV en español).
    if raw_power.str.contains(",").any() and not raw_power.str.contains(r"\.").any():
        raw_power = raw_power.str.replace(",", ".", regex=False)
    power = pd.to_numeric(raw_power, errors="coerce")

    unit = power_unit.strip().lower()
    if unit in ("auto", ""):
        column_hint = f"{power_col}".lower()
        if "kw" in column_hint:
            unit = "kw"
        else:
            peak = float(np.nanmax(power.to_numpy(dtype=float))) if power.notna().any() else 0.0
            unit = "kw" if peak <= 100.0 else "w"
    if unit in ("kw", "kilowatt", "kilovatio"):
        power_w = power * 1000.0
        unit_label = "kW"
    else:
        power_w = power
        unit_label = "W"

    frame = pd.DataFrame(
        {
            "time_utc": parsed.dt.floor("h"),
            "power_w": power_w.astype(float),
        }
    ).dropna()
    frame = frame[frame["power_w"] >= 0.0]
    if frame.empty:
        raise ValueError("No quedaron filas válidas con fecha y potencia.")

    hourly = (
        frame.groupby("time_utc", as_index=False)["power_w"]
        .mean()
        .sort_values("time_utc")
        .reset_index(drop=True)
    )
    info = {
        "path": str(path),
        "time_col": time_col,
        "power_col": power_col,
        "power_unit_detected": unit_label,
        "rows_csv": int(len(df)),
        "rows_hourly": int(len(hourly)),
        "start": hourly["time_utc"].min(),
        "end": hourly["time_utc"].max(),
    }
    return hourly, info


def calibrate(
    config: AppConfig,
    csv_path: str | Path,
    *,
    time_col: str | None = None,
    power_col: str | None = None,
    power_unit: str = "auto",
    timezone: str | None = None,
    client: OpenMeteoClient | None = None,
) -> dict[str, Any]:
    """Recalibra el modelo con producción real y lo guarda."""
    config.validate()
    logger = get_logger()
    client = client or OpenMeteoClient()
    hourly, info = load_production_csv(
        csv_path,
        time_col=time_col,
        power_col=power_col,
        power_unit=power_unit,
        timezone=timezone,
    )
    logger.info(
        "CSV: %d filas → %d horas (%s a %s), potencia en %s.",
        info["rows_csv"],
        info["rows_hourly"],
        info["start"],
        info["end"],
        info["power_unit_detected"],
    )

    start: date = hourly["time_utc"].min().date()
    end: date = hourly["time_utc"].max().date()
    logger.info("Descargando clima histórico %s → %s…", start, end)
    weather = client.fetch_archive(config, start, end)
    features = build_features(weather, config)

    merged = features.merge(hourly, on="time_utc", how="inner")
    if merged.empty:
        raise RuntimeError(
            "El CSV y el clima histórico no comparten ninguna hora. "
            "Revisa la zona horaria (--tz) y el rango de fechas."
        )

    day = daylight_rows(merged)
    day = day[day["power_w"].notna()].copy()
    if len(day) < 100:
        raise RuntimeError(
            f"Solo hay {len(day)} horas diurnas coincidentes. Se necesitan al menos 100. "
            "Aporta un CSV más largo (varias semanas) o revisa la zona horaria."
        )

    y = day["power_w"].astype(float)
    X = feature_matrix(day)
    logger.info("Entrenando con %d horas reales…", len(X))
    artifact = train_model(
        X,
        y,
        config,
        source="real_data",
        data_range=f"{start.isoformat()} → {end.isoformat()}",
        baseline_X=day[["global_tilted_irradiance", "temperature_2m"]],
        extra={
            "calibration": info,
            "weather_source": "Open-Meteo archive",
        },
    )

    # La potencia pico observada es información útil para el informe.
    artifact["extra"]["measured_peak_w"] = round(float(y.max()), 1)
    artifact["extra"]["measured_mean_w"] = round(float(y.mean()), 1)
    artifact["extra"]["csv_path"] = str(csv_path)

    path = save_artifact(artifact)
    logger.info("Modelo calibrado guardado en %s", path)
    return artifact
