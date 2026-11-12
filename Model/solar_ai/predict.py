"""Generación del pronóstico y exportación de resultados."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .features import build_features, feature_matrix
from .model import load_artifact, predict_power
from .utils import get_logger, now_utc
from .weather import OpenMeteoClient


@dataclass
class ForecastResult:
    hours: pd.DataFrame
    daily: pd.DataFrame
    summary: dict[str, Any]
    artifact: dict[str, Any]
    config: AppConfig

    # ------------------------------------------------------------------
    # Exportación
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        hours = []
        for row in self.hours.itertuples(index=False):
            hours.append(
                {
                    "time": _iso(row.time_local),
                    "power_w": round(float(row.power_w), 1),
                    "power_kw": round(float(row.power_w) / 1000.0, 3),
                    "energy_kwh": round(float(row.energy_kwh), 3),
                    "gti_wm2": round(float(row.global_tilted_irradiance), 1),
                    "temperature_c": round(float(row.temperature_2m), 2),
                    "cloud_cover_pct": round(float(row.cloud_cover), 1),
                    "wind_kmh": round(float(row.wind_speed_10m), 1),
                }
            )
        return {
            "generated_at": now_utc().isoformat(),
            "site": self.summary["site"],
            "system": self.summary["system"],
            "forecast": {
                "start": self.summary["forecast_start"],
                "end": self.summary["forecast_end"],
                "hours": hours,
            },
            "daily": self.daily.to_dict(orient="records"),
            "summary": {
                k: v for k, v in self.summary.items() if k not in ("site", "system")
            },
            "model": self.summary["model"],
        }

    def save_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        return path

    def save_csv(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "time",
            "power_w",
            "power_kw",
            "energy_kwh",
            "gti_wm2",
            "temperature_c",
            "cloud_cover_pct",
            "wind_kmh",
        ]
        records = self.to_dict()["forecast"]["hours"]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        return path


def _iso(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def run_forecast(
    config: AppConfig,
    *,
    days: int = 7,
    artifact: dict[str, Any] | None = None,
    client: OpenMeteoClient | None = None,
    auto_train: bool = True,
) -> ForecastResult:
    """Descarga el pronóstico, aplica el modelo y agrega los resultados."""
    config.validate()
    logger = get_logger()
    client = client or OpenMeteoClient()

    if artifact is None:
        try:
            artifact = load_artifact(config=config)
        except FileNotFoundError:
            if not auto_train:
                raise
            logger.info("No hay modelo para esta configuración; entrenando…")
            from .train import train

            artifact = train(config, client=client)

    weather = client.fetch_forecast(config, days)
    features = build_features(weather, config)
    X = feature_matrix(features)
    power = predict_power(
        artifact, X, ac_limit_w=config.system.ac_limit_w
    )
    # De noche no hay generación, independientemente de lo que diga el modelo.
    power = np.where(features["is_day"].to_numpy(), power, 0.0)
    power = np.where(
        features["global_tilted_irradiance"].to_numpy() > 0.5, power, 0.0
    )
    features["power_w"] = np.clip(power, 0.0, config.system.ac_limit_w)
    features["energy_kwh"] = features["power_w"] * 1.0 / 1000.0

    daily = aggregate_daily(features)
    summary = build_summary(features, daily, artifact, config)
    return ForecastResult(
        hours=features, daily=daily, summary=summary, artifact=artifact, config=config
    )


def aggregate_daily(features: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for day, group in features.groupby(features["time_local"].dt.date, sort=True):
        peak_idx = group["power_w"].idxmax()
        peak_row = group.loc[peak_idx]
        records.append(
            {
                "date": day.isoformat() if isinstance(day, date) else str(day),
                "energy_kwh": round(float(group["energy_kwh"].sum()), 2),
                "peak_w": round(float(group["power_w"].max()), 1),
                "peak_time": _iso(peak_row["time_local"])[11:16],
                "gti_max_wm2": round(float(group["global_tilted_irradiance"].max()), 1),
                "temp_max_c": round(float(group["temperature_2m"].max()), 1),
                "cloud_mean_pct": round(float(group["cloud_cover"].mean()), 1),
            }
        )
    return pd.DataFrame(records)


def build_summary(
    features: pd.DataFrame,
    daily: pd.DataFrame,
    artifact: dict[str, Any],
    config: AppConfig,
) -> dict[str, Any]:
    tz = features["time_local"].dt.tz
    now_local = pd.Timestamp.now(tz=tz) if tz is not None else pd.Timestamp.now()
    today = now_local.date()
    tomorrow = today + timedelta(days=1)
    dates = features["time_local"].dt.date

    total_kwh = float(features["energy_kwh"].sum())
    today_kwh = float(features.loc[dates == today, "energy_kwh"].sum())
    remaining_mask = (dates == today) & (features["time_local"] >= now_local)
    remaining_today_kwh = float(features.loc[remaining_mask, "energy_kwh"].sum())
    tomorrow_kwh = float(features.loc[dates == tomorrow, "energy_kwh"].sum())

    peak_idx = features["power_w"].idxmax()
    peak_row = features.loc[peak_idx]
    n_days = max(1, int(features["time_local"].dt.date.nunique()))
    capacity = config.system.kwp
    capacity_factor = total_kwh / (capacity * n_days * 24.0) * 100.0 if capacity > 0 else 0.0

    metrics = artifact.get("metrics", {})
    rmse = float(metrics.get("rmse_w") or 0.0)
    return {
        "site": {
            "name": config.site.name,
            "latitude": config.site.latitude,
            "longitude": config.site.longitude,
        },
        "system": {
            "kwp": config.system.kwp,
            "tilt": config.system.tilt,
            "azimuth": config.system.azimuth,
            "ac_kwp": config.system.ac_kwp,
        },
        "forecast_start": _iso(features["time_local"].iloc[0]),
        "forecast_end": _iso(features["time_local"].iloc[-1]),
        "total_kwh": round(total_kwh, 2),
        "today_kwh": round(today_kwh, 2),
        "remaining_today_kwh": round(remaining_today_kwh, 2),
        "tomorrow_kwh": round(tomorrow_kwh, 2),
        "peak_w": round(float(peak_row["power_w"]), 1),
        "peak_time": _iso(peak_row["time_local"]),
        "capacity_factor_pct": round(capacity_factor, 2),
        "days": n_days,
        "model": {
            "source": artifact.get("source"),
            "created_at": artifact.get("created_at"),
            "data_range": artifact.get("data_range"),
            "r2": metrics.get("r2"),
            "mae_w": metrics.get("mae_w"),
            "nmae_pct": metrics.get("nmae_pct"),
            "rmse_w": metrics.get("rmse_w"),
            "uncertainty_95_w": round(1.96 * rmse, 1) if rmse else None,
        },
    }
