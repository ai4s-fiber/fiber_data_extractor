from app.services.document_type import classify_document_type


def test_review_title_is_classified_with_high_confidence():
    text = """# Additive Manufacturing of Polymer Matrix Composite Materials: A Review

Herein, we review combinations of fabrication and filler alignment methods.
REVIEW
"""

    result = classify_document_type(text)

    assert result.kind == "review"
    assert result.confidence >= 0.9


def test_research_article_with_literature_review_sentence_is_not_skipped():
    text = """# Mechanical properties of aligned flax composites

Abstract
We prepared aligned flax composites and measured tensile strength.
Prior literature was reviewed to select the processing temperature.
"""

    result = classify_document_type(text)

    assert result.kind == "research"
