"""Paper (literature) model."""

from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    paper_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi_or_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    journal: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="uploaded"
    )  # uploaded, queued, extracting, review, completed, failed
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
