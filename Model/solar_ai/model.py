"""Modelo de regresión lineal, validación temporal y persistencia."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from .config import AppConfig
from .utils import get_logger, now_utc


def _make_model(poly: bool) -> Pipeline:
    steps: list[tuple[str, Any]] = []
    if poly:
        # Interacciones de segundo orden: sigue siendo un modelo lineal en
        # sus coeficientes, pero captura curvaturas suaves (saturación, etc.).
        steps.append(
            (
                "poly",
                PolynomialFeatures(degree=2, interaction_only=True, include_bias=False),
            )
        )
    steps.extend(
        [
            ("scaler", StandardScaler()),
            ("regression", LinearRegression()),
        ]
    )
    return Pipeline(steps)


def regression_metrics(
    y_true: np.ndarray | pd.Series,
    y_pred: np.ndarray | pd.Series,
    capacity_w: float,
) -> dict[str, float]:
    """Métricas habituales, expresadas también respecto a la potencia pico."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), 0.0, None)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) == 0:
        raise ValueError("No hay muestras válidas para calcular métricas.")

    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    r2 = float(r2_score(y_true, y_pred)) if len(y_true) > 1 else float("nan")
    daylight = y_true > max(1.0, 0.02 * capacity_w)
    mape = (
        float(np.mean(np.abs((y_true[daylight] - y_pred[daylight]) / y_true[daylight])) * 100.0)
        if daylight.any()
        else float("nan")
    )
    return {
        "r2": round(r2, 5),
        "mae_w": round(mae, 2),
        "rmse_w": round(rmse, 2),
        "mape_pct": round(mape, 2),
        "nmae_pct": round(mae / capacity_w * 100.0, 2),
        "nrmse_pct": round(rmse / capacity_w * 100.0, 2),
        "samples": int(len(y_true)),
    }


