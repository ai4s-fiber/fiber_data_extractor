"""Page inventory model — per-page metadata for extraction planning."""

from datetime import datetime, timezone
from sqlalchemy import Text, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PageInventory(Base):
    __tablename__ = "page_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text_length: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    has_table_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    has_figure_caption: Mapped[bool] = mapped_column(Boolean, default=False)
    has_experimental_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    has_supplementary_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
