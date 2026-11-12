"""Informe HTML autocontenido con gráfico SVG y tablas."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from .utils import project_root

if TYPE_CHECKING:  # pragma: no cover
    from .predict import ForecastResult


def _fmt_ts(value: Any, fmt: str = "%d/%m %H:%M") -> str:
    if isinstance(value, str):
        try:
            value = pd.Timestamp(value)
        except Exception:  # noqa: BLE001
            return value
    if isinstance(value, pd.Timestamp):
        return value.strftime(fmt)
    return str(value)


def _svg_chart(hours: pd.DataFrame, width: int = 1120, height: int = 340) -> str:
    n = len(hours)
    if n == 0:
        return "<p>Sin datos.</p>"

    ml, mr, mt, mb = 62, 22, 18, 46
    plot_w = width - ml - mr
    plot_h = height - mt - mb
    peak = float(hours["power_w"].max())
    y_max = max(peak * 1.12, 100.0)

    def x_at(i: int) -> float:
        return ml + (plot_w * i / max(n - 1, 1))

    def y_at(power: float) -> float:
        return mt + plot_h * (1.0 - max(power, 0.0) / y_max)

    points = [(x_at(i), y_at(float(p))) for i, p in enumerate(hours["power_w"])]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    base_y = mt + plot_h
    area = (
        f"M {points[0][0]:.1f} {base_y:.1f} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y in points)
        + f" L {points[-1][0]:.1f} {base_y:.1f} Z"
    )

    parts: list[str] = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="Potencia prevista por hora" '
        'xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        '<linearGradient id="area" x1="0" y1="0" x2="0" y2="1">',
        '<stop offset="0%" stop-color="#0b7a53" stop-opacity="0.34"/>',
        '<stop offset="100%" stop-color="#0b7a53" stop-opacity="0.02"/>',
        "</linearGradient>",
        "</defs>",
    ]

    # Rejilla horizontal.
    for step in range(5):
        frac = step / 4.0
        y = mt + plot_h * frac
        value = y_max * (1.0 - frac)
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" '
            'stroke="#dfe5ea" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{ml - 9}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="axis">{value / 1000:.1f}</text>'
        )
    parts.append(
        f'<text x="{ml - 9}" y="{mt - 5}" text-anchor="end" class="axis">kW</text>'
    )

    # Separadores y etiquetas de día.
    dates = hours["time_local"].dt.date.to_numpy()
    day_starts = [0] + [i for i in range(1, n) if dates[i] != dates[i - 1]]
    for i in day_starts:
        x = x_at(i)
        parts.append(
            f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{base_y}" '
            'stroke="#eef2f4" stroke-width="1"/>'
        )
        label = pd.Timestamp(dates[i]).strftime("%d/%m")
        parts.append(
            f'<text x="{x + 4:.1f}" y="{height - 24}" class="axis">{label}</text>'
        )

    # Área y línea de potencia.
    parts.append(f'<path d="{area}" fill="url(#area)"/>')
    parts.append(
        f'<polyline points="{line}" fill="none" stroke="#0b7a53" '
        'stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # Puntos con tooltip nativo.
    for i in range(0, n, 2):
        p = float(hours["power_w"].iloc[i])
        t = hours["time_local"].iloc[i]
        parts.append(
            f'<circle cx="{points[i][0]:.1f}" cy="{points[i][1]:.1f}" r="2.6" '
            f'fill="#0b7a53"><title>{html.escape(_fmt_ts(t))} · '
            f"{p / 1000:.2f} kW</title></circle>"
        )

    # Eje base.
    parts.append(
        f'<line x1="{ml}" y1="{base_y}" x2="{width - mr}" y2="{base_y}" '
        'stroke="#b9c4cc" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _daily_table(daily: pd.DataFrame) -> str:
    rows = []
    for row in daily.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.date))}</td>"
            f"<td class='num'>{row.energy_kwh:.2f}</td>"
            f"<td class='num'>{(row.peak_w / 1000):.2f}</td>"
            f"<td>{html.escape(str(row.peak_time))}</td>"
            f"<td class='num'>{row.gti_max_wm2:.0f}</td>"
            f"<td class='num'>{row.temp_max_c:.1f}</td>"
            f"<td class='num'>{row.cloud_mean_pct:.0f}%</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Fecha</th><th>Energía (kWh)</th><th>Pico (kW)</th>"
        "<th>Hora pico</th><th>GTI máx (W/m²)</th><th>T máx (°C)</th>"
        "<th>Nubes medias</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _hourly_table(hours: pd.DataFrame) -> str:
    rows = []
    for row in hours.itertuples(index=False):
        cls = " class='night'" if float(row.power_w) <= 0.0 else ""
        rows.append(
            f"<tr{cls}>"
            f"<td>{html.escape(_fmt_ts(row.time_local))}</td>"
            f"<td class='num'>{float(row.power_w) / 1000:.2f}</td>"
            f"<td class='num'>{float(row.energy_kwh):.2f}</td>"
            f"<td class='num'>{float(row.global_tilted_irradiance):.0f}</td>"
            f"<td class='num'>{float(row.temperature_2m):.1f}</td>"
            f"<td class='num'>{float(row.cloud_cover):.0f}%</td>"
            f"<td class='num'>{float(row.wind_speed_10m):.0f}</td>"
            "</tr>"
        )
    return (
        "<div class='scroll'><table><thead><tr>"
        "<th>Hora local</th><th>Potencia (kW)</th><th>Energía (kWh)</th>"
        "<th>GTI (W/m²)</th><th>Temperatura (°C)</th><th>Nubes</th>"
        "<th>Viento (km/h)</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def write_html_report(
    result: "ForecastResult",
    path: str | Path | None = None,
) -> Path:
    summary = result.summary
    hours = result.hours
    daily = result.daily
    model = summary["model"]

    if path is None:
        stamp = pd.Timestamp(summary["forecast_start"]).strftime("%Y%m%d_%H%M")
        path = project_root() / "reports" / f"forecast_{stamp}.html"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    uncertainty = model.get("uncertainty_95_w")
    uncertainty_txt = (
        f"±{uncertainty / 1000:.2f} kW" if uncertainty else "no estimada"
    )
    cards = [
        ("Total del periodo", f"{summary['total_kwh']:.2f} kWh", f"{summary['days']} días"),
        ("Hoy (día completo)", f"{summary['today_kwh']:.2f} kWh", "00:00–24:00"),
        ("Restante hoy", f"{summary['remaining_today_kwh']:.2f} kWh", "desde ahora"),
        ("Mañana", f"{summary['tomorrow_kwh']:.2f} kWh", "día completo"),
        ("Pico previsto", f"{summary['peak_w'] / 1000:.2f} kW", _fmt_ts(summary["peak_time"])),
        ("Factor de capacidad", f"{summary['capacity_factor_pct']:.1f}%", "del periodo"),
    ]
    cards_html = "".join(
        f'<div class="card"><span>{html.escape(title)}</span>'
        f"<strong>{html.escape(value)}</strong>"
        f"<small>{html.escape(sub)}</small></div>"
        for title, value, sub in cards
    )

    site = summary["site"]
    system = summary["system"]
    title = f"Pronóstico solar · {site['name']}"
    subtitle = (
        f"{system['kwp']:.2f} kWp · inclinación {system['tilt']:.0f}° · "
        f"azimut {system['azimuth']:.0f}° (0 = sur)"
    )
    model_line = (
        f"Regresión lineal · R² {model.get('r2')} · "
        f"MAE {model.get('mae_w')} W ({model.get('nmae_pct')}% de la pico) · "
        f"incertidumbre estimada {uncertainty_txt} · "
        f"fuente: {model.get('source')}"
    )

    html_doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ --ink:#17242d; --muted:#5c6b75; --line:#dfe5ea; --accent:#0b7a53; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:Inter,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:#f4f7f8; color:var(--ink); }}
  main {{ max-width:1240px; margin:0 auto; padding:28px 20px 48px; }}
  header {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-end;
            flex-wrap:wrap; margin-bottom:20px; }}
  h1 {{ margin:0 0 6px; font-size:30px; letter-spacing:-.02em; }}
  .sub {{ color:var(--muted); font-size:14px; }}
  .badge {{ background:#e2f1ea; color:#07543a; border-radius:999px; padding:6px 12px;
            font-size:12px; font-weight:600; }}
  .cards {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:22px 0; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:16px;
           box-shadow:0 1px 2px rgba(16,34,44,.04); }}
  .card span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase;
                letter-spacing:.06em; }}
  .card strong {{ display:block; font-size:24px; margin:8px 0 3px; letter-spacing:-.02em; }}
  .card small {{ color:var(--muted); font-size:12px; }}
  section {{ background:#fff; border:1px solid var(--line); border-radius:16px;
             padding:18px 18px 10px; margin:16px 0; box-shadow:0 1px 2px rgba(16,34,44,.04); }}
  h2 {{ margin:0 0 12px; font-size:17px; }}
  .chart {{ width:100%; height:auto; display:block; }}
  .axis {{ fill:#6c7b85; font-size:12px; font-family:inherit; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #eef2f4; }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
        letter-spacing:.04em; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr.night td {{ color:#9aa7af; }}
  .scroll {{ max-height:460px; overflow:auto; }}
  .scroll thead th {{ position:sticky; top:0; background:#fff; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:18px; line-height:1.6; }}
  @media (max-width:980px) {{ .cards {{ grid-template-columns:repeat(3,1fr); }} }}
  @media (max-width:600px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }}
    h1 {{ font-size:24px; }} }}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>{html.escape(title)}</h1>
      <div class="sub">{html.escape(subtitle)}</div>
      <div class="sub">{html.escape(str(site['latitude']))}, {html.escape(str(site['longitude']))} ·
        {html.escape(_fmt_ts(summary['forecast_start']))} → {html.escape(_fmt_ts(summary['forecast_end']))}</div>
    </div>
    <div class="badge">Generado con Open-Meteo + regresión lineal</div>
  </header>

  <div class="cards">{cards_html}</div>

  <section>
    <h2>Potencia prevista</h2>
    {_svg_chart(hours)}
  </section>

  <section>
    <h2>Resumen diario</h2>
    {_daily_table(daily)}
  </section>

  <section>
    <h2>Detalle horario</h2>
    {_hourly_table(hours)}
  </section>

  <footer>
    {html.escape(model_line)}<br>
    La predicción es una estimación. La incertidumbre real depende de la calidad
    del pronóstico meteorológico, del estado de los paneles y de la calibración
    con datos de producción. Modelo entrenado el {html.escape(str(model.get('created_at'))[:19])}.
  </footer>
</main>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")
    return path