def train_model(
    X: pd.DataFrame,
    y: pd.Series,
    config: AppConfig,
    *,
    source: str,
    data_range: str | None = None,
    nominal_y: pd.Series | None = None,
    baseline_X: pd.DataFrame | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entrena y evalúa el modelo con partición cronológica y CV temporal."""
    logger = get_logger()
    valid = y.notna()
    X = X.loc[valid].reset_index(drop=True)
    y = y.loc[valid].reset_index(drop=True)
    nominal_y = nominal_y.loc[valid].reset_index(drop=True) if nominal_y is not None else None
    baseline_X = (
        baseline_X.loc[valid].reset_index(drop=True)
        if baseline_X is not None
        else None
    )

    n = len(X)
    if n < 100:
        raise ValueError(
            f"Hay muy pocas muestras para entrenar ({n}). Se necesitan al menos 100."
        )

    split = int(n * (1.0 - config.training.test_fraction))
    split = max(50, min(split, n - 30))
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]

    poly = bool(config.training.poly)
    capacity = config.system.dc_capacity_w

    # Validación cruzada temporal (sin mezclar pasado y futuro).
    n_splits = min(5, max(2, len(X_train) // 250))
    cv_metrics: list[dict[str, float]] = []
    if len(X_train) > n_splits * 10:
        for fold, (tr, te) in enumerate(
            TimeSeriesSplit(n_splits=n_splits).split(X_train), start=1
        ):
            fold_model = _make_model(poly)
            fold_model.fit(X_train.iloc[tr], y_train.iloc[tr])
            fold_pred = np.clip(fold_model.predict(X_train.iloc[te]), 0.0, None)
            cv_metrics.append(
                regression_metrics(y_train.iloc[te], fold_pred, capacity)
            )
        logger.debug("CV temporal terminada con %d folds.", len(cv_metrics))

    model = _make_model(poly)
    model.fit(X_train, y_train)
    pred = np.clip(model.predict(X_test), 0.0, config.system.ac_limit_w)
    metrics = regression_metrics(y_test, pred, capacity)
    metrics["train_samples"] = int(len(X_train))
    metrics["test_samples"] = int(len(y_test))
    metrics["capacity_w"] = round(capacity, 2)

    if cv_metrics:
        for key in ("r2", "mae_w", "rmse_w", "nmae_pct"):
            metrics[f"cv_{key}"] = round(
                float(np.nanmean([m[key] for m in cv_metrics])), 5
            )
        metrics["cv_folds"] = len(cv_metrics)

    # Referencia sencilla: regresión lineal solo con irradiancia y temperatura
    # ambiente (sin geometría solar ni temperatura de célula).
    if baseline_X is not None:
        base_train = baseline_X.iloc[:split]
        base_test = baseline_X.iloc[split:]
    else:
        base_train = X_train[["gti_temp"]]
        base_test = X_test[["gti_temp"]]
    baseline = LinearRegression()
    baseline.fit(base_train, y_train)
    baseline_pred = np.clip(
        baseline.predict(base_test), 0.0, config.system.ac_limit_w
    )
    baseline_metrics = regression_metrics(y_test, baseline_pred, capacity)
    metrics["baseline_r2"] = baseline_metrics["r2"]
    metrics["baseline_mae_w"] = baseline_metrics["mae_w"]
    metrics["baseline_nmae_pct"] = baseline_metrics["nmae_pct"]

    if nominal_y is not None:
        nominal_test = nominal_y.iloc[split:]
        metrics["mae_vs_physics_w"] = round(
            float(mean_absolute_error(nominal_test, pred)), 2
        )
        metrics["mae_vs_physics_pct"] = round(
            float(mean_absolute_error(nominal_test, pred)) / capacity * 100.0, 2
        )

    artifact: dict[str, Any] = {
        "schema_version": 1,
        "created_at": now_utc().isoformat(),
        "source": source,
        "target_unit": "W",
        "feature_names": list(X.columns),
        "poly": poly,
        "model": model,
        "config": config.to_dict(),
        "metrics": metrics,
        "data_range": data_range,
        "extra": extra or {},
    }
    return artifact


def save_artifact(
    artifact: dict[str, Any],
    path: Path | None = None,
    metrics_path: Path | None = None,
) -> Path:
    config = AppConfig.from_dict(artifact.get("config"))
    path = path or config.model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)

    metrics_path = metrics_path or config.metrics_path()
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "created_at": artifact.get("created_at"),
                "source": artifact.get("source"),
                "data_range": artifact.get("data_range"),
                "metrics": artifact.get("metrics"),
                "config": artifact.get("config"),
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    return path


def load_artifact(path: Path | None = None, config: AppConfig | None = None) -> dict[str, Any]:
    if path is None:
        if config is None:
            config = AppConfig()
        path = config.model_path()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el modelo entrenado: {path}")
    artifact = joblib.load(path)
    if not isinstance(artifact, dict) or "model" not in artifact:
        raise RuntimeError(f"El archivo {path} no contiene un modelo válido.")
    return artifact


def predict_power(
    artifact: dict[str, Any],
    X: pd.DataFrame,
    *,
    ac_limit_w: float | None = None,
) -> np.ndarray:
    names = artifact["feature_names"]
    missing = [c for c in names if c not in X.columns]
    if missing:
        raise ValueError(f"Faltan columnas para predecir: {', '.join(missing)}")
    power = artifact["model"].predict(X[names])
    power = np.clip(np.asarray(power, dtype=float), 0.0, None)
    if ac_limit_w is not None:
        power = np.minimum(power, ac_limit_w)
    return power


def artifact_summary(artifact: dict[str, Any]) -> str:
    metrics = artifact.get("metrics", {})
    return (
        f"fuente={artifact.get('source')} · "
        f"R²={metrics.get('r2')} · "
        f"MAE={metrics.get('mae_w')} W ({metrics.get('nmae_pct')}% de la pico) · "
        f"entrenado={str(artifact.get('created_at'))[:19]}"
    )
