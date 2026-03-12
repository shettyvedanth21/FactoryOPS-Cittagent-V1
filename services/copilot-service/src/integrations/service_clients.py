import httpx

from src.config import settings


async def get_current_tariff() -> tuple[float, str]:
    url = f"{settings.reporting_service_url.rstrip('/')}/api/v1/settings/tariff"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            payload = res.json()
            rate = float(payload.get("rate") or 0.0)
            currency = payload.get("currency") or "INR"
            return rate, currency
    except Exception:
        return 0.0, "INR"
