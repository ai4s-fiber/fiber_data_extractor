"""Package import smoke tests."""

from app.services.extractor_v7 import V7ExtractorService, ExtractionCancelled, build_extraction_report


def test_v7_exports():
    assert V7ExtractorService is not None
    assert issubclass(ExtractionCancelled, Exception)
    report = build_extraction_report(
        {"paper_title": "t"},
        1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 0, {},
    )
    assert report["生成记录数"] == 1
