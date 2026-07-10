"""Pydantic schemas for export jobs."""

from datetime import datetime
from pydantic import BaseModel


class ExportRequest(BaseModel):
    review_status_filter: list[str] | None = None  # e.g. ["approved"], default all approved


class ExportJobOut(BaseModel):
    id: int
    project_id: int
    status: str
    filter_json: str | None
    file_object_key: str | None
    created_at: datetime
    finished_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class ExportCreateResult(ExportJobOut):
    exported_record_count: int = 0
    cleared_record_count: int = 0
