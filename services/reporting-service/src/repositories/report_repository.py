from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from src.models import EnergyReport, ReportType, ReportStatus


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_report(
        self,
        report_id: str,
        tenant_id: str,
        report_type: str,
        params: dict
    ) -> EnergyReport:
        report = EnergyReport(
            report_id=report_id,
            tenant_id=tenant_id,
            report_type=ReportType(report_type),
            status="pending",
            params=params,
            created_at=datetime.utcnow()
        )
        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)
        return report
    
    async def get_report(
        self,
        report_id: str,
        tenant_id: Optional[str]
    ) -> Optional[EnergyReport]:
        query = select(EnergyReport).where(EnergyReport.report_id == report_id)
        if tenant_id is not None:
            query = query.where(EnergyReport.tenant_id == tenant_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def update_report(
        self,
        report_id: str,
        **kwargs
    ) -> None:
        update_values = {k: v for k, v in kwargs.items() if v is not None}
        await self.db.execute(
            update(EnergyReport)
            .where(EnergyReport.report_id == report_id)
            .values(**update_values)
        )
        await self.db.commit()
    
    async def list_reports(
        self,
        tenant_id: str,
        limit: int = 20,
        offset: int = 0,
        report_type: Optional[str] = None
    ) -> list[EnergyReport]:
        query = select(EnergyReport).where(EnergyReport.tenant_id == tenant_id)
        
        if report_type:
            query = query.where(EnergyReport.report_type == ReportType(report_type))
        
        query = query.order_by(EnergyReport.created_at.desc()).limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def find_active_duplicate(
        self,
        tenant_id: str,
        report_type: str,
        dedup_signature: str,
        limit: int = 50,
    ) -> Optional[EnergyReport]:
        query = (
            select(EnergyReport)
            .where(EnergyReport.tenant_id == tenant_id)
            .where(EnergyReport.report_type == ReportType(report_type))
            .where(EnergyReport.status.in_([ReportStatus.pending, ReportStatus.processing]))
            .order_by(EnergyReport.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(query)
        for report in result.scalars().all():
            params = report.params or {}
            if params.get("dedup_signature") == dedup_signature:
                return report
        return None
