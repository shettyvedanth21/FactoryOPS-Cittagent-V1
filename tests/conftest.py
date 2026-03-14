"""
Shared session-scoped fixtures for live-stack E2E tests.
"""

import time
import uuid

import httpx
import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.simulator import TelemetrySimulator


SERVICES = {
    "device": "http://localhost:8000",
    "data": "http://localhost:8081",
    "rules": "http://localhost:8002",
    "analytics": "http://localhost:8003",
    "reporting": "http://localhost:8085",
    "waste": "http://localhost:8087",
    "copilot": "http://localhost:8007",
}

TEST_RUN_ID = uuid.uuid4().hex[:8].upper()
TEST_DEVICE_ID = f"E2E-{TEST_RUN_ID}"


def pytest_configure(config):
    config.e2e = {
        "device_id": TEST_DEVICE_ID,
        "channel_id": None,
        "shift_id": None,
        "rule_id": None,
        "anomaly_job_id": None,
        "failure_job_id": None,
        "report_id": None,
        "waste_job_id": None,
        "waste_device_row": None,
    }


@pytest.fixture(scope="session")
def state(pytestconfig):
    return pytestconfig.e2e


@pytest.fixture(scope="session")
def device_id(pytestconfig):
    return pytestconfig.e2e["device_id"]


@pytest.fixture(scope="session")
def api():
    return APIClient(SERVICES)


@pytest.fixture(scope="session")
def simulator(device_id):
    sim = TelemetrySimulator(
        broker_host="localhost",
        broker_port=1883,
        device_id=device_id,
    )
    yield sim
    sim.disconnect()


@pytest.fixture(scope="session", autouse=True)
def verify_all_services_healthy():
    health_map = {
        "device-service": f"{SERVICES['device']}/health",
        "data-service": f"{SERVICES['data']}/api/v1/data/health",
        "rule-engine": f"{SERVICES['rules']}/health",
        "analytics-service": f"{SERVICES['analytics']}/health/live",
        "reporting-service": f"{SERVICES['reporting']}/health",
        "waste-service": f"{SERVICES['waste']}/health",
        "copilot-service": f"{SERVICES['copilot']}/health",
    }

    for name, url in health_map.items():
        ok = False
        last_err = None
        for _ in range(12):
            try:
                resp = httpx.get(url, timeout=5)
                if resp.status_code == 200:
                    ok = True
                    break
            except Exception as exc:  # pragma: no cover
                last_err = exc
            time.sleep(5)
        if not ok:
            pytest.fail(
                f"\n{'=' * 60}\n"
                f"SERVICE NOT READY: {name}\n"
                f"URL: {url}\n"
                f"Last error: {last_err}\n"
                f"Fix: docker-compose up -d && wait 30s\n"
                f"{'=' * 60}"
            )


@pytest.fixture(scope="session", autouse=True)
def cleanup(api, device_id, state):
    yield
    try:
        if state.get("rule_id"):
            api.rules.delete_rule(state["rule_id"])
    except Exception:
        pass
    try:
        if state.get("channel_id"):
            api.reporting.delete_notification_channel(state["channel_id"])
    except Exception:
        pass
    try:
        api.device.delete_device(device_id)
    except Exception:
        pass
