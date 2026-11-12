"""Cliente de Open-Meteo (forecast + archive) con caché local.

Documentación oficial: https://open-meteo.com/en/docs
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

from .config import AppConfig
from .utils import (
    cache_file,
    cache_read,
    cache_write,
    get_logger,
    http_get_json,
    short_hash,
)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Variables horarias que alimentan las características del modelo.
HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_10m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    # Open-Meteo calcula la irradiancia sobre el plano inclinado del panel.
    "global_tilted_irradiance",
    "snow_depth",
    "precipitation",
    "weather_code",
    "is_day",
]

NUMERIC_COLUMNS = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_10m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
    "snow_depth",
    "precipitation",
    "weather_code",
    "is_day",
]


class OpenMeteoClient:
    """Consulta Open-Meteo y normaliza la respuesta a un DataFrame UTC."""

    def __init__(
        self,
        *,
        forecast_ttl: float = 1800.0,
        cache_enabled: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self.forecast_ttl = forecast_ttl
        self.cache_enabled = cache_enabled
        self.session = session or requests.Session()
        self.logger = get_logger()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def fetch_forecast(self, config: AppConfig, days: int = 7) -> pd.DataFrame:
        """Descarga el pronóstico horario para los próximos ``days`` días."""
        if not 1 <= days <= 16:
            raise ValueError("El número de días de pronóstico debe estar entre 1 y 16.")
        params = self._base_params(config)
        params.update({"forecast_days": days})
        key = short_hash(json.dumps(params, sort_keys=True, default=str))
        path = cache_file("forecast", key)
        payload = self._cached(path, self.forecast_ttl)
        if payload is None:
            self.logger.info("Consultando pronóstico de Open-Meteo…")
            payload = http_get_json(FORECAST_URL, params, session=self.session)
            self._store(path, payload)
        df = self._to_frame(payload)
        df.attrs["source"] = "forecast"
        return df

    def fetch_archive(
        self,
        config: AppConfig,
        start_date: date,
        end_date: date,
        *,
        chunk_days: int = 180,
    ) -> pd.DataFrame:
        """Descarga el histórico horario entre dos fechas (inclusive).

        Se trocea en bloques para evitar respuestas demasiado grandes y se
        cachea cada bloque de forma permanente.
        """
        if start_date > end_date:
            raise ValueError("La fecha de inicio no puede ser posterior a la final.")
        frames: list[pd.DataFrame] = []
        cursor = start_date
        while cursor <= end_date:
            chunk_end = min(cursor + timedelta(days=chunk_days - 1), end_date)
            params = self._base_params(config)
            params.update(
                {
                    "start_date": cursor.isoformat(),
                    "end_date": chunk_end.isoformat(),
                }
            )
            key = short_hash(json.dumps(params, sort_keys=True, default=str))
            path = cache_file("archive", key)
            payload = self._cached(path, None)
            if payload is None:
                self.logger.info(
                    "Descargando histórico %s → %s…", cursor.isoformat(), chunk_end.isoformat()
                )
                payload = http_get_json(ARCHIVE_URL, params, session=self.session)
                self._store(path, payload)
            frames.append(self._to_frame(payload))
            cursor = chunk_end + timedelta(days=1)

        if not frames:
            raise RuntimeError("Open-Meteo no devolvió datos históricos.")
        df = pd.concat(frames, ignore_index=True)
        df = (
            df.drop_duplicates(subset="time_utc")
            .sort_values("time_utc")
            .reset_index(drop=True)
        )
        df.attrs["source"] = "archive"
        df.attrs["timezone"] = frames[0].attrs.get("timezone", "UTC")
        return df

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------
    def _base_params(self, config: AppConfig) -> dict[str, Any]:
        return {
            "latitude": round(config.site.latitude, 4),
            "longitude": round(config.site.longitude, 4),
            "hourly": ",".join(HOURLY_VARIABLES),
            "tilt": round(config.system.tilt, 2),
            "azimuth": round(config.system.azimuth, 2),
            "timezone": "auto",
            # Timestamps Unix en UTC real: evita el desfase de una hora que
            # introduce Open-Meteo al usar offsets fijos a través del DST.
            "timeformat": "unixtime",
        }

    def _cached(self, path, ttl: float | None) -> dict[str, Any] | None:
        if not self.cache_enabled:
            return None
        return cache_read(path, ttl)

    def _store(self, path, payload: dict[str, Any]) -> None:
        if self.cache_enabled:
            cache_write(path, payload)

    @staticmethod
    def _to_frame(payload: dict[str, Any]) -> pd.DataFrame:
        hourly = payload.get("hourly")
        if not hourly or "time" not in hourly:
            raise RuntimeError(
                "La respuesta de Open-Meteo no contiene datos horarios. "
                f"Respuesta: {str(payload)[:300]}"
            )

        times = hourly["time"]
        if not times:
            raise RuntimeError("Open-Meteo devolvió una lista de horas vacía.")
        first = times[0]
        if isinstance(first, str) and not first.lstrip("-").isdigit():
            # Compatibilidad con respuestas ISO (p. ej. pruebas o API antigua).
            parsed = pd.to_datetime(times, errors="coerce")
            offset = pd.Timedelta(seconds=int(payload.get("utc_offset_seconds", 0)))
            time_utc = (pd.DatetimeIndex(parsed) - offset).tz_localize("UTC")
        else:
            # timeformat=unixtime: segundos Unix en UTC real.
            time_utc = pd.to_datetime(times, unit="s", utc=True, errors="coerce")

        data: dict[str, Any] = {"time_utc": time_utc}
        for name, values in hourly.items():
            if name == "time":
                continue
            data[name] = values

        df = pd.DataFrame(data)
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Variables que falten se crean vacías para que las características
        # puedan rellenarlas con valores conservadores.
        for col in NUMERIC_COLUMNS:
            if col not in df.columns:
                df[col] = float("nan")

        df = df.sort_values("time_utc").reset_index(drop=True)
        df.attrs["timezone"] = payload.get("timezone") or "UTC"
        df.attrs["latitude"] = payload.get("latitude")
        df.attrs["longitude"] = payload.get("longitude")
        df.attrs["elevation"] = payload.get("elevation")
        df.attrs["utc_offset_seconds"] = int(payload.get("utc_offset_seconds", 0))
        return df


def default_archive_range(years: float, *, delay_days: int = 6) -> tuple[date, date]:
    """Rango de histórico que suele estar ya disponible en el archivo."""
    end = date.today() - timedelta(days=delay_days)
    days = max(30, int(round(365.25 * years)))
    start = end - timedelta(days=days)
    return start, end
