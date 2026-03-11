"""Schema re-exports for convenience."""

from app.schemas.device import (
    DeviceBase,
    DeviceCreate,
    DeviceUpdate,
    DeviceResponse,
    DeviceListResponse,
    DeviceSingleResponse,
    DeviceDeleteResponse,
    ErrorResponse,
    DashboardWidgetConfigUpdateRequest,
    DashboardWidgetConfigResponse,
)

__all__ = [
    "DeviceBase",
    "DeviceCreate",
    "DeviceUpdate",
    "DeviceResponse",
    "DeviceListResponse",
    "DeviceSingleResponse",
    "DeviceDeleteResponse",
    "ErrorResponse",
    "DashboardWidgetConfigUpdateRequest",
    "DashboardWidgetConfigResponse",
]
