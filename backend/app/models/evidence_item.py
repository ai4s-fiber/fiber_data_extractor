"""Evidence item model — links evidence to candidate records."""

from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    candidate_record_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("candidate_records.id"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # metadata, experimental_text, table, figure_caption, vision_page, supplementary_hint
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
