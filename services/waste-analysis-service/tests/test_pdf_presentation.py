from src.pdf.formatting import duration_label
from src.pdf.charts import offhours_cost_bar, overconsumption_cost_bar


def test_duration_label_human_readable():
    assert duration_label(None) == "—"
    assert duration_label(1800) == "30 min"
    assert duration_label(8700) == "2 hr 25 min"


def test_offhours_chart_generation_behavior():
    rows = [{"device_name": "D1", "offhours_cost": 0.0}]
    assert offhours_cost_bar(rows) is None
    rows = [{"device_name": "D1", "offhours_cost": 12.3}]
    uri = offhours_cost_bar(rows)
    assert isinstance(uri, str) and uri.startswith("data:image/png;base64,")


def test_overconsumption_chart_generation_behavior():
    rows = [{"device_name": "D1", "overconsumption_cost": 0.0}]
    assert overconsumption_cost_bar(rows) is None
    rows = [{"device_name": "D1", "overconsumption_cost": 7.8}]
    uri = overconsumption_cost_bar(rows)
    assert isinstance(uri, str) and uri.startswith("data:image/png;base64,")
