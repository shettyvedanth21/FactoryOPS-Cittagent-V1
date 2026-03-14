"""Device model - re-export for convenience."""

from app.models.device import (
    Device,
    DeviceStatus,
    DeviceDashboardWidget,
    DeviceDashboardWidgetSetting,
    WasteSiteConfig,
)

__all__ = [
    "Device",
    "DeviceStatus",
    "DeviceDashboardWidget",
    "DeviceDashboardWidgetSetting",
    "WasteSiteConfig",
]
