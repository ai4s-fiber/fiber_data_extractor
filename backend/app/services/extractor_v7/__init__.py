"""V7 multi-stage extraction pipeline package."""

from app.services.extractor_v7.exceptions import ExtractionCancelled, NoExtractableResults
from app.services.extractor_v7.reporting import build_extraction_report


def __getattr__(name: str):
    if name == "V7ExtractorService":
        from app.services.extractor_v7.service import V7ExtractorService

        return V7ExtractorService
    raise AttributeError(name)

__all__ = [
    "V7ExtractorService",
    "ExtractionCancelled",
    "NoExtractableResults",
    "build_extraction_report",
]
