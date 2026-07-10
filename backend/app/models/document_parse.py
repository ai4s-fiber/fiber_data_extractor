"""MinerU document parse artifacts and normalized document blocks."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentParseRun(Base):
    __tablename__ = "document_parse_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    parser_name: Mapped[str] = mapped_column(String(50), nullable=False, default="mineru")
    mineru_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mineru_backend: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parse_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_result_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DocumentBlock(Base):
    __tablename__ = "document_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_parse_runs.id"), nullable=False
    )
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    block_id: Mapped[str] = mapped_column(String(120), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_type: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    section_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_block_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    related_block_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DocumentTable(Base):
    __tablename__ = "document_tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_parse_runs.id"), nullable=False
    )
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    table_id: Mapped[str] = mapped_column(String(120), nullable=False)
    block_id: Mapped[str] = mapped_column(String(120), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DocumentFigure(Base):
    __tablename__ = "document_figures"

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_parse_runs.id"), nullable=False
    )
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    figure_id: Mapped[str] = mapped_column(String(120), nullable=False)
    block_id: Mapped[str] = mapped_column(String(120), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_type: Mapped[str] = mapped_column(String(50), nullable=False, default="figure")
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
