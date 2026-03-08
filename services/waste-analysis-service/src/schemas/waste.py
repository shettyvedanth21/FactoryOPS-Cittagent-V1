from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WasteAnalysisRunRequest(BaseModel):
    job_name: Optional[str] = Field(default=None, max_length=255)
    scope: Literal["all", "selected"]
    device_ids: Optional[list[str]] = None
    start_date: date
    end_date: date
    granularity: Literal["daily", "weekly", "monthly"] = "daily"


class WasteAnalysisRunResponse(BaseModel):
    job_id: str
    status: str
    estimated_completion_seconds: int


class WasteStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: int
    stage: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class WasteDownloadResponse(BaseModel):
    job_id: str
    download_url: str
    expires_in_seconds: int = 900


class WasteHistoryItem(BaseModel):
    job_id: str
    job_name: Optional[str]
    status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str]
    completed_at: Optional[str]
    progress_pct: int


class WasteHistoryResponse(BaseModel):
    items: list[WasteHistoryItem]
