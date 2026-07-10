"""Import all models to ensure FK tables are registered in Base.metadata."""

from app.models.base import Base
from app.models.project import Project
from app.models.paper import Paper
from app.models.extraction_job import ExtractionJob
from app.models.page_inventory import PageInventory
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.review_log import ReviewLog
from app.models.export_job import ExportJob
from app.models.sample_catalog import SampleCatalog
from app.models.fact_candidate import FactCandidate
from app.models.document_parse import (
    DocumentParseRun,
    DocumentBlock,
    DocumentTable,
    DocumentFigure,
)
