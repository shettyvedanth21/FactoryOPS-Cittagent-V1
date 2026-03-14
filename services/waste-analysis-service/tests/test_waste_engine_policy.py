from datetime import datetime, timedelta, timezone

from src.services.waste_engine import compute_device_waste


def _rows(count: int = 5):
    start = datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(count):
        out.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "power": 1000.0,
                "current": 6.0,
                "voltage": 230.0,
            }
        )
    return out


def test_unoccupied_is_disabled_by_policy():
    res = compute_device_waste(
        device_id="D1",
        device_name="Device 1",
        data_source_type="metered",
        rows=_rows(),
        threshold=2.0,
        overconsumption_threshold=5.0,
        tariff_rate=8.5,
        shifts=[{"day_of_week": 4, "shift_start": "08:00", "shift_end": "18:00"}],
    )

    assert res.unoccupied_duration_sec is None
    assert res.unoccupied_energy_kwh is None
    assert res.unoccupied_cost is None
    assert res.unoccupied_skipped_reason == "Disabled by policy"
    assert res.unoccupied_pf_estimated is False


def test_power_watts_not_inflated_for_offhours():
    rows = [
        {
            "timestamp": datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
            "power": 250.0,  # watts
            "current": 6.0,
            "voltage": 230.0,
        },
        {
            "timestamp": datetime(2026, 3, 13, 0, 1, tzinfo=timezone.utc),
            "power": 250.0,
            "current": 6.0,
            "voltage": 230.0,
        },
        {
            "timestamp": datetime(2026, 3, 13, 0, 2, tzinfo=timezone.utc),
            "power": 250.0,
            "current": 6.0,
            "voltage": 230.0,
        },
    ]
    res = compute_device_waste(
        device_id="D2",
        device_name="Device 2",
        data_source_type="metered",
        rows=rows,
        threshold=2.0,
        overconsumption_threshold=10.0,
        tariff_rate=8.5,
        shifts=[{"day_of_week": 4, "shift_start": "08:00", "shift_end": "18:00"}],
    )
    assert res.total_energy_kwh < 0.02
    assert res.offhours_energy_kwh is not None and res.offhours_energy_kwh < 0.02
    assert res.total_cost is not None and res.offhours_cost is not None
    assert res.offhours_cost <= res.total_cost + 0.01
