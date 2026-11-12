"""Pruebas del modelo de regresión y su persistencia."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solar_ai.config import AppConfig
from solar_ai.features import FEATURES
from solar_ai.model import (
    load_artifact,
    predict_power,
    regression_metrics,
    save_artifact,
    train_model,
)


def _dataset(n: int = 600) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(11)
    X = pd.DataFrame(rng.normal(size=(n, len(FEATURES))), columns=FEATURES)
    X["gti_temp"] = rng.uniform(0.0, 1.0, n)
    X["temp_factor"] = rng.uniform(0.85, 1.05, n)
    y = 2800.0 * X["gti_temp"] + 15.0 + rng.normal(0.0, 8.0, n)
    return X, pd.Series(y)


def test_train_metrics_and_prediction():
    X, y = _dataset()
    artifact = train_model(
        X,
        y,
        AppConfig(),
        source="test",
        baseline_X=X[["gti_temp"]],
    )
    assert artifact["metrics"]["r2"] > 0.99
    assert artifact["metrics"]["mae_w"] < 30.0
    pred = predict_power(artifact, X)
    assert pred.shape == (len(X),)
    assert (pred >= 0).all()
    assert (pred <= 3000.0).all()


def test_save_and_load_artifact(tmp_path):
    X, y = _dataset(300)
    artifact = train_model(X, y, AppConfig(), source="test", baseline_X=X[["gti_temp"]])
    model_path = tmp_path / "model.joblib"
    metrics_path = tmp_path / "metrics.json"
    save_artifact(artifact, model_path, metrics_path)
    assert model_path.exists() and metrics_path.exists()
    loaded = load_artifact(model_path)
    assert loaded["feature_names"] == artifact["feature_names"]
    assert np.allclose(predict_power(loaded, X), predict_power(artifact, X))


def test_too_few_samples_raises():
    X, y = _dataset(40)
    with pytest.raises(ValueError, match="muy pocas"):
        train_model(X, y, AppConfig(), source="test")


def test_metrics_are_non_negative():
    metrics = regression_metrics([0, 1000, 2000], [0, 900, 2100], 3000)
    assert metrics["mae_w"] >= 0
    assert metrics["rmse_w"] >= metrics["mae_w"]
    assert metrics["r2"] > 0.9
