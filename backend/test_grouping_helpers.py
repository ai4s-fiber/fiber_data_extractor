"""Minimal tests for deterministic V7 grouping helpers."""
from app.services.grouping import assign_fact_to_sample, build_sample_cards, group_samples


def main() -> None:
    sample_mentions = [
        {
            "mention_text": "S1",
            "normalized_sample_id": "S1",
            "aliases": [],
            "context_text": "Samples S1 and S2 were prepared with 1 wt% and 2 wt% CNT.",
            "source_location": "p.3, Experimental section",
            "source_type": "text",
            "confidence": 0.9,
        },
        {
            "mention_text": "S2",
            "normalized_sample_id": "S2",
            "aliases": [],
            "context_text": "Samples S1 and S2 were prepared with 1 wt% and 2 wt% CNT.",
            "source_location": "p.3, Experimental section",
            "source_type": "text",
            "confidence": 0.9,
        },
        {
            "mention_text": "optimized sample",
            "normalized_sample_id": "optimized sample",
            "aliases": [],
            "context_text": "the optimized sample showed high strength",
            "source_location": "p.5, results",
            "source_type": "text",
            "confidence": 0.4,
        },
    ]
    variable_candidates = [
        {
            "sample_id": "S1",
            "variable_name_raw": "CNT content",
            "variable_value_raw": "1",
            "variable_unit_raw": "wt%",
            "context_text": "",
            "source_location": "p.3, Experimental section",
            "confidence": 0.9,
        },
        {
            "sample_id": "S2",
            "variable_name_raw": "CNT content",
            "variable_value_raw": "2",
            "variable_unit_raw": "wt%",
            "context_text": "",
            "source_location": "p.3, Experimental section",
            "confidence": 0.9,
        },
    ]
    groups = group_samples(sample_mentions, variable_candidates)
    assert groups[0]["sample_group_id"] == "G001"
    assert set(groups[0]["sample_ids"]) == {"S1", "S2"}
    assert groups[0]["group_variable_name"] == "CNT content"
    assert not groups[0]["is_provisional"]

    fact = {
        "fact_type": "performance",
        "candidate_sample_ids": ["S2"],
        "metric_or_parameter": "tensile strength",
        "value": "25",
        "unit": "MPa",
        "evidence_text": "S2 showed tensile strength of 25 MPa",
        "source_location": "p.5, Fig. 2a",
    }
    assignment = assign_fact_to_sample(fact, sample_mentions, groups)
    assert assignment["sample_id"] == "S2"
    assert assignment["status"] == "assigned"
    assert assignment["confidence"] >= 0.9

    cards = build_sample_cards(
        sample_mentions,
        variable_candidates,
        groups,
        [
            {
                "fact_type": "composition",
                "assigned_sample_id": "S1",
                "metric_or_parameter": "CNT loading",
                "value": "1",
                "unit": "wt%",
                "evidence_text": "S1 contained 1 wt% CNT",
                "source_location": "p.3, Experimental section",
            }
        ],
    )
    by_id = {card["sample_id"]: card for card in cards}
    assert by_id["S1"]["sample_group_id"] == "G001"
    assert by_id["S1"]["variable_name"] == "CNT content"
    assert "CNT loading" in by_id["S1"]["composition_expression"]
    assert "optimized sample" not in by_id
    print("grouping helper tests passed")


if __name__ == "__main__":
    main()
