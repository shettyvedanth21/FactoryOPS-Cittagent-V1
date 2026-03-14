from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from src.services.dataset_service import DatasetService


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        payload = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return _FakeResponse(payload, status_code=200)


@pytest.mark.asyncio
async def test_load_dataset_falls_back_to_data_service_when_s3_key_missing():
    s3 = AsyncMock()
    s3.download_file = AsyncMock(side_effect=RuntimeError("NoSuchKey"))
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "data": {
            "items": [
                {"timestamp": (now - timedelta(minutes=2)).isoformat(), "power": 1000.0, "current": 5.0},
                {"timestamp": (now - timedelta(minutes=1)).isoformat(), "power": 1100.0, "current": 5.2},
            ]
        }
    }

    with patch("src.services.dataset_service.httpx.AsyncClient", return_value=_FakeClient([payload])):
        with patch("src.services.dataset_service.get_settings") as get_settings:
            cfg = get_settings.return_value
            cfg.ml_require_exact_dataset_range = True
            cfg.app_env = "development"
            cfg.data_service_url = "http://data-service:8081"
            cfg.data_service_query_timeout_seconds = 10
            cfg.data_service_query_limit = 10000
            cfg.data_service_fallback_chunk_hours = 24

            df = await svc.load_dataset(
                device_id="D1",
                start_time=now - timedelta(hours=1),
                end_time=now,
                s3_key=None,
            )

    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "timestamp" in df.columns


@pytest.mark.asyncio
async def test_data_service_fallback_chunks_and_dedupes():
    s3 = AsyncMock()
    s3.download_file = AsyncMock(side_effect=RuntimeError("NoSuchKey"))
    s3.list_objects = AsyncMock(return_value=[])
    svc = DatasetService(s3)

    base = datetime.now(timezone.utc).replace(microsecond=0)
    t1 = (base - timedelta(hours=7)).isoformat()
    t2 = (base - timedelta(hours=6, minutes=30)).isoformat()
    responses = [
        {"data": {"items": [{"timestamp": t1, "power": 1000.0}, {"timestamp": t2, "power": 1200.0}]}},
        {"data": {"items": [{"timestamp": t2, "power": 1200.0}]}},  # duplicate across chunk boundary
    ]

    with patch("src.services.dataset_service.httpx.AsyncClient", return_value=_FakeClient(responses)):
        with patch("src.services.dataset_service.get_settings") as get_settings:
            cfg = get_settings.return_value
            cfg.ml_require_exact_dataset_range = True
            cfg.app_env = "development"
            cfg.data_service_url = "http://data-service:8081"
            cfg.data_service_query_timeout_seconds = 10
            cfg.data_service_query_limit = 10000
            cfg.data_service_fallback_chunk_hours = 6

            df = await svc.load_dataset(
                device_id="D2",
                start_time=base - timedelta(hours=13),
                end_time=base - timedelta(hours=1),
                s3_key=None,
            )

    assert len(df) == 2
