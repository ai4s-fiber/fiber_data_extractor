"""Pydantic schemas for papers."""

from datetime import datetime
from pydantic import BaseModel


class PaperOut(BaseModel):
    id: int
    project_id: int
    uploaded_by: int
    original_filename: str
    paper_title: str | None
    doi_or_url: str | None
    year: int | None
    journal: str | None
    status: str
    page_count: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaperUpdate(BaseModel):
    paper_title: str | None = None
    doi_or_url: str | None = None
    year: int | None = None
    journal: str | None = None
