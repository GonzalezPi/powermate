"""Utilidades comunes: rutas, caché, HTTP, logging y formato."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

LOGGER_NAME = "solar_ai"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_dirs() -> None:
    for sub in ("data/cache", "data/models", "reports"):
        (project_root() / sub).mkdir(parents=True, exist_ok=True)


def setup_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def short_hash(payload: str, length: int = 12) -> str:
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:length]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def cache_file(namespace: str, key: str) -> Path:
    return project_root() / "data" / "cache" / f"{namespace}_{key}.json.gz"


def cache_read(path: Path, max_age_seconds: float | None = None) -> Any | None:
    """Lee una entrada de caché (JSON comprimido). Devuelve None si no es válida."""
    if not path.exists():
        return None
    if max_age_seconds is not None:
        age = time.time() - path.stat().st_mtime
        if age > max_age_seconds:
            return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def cache_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def http_get_json(
    url: str,
    params: dict[str, Any],
    *,
    retries: int = 3,
    timeout: float = 45.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """GET con reintentos y mensajes de error claros."""
    logger = get_logger()
    sess = session or requests.Session()
    headers = {"User-Agent": "SolarPredictAI/1.0 (+https://open-meteo.com/)"}
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = sess.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("error"):
                    reason = payload.get("reason", "error desconocido")
                    raise RuntimeError(f"Open-Meteo devolvió un error: {reason}")
                return payload
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(
                    f"Open-Meteo respondió HTTP {response.status_code}"
                )
            else:
                try:
                    detail = response.json().get("reason", response.text[:200])
                except Exception:  # noqa: BLE001
                    detail = response.text[:200]
                raise RuntimeError(
                    f"Open-Meteo respondió HTTP {response.status_code}: {detail}"
                )
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                delay = 1.5 * (2 ** (attempt - 1))
                logger.warning(
                    "Error consultando Open-Meteo (%s). Reintento %d/%d en %.0fs…",
                    exc,
                    attempt,
                    retries,
                    delay,
                )
                time.sleep(delay)

    raise RuntimeError(
        f"No se pudo consultar Open-Meteo tras {retries} intentos: {last_error}"
    )


def format_power(watts: float) -> str:
    if abs(watts) >= 1000:
        return f"{watts / 1000.0:.2f} kW"
    return f"{watts:.0f} W"


def format_kwh(kwh: float) -> str:
    return f"{kwh:.2f} kWh"


def resolve_zoneinfo(name: str | None):
    """Devuelve un ZoneInfo válido o UTC como fallback."""
    from zoneinfo import ZoneInfo

    if not name or name == "auto":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")
