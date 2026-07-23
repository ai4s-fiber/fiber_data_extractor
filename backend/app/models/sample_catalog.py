"""Sample catalog model — stores AI-identified samples per paper (Stage 1 of V7 pipeline)."""

from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SampleCatalog(Base):
    __tablename__ = "sample_catalogs"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    sample_id: Mapped[str] = mapped_column(Text, nullable=False)
    sample_aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_group_id: Mapped[str] = mapped_column(String(100), nullable=False, default="Group-A")
    material_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiber_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    composition_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
