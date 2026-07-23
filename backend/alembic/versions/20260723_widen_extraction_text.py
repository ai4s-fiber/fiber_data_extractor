"""widen model-generated extraction text

Revision ID: 20260723_widen_extraction_text
Revises: 20260723_bulk_quality_state
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_widen_extraction_text"
down_revision = "20260723_bulk_quality_state"
branch_labels = None
depends_on = None


TEXT_COLUMNS = {
    "candidate_records": {
        "paper_title",
        "doi_or_url",
        "journal",
        "sample_id",
        "material_system",
        "fiber_type",
        "variable_name",
        "variable_value",
        "variable_unit",
        "composition_expression",
        "matrix_name",
        "matrix_content",
        "matrix_unit",
        "additive_expression",
        "solvent_or_aid",
        "composition_evidence",
        "process_route",
        "spinning_method",
        "process_parameters",
        "post_treatment",
        "process_evidence",
        "structure_methods",
        "structure_features",
        "structure_evidence",
        "performance_category",
        "performance_metric",
        "performance_value",
        "performance_unit",
        "performance_method",
        "performance_condition",
        "performance_evidence",
        "evidence_text",
        "reviewer_comment",
        "source_location",
    },
    "sample_catalogs": {
        "sample_id",
        "sample_aliases",
        "material_system",
        "fiber_type",
        "variable_name",
        "variable_value",
        "variable_unit",
        "composition_expression",
        "process_route",
        "source_location",
        "evidence_text",
    },
    "fact_candidates": {
        "subject_text",
        "candidate_sample_ids",
        "metric_or_parameter",
        "value",
        "unit",
        "method",
        "condition",
        "category",
        "evidence_text",
        "source_location",
        "assigned_sample_id",
    },
    "evidence_items": {
        "source_location",
        "evidence_text",
        "normalized_payload",
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite does not enforce VARCHAR lengths. New databases use the
        # SQLAlchemy Text metadata and existing SQLite databases are safe.
        return
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table_name, target_columns in TEXT_COLUMNS.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {
            column["name"]: column["type"]
            for column in inspector.get_columns(table_name)
        }
        for column_name in sorted(target_columns):
            existing_type = existing_columns.get(column_name)
            if existing_type is None or isinstance(existing_type, sa.Text):
                continue
            op.alter_column(
                table_name,
                column_name,
                existing_type=existing_type,
                type_=sa.Text(),
                existing_nullable=True,
            )


def downgrade() -> None:
    # Do not shrink populated text columns and risk truncating extraction data.
    pass
