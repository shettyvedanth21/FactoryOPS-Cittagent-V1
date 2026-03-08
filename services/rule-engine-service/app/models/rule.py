"""SQLAlchemy models for Rule Engine Service."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
import uuid

from sqlalchemy import String, DateTime, Float, Integer, Text, ForeignKey, JSON
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RuleStatus(str, Enum):
    """Rule status enumeration."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RuleScope(str, Enum):
    """Rule scope enumeration."""

    ALL_DEVICES = "all_devices"
    SELECTED_DEVICES = "selected_devices"


class ConditionOperator(str, Enum):
    """Condition operator enumeration."""

    GREATER_THAN = ">"
    LESS_THAN = "<"
    EQUAL = "="
    NOT_EQUAL = "!="
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN_OR_EQUAL = "<="


class RuleType(str, Enum):
    """Rule type enumeration."""

    THRESHOLD = "threshold"
    TIME_BASED = "time_based"


class CooldownMode(str, Enum):
    """Cooldown behavior mode."""

    INTERVAL = "interval"
    NO_REPEAT = "no_repeat"


class Rule(Base):
    """Rule model for real-time telemetry evaluation."""

    __tablename__ = "rules"

    rule_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scope: Mapped[RuleScope] = mapped_column(
        String(50),
        default=RuleScope.SELECTED_DEVICES,
        nullable=False,
    )

    property: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    condition: Mapped[Optional[ConditionOperator]] = mapped_column(String(20), nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    rule_type: Mapped[RuleType] = mapped_column(
        String(20),
        default=RuleType.THRESHOLD,
        nullable=False,
        index=True,
    )
    cooldown_mode: Mapped[CooldownMode] = mapped_column(
        String(20),
        default=CooldownMode.INTERVAL,
        nullable=False,
    )
    time_window_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    time_window_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    time_condition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    triggered_once: Mapped[bool] = mapped_column(default=False, nullable=False)

    status: Mapped[RuleStatus] = mapped_column(
        String(50),
        default=RuleStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    # JSON field storing list of strings
    notification_channels: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
        nullable=False,
    )

    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # JSON field storing list of strings
    device_ids: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Rule(rule_id={self.rule_id}, name={self.rule_name}, status={self.status})>"

    def is_active(self) -> bool:
        return self.status == RuleStatus.ACTIVE and self.deleted_at is None

    def is_in_cooldown(self) -> bool:
        if self.cooldown_mode == CooldownMode.NO_REPEAT:
            return bool(self.triggered_once)

        if self.last_triggered_at is None:
            return False

        from datetime import timedelta, timezone

        cooldown_end = self.last_triggered_at + timedelta(minutes=self.cooldown_minutes)
        now = datetime.now(timezone.utc)
        
        if self.last_triggered_at.tzinfo is None:
            cooldown_end = cooldown_end.replace(tzinfo=timezone.utc)
        
        return now < cooldown_end

    def applies_to_device(self, device_id: str) -> bool:
        if self.scope == RuleScope.ALL_DEVICES:
            return True
        return device_id in self.device_ids


class Alert(Base):
    """Alert model for storing rule evaluation results."""

    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.rule_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    device_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(
        String(50),
        default="open",
        nullable=False,
        index=True,
    )

    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Alert(alert_id={self.alert_id}, rule_id={self.rule_id}, status={self.status})>"


class ActivityEvent(Base):
    """Activity event model for device/rule alert history."""

    __tablename__ = "activity_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    tenant_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    rule_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    alert_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    is_read: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<ActivityEvent(event_id={self.event_id}, type={self.event_type}, device_id={self.device_id})>"
