from app.services.gold_evaluation import evaluate_gold_set


def _gold():
    return {
        "schema_version": 1,
        "name": "unit-gold",
        "thresholds": {
            "precision": 1.0,
            "recall": 1.0,
            "sample_assignment_accuracy": 1.0,
            "unit_accuracy": 1.0,
            "evidence_coverage": 1.0,
            "document_type_accuracy": 1.0,
        },
        "papers": [
            {
                "filename": "research.pdf",
                "sha256": "a" * 64,
                "document_type": "research",
                "exhaustive": True,
                "facts": [
                    {
                        "sample_id": "S1",
                        "metric": "tensile_strength",
                        "value": "12",
                        "unit": "MPa",
                        "evidence_contains": ["S1", "12 MPa"],
                    }
                ],
            },
            {
                "filename": "review.pdf",
                "sha256": "b" * 64,
                "document_type": "review",
                "exhaustive": True,
                "facts": [],
            },
        ],
    }


def test_gold_gate_passes_exact_results_and_review_skip():
    actual = [
        {
            "filename": "research.pdf",
            "sha256": "a" * 64,
            "document_type": "research",
            "candidates": [
                {
                    "sample_id": "S1",
                    "metric": "tensile strength",
                    "value": "12.0000001",
                    "unit": "MPa",
                    "evidence": "S1 reached 12 MPa.",
                }
            ],
        },
        {
            "filename": "review.pdf",
            "sha256": "b" * 64,
            "document_type": "review",
            "candidates": [],
        },
    ]

    result = evaluate_gold_set(_gold(), actual)

    assert result["gate_passed"] is True
    assert result["metrics"]["precision"] == 1.0
    assert result["metrics"]["recall"] == 1.0
    assert result["counts"]["matched_facts"] == 1


def test_gold_gate_reports_wrong_sample_and_unexpected_candidate():
    actual = [
        {
            "filename": "research.pdf",
            "sha256": "a" * 64,
            "document_type": "research",
            "candidates": [
                {
                    "sample_id": "S2",
                    "metric": "tensile_strength",
                    "value": "12",
                    "unit": "MPa",
                    "evidence": "S2 reached 12 MPa.",
                },
                {
                    "sample_id": "S1",
                    "metric": "density",
                    "value": "1000",
                    "unit": "kg m^-3",
                    "evidence": "S1 density was 1000 kg m^-3.",
                },
            ],
        },
        {
            "filename": "review.pdf",
            "sha256": "b" * 64,
            "document_type": "research",
            "candidates": [],
        },
    ]

    result = evaluate_gold_set(_gold(), actual)

    assert result["gate_passed"] is False
    assert result["metrics"]["precision"] == 0.0
    assert result["metrics"]["recall"] == 0.0
    assert result["metrics"]["sample_assignment_accuracy"] == 0.0
    assert result["metrics"]["unit_accuracy"] == 1.0
    assert result["metrics"]["document_type_accuracy"] == 0.5
    assert result["papers"][0]["missing_facts"][0]["sample_id"] == "S1"
    assert len(result["papers"][0]["unexpected_candidates"]) == 2
