from app.main import app
from app.models import Base
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.models.project import Project
from app.models.review_log import ReviewLog
from app.services.extractor_v7 import V7ExtractorService
from app.services.workbook_export import MAIN_DATA_COLUMNS


def column_names(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


def route_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_user_tables_are_removed_from_model_registry():
    assert "users" not in Base.metadata.tables
    assert "project_members" not in Base.metadata.tables


def test_business_tables_do_not_have_user_foreign_key_columns():
    assert "created_by" not in column_names(Project)
    assert "uploaded_by" not in column_names(Paper)
    assert "created_by" not in column_names(ExtractionJob)
    assert "created_by" not in column_names(ExportJob)
    assert "assigned_to" not in column_names(CandidateRecord)
    assert "reviewed_by" not in column_names(CandidateRecord)
    assert "user_id" not in column_names(ReviewLog)


def test_auth_user_member_routes_are_not_registered():
    paths = route_paths()
    assert not any(path.startswith("/api/auth") for path in paths)
    assert not any(path.startswith("/api/users") for path in paths)
    assert not any(path.startswith("/api/admin") for path in paths)
    assert not any("/members" in path for path in paths)


def test_core_extraction_contract_is_preserved():
    assert hasattr(V7ExtractorService, "run_full_pipeline_for_paper")
    assert callable(V7ExtractorService.run_full_pipeline_for_paper)
    assert len(MAIN_DATA_COLUMNS) == 40
    assert "record_id" in MAIN_DATA_COLUMNS
    assert "evidence_text" in MAIN_DATA_COLUMNS
