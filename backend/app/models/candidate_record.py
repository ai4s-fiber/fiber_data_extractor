"""Candidate record model — the core 40-column table for review and export."""

from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CandidateRecord(Base):
    __tablename__ = "candidate_records"

    # --- System fields ---
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    source_paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extraction_jobs.id"), nullable=True
    )

    # --- 40 Excel columns ---
    # 1. record_id
    record_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 2. paper_id (business field, not FK)
    paper_id_str: Mapped[str | None] = mapped_column("paper_id_biz", String(100), nullable=True)
    # 3. paper_title
    paper_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 4. doi_or_url
    doi_or_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 5. year
    year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 6. journal
    journal: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 7. sample_group_id
    sample_group_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 8. sample_id
    sample_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 9. material_system
    material_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 10. fiber_type
    fiber_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 11. variable_name
    variable_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 12. variable_value
    variable_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 13. variable_unit
    variable_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 14. composition_expression
    composition_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 15. matrix_name
    matrix_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 16. matrix_content
    matrix_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 17. matrix_unit
    matrix_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 18. additive_expression
    additive_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 19. solvent_or_aid
    solvent_or_aid: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 20. composition_evidence
    composition_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 21. process_route
    process_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 22. spinning_method
    spinning_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 23. process_parameters
    process_parameters: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 24. post_treatment
    post_treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 25. process_evidence
    process_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 26. structure_methods
    structure_methods: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 27. structure_features
    structure_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 28. structure_evidence
    structure_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 29. performance_category
    performance_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 30. performance_metric
    performance_metric: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 31. performance_value
    performance_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 32. performance_unit
    performance_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 33. performance_method
    performance_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 34. performance_condition
    performance_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 35. performance_evidence
    performance_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 36. extraction_method
    extraction_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 37. evidence_text
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 38. ai_confidence
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 39. review_status
    review_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="pending"
    )  # pending, modified, approved, uncertain, missing, deleted
    # 40. reviewer_comment
    reviewer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Additional system fields ---
    candidate_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft"
    )  # draft, submitted, approved, rejected
    source_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
