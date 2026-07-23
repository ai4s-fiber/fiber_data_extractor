"""Fact candidate model — stores extracted facts before sample assignment (Stage 2 of V7 pipeline)."""

from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FactCandidate(Base):
    __tablename__ = "fact_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    fact_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fact_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # composition | process | structure | performance
    subject_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_sample_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric_or_parameter: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_block_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evidence_items.id"), nullable=True
    )
    extraction_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="AI_text"
    )  # AI_text | AI_table | AI_figure | AI_inferred
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Sample assignment result
    assigned_sample_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assignment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unassigned"
    )  # unassigned | assigned | uncertain | multiple

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
