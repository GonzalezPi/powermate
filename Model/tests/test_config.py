"""Pruebas de configuración."""

from __future__ import annotations

import json

import pytest

from solar_ai.config import AppConfig


def test_roundtrip(tmp_path):
    config = AppConfig()
    config.site.name = "Valencia"
    config.system.kwp = 5.5
    path = config.save(tmp_path / "config.json")
    loaded = AppConfig.load(path)
    assert loaded.site.name == "Valencia"
    assert loaded.system.kwp == 5.5
    assert loaded.model_key() == config.model_key()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"site": {"latitude": 39.5}, "desconocido": 123}), encoding="utf-8"
    )
    config = AppConfig.load(path)
    assert config.site.latitude == 39.5
    assert config.system.kwp == 3.0


def test_model_key_changes_with_system():
    a = AppConfig()
    b = AppConfig()
    b.system.kwp = 5.0
    assert a.model_key() != b.model_key()


def test_invalid_configuration_raises():
    config = AppConfig()
    config.system.tilt = 120
    with pytest.raises(ValueError, match="inclinación"):
        config.validate()
    config = AppConfig()
    config.system.kwp = 0
    with pytest.raises(ValueError, match="kWp"):
        config.validate()
