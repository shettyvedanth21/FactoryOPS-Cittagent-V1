from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Template
from weasyprint import HTML

from src.config import settings
from src.pdf import charts
from src.pdf.formatting import duration_label

IST = ZoneInfo("Asia/Kolkata")


def _to_ist(value) -> str:
    if value is None:
        return "N/A"
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    except Exception:
        return str(value)


def generate_waste_pdf(payload: dict) -> bytes:
    devices = payload.get("device_summaries", [])
    max_devices = max(1, int(settings.WASTE_PDF_MAX_DEVICES))

    def _waste_cost(d: dict) -> float:
        return float(
            (d.get("idle_cost") or 0.0)
            + (d.get("offhours_cost") or 0.0)
            + (d.get("overconsumption_cost") or 0.0)
        )

    if len(devices) > max_devices:
        render_devices = sorted(devices, key=_waste_cost, reverse=True)[:max_devices]
        payload["pdf_omitted_devices"] = len(devices) - max_devices
    else:
        render_devices = devices
        payload["pdf_omitted_devices"] = 0

    payload["pdf_devices"] = render_devices
    for d in payload["pdf_devices"]:
        d["offhours_duration_label"] = duration_label(d.get("offhours_duration_sec"))
        d["overconsumption_duration_label"] = duration_label(d.get("overconsumption_duration_sec"))
    payload["pdf_any_pf_estimated"] = any(
        bool(d.get("pf_estimated"))
        or bool(d.get("offhours_pf_estimated"))
        or bool(d.get("overconsumption_pf_estimated"))
        for d in render_devices
    )
    payload["generated_at"] = _to_ist(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")) )
    payload["charts"] = {
        "idle_cost": charts.idle_cost_bar(render_devices),
        "standby": charts.standby_bar(render_devices),
        "offhours_cost": charts.offhours_cost_bar(render_devices),
        "overconsumption_cost": charts.overconsumption_cost_bar(render_devices),
        "energy": charts.total_energy_bar(render_devices),
    }
    html = Template(_template()).render(**payload)
    return HTML(string=html).write_pdf()


def _template() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset=\"utf-8\"/>
<style>
@page { size: A4; margin: 1.4cm; }
body { font-family: 'Segoe UI', Arial, sans-serif; color:#0f172a; font-size:11px; }
.header { background: linear-gradient(120deg,#0f172a,#1d4ed8); color:white; border-radius:12px; padding:14px 16px; margin-bottom:12px; }
.header h1 { margin:0 0 6px 0; font-size:20px; }
.sub { font-size:10px; opacity:0.94; }
.grid { display: table; width:100%; border-spacing:8px; table-layout:fixed; margin:10px 0 12px 0; }
.card { display:table-cell; border:1px solid #dbeafe; border-radius:10px; text-align:center; padding:10px; background:#f8fafc; }
.card .k { font-size:9px; text-transform:uppercase; color:#475569; }
.card .v { font-size:16px; font-weight:700; color:#1e3a8a; margin-top:5px; }
.section { margin-top:12px; break-inside: avoid; }
.section h2 { margin:0 0 6px 0; padding-bottom:4px; border-bottom:1px solid #e2e8f0; color:#1e3a8a; font-size:13px; }
table { width:100%; border-collapse: collapse; margin-top:6px; }
th,td { border:1px solid #e2e8f0; padding:6px; font-size:9.5px; }
th { background:#f8fafc; text-align:left; }
.warn { background:#fff7ed; border-left:3px solid #f59e0b; padding:7px 10px; border-radius:6px; margin:6px 0; color:#9a3412; }
.chart { text-align:center; margin-top:8px; padding:8px; border:1px solid #e2e8f0; border-radius:8px; background:#ffffff; }
.chart img { width:100%; max-width:560px; max-height:220px; object-fit:contain; border:0; }
.page-break { page-break-before: always; }
</style>
</head>
<body>
<div class=\"header\">
  <h1>Energy Waste Analysis Report</h1>
  <div class=\"sub\">Generated: {{ generated_at }}</div>
  <div class=\"sub\">Period: {{ start_date }} to {{ end_date }} | Scope: {{ scope_label }}</div>
  <div class=\"sub\">Tariff Used: {% if tariff_rate_used is not none %}{{ currency }} {{ tariff_rate_used }}/kWh{% else %}Not configured{% endif %}</div>
</div>

<div class=\"grid\">
  <div class=\"card\"><div class=\"k\">Total Waste Cost</div><div class=\"v\">{% if total_waste_cost is not none %}{{ currency }} {{ total_waste_cost }}{% else %}N/A{% endif %}</div></div>
  <div class=\"card\"><div class=\"k\">Idle Energy</div><div class=\"v\">{{ total_idle_kwh }} kWh</div></div>
  <div class=\"card\"><div class=\"k\">Idle Time</div><div class=\"v\">{{ total_idle_label }}</div></div>
  <div class=\"card\"><div class=\"k\">Worst Offender</div><div class=\"v\">{{ worst_device }}</div></div>
</div>
<div class=\"grid\" style=\"margin-top:0\">
  <div class=\"card\"><div class=\"k\">Total Energy</div><div class=\"v\">{{ total_energy_kwh }} kWh</div></div>
  <div class=\"card\"><div class=\"k\">Energy Cost</div><div class=\"v\">{% if total_energy_cost is not none %}{{ currency }} {{ total_energy_cost }}{% else %}N/A{% endif %}</div></div>
  <div class=\"card\"><div class=\"k\">Devices</div><div class=\"v\">{{ device_summaries|length }}</div></div>
  <div class=\"card\"><div class=\"k\">Tariff</div><div class=\"v\">{% if tariff_rate_used is not none %}{{ currency }} {{ tariff_rate_used }}/kWh{% else %}N/A{% endif %}</div></div>
</div>

<div class=\"section\">
  <h2>Idle Running Analysis</h2>
  <table>
    <tr><th>Device</th><th>Idle Time</th><th>Idle Energy (kWh)</th><th>Idle Cost</th><th>Data Quality</th></tr>
    {% for d in pdf_devices %}
    <tr>
      <td>{{ d.device_name }}</td>
      <td>{{ d.idle_duration_label }}</td>
      <td>{{ d.idle_energy_kwh }}</td>
      <td>{% if d.idle_cost is not none %}{{ currency }} {{ d.idle_cost }}{% else %}N/A{% endif %}</td>
      <td>{{ d.data_quality }}</td>
    </tr>
    {% endfor %}
  </table>
  {% if charts.idle_cost %}<div class=\"chart\"><img src=\"{{ charts.idle_cost }}\"/></div>{% endif %}
</div>

<div class=\"section\">
  <h2>Standby Energy Loss</h2>
  <div class=\"warn\" style=\"background:#eff6ff;border-left-color:#2563eb;color:#1e3a8a\">
    Standby is diagnostic and is excluded from Total Waste Cost aggregation.
  </div>
  <table>
    <tr><th>Device</th><th>Avg Standby Power (kW)</th><th>Standby Energy (kWh)</th><th>Standby Cost</th></tr>
    {% for d in pdf_devices %}
    <tr>
      <td>{{ d.device_name }}</td>
      <td>{{ d.standby_power_kw if d.standby_power_kw is not none else 'N/A' }}</td>
      <td>{{ d.standby_energy_kwh if d.standby_energy_kwh is not none else 'N/A' }}</td>
      <td>{% if d.standby_cost is not none %}{{ currency }} {{ d.standby_cost }}{% else %}N/A{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  {% if charts.standby %}<div class=\"chart\"><img src=\"{{ charts.standby }}\"/></div>{% endif %}
</div>

<div class=\"section\">
  <h2>Off-Hours Running Analysis</h2>
  {% if pdf_omitted_devices > 0 %}
  <div class=\"warn\" style=\"background:#eff6ff;border-left-color:#2563eb;color:#1e3a8a\">
    Showing top {{ pdf_devices|length }} devices by waste cost. {{ pdf_omitted_devices }} additional devices are available in JSON export.
  </div>
  {% endif %}
  <table>
    <tr><th>Device</th><th>Duration</th><th>Energy</th><th>Cost / Note</th></tr>
    {% for d in pdf_devices %}
    <tr>
      <td>{{ d.device_name }}{% if d.offhours_pf_estimated %}*{% endif %}</td>
      <td>{{ d.offhours_duration_label }}</td>
      <td>{% if d.offhours_skipped_reason %}—{% elif d.offhours_energy_kwh is not none %}{{ d.offhours_energy_kwh }} kWh{% else %}—{% endif %}</td>
      <td>{% if d.offhours_skipped_reason %}{{ d.offhours_skipped_reason }}{% elif d.offhours_cost is not none %}{{ currency }} {{ d.offhours_cost }}{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  {% if charts.offhours_cost %}<div class=\"chart\"><img src=\"{{ charts.offhours_cost }}\"/></div>{% endif %}
</div>

<div class=\"section\">
  <h2>Overconsumption Analysis</h2>
  <table>
    <tr><th>Device</th><th>Duration</th><th>Energy</th><th>Cost / Note</th></tr>
    {% for d in pdf_devices %}
    <tr>
      <td>{{ d.device_name }}{% if d.overconsumption_pf_estimated %}*{% endif %}</td>
      <td>{{ d.overconsumption_duration_label }}</td>
      <td>{% if d.overconsumption_skipped_reason %}—{% elif d.overconsumption_kwh is not none %}{{ d.overconsumption_kwh }} kWh{% else %}—{% endif %}</td>
      <td>{% if d.overconsumption_skipped_reason %}{{ d.overconsumption_skipped_reason }}{% elif d.overconsumption_cost is not none %}{{ currency }} {{ d.overconsumption_cost }}{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  {% if charts.overconsumption_cost %}<div class=\"chart\"><img src=\"{{ charts.overconsumption_cost }}\"/></div>{% endif %}
  {% if pdf_any_pf_estimated %}
  <div class=\"warn\">* Power factor estimated at 0.85 for one or more category calculations.</div>
  {% endif %}
</div>

<div class=\"section page-break\">
  <h2>Total Consumption by Device</h2>
  <table>
    <tr><th>Device</th><th>Total kWh</th><th>Total Cost</th><th>Method</th><th>PF Estimated</th></tr>
    {% for d in pdf_devices %}
    <tr>
      <td>{{ d.device_name }}</td>
      <td>{{ d.total_energy_kwh }}</td>
      <td>{% if d.total_cost is not none %}{{ currency }} {{ d.total_cost }}{% else %}N/A{% endif %}</td>
      <td>{{ d.calculation_method }}</td>
      <td>{{ 'Yes' if d.pf_estimated else 'No' }}</td>
    </tr>
    {% endfor %}
  </table>
  {% if charts.energy %}<div class=\"chart\"><img src=\"{{ charts.energy }}\"/></div>{% endif %}
</div>

<div class=\"section\">
  <h2>Key Insights</h2>
  {% for item in insights %}
  <div class=\"warn\" style=\"background:#eff6ff;border-left-color:#2563eb;color:#1e3a8a\">{{ loop.index }}. {{ item }}</div>
  {% endfor %}
</div>

<div class=\"section\">
  <h2>Data Notes</h2>
  {% if warnings|length == 0 %}
  <div class=\"warn\">No data quality warnings for this run.</div>
  {% endif %}
  {% for w in warnings %}
  <div class=\"warn\">{{ w }}</div>
  {% endfor %}
</div>

</body>
</html>
"""
