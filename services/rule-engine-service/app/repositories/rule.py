"""Rule repository layer - data access abstraction."""

from typing import Optional, List, Dict, Any

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule, RuleStatus, Alert, ActivityEvent


class RuleRepository:
    """Repository for Rule entity operations.
    
    Implements repository pattern for clean separation between
    data access and business logic layers.
    """
    
    def __init__(self, session: AsyncSession):
        self._session = session
    
    async def create(self, rule: Rule) -> Rule:
        """Create a new rule in the database."""
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule
    
    async def get_by_id(
        self, 
        rule_id: str, 
        tenant_id: Optional[str] = None
    ) -> Optional[Rule]:
        """Get rule by ID with optional tenant filtering."""
        query = select(Rule).where(Rule.rule_id == rule_id)
        
        # Apply tenant filter if provided (for future multi-tenancy)
        if tenant_id is not None:
            query = query.where(Rule.tenant_id == tenant_id)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_active_rules_for_device(
        self,
        device_id: str,
        tenant_id: Optional[str] = None,
    ) -> List[Rule]:
        """Get all active rules that apply to a specific device.
        
        Args:
            device_id: Device identifier
            tenant_id: Optional tenant ID for multi-tenancy
            
        Returns:
            List of active rules applicable to the device
        """
        query = select(Rule).where(
            and_(
                Rule.status == RuleStatus.ACTIVE,
                Rule.deleted_at.is_(None),
                or_(
                    Rule.scope == "all_devices",
                    (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                )
            )
        )
        
        # Apply tenant filter if provided
        if tenant_id is not None:
            query = query.where(Rule.tenant_id == tenant_id)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def list_rules(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[RuleStatus] = None,
        device_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Rule], int]:
        """List rules with filtering and pagination.
        
        Returns:
            Tuple of (rules list, total count)
        """
        # Build base query
        query = select(Rule).where(Rule.deleted_at.is_(None))
        count_query = select(func.count(Rule.rule_id)).where(Rule.deleted_at.is_(None))
        
        # Apply filters
        if tenant_id is not None:
            query = query.where(Rule.tenant_id == tenant_id)
            count_query = count_query.where(Rule.tenant_id == tenant_id)
        
        if status:
            query = query.where(Rule.status == status)
            count_query = count_query.where(Rule.status == status)
        
        if device_id:
            # Filter rules that apply to specific device
            query = query.where(
                or_(
                    Rule.scope == "all_devices",
                    (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                )
            )
            count_query = count_query.where(
                or_(
                    Rule.scope == "all_devices",
                    (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                )
            )
        
        # Get total count
        count_result = await self._session.execute(count_query)
        total = count_result.scalar()
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        result = await self._session.execute(query)
        rules = result.scalars().all()
        
        return list(rules), total
    
    async def update(self, rule: Rule) -> Rule:
        """Update an existing rule."""
        await self._session.flush()
        await self._session.refresh(rule)
        return rule
    
    async def update_last_triggered(self, rule_id: str) -> None:
        """Update the last_triggered_at timestamp for a rule."""
        rule = await self.get_by_id(rule_id)
        if rule:
            # IMPORTANT: store timezone-aware timestamp
            rule.last_triggered_at = datetime.now(timezone.utc)
            await self._session.flush()
    
    async def update_status(self, rule_id: str, status: RuleStatus) -> Optional[Rule]:
        """Update rule status."""
        rule = await self.get_by_id(rule_id)
        if rule:
            rule.status = status
            rule.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            await self._session.refresh(rule)
        return rule
    
    async def delete(self, rule: Rule, soft: bool = True) -> None:
        """Delete a rule (soft or hard delete)."""
        if soft:
            rule.deleted_at = datetime.now(timezone.utc)
            rule.status = RuleStatus.ARCHIVED
            await self._session.flush()
        else:
            await self._session.delete(rule)
            await self._session.flush()
    
    async def exists(self, rule_id: str) -> bool:
        """Check if a rule with given ID exists."""
        query = select(func.count(Rule.rule_id)).where(
            Rule.rule_id == rule_id,
            Rule.deleted_at.is_(None)
        )
        result = await self._session.execute(query)
        return result.scalar() > 0
    
    async def count_active_rules_for_device(self, device_id: str) -> int:
        """Count active rules for a specific device."""
        query = select(func.count(Rule.rule_id)).where(
            and_(
                Rule.status == RuleStatus.ACTIVE,
                Rule.deleted_at.is_(None),
                or_(
                    Rule.scope == "all_devices",
                    (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                )
            )
        )
        result = await self._session.execute(query)
        return result.scalar()


class AlertRepository:
    """Repository for Alert entity operations."""
    
    def __init__(self, session: AsyncSession):
        self._session = session
    
    async def create(self, alert: Alert) -> Alert:
        """Create a new alert in the database."""
        self._session.add(alert)
        await self._session.flush()
        await self._session.refresh(alert)
        return alert
    
    async def get_by_id(
        self, 
        alert_id: str | UUID,
        tenant_id: Optional[str] = None
    ) -> Optional[Alert]:
        """Get alert by ID with optional tenant filtering."""
        alert_id_str = str(alert_id)
        query = select(Alert).where(Alert.alert_id == alert_id_str)
        
        if tenant_id is not None:
            query = query.where(Alert.tenant_id == tenant_id)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def list_alerts(
        self,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
        rule_id: Optional[str | UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Alert], int]:
        """List alerts with filtering and pagination."""
        query = select(Alert)
        count_query = select(func.count(Alert.alert_id))
        
        # Apply filters
        if tenant_id is not None:
            query = query.where(Alert.tenant_id == tenant_id)
            count_query = count_query.where(Alert.tenant_id == tenant_id)
        
        if device_id:
            query = query.where(Alert.device_id == device_id)
            count_query = count_query.where(Alert.device_id == device_id)
        
        if rule_id:
            rule_id_str = str(rule_id)
            query = query.where(Alert.rule_id == rule_id_str)
            count_query = count_query.where(Alert.rule_id == rule_id_str)
        
        if status:
            query = query.where(Alert.status == status)
            count_query = count_query.where(Alert.status == status)
        
        # Get total count
        count_result = await self._session.execute(count_query)
        total = count_result.scalar()
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        query = query.order_by(Alert.created_at.desc())
        
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------
    # NEW – permanent, non-breaking extensions
    # ------------------------------------------------------------------

    async def acknowledge_alert(
        self,
        alert_id: str,
        acknowledged_by: Optional[str] = None,
    ) -> Optional[Alert]:
        """
        Mark an alert as acknowledged.
        """
        alert = await self.get_by_id(alert_id)

        if not alert:
            return None

        alert.status = "acknowledged"
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.now(timezone.utc)

        await self._session.flush()
        await self._session.refresh(alert)

        return alert

    async def resolve_alert(
        self,
        alert_id: str,
    ) -> Optional[Alert]:
        """
        Mark an alert as resolved.
        """
        alert = await self.get_by_id(alert_id)

        if not alert:
            return None

        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)

        await self._session.flush()
        await self._session.refresh(alert)

        return alert

    async def count_by_status(
        self,
        *,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Count alerts grouped by status."""
        query = select(Alert.status, func.count(Alert.alert_id)).group_by(Alert.status)
        if tenant_id is not None:
            query = query.where(Alert.tenant_id == tenant_id)
        result = await self._session.execute(query)
        rows = result.all()
        return {str(status): int(count) for status, count in rows}

    async def latest_for_rule_device(
        self,
        *,
        rule_id: str,
        device_id: str,
    ) -> Optional[Alert]:
        """Return latest alert for a rule+device pair."""
        result = await self._session.execute(
            select(Alert)
            .where(
                Alert.rule_id == rule_id,
                Alert.device_id == device_id,
            )
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class ActivityEventRepository:
    """Repository for activity event operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            tenant_id=tenant_id,
            device_id=device_id,
            rule_id=rule_id,
            alert_id=alert_id,
            event_type=event_type,
            title=title,
            message=message,
            metadata_json=metadata_json or {},
            is_read=False,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def list_events(
        self,
        *,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
        event_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[ActivityEvent], int]:
        query = select(ActivityEvent)
        count_query = select(func.count(ActivityEvent.event_id))

        if tenant_id is not None:
            query = query.where(ActivityEvent.tenant_id == tenant_id)
            count_query = count_query.where(ActivityEvent.tenant_id == tenant_id)

        if device_id:
            query = query.where(ActivityEvent.device_id == device_id)
            count_query = count_query.where(ActivityEvent.device_id == device_id)

        if event_type:
            query = query.where(ActivityEvent.event_type == event_type)
            count_query = count_query.where(ActivityEvent.event_type == event_type)

        total_result = await self._session.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(ActivityEvent.created_at.desc()).offset(offset).limit(page_size)

        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def unread_count(
        self,
        *,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> int:
        query = select(func.count(ActivityEvent.event_id)).where(ActivityEvent.is_read.is_(False))

        if tenant_id is not None:
            query = query.where(ActivityEvent.tenant_id == tenant_id)
        if device_id:
            query = query.where(ActivityEvent.device_id == device_id)

        result = await self._session.execute(query)
        return result.scalar() or 0

    async def mark_all_read(
        self,
        *,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> int:
        query = select(ActivityEvent).where(ActivityEvent.is_read.is_(False))

        if tenant_id is not None:
            query = query.where(ActivityEvent.tenant_id == tenant_id)
        if device_id:
            query = query.where(ActivityEvent.device_id == device_id)

        rows = (await self._session.execute(query)).scalars().all()
        now = datetime.now(timezone.utc)
        for event in rows:
            event.is_read = True
            event.read_at = now

        await self._session.flush()
        return len(rows)

    async def clear_history(
        self,
        *,
        tenant_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> int:
        query = select(ActivityEvent)

        if tenant_id is not None:
            query = query.where(ActivityEvent.tenant_id == tenant_id)
        if device_id:
            query = query.where(ActivityEvent.device_id == device_id)

        rows = (await self._session.execute(query)).scalars().all()
        count = len(rows)
        for event in rows:
            await self._session.delete(event)
        await self._session.flush()
        return count

    async def count_by_event_types(
        self,
        event_types: List[str],
        *,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Count activity events grouped by event type."""
        if not event_types:
            return {}

        query = (
            select(ActivityEvent.event_type, func.count(ActivityEvent.event_id))
            .where(ActivityEvent.event_type.in_(event_types))
            .group_by(ActivityEvent.event_type)
        )
        if tenant_id is not None:
            query = query.where(ActivityEvent.tenant_id == tenant_id)

        result = await self._session.execute(query)
        rows = result.all()
        return {str(event_type): int(count) for event_type, count in rows}
