from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import settings


class DataServiceClient:
    def __init__(self):
        self.base = settings.data_service_url.rstrip("/")

    async def fetch_telemetry(
        self,
        device_id: str,
        start: datetime,
        end: datetime,
        fields: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        params = {
            "start_time": start.replace(tzinfo=timezone.utc).isoformat(),
            "end_time": end.replace(tzinfo=timezone.utc).isoformat(),
            "limit": limit,
        }
        if fields:
            params["fields"] = ",".join(fields)

        url = f"{self.base}/api/v1/data/telemetry/{device_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(url, params=params)
            res.raise_for_status()
            return res.json()
