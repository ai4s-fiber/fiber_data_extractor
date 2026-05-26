"""Pydantic schemas for candidate records."""

from datetime import datetime
from pydantic import BaseModel


class CandidateRecordCreate(BaseModel):
    """Create a new candidate record. All 40 fields optional on creation."""
    source_paper_id: int
    record_id: str | None = None
    paper_id_str: str | None = None
    paper_title: str | None = None
    doi_or_url: str | None = None
    year: str | None = None
    journal: str | None = None
    sample_group_id: str | None = None
    sample_id: str | None = None
    material_system: str | None = None
    fiber_type: str | None = None
    variable_name: str | None = None
    variable_value: str | None = None
    variable_unit: str | None = None
    composition_expression: str | None = None
    matrix_name: str | None = None
    matrix_content: str | None = None
    matrix_unit: str | None = None
    additive_expression: str | None = None
    solvent_or_aid: str | None = None
    composition_evidence: str | None = None
    process_route: str | None = None
    spinning_method: str | None = None
    process_parameters: str | None = None
    post_treatment: str | None = None
    process_evidence: str | None = None
    structure_methods: str | None = None
    structure_features: str | None = None
    structure_evidence: str | None = None
    performance_category: str | None = None
    performance_metric: str | None = None
    performance_value: str | None = None
    performance_unit: str | None = None
    performance_method: str | None = None
    performance_condition: str | None = None
    performance_evidence: str | None = None
    extraction_method: str | None = None
    evidence_text: str | None = None
    ai_confidence: float | None = None
    review_status: str | None = "pending"
    reviewer_comment: str | None = None
    source_location: str | None = None


class CandidateRecordUpdate(BaseModel):
    """Update a candidate record. All fields optional."""
    record_id: str | None = None
    paper_id_str: str | None = None
    paper_title: str | None = None
    doi_or_url: str | None = None
    year: str | None = None
    journal: str | None = None
    sample_group_id: str | None = None
    sample_id: str | None = None
    material_system: str | None = None
    fiber_type: str | None = None
    variable_name: str | None = None
    variable_value: str | None = None
    variable_unit: str | None = None
    composition_expression: str | None = None
    matrix_name: str | None = None
    matrix_content: str | None = None
    matrix_unit: str | None = None
    additive_expression: str | None = None
    solvent_or_aid: str | None = None
    composition_evidence: str | None = None
    process_route: str | None = None
    spinning_method: str | None = None
    process_parameters: str | None = None
    post_treatment: str | None = None
    process_evidence: str | None = None
    structure_methods: str | None = None
    structure_features: str | None = None
    structure_evidence: str | None = None
    performance_category: str | None = None
    performance_metric: str | None = None
    performance_value: str | None = None
    performance_unit: str | None = None
    performance_method: str | None = None
    performance_condition: str | None = None
    performance_evidence: str | None = None
    extraction_method: str | None = None
    evidence_text: str | None = None
    ai_confidence: float | None = None
    review_status: str | None = None
    reviewer_comment: str | None = None
    source_location: str | None = None


class CandidateRecordOut(BaseModel):
    """Full candidate record output."""
    id: int
    project_id: int
    source_paper_id: int
    job_id: int | None
    record_id: str | None
    paper_id_str: str | None
    paper_title: str | None
    doi_or_url: str | None
    year: str | None
    journal: str | None
    sample_group_id: str | None
    sample_id: str | None
    material_system: str | None
    fiber_type: str | None
    variable_name: str | None
    variable_value: str | None
    variable_unit: str | None
    composition_expression: str | None
    matrix_name: str | None
    matrix_content: str | None
    matrix_unit: str | None
    additive_expression: str | None
    solvent_or_aid: str | None
    composition_evidence: str | None
    process_route: str | None
    spinning_method: str | None
    process_parameters: str | None
    post_treatment: str | None
    process_evidence: str | None
    structure_methods: str | None
    structure_features: str | None
    structure_evidence: str | None
    performance_category: str | None
    performance_metric: str | None
    performance_value: str | None
    performance_unit: str | None
    performance_method: str | None
    performance_condition: str | None
    performance_evidence: str | None
    extraction_method: str | None
    evidence_text: str | None
    ai_confidence: float | None
    review_status: str | None
    reviewer_comment: str | None
    candidate_status: str
    source_location: str | None
    assigned_to: int | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CandidateListItem(BaseModel):
    """Minimal candidate row for the review queue table."""
    id: int
    sample_id: str | None
    performance_metric: str | None
    performance_value: str | None
    performance_unit: str | None
    review_status: str | None
    ai_confidence: float | None
    source_location: str | None
    paper_title: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewAction(BaseModel):
    """Action to take on a candidate record."""
    action: str  # approved, modified, uncertain, missing, deleted
    comment: str | None = None
