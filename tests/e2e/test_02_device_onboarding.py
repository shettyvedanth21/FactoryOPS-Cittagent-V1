def test_create_device(api, device_id):
    result = api.device.create_device(
        {
            "device_id": device_id,
            "device_name": f"E2E Compressor {device_id}",
            "device_type": "compressor",
            "location": "E2E Test Floor",
            "data_source_type": "metered",
            "phase_type": "single",
        }
    )
    assert result["device_id"] == device_id
    assert result["device_name"] == f"E2E Compressor {device_id}"


def test_device_retrievable(api, device_id):
    result = api.device.get_device(device_id)
    assert result["device_id"] == device_id


def test_device_initial_status_valid(api, device_id):
    result = api.device.get_device(device_id)
    status = result.get("runtime_status") or result.get("status") or result.get("legacy_status")
    assert status in ("stopped", "running", "idle", "active", "offline", "online", "unknown")


def test_set_telemetry_widgets(api, device_id):
    result = api.device.set_dashboard_widgets(
        device_id,
        {
            "widgets": []
        },
    )
    assert result["success"] is True
    assert isinstance(result["selected_fields"], list)
