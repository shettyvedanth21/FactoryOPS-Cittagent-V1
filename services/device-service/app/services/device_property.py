"""Device property service - handles dynamic property discovery from telemetry."""

from typing import Optional, List, Dict, Set
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_
from sqlalchemy.orm import selectinload

from app.models.device import (
    DeviceProperty,
    Device,
    DeviceDashboardWidget,
    DeviceDashboardWidgetSetting,
)
import logging

logger = logging.getLogger(__name__)


class DevicePropertyService:
    """Service for managing device properties discovered from telemetry."""
    
    EXCLUDED_FIELDS = {'timestamp', 'device_id', 'schema_version', 'enrichment_status', 'table', 'enriched_at'}
    
    def __init__(self, session: AsyncSession):
        self._session = session
    
    async def discover_properties(
        self, 
        device_id: str, 
        telemetry_data: dict
    ) -> List[DeviceProperty]:
        """Discover and update properties from telemetry data.
        
        Args:
            device_id: Device ID
            telemetry_data: Dictionary containing telemetry fields
            
        Returns:
            List of updated/created DeviceProperty instances
        """
        discovered_fields = []
        
        for key, value in telemetry_data.items():
            if key in self.EXCLUDED_FIELDS:
                continue
            
            if isinstance(value, (int, float)):
                is_numeric = True
                data_type = "float" if isinstance(value, float) else "integer"
            elif isinstance(value, str):
                is_numeric = False
                data_type = "string"
            else:
                continue
            
            discovered_fields.append((key, is_numeric, data_type))
        
        if not discovered_fields:
            return []
        
        updated_properties = []
        now = datetime.utcnow()
        
        for field_name, is_numeric, data_type in discovered_fields:
            query = select(DeviceProperty).where(
                and_(
                    DeviceProperty.device_id == device_id,
                    DeviceProperty.property_name == field_name
                )
            )
            result = await self._session.execute(query)
            existing = result.scalar_one_or_none()
            
            if existing:
                existing.last_seen_at = now
                existing.is_numeric = is_numeric
                existing.data_type = data_type
                updated_properties.append(existing)
            else:
                new_property = DeviceProperty(
                    device_id=device_id,
                    property_name=field_name,
                    is_numeric=is_numeric,
                    data_type=data_type,
                    discovered_at=now,
                    last_seen_at=now
                )
                self._session.add(new_property)
                updated_properties.append(new_property)
        
        await self._session.commit()
        
        for prop in updated_properties:
            await self._session.refresh(prop)
        
        return updated_properties
    
    async def get_device_properties(
        self, 
        device_id: str,
        numeric_only: bool = True
    ) -> List[DeviceProperty]:
        """Get all properties for a device.
        
        Args:
            device_id: Device ID
            numeric_only: Only return numeric properties (for rules)
            
        Returns:
            List of DeviceProperty instances
        """
        query = select(DeviceProperty).where(
            DeviceProperty.device_id == device_id
        )
        
        if numeric_only:
            query = query.where(DeviceProperty.is_numeric == True)
        
        query = query.order_by(DeviceProperty.property_name)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def get_all_devices_properties(
        self,
        tenant_id: Optional[str] = None
    ) -> Dict[str, List[str]]:
        """Get properties for all active devices.
        
        Returns:
            Dictionary mapping device_id to list of property names
        """
        # Get all devices regardless of status (runtime status is for display, not filtering)
        device_query = select(Device.device_id)
        
        if tenant_id:
            device_query = device_query.where(Device.tenant_id == tenant_id)
        
        device_result = await self._session.execute(device_query)
        device_ids = [row[0] for row in device_result.fetchall()]
        
        result_dict: Dict[str, List[str]] = {}
        
        for dev_id in device_ids:
            props = await self.get_device_properties(dev_id, numeric_only=True)
            result_dict[dev_id] = [p.property_name for p in props]
        
        return result_dict
    
    async def get_common_properties(
        self,
        device_ids: List[str]
    ) -> List[str]:
        """Get common properties across multiple devices (intersection).
        
        Args:
            device_ids: List of device IDs
            
        Returns:
            List of property names common to all devices
        """
        if not device_ids:
            return []
        
        if len(device_ids) == 1:
            props = await self.get_device_properties(device_ids[0], numeric_only=True)
            return [p.property_name for p in props]
        
        property_sets: List[Set[str]] = []
        
        for device_id in device_ids:
            props = await self.get_device_properties(device_id, numeric_only=True)
            property_sets.append(set(p.property_name for p in props))
        
        common = property_sets[0]
        for prop_set in property_sets[1:]:
            common = common.intersection(prop_set)
        
        return sorted(list(common))
    
    async def sync_from_telemetry(
        self,
        device_id: str,
        telemetry_values: Dict[str, float]
    ) -> List[DeviceProperty]:
        """Sync properties from incoming telemetry values.
        
        Args:
            device_id: Device ID
            telemetry_values: Dictionary of parameter values
            
        Returns:
            List of updated/created properties
        """
        return await self.discover_properties(device_id, telemetry_values)
    
    async def cleanup_stale_properties(self, days: int = 30) -> int:
        """Remove properties not seen in specified days.
        
        Args:
            days: Number of days to consider stale
            
        Returns:
            Number of properties deleted
        """
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        query = delete(DeviceProperty).where(
            DeviceProperty.last_seen_at < cutoff
        )
        
        result = await self._session.execute(query)
        await self._session.commit()
        
        return result.rowcount

    async def get_dashboard_widget_config(self, device_id: str) -> Dict[str, object]:
        """Return widget configuration for a device.

        If no explicit selection exists, defaults to all discovered numeric fields.
        """
        device_query = select(Device).where(Device.device_id == device_id)
        device_result = await self._session.execute(device_query)
        if device_result.scalar_one_or_none() is None:
            raise ValueError(f"Device '{device_id}' not found")

        properties = await self.get_device_properties(device_id=device_id, numeric_only=True)
        available_fields = sorted([p.property_name for p in properties])

        widget_query = (
            select(DeviceDashboardWidget)
            .where(DeviceDashboardWidget.device_id == device_id)
            .order_by(DeviceDashboardWidget.display_order.asc(), DeviceDashboardWidget.field_name.asc())
        )
        widget_result = await self._session.execute(widget_query)
        selected_fields = [w.field_name for w in widget_result.scalars().all()]

        settings_query = select(DeviceDashboardWidgetSetting).where(
            DeviceDashboardWidgetSetting.device_id == device_id
        )
        settings_result = await self._session.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        # Backward compatibility: if legacy selected rows exist without settings row,
        # treat them as explicit config to avoid silently reverting to default mode.
        has_explicit_config = bool((settings and settings.is_configured) or selected_fields)
        default_applied = not has_explicit_config
        effective_fields = selected_fields if has_explicit_config else available_fields

        return {
            "device_id": device_id,
            "available_fields": available_fields,
            "selected_fields": selected_fields,
            "effective_fields": effective_fields,
            "default_applied": default_applied,
        }

    async def replace_dashboard_widget_config(self, device_id: str, selected_fields: List[str]) -> Dict[str, object]:
        """Replace widget configuration for a device in an idempotent way."""
        device_query = select(Device).where(Device.device_id == device_id)
        device_result = await self._session.execute(device_query)
        if device_result.scalar_one_or_none() is None:
            raise ValueError(f"Device '{device_id}' not found")

        properties = await self.get_device_properties(device_id=device_id, numeric_only=True)
        available_fields = sorted([p.property_name for p in properties])
        available_set = set(available_fields)

        requested = [f.strip() for f in selected_fields if f and f.strip()]
        deduped: List[str] = []
        seen = set()
        for field in requested:
            if field not in seen:
                seen.add(field)
                deduped.append(field)

        invalid_fields = sorted([field for field in deduped if field not in available_set])
        if invalid_fields:
            raise LookupError(f"Unknown/unavailable widget fields: {invalid_fields}")

        await self._session.execute(
            delete(DeviceDashboardWidget).where(DeviceDashboardWidget.device_id == device_id)
        )
        for order, field_name in enumerate(deduped):
            self._session.add(
                DeviceDashboardWidget(
                    device_id=device_id,
                    field_name=field_name,
                    display_order=order,
                )
            )

        settings_query = select(DeviceDashboardWidgetSetting).where(
            DeviceDashboardWidgetSetting.device_id == device_id
        )
        settings_result = await self._session.execute(settings_query)
        settings = settings_result.scalar_one_or_none()
        if settings is None:
            self._session.add(
                DeviceDashboardWidgetSetting(
                    device_id=device_id,
                    is_configured=True,
                )
            )
        else:
            settings.is_configured = True

        await self._session.commit()
        return await self.get_dashboard_widget_config(device_id)
