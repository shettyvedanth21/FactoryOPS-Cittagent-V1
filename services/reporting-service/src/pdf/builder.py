from datetime import datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from weasyprint import HTML
from jinja2 import Template

from src.pdf import charts

IST = ZoneInfo("Asia/Kolkata")


def _to_ist_label(value: Any, fallback: str = "N/A", include_tz: bool = True) -> str:
    if not value:
        return fallback
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        out = dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p")
        return f"{out} IST" if include_tz else out
    except Exception:
        return str(value)


def generate_consumption_pdf(data: dict) -> bytes:
    daily_series = data.get("daily_series", [])
    per_device = data.get("per_device", [])
    
    charts_dict = {}
    if daily_series:
        charts_dict["daily_energy"] = charts.daily_energy_bar_chart(daily_series)
    if per_device:
        charts_dict["device_share"] = charts.device_share_donut(per_device)

    data["charts"] = charts_dict
    data["generated_at"] = _to_ist_label(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))
    data["peak_timestamp"] = _to_ist_label(data.get("peak_timestamp"))
    data["tariff_fetched_at"] = _to_ist_label(data.get("tariff_fetched_at"))
    
    template = Template(get_consumption_report_template())
    html_content = template.render(**data)
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes


def generate_comparison_pdf(data: dict) -> bytes:
    comparison = data.get("comparison", {})
    metrics = comparison.get("metrics", {})
    
    if metrics:
        data["comparison_chart"] = charts.comparison_bar_chart(metrics)
    
    data["generated_at"] = _to_ist_label(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")))
    
    template = Template(get_comparison_report_template())
    html_content = template.render(**data)
    
    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes


def get_consumption_report_template():
    return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Energy Consumption Report</title>
    <style>
        @page { size: A4; margin: 1.4cm; }
        body { font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; color: #0f172a; line-height: 1.35; }
        .header {
            margin-bottom: 18px;
            border-radius: 12px;
            padding: 16px 18px;
            color: white;
            background: linear-gradient(120deg, #0f172a 0%, #1e3a8a 100%);
        }
        .header h1 { margin: 0 0 8px 0; font-size: 21px; font-weight: 700; letter-spacing: 0.2px; }
        .header p { margin: 2px 0; color: rgba(255,255,255,0.9); font-size: 10.5px; }
        .section { margin-bottom: 14px; break-inside: avoid; }
        .section h2 { color: #1e3a8a; font-size: 13px; border-bottom: 1px solid #e2e8f0; padding-bottom: 5px; margin: 0 0 8px 0; }
        .kpi-grid { display: table; width: 100%; border-spacing: 8px; margin-bottom: 10px; table-layout: fixed; }
        .kpi-box {
            display: table-cell; width: 25%;
            border: 1px solid #dbeafe; background: #f8fafc;
            border-radius: 10px; padding: 10px 6px; text-align: center;
        }
        .kpi-label { font-size: 9px; color: #475569; text-transform: uppercase; letter-spacing: 0.5px; }
        .kpi-value { font-size: 16px; color: #1e3a8a; font-weight: 700; margin-top: 4px; }
        .meta { color: #334155; font-size: 10px; margin-top: 4px; }
        .warn-box {
            background-color: #fff7ed; padding: 8px 10px; border-left: 3px solid #f59e0b;
            color: #92400e; border-radius: 6px; margin-top: 8px; font-size: 10px;
        }
        .error-box {
            background-color: #fef2f2; padding: 8px 10px; border-left: 3px solid #ef4444;
            color: #991b1b; border-radius: 6px; margin-top: 8px; font-size: 10px;
        }
        table { width: 100%; border-collapse: collapse; margin-top: 8px; }
        th, td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; font-size: 9.5px; }
        th { background-color: #f8fafc; color: #1e293b; font-weight: 600; }
        tr:nth-child(even) { background-color: #f8fbff; }
        .grid-2 { display: table; width: 100%; border-spacing: 8px; table-layout: fixed; }
        .col { display: table-cell; width: 50%; vertical-align: top; }
        .chart-container { text-align: center; margin-top: 8px; }
        .chart-container img { max-width: 100%; max-height: 280px; height: auto; border: 1px solid #e2e8f0; border-radius: 8px; }
        .insight {
            background-color: #eff6ff; padding: 8px 10px; margin: 6px 0;
            border-left: 3px solid #2563eb; border-radius: 6px; font-size: 10px;
        }
        .footer { text-align: center; margin-top: 12px; font-size: 9px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 7px; }
        .page-split { page-break-before: always; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Energy Consumption Report</h1>
        <p><strong>Scope:</strong> {{ device_label }}</p>
        <p><strong>Period:</strong> {{ start_date }} to {{ end_date }}</p>
        <p><strong>Generated:</strong> {{ generated_at }} | <strong>Tariff:</strong> {% if tariff_rate_used is not none %}{{ currency }} {{ tariff_rate_used }} / kWh{% else %}Not configured{% endif %}</p>
    </div>
    
    <div class="section">
        <h2>Executive Summary</h2>
        <div class="kpi-grid">
            <div class="kpi-box">
                <div class="kpi-label">Total Energy</div>
                <div class="kpi-value">{{ total_kwh }} kWh</div>
            </div>
            <div class="kpi-box">
                <div class="kpi-label">Peak Demand</div>
                <div class="kpi-value">{% if peak_demand_kw is not none %}{{ peak_demand_kw }} kW{% else %}N/A{% endif %}</div>
            </div>
            <div class="kpi-box">
                <div class="kpi-label">Load Factor</div>
                <div class="kpi-value">{% if load_factor_pct is not none %}{{ load_factor_pct }}%{% else %}N/A{% endif %}</div>
            </div>
            <div class="kpi-box">
                <div class="kpi-label">Total Cost</div>
                <div class="kpi-value">{% if total_cost is not none %}{{ currency }} {{ total_cost }}{% else %}N/A{% endif %}</div>
            </div>
        </div>
        {% if peak_timestamp and peak_timestamp != "N/A" %}
        <p class="meta"><strong>Peak demand timestamp:</strong> {{ peak_timestamp }}</p>
        {% endif %}
        {% if overall_quality != "high" %}
        <div class="warn-box">Some calculations are estimated due to partial telemetry. See Data Notes section.</div>
        {% endif %}
    </div>

    <div class="section">
        <h2>Trend & Device Share</h2>
        <div class="grid-2">
            <div class="col">
                {% if charts.daily_energy %}
                <div class="chart-container">
                    <img src="{{ charts.daily_energy }}" alt="Daily Energy Chart" />
                </div>
                {% endif %}
            </div>
            <div class="col">
                {% if charts.device_share %}
                <div class="chart-container">
                    <img src="{{ charts.device_share }}" alt="Device Energy Share" />
                </div>
                {% endif %}
            </div>
        </div>
    </div>

    {% if per_device %}
    <div class="section page-split">
        <h2>Device Breakdown</h2>
        <table>
            <tr><th>Device</th><th>kWh</th><th>Peak kW</th><th>Load Factor %</th><th>Quality</th><th>Method</th></tr>
            {% for d in per_device %}
            <tr>
                <td>{{ d.device_name }}</td>
                <td>{{ d.total_kwh if d.total_kwh is not none else "N/A" }}</td>
                <td>{{ d.peak_demand_kw if d.peak_demand_kw is not none else "N/A" }}</td>
                <td>{{ d.load_factor_pct if d.load_factor_pct is not none else "N/A" }}</td>
                <td>{{ d.quality }}</td>
                <td>{{ d.method }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    {% if daily_series %}
    <div class="section">
        <h2>Daily Energy Breakdown</h2>
        <table>
            <tr><th>Date</th><th>Energy (kWh)</th></tr>
            {% for day in daily_series %}
            <tr><td>{{ day.date }}</td><td>{{ day.kwh }}</td></tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="section">
        <h2>Cost Estimation</h2>
        <p class="meta"><strong>Tariff fetched at:</strong> {{ tariff_fetched_at }}</p>
        {% if tariff_rate_used is not none %}
        <p class="meta"><strong>Tariff used:</strong> {{ currency }} {{ tariff_rate_used }} / kWh</p>
        <p class="meta"><strong>Total estimated cost:</strong> {{ currency }} {{ total_cost }}</p>
        {% else %}
        <div class="error-box">Tariff not configured — cost calculation skipped.</div>
        {% endif %}
    </div>
    
    {% if insights %}
    <div class="section">
        <h2>Key Insights</h2>
        {% for insight in insights %}
        <div class="insight">{{ loop.index }}. {{ insight }}</div>
        {% endfor %}
    </div>
    {% endif %}

    {% if warnings %}
    <div class="section">
        <h2>Data Notes</h2>
        {% for warning in warnings %}
        <div class="warn-box">{{ warning }}</div>
        {% endfor %}
    </div>
    {% endif %}
    
    <div class="footer">
        <p>Page <span class="page-num"></span> | Generated by Energy Intelligence Platform | Report ID: {{ report_id }}</p>
    </div>
</body>
</html>
"""


def get_comparison_report_template():
    return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Energy Comparison Report</title>
    <style>
        @page { size: A4; margin: 2cm; }
        body { font-family: Arial, sans-serif; font-size: 11px; color: #333; }
        .header { text-align: center; margin-bottom: 30px; border-bottom: 2px solid #1E3A5F; padding-bottom: 10px; }
        .header h1 { color: #1E3A5F; margin: 0; font-size: 24px; }
        .section { margin-bottom: 25px; }
        .section h2 { color: #1E3A5F; font-size: 14px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 15px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f5f5f5; }
        .winner { background: #d4edda; padding: 15px; text-align: center; border-radius: 5px; margin: 20px 0; }
        .winner h3 { color: #155724; margin: 0; }
        .chart-container { text-align: center; margin: 15px 0; }
        .chart-container img { max-width: 100%; }
        .insight { background-color: #e8f4f8; padding: 10px; margin: 5px 0; border-left: 3px solid #1E3A5F; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Energy Comparison Report</h1>
        <p><strong>Comparing:</strong> {{ device_a_name }} vs {{ device_b_name }}</p>
        <p><strong>Period:</strong> {{ start_date }} to {{ end_date }}</p>
        <p><strong>Generated:</strong> {{ generated_at }}</p>
    </div>
    
    {% if comparison.energy_comparison %}
    <div class="section">
        <h2>Energy Comparison</h2>
        <table>
            <tr><th>Device</th><th>Energy (kWh)</th></tr>
            <tr><td>{{ device_a_name }}</td><td>{{ comparison.energy_comparison.device_a_kwh }}</td></tr>
            <tr><td>{{ device_b_name }}</td><td>{{ comparison.energy_comparison.device_b_kwh }}</td></tr>
        </table>
        <p><strong>Difference:</strong> {{ comparison.energy_comparison.difference_kwh }} kWh ({{ comparison.energy_comparison.difference_percent }}%)</p>
        <p><strong>Higher Consumer:</strong> {{ comparison.energy_comparison.higher_consumer }}</p>
    </div>
    {% endif %}
    
    {% if comparison.demand_comparison %}
    <div class="section">
        <h2>Demand Comparison</h2>
        <table>
            <tr><th>Device</th><th>Peak Demand (kW)</th></tr>
            <tr><td>{{ device_a_name }}</td><td>{{ comparison.demand_comparison.device_a_peak_kw }}</td></tr>
            <tr><td>{{ device_b_name }}</td><td>{{ comparison.demand_comparison.device_b_peak_kw }}</td></tr>
        </table>
        <p><strong>Difference:</strong> {{ comparison.demand_comparison.difference_kw }} kW ({{ comparison.demand_comparison.difference_percent }}%)</p>
        <p><strong>Higher Demand:</strong> {{ comparison.demand_comparison.higher_demand }}</p>
    </div>
    {% endif %}
    
    {% if insights %}
    <div class="section">
        <h2>Key Insights</h2>
        {% for insight in insights %}
        <div class="insight">{{ insight }}</div>
        {% endfor %}
    </div>
    {% endif %}
    
    {% if winner %}
    <div class="winner">
        <h3>Winner: {{ winner }}</h3>
        <p>{{ winner }} is the more efficient choice based on the analysis.</p>
    </div>
    {% endif %}
</body>
</html>
"""


pdf_builder = type('PDFBuilder', (), {
    'generate_consumption_pdf': staticmethod(generate_consumption_pdf),
    'generate_comparison_pdf': staticmethod(generate_comparison_pdf)
})()
