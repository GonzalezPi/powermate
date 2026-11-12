"""Configuración de la instalación, del sistema fotovoltaico y del entrenamiento."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import project_root


@dataclass
class SiteConfig:
    """Ubicación geográfica de la instalación."""

    name: str = "Junín"
    latitude: float = -34.5833
    longitude: float = -60.95
    # "auto" deja que Open-Meteo resuelva la zona horaria a partir de las coordenadas.
    timezone: str = "America/Argentina/Buenos_Aires"


@dataclass
class SystemConfig:
    """Parámetros físicos del sistema fotovoltaico.

    ``azimuth`` sigue la convención de Open-Meteo:
    0 = sur, -90 = este, 90 = oeste, ±180 = norte.
    """

    kwp: float = 3.0
    tilt: float = 30.0
    azimuth: float = 0.0
    ac_kwp: float | None = 3.0
    inverter_efficiency: float = 0.96
    temp_coefficient: float = -0.0035
    noct: float = 45.0
    soiling_loss: float = 0.03
    wiring_loss: float = 0.02
    mismatch_loss: float = 0.02
    snow_loss: bool = True
    snow_zero_cm: float = 2.0

    @property
    def ac_limit_w(self) -> float:
        """Límite de potencia AC en vatios."""
        return (self.ac_kwp if self.ac_kwp is not None else self.kwp) * 1000.0

    @property
    def dc_capacity_w(self) -> float:
        return self.kwp * 1000.0


@dataclass
class TrainingConfig:
    """Parámetros del entrenamiento supervisado."""

    years: float = 2.0
    test_fraction: float = 0.2
    random_state: int = 42
    domain_randomization: bool = True
    poly: bool = False


@dataclass
class AppConfig:
    """Configuración completa de la aplicación."""

    site: SiteConfig = field(default_factory=SiteConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        data = data or {}
        return cls(
            site=_from_dataclass(SiteConfig, data.get("site")),
            system=_from_dataclass(SystemConfig, data.get("system")),
            training=_from_dataclass(TrainingConfig, data.get("training")),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        """Carga la configuración desde JSON.

        Si ``path`` es ``None`` se usa ``config.json`` en la raíz del proyecto.
        Si el archivo no existe se devuelve la configuración por defecto.
        """
        if path is None:
            path = project_root() / "config.json"
            if not Path(path).exists():
                return cls()
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No existe el archivo de configuración: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else project_root() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        return path

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def validate(self) -> None:
        s, sys_cfg, t = self.site, self.system, self.training
        if not -90.0 <= s.latitude <= 90.0:
            raise ValueError("La latitud debe estar entre -90 y 90.")
        if not -180.0 <= s.longitude <= 180.0:
            raise ValueError("La longitud debe estar entre -180 y 180.")
        if s.latitude in (90.0, -90.0):
            raise ValueError("La latitud no puede ser exactamente ±90 (sin orientación).")
        if sys_cfg.kwp <= 0:
            raise ValueError("La potencia pico (kWp) debe ser mayor que 0.")
        if not 0.0 <= sys_cfg.tilt <= 90.0:
            raise ValueError("La inclinación (tilt) debe estar entre 0 y 90 grados.")
        if not -180.0 <= sys_cfg.azimuth <= 180.0:
            raise ValueError("El azimut debe estar entre -180 y 180 (0 = sur).")
        if sys_cfg.ac_kwp is not None and sys_cfg.ac_kwp <= 0:
            raise ValueError("La potencia AC (ac_kwp) debe ser mayor que 0 o null.")
        if not 0.0 < sys_cfg.inverter_efficiency <= 1.0:
            raise ValueError("El rendimiento del inversor debe estar en (0, 1].")
        for name in ("soiling_loss", "wiring_loss", "mismatch_loss"):
            value = getattr(sys_cfg, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name} debe estar en [0, 1).")
        if not -0.02 <= sys_cfg.temp_coefficient <= 0.0:
            raise ValueError(
                "El coeficiente de temperatura debe estar entre -0.02 y 0 (por °C)."
            )
        if sys_cfg.noct <= 0:
            raise ValueError("El NOCT debe ser mayor que 0.")
        if not 0.05 <= t.years <= 20.0:
            raise ValueError("Los años de histórico deben estar entre 0.05 y 20.")
        if not 0.05 <= t.test_fraction <= 0.5:
            raise ValueError("test_fraction debe estar entre 0.05 y 0.5.")

    def model_key(self) -> str:
        """Identificador estable para la instalación configurada."""
        ac = "none" if self.system.ac_kwp is None else f"{self.system.ac_kwp:.3f}"
        payload = "|".join(
            [
                f"{self.site.latitude:.4f}",
                f"{self.site.longitude:.4f}",
                f"{self.system.kwp:.3f}",
                f"{self.system.tilt:.1f}",
                f"{self.system.azimuth:.1f}",
                ac,
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]

    def model_path(self) -> Path:
        return project_root() / "data" / "models" / f"model_{self.model_key()}.joblib"

    def metrics_path(self) -> Path:
        return project_root() / "data" / "models" / f"metrics_{self.model_key()}.json"

    def describe(self) -> str:
        az = self.system.azimuth
        if abs(az) < 5:
            orientation = "sur"
        elif az < 0:
            orientation = f"{-az:.0f}° hacia el este"
        else:
            orientation = f"{az:.0f}° hacia el oeste"
        ac = "sin límite" if self.system.ac_kwp is None else f"{self.system.ac_kwp:.2f} kW"
        return (
            f"{self.site.name} ({self.site.latitude:.4f}, {self.site.longitude:.4f}) · "
            f"{self.system.kwp:.2f} kWp · {self.system.tilt:.0f}° inclinación · "
            f"azimut {az:.0f}° ({orientation}) · inversor {ac}"
        )


def _from_dataclass(cls: type, data: dict[str, Any] | None):
    """Construye un dataclass ignorando claves desconocidas."""
    if not data:
        return cls()
    valid = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in valid})
