"""Interfaz de línea de comandos de SolarPredict AI."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .calibrate import calibrate
from .config import AppConfig
from .model import artifact_summary, load_artifact
from .predict import ForecastResult, run_forecast
from .report import write_html_report
from .train import train
from .utils import format_kwh, format_power, get_logger, setup_logging


# ----------------------------------------------------------------------
# Utilidades de presentación
# ----------------------------------------------------------------------
def _hr(char: str = "=", width: int = 78) -> str:
    return char * width


def _fmt_ts(value: Any, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = pd.Timestamp(value)
        except Exception:  # noqa: BLE001
            return value
    if isinstance(value, pd.Timestamp):
        return value.strftime(fmt)
    return str(value)


def _table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    head = "  ".join(
        h.ljust(widths[i]) if aligns[i] == "l" else h.rjust(widths[i])
        for i, h in enumerate(headers)
    )
    lines.append(head)
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(
            "  ".join(
                cell.ljust(widths[i]) if aligns[i] == "l" else cell.rjust(widths[i])
                for i, cell in enumerate(row)
            )
        )
    return "\n".join(lines)


def _print_header(config: AppConfig) -> None:
    print(_hr())
    print("  SolarPredict AI · predicción de generación fotovoltaica")
    print(f"  {config.describe()}")
    print(_hr())


def _apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    site, system, training = config.site, config.system, config.training
    if getattr(args, "name", None):
        site.name = args.name
    if getattr(args, "lat", None) is not None:
        site.latitude = args.lat
    if getattr(args, "lon", None) is not None:
        site.longitude = args.lon
    if getattr(args, "timezone", None):
        site.timezone = args.timezone
    if getattr(args, "kwp", None) is not None:
        system.kwp = args.kwp
    if getattr(args, "tilt", None) is not None:
        system.tilt = args.tilt
    if getattr(args, "azimuth", None) is not None:
        system.azimuth = args.azimuth
    if getattr(args, "ac_kwp", None) is not None:
        system.ac_kwp = args.ac_kwp
    if getattr(args, "no_ac_limit", False):
        system.ac_kwp = None
    if getattr(args, "years", None) is not None:
        training.years = args.years
    if getattr(args, "test_fraction", None) is not None:
        training.test_fraction = args.test_fraction
    if getattr(args, "poly", None):
        training.poly = True
    if getattr(args, "no_randomization", False):
        training.domain_randomization = False
    config.validate()
    return config


def _maybe_save_config(config: AppConfig, args: argparse.Namespace) -> None:
    if getattr(args, "save_config", False):
        path = config.save()
        print(f"Configuración guardada en {path}")


# ----------------------------------------------------------------------
# Comandos
# ----------------------------------------------------------------------
def cmd_train(args: argparse.Namespace) -> int:
    config = _apply_overrides(AppConfig.load(args.config), args)
    _print_header(config)
    artifact = train(config)
    _maybe_save_config(config, args)
    _print_train_result(artifact, config)
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    config = _apply_overrides(AppConfig.load(args.config), args)
    _print_header(config)
    artifact = calibrate(
        config,
        args.csv,
        time_col=args.time_col,
        power_col=args.power_col,
        power_unit=args.power_unit,
        timezone=args.tz,
    )
    _maybe_save_config(config, args)
    _print_train_result(artifact, config)
    extra = artifact.get("extra", {})
    if "measured_peak_w" in extra:
        print(
            f"  Pico medido en el CSV: {format_power(extra['measured_peak_w'])} · "
            f"media: {format_power(extra['measured_mean_w'])}"
        )
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    config = _apply_overrides(AppConfig.load(args.config), args)
    _print_header(config)

    artifact = None
    if args.model:
        artifact = load_artifact(Path(args.model))
        saved_cfg = AppConfig.from_dict(artifact.get("config"))
        if abs(saved_cfg.system.kwp - config.system.kwp) > 1e-9:
            print(
                "Aviso: el modelo se entrenó para "
                f"{saved_cfg.system.kwp:.2f} kWp, pero la configuración actual es "
                f"{config.system.kwp:.2f} kWp."
            )

    result = run_forecast(
        config,
        days=args.days,
        artifact=artifact,
        auto_train=not args.no_auto_train,
    )
    _print_forecast(result)
    _maybe_save_config(config, args)

    if args.json:
        path = result.save_json(args.json)
        print(f"\nJSON guardado en {path}")
    if args.csv:
        path = result.save_csv(args.csv)
        print(f"CSV guardado en {path}")
    if args.html is not None:
        path = write_html_report(result, args.html or None)
        print(f"Informe HTML guardado en {path}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    _print_header(config)
    print(f"  Clave de modelo: {config.model_key()}")
    print(f"  Ruta esperada:   {config.model_path()}")
    print(f"  Zona horaria:    {config.site.timezone}")
    print(f"  Histórico:       {config.training.years:.1f} años")
    print()
    try:
        artifact = load_artifact(config=config)
    except FileNotFoundError:
        print("  No hay modelo entrenado para esta configuración.")
        print("  Ejecuta: python main.py train")
        return 0

    print(f"  Modelo: {artifact_summary(artifact)}")
    metrics = artifact.get("metrics", {})
    print("\n  Métricas de test (partición cronológica):")
    print(
        _table(
            ["R²", "MAE", "RMSE", "nMAE", "MAPE"],
            [
                [
                    f"{metrics.get('r2')}",
                    f"{metrics.get('mae_w')} W",
                    f"{metrics.get('rmse_w')} W",
                    f"{metrics.get('nmae_pct')}%",
                    f"{metrics.get('mape_pct')}%",
                ]
            ],
            ["r", "r", "r", "r", "r"],
        )
    )
    print("\n  Comparación con línea base (solo irradiancia + temperatura):")
    print(
        f"    R² {metrics.get('baseline_r2')} · "
        f"MAE {metrics.get('baseline_mae_w')} W "
        f"({metrics.get('baseline_nmae_pct')}% de la pico)"
    )
    if "cv_r2" in metrics:
        print(
            f"\n  Validación cruzada temporal ({metrics.get('cv_folds')} folds): "
            f"R² {metrics.get('cv_r2')} · MAE {metrics.get('cv_mae_w')} W"
        )
    print(f"\n  Datos: {artifact.get('data_range')}")
    print(f"  Creado: {str(artifact.get('created_at'))[:19]}")
    return 0


# ----------------------------------------------------------------------
# Salidas
# ----------------------------------------------------------------------
def _print_train_result(artifact: dict[str, Any], config: AppConfig) -> None:
    metrics = artifact.get("metrics", {})
    extra = artifact.get("extra", {})
    print("\nModelo entrenado correctamente.")
    print(f"  Fuente:        {artifact.get('source')}")
    print(f"  Datos:         {artifact.get('data_range')}")
    print(
        f"  Muestras:      {metrics.get('train_samples')} entrenamiento · "
        f"{metrics.get('test_samples')} test"
    )
    if "training_rows" in extra:
        print(f"  Horas diurnas: {extra['training_rows']}")
    print(
        f"  Test:          R² {metrics.get('r2')} · "
        f"MAE {metrics.get('mae_w')} W ({metrics.get('nmae_pct')}% de la pico) · "
        f"RMSE {metrics.get('rmse_w')} W"
    )
    if "cv_r2" in metrics:
        print(
            f"  CV temporal:   R² {metrics.get('cv_r2')} · "
            f"MAE {metrics.get('cv_mae_w')} W ({metrics.get('cv_nmae_pct')}%)"
        )
    print(
        f"  Baseline:      R² {metrics.get('baseline_r2')} · "
        f"MAE {metrics.get('baseline_mae_w')} W "
        f"({metrics.get('baseline_nmae_pct')}% de la pico)"
    )
    if "mae_vs_physics_pct" in metrics:
        print(
            f"  Vs. gemelo:    MAE {metrics.get('mae_vs_physics_w')} W "
            f"({metrics.get('mae_vs_physics_pct')}% de la pico)"
        )
    print(f"  Modelo:        {config.model_path()}")
    print(f"  Métricas:      {config.metrics_path()}")


def _print_forecast(result: ForecastResult) -> None:
    s = result.summary
    model = s["model"]
    print(
        f"\nPronóstico: {_fmt_ts(s['forecast_start'])} → {_fmt_ts(s['forecast_end'])} "
        f"({s['days']} días)"
    )
    print(
        f"  Total:          {format_kwh(s['total_kwh'])} · "
        f"pico {format_power(s['peak_w'])} ({_fmt_ts(s['peak_time'], '%d/%m %H:%M')}) · "
        f"factor de capacidad {s['capacity_factor_pct']:.1f}%"
    )
    print(
        f"  Hoy:            {format_kwh(s['today_kwh'])} (día completo) · "
        f"restante {format_kwh(s['remaining_today_kwh'])} · "
        f"mañana {format_kwh(s['tomorrow_kwh'])}"
    )
    uncertainty = model.get("uncertainty_95_w")
    unc_txt = f" · incertidumbre ±{format_power(uncertainty)}" if uncertainty else ""
    print(
        f"  Modelo:         R² {model.get('r2')} · MAE {model.get('mae_w')} W "
        f"({model.get('nmae_pct')}% de la pico){unc_txt}"
    )

    print("\nResumen diario:")
    rows = []
    for row in result.daily.itertuples(index=False):
        rows.append(
            [
                str(row.date),
                f"{row.energy_kwh:.2f}",
                f"{row.peak_w / 1000:.2f}",
                str(row.peak_time),
                f"{row.gti_max_wm2:.0f}",
                f"{row.temp_max_c:.1f}",
                f"{row.cloud_mean_pct:.0f}%",
            ]
        )
    print(
        _table(
            ["Fecha", "kWh", "Pico kW", "Hora", "GTI máx", "T máx", "Nubes"],
            rows,
            ["l", "r", "r", "l", "r", "r", "r"],
        )
    )

    print("\nDetalle horario (primeras 24 h):")
    shown = result.hours.head(24)
    rows = []
    for row in shown.itertuples(index=False):
        rows.append(
            [
                row.time_local.strftime("%d/%m %H:%M"),
                f"{row.power_w / 1000:.2f}",
                f"{row.energy_kwh:.2f}",
                f"{row.global_tilted_irradiance:.0f}",
                f"{row.temperature_2m:.1f}",
                f"{row.cloud_cover:.0f}%",
            ]
        )
    print(
        _table(
            ["Hora local", "kW", "kWh", "GTI W/m²", "T °C", "Nubes"],
            rows,
            ["l", "r", "r", "r", "r", "r"],
        )
    )
    print(
        "\nUsa --csv, --json o --html para exportar el detalle completo "
        f"({len(result.hours)} horas)."
    )


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser, *, training: bool = False) -> None:
    parser.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="Mostrar detalles de depuración",
    )
    parser.add_argument("--config", type=str, default=None, help="Ruta de config.json")
    parser.add_argument("--name", type=str, help="Nombre de la ubicación")
    parser.add_argument("--lat", type=float, help="Latitud")
    parser.add_argument("--lon", type=float, help="Longitud")
    parser.add_argument("--timezone", type=str, help="Zona horaria IANA o 'auto'")
    parser.add_argument("--kwp", type=float, help="Potencia pico DC en kWp")
    parser.add_argument("--tilt", type=float, help="Inclinación en grados (0-90)")
    parser.add_argument("--azimuth", type=float, help="Azimut: 0=sur, -90=este, 90=oeste")
    parser.add_argument("--ac-kwp", type=float, help="Potencia AC del inversor en kW")
    parser.add_argument(
        "--no-ac-limit", action="store_true", help="Desactivar el límite del inversor"
    )
    parser.add_argument(
        "--save-config", action="store_true", help="Guardar los cambios en config.json"
    )
    if training:
        parser.add_argument("--years", type=float, help="Años de histórico para entrenar")
        parser.add_argument(
            "--test-fraction", type=float, help="Fracción de test (0.05-0.5)"
        )
        parser.add_argument(
            "--poly", action="store_true", default=None,
            help="Añadir interacciones polinómicas (sigue siendo regresión lineal)",
        )
        parser.add_argument(
            "--no-randomization", action="store_true",
            help="Desactivar la aleatorización de parámetros del gemelo digital",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solar-ai",
        description="Predicción de generación solar con Open-Meteo y regresión lineal.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Mostrar detalles de depuración")
    sub = parser.add_subparsers(dest="command", required=True)

    p_train = sub.add_parser(
        "train", help="Entrenar el modelo con histórico de Open-Meteo"
    )
    _add_common(p_train, training=True)
    p_train.set_defaults(func=cmd_train)

    p_predict = sub.add_parser("predict", help="Predecir la generación futura")
    _add_common(p_predict)
    p_predict.add_argument(
        "--days", type=int, default=7, help="Días de pronóstico (1-16, por defecto 7)"
    )
    p_predict.add_argument("--model", type=str, help="Ruta de un modelo .joblib concreto")
    p_predict.add_argument(
        "--no-auto-train", action="store_true",
        help="No entrenar automáticamente si falta el modelo",
    )
    p_predict.add_argument("--json", type=str, help="Exportar resultados a JSON")
    p_predict.add_argument("--csv", type=str, help="Exportar resultados a CSV")
    p_predict.add_argument(
        "--html", nargs="?", const="", default=None,
        help="Generar informe HTML (ruta opcional)",
    )
    p_predict.set_defaults(func=cmd_predict)

    p_cal = sub.add_parser(
        "calibrate", help="Recalibrar con un CSV de producción real"
    )
    _add_common(p_cal, training=True)
    p_cal.add_argument("--csv", required=True, help="CSV con fecha y potencia")
    p_cal.add_argument("--time-col", type=str, help="Nombre de la columna de fecha")
    p_cal.add_argument("--power-col", type=str, help="Nombre de la columna de potencia")
    p_cal.add_argument(
        "--power-unit", type=str, default="auto", choices=["auto", "W", "kW"],
        help="Unidad de la potencia del CSV (por defecto auto)",
    )
    p_cal.add_argument(
        "--tz", type=str, help="Zona horaria para fechas sin offset (por defecto UTC)"
    )
    p_cal.set_defaults(func=cmd_calibrate)

    p_info = sub.add_parser("info", help="Mostrar configuración y métricas del modelo")
    p_info.add_argument("--config", type=str, default=None)
    p_info.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="Mostrar detalles de depuración",
    )
    p_info.set_defaults(func=cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    logger = get_logger()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        logger.error("Cancelado por el usuario.")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.error("%s", exc)
        if args.verbose:
            traceback.print_exc()
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
