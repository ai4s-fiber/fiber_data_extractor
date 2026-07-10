"""Pydantic schemas for papers."""

from datetime import datetime
from pydantic import BaseModel


class PaperExtractRequest(BaseModel):
    model_mode: str = "auto"
    parser_strategy: str | None = None
    confirm_wipe: bool = False


class PaperOut(BaseModel):
    id: int
    project_id: int
    original_filename: str
    paper_title: str | None
    doi_or_url: str | None
    year: int | None
    journal: str | None
    status: str
    page_count: int | None
    created_at: datetime
    updated_at: datetime
    latest_job_id: int | None = None
    latest_requested_mode: str | None = None
    latest_resolved_mode: str | None = None
    latest_job_status: str | None = None
    latest_job_step: str | None = None
    latest_job_percent: int | None = None
    latest_job_message: str | None = None
    latest_error_message: str | None = None

    model_config = {"from_attributes": True}


class ExtractionJobOut(BaseModel):
    job_id: int
    paper_id: int
    requested_mode: str
    resolved_mode: str | None
    parser_strategy: str = "mineru_cloud"
    status: str
    step: str
    percent: int
    error_code: str | None = None
    error_message: str | None = None


class PaperUpdate(BaseModel):
    paper_title: str | None = None
    doi_or_url: str | None = None
    year: int | None = None
    journal: str | None = None
