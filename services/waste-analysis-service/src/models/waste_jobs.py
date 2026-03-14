from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class WasteScope(PyEnum):
    all = "all"
    selected = "selected"


class WasteStatus(PyEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class WasteGranularity(PyEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class WasteAnalysisJob(Base):
    __tablename__ = "waste_analysis_jobs"

    id = Column(String(36), primary_key=True)
    job_name = Column(String(255), nullable=True)
    scope = Column(Enum(WasteScope), nullable=False)
    device_ids = Column(JSON, nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    granularity = Column(Enum(WasteGranularity), nullable=False)
    status = Column(Enum(WasteStatus), nullable=False, default=WasteStatus.pending)
    progress_pct = Column(Integer, nullable=False, default=0)
    stage = Column(String(255), nullable=True)
    result_json = Column(JSON, nullable=True)
    s3_key = Column(String(500), nullable=True)
    download_url = Column(String(500), nullable=True)
    tariff_rate_used = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class WasteDeviceSummary(Base):
    __tablename__ = "waste_device_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(100), nullable=False)
    device_name = Column(String(255), nullable=True)
    data_source_type = Column(String(20), nullable=True)

    idle_duration_sec = Column(Integer, nullable=False, default=0)
    idle_energy_kwh = Column(Float, nullable=False, default=0)
    idle_cost = Column(Float, nullable=True)

    standby_power_kw = Column(Float, nullable=True)
    standby_energy_kwh = Column(Float, nullable=True)
    standby_cost = Column(Float, nullable=True)

    total_energy_kwh = Column(Float, nullable=False, default=0)
    total_cost = Column(Float, nullable=True)

    offhours_energy_kwh = Column(Float, nullable=True)
    offhours_cost = Column(Float, nullable=True)
    offhours_duration_sec = Column(Integer, nullable=True)
    offhours_skipped_reason = Column(String(100), nullable=True)
    offhours_pf_estimated = Column(Boolean, nullable=False, default=False)

    overconsumption_duration_sec = Column(Integer, nullable=True)
    overconsumption_kwh = Column(Float, nullable=True)
    overconsumption_cost = Column(Float, nullable=True)
    overconsumption_skipped_reason = Column(String(100), nullable=True)
    overconsumption_pf_estimated = Column(Boolean, nullable=False, default=False)

    unoccupied_duration_sec = Column(Integer, nullable=True)
    unoccupied_energy_kwh = Column(Float, nullable=True)
    unoccupied_cost = Column(Float, nullable=True)
    unoccupied_skipped_reason = Column(String(100), nullable=True)
    unoccupied_pf_estimated = Column(Boolean, nullable=False, default=False)

    data_quality = Column(String(20), nullable=True)
    energy_quality = Column(String(20), nullable=True)
    idle_quality = Column(String(20), nullable=True)
    standby_quality = Column(String(20), nullable=True)
    overall_quality = Column(String(20), nullable=True)
    idle_status = Column(String(32), nullable=True)
    pf_estimated = Column(Boolean, nullable=False, default=False)
    warnings = Column(JSON, nullable=True)
    calculation_method = Column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_waste_job_device", "job_id", "device_id"),
        UniqueConstraint("job_id", "device_id", name="uq_waste_job_device"),
    )
