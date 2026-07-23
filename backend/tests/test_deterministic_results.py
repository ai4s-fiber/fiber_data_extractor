"""High-precision deterministic result recovery tests."""

from app.services.extractor_v7.deterministic_results import (
    recover_explicit_contrast_result_facts,
    recover_explicit_frequency_range_facts,
)


def _fact_by_value(facts: list[dict], value: str) -> dict:
    return next(fact for fact in facts if fact["value"] == value)


def test_recovers_explicit_static_contrast_results_from_real_mineru_text():
    chunk = {
        "source_block_id": "B000043",
        "page_number": 3,
        "section_name": "results",
        "source_type": "text",
        "raw_text": (
            "From the above results, when the applied load was 350 N, the "
            "displacement deformation of the fiber-reinforced structure material "
            "was 8.8 mm, whereas that of the TPU material structure was 16.7 mm. "
            "The stress at which softening occurred in the fiber-filled composite "
            "material was 430 N, whereas that in the TPU material was 350 N. The "
            "load-bearing stability of the composite material increased by 23%, "
            "and its resistance to deformation improved."
        ),
    }

    facts = recover_explicit_contrast_result_facts([chunk], [])

    assert len(facts) == 5
    assert _fact_by_value(facts, "8.8")["assigned_sample_id"] == (
        "fiber-reinforced structure material"
    )
    assert _fact_by_value(facts, "16.7")["assigned_sample_id"] == (
        "TPU material structure"
    )
    assert _fact_by_value(facts, "430")["assigned_sample_id"] == (
        "fiber-filled composite material"
    )
    assert _fact_by_value(facts, "350")["assigned_sample_id"] == "TPU material"
    assert _fact_by_value(facts, "23")["assigned_sample_id"] == "composite material"
    assert _fact_by_value(facts, "8.8")["condition"] == "applied load 350 N"


def test_recovers_dynamic_contrast_and_repairs_existing_wrong_assignment():
    chunk = {
        "source_block_id": "B000098",
        "page_number": 6,
        "section_name": "results",
        "source_type": "text",
        "raw_text": (
            "Figure 5c shows the time-domain response of the acceleration when "
            "the impact velocity was 5 m s-1. The TPU had a dimensionless maximum "
            "acceleration of 69, whereas the fiber enhancement had a dimensionless "
            "maximum acceleration of 37, which is a decrease of more than 46%."
        ),
    }
    existing = [{
        "fact_type": "performance",
        "metric_or_parameter": "maximum acceleration",
        "value": "37",
        "unit": "",
        "assigned_sample_id": "TPU",
        "candidate_sample_ids": ["TPU"],
        "assignment_status": "assigned",
        "assignment_confidence": 0.71,
        "evidence_text": chunk["raw_text"],
        "_source_block_id": "B000098",
    }]

    recovered = recover_explicit_contrast_result_facts([chunk], existing)

    assert {fact["value"] for fact in recovered} == {"69", "more than 46"}
    assert existing[0]["assigned_sample_id"] == "fiber enhancement"
    assert existing[0]["unit"] == "dimensionless"
    assert existing[0]["assignment_confidence"] == 0.97
    assert _fact_by_value(recovered, "69")["assigned_sample_id"] == "TPU"
    change = _fact_by_value(recovered, "more than 46")
    assert change["assigned_sample_id"] == "fiber enhancement"
    assert change["metric_or_parameter"] == "acceleration_reduction"
    assert change["condition"] == "impact velocity 5 m/s; compared with TPU"


def test_explicit_contrast_recovery_ignores_non_result_sections():
    chunk = {
        "source_block_id": "B000010",
        "page_number": 1,
        "section_name": "introduction",
        "source_type": "text",
        "raw_text": (
            "The TPU had a dimensionless maximum acceleration of 69, whereas the "
            "fiber enhancement had a dimensionless maximum acceleration of 37."
        ),
    }

    assert recover_explicit_contrast_result_facts([chunk], []) == []


def test_recovers_bandgap_normalized_and_transmission_ranges():
    chunks = [
        {
            "source_block_id": "B72",
            "page_number": 5,
            "order_index": 72,
            "section_name": "results",
            "source_type": "text",
            "raw_text": (
                "A directional bandgap appeared in the frequencies from 1050 to "
                "1400 Hz. Therefore, the corresponding normalized frequency is "
                "0.145 to 0.194."
            ),
        },
        {
            "source_block_id": "B76",
            "page_number": 5,
            "order_index": 76,
            "section_name": "results",
            "source_type": "text",
            "raw_text": (
                "The acoustic transmission spectrum of composite S1 showed a clear "
                "decay in transmission efficiency in the range of 1250-1500 Hz."
            ),
        },
    ]

    facts = recover_explicit_frequency_range_facts(chunks, [])
    by_metric = {fact["metric_or_parameter"]: fact for fact in facts}

    assert by_metric["bandgap_frequency_range"]["value"] == "1050-1400"
    assert by_metric["normalized_bandgap_frequency_range"]["value"] == "0.145-0.194"
    assert by_metric["transmission_attenuation_frequency_range"]["value"] == "1250-1500"


def test_does_not_duplicate_existing_grounded_range():
    chunk = {
        "source_block_id": "B72",
        "page_number": 5,
        "section_name": "results",
        "source_type": "text",
        "raw_text": "A directional bandgap occurred from 1050 to 1400 Hz.",
    }
    existing = [{
        "fact_type": "performance",
        "metric_or_parameter": "bandgap frequency range",
        "value": "1050-1400",
        "unit": "Hz",
        "evidence_text": chunk["raw_text"],
        "_source_block_id": "B72",
    }]

    assert recover_explicit_frequency_range_facts([chunk], existing) == []


def test_ignores_plain_test_frequency_range():
    chunk = {
        "source_block_id": "B1",
        "page_number": 1,
        "section_name": "results",
        "source_type": "text",
        "raw_text": "The specimen was tested in the frequency range of 10-100 Hz.",
    }

    assert recover_explicit_frequency_range_facts([chunk], []) == []
