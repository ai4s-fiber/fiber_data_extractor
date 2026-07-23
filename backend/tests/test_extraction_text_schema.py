import pytest
from sqlalchemy import Text

from app.core.schema_repair import EXTRACTION_TEXT_COLUMNS
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.sample_catalog import SampleCatalog


@pytest.mark.parametrize(
    ("model", "column_names"),
    [
        (
            CandidateRecord,
            [
                "sample_id",
                "composition_expression",
                "process_route",
                "process_parameters",
                "performance_value",
                "source_location",
            ],
        ),
        (
            SampleCatalog,
            ["sample_id", "variable_value", "process_route", "source_location"],
        ),
        (
            FactCandidate,
            ["metric_or_parameter", "value", "condition", "assigned_sample_id"],
        ),
        (EvidenceItem, ["source_location", "evidence_text"]),
    ],
)
def test_model_generated_text_fields_are_unbounded(model, column_names):
    for column_name in column_names:
        assert isinstance(model.__table__.c[column_name].type, Text)
        assert column_name in EXTRACTION_TEXT_COLUMNS[model.__tablename__]
