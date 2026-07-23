from types import SimpleNamespace

from scripts.benchmark.run_extraction_benchmark import _quality_score


def _row(**overrides):
    values = {
        "sample_id": "S1",
        "performance_metric": "tensile_strength",
        "performance_value": "12",
        "performance_unit": "MPa",
        "performance_condition": "room temperature",
        "performance_evidence": "S1 reached 12 MPa.",
        "evidence_text": "S1 reached 12 MPa.",
        "composition_expression": "fiber/epoxy",
        "matrix_name": "epoxy",
        "additive_expression": "fiber",
        "composition_evidence": "fiber/epoxy",
        "process_route": "molding",
        "spinning_method": "",
        "process_parameters": "",
        "post_treatment": "",
        "process_evidence": "molding",
        "structure_methods": "SEM",
        "structure_features": "aligned",
        "structure_evidence": "SEM",
        "extraction_method": "AI_holistic_table",
        "reviewer_comment": "",
        "ai_confidence": 0.88,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_quality_proxy_penalizes_review_flags_and_duplicates():
    rows = [
        _row(),
        _row(
            reviewer_comment="qa_reason=checklist_failed",
            performance_condition="room temperature; export_tier_B_review",
            ai_confidence=0.6,
        ),
    ]

    score, coverage = _quality_score(rows)

    assert score < 1.0
    assert coverage["qa_flagged"] == 1
    assert coverage["actionable_qa"] == 1
    assert coverage["duplicate_rows"] == 1
    assert coverage["table_grounded"] == 2
    assert coverage["low_confidence"] == 1


def test_quality_proxy_treats_deterministic_process_rows_as_grounded_not_actionable():
    row = _row(
        performance_metric="voltage",
        performance_value="20",
        performance_unit="kV",
        extraction_method="rule_table_process",
        reviewer_comment=(
            "metric_priority=Secondary; export_target=Result_Facts_QA; "
            "qa_reason=fact_type=process;experimental_condition"
        ),
    )

    score, coverage = _quality_score([row])

    assert score == 1.0
    assert coverage["qa_flagged"] == 1
    assert coverage["actionable_qa"] == 0
    assert coverage["process_rows"] == 1
    assert coverage["deterministic_process_rows"] == 1
    assert coverage["table_grounded"] == 1
    assert coverage["narrative"] == 0


def test_quality_proxy_counts_deterministic_performance_as_table_grounded():
    row = _row(extraction_method="rule_table_performance")

    score, coverage = _quality_score([row])

    assert score == 1.0
    assert coverage["deterministic_performance_rows"] == 1
    assert coverage["table_grounded"] == 1
    assert coverage["narrative"] == 0


def test_core_performance_count_uses_actual_export_target():
    rows = [
        _row(
            reviewer_comment="metric_priority=Core; export_target=Core_Final_Records",
        ),
        _row(
            performance_value="<100",
            reviewer_comment=(
                "metric_priority=Core; export_target=Result_Facts_QA; "
                "qa_reason=export_tier_B_review;checklist_failed"
            ),
        ),
    ]

    _, coverage = _quality_score(rows)

    assert coverage["core_performance_rows"] == 1
    assert coverage["actionable_qa"] == 1
