"""Import all models to ensure FK tables are registered in Base.metadata."""

from app.models.user import User
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.paper import Paper
from app.models.extraction_job import ExtractionJob
from app.models.page_inventory import PageInventory
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.review_log import ReviewLog
from app.models.export_job import ExportJob
