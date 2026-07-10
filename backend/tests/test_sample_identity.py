"""Generic sample identity clustering tests."""

from app.services.extractor_v7.sample_identity import (
    apply_sample_alias_map,
    build_sample_alias_map,
    merge_sample_identities,
)


def test_merge_similar_loading_variants():
    mentions = [
        {"normalized_sample_id": "1.0 wt% CNCs", "mention_text": "1.0 wt% CNCs", "aliases": []},
        {"normalized_sample_id": "PCF_1.0wtCNC", "mention_text": "PCF_1.0wtCNC", "aliases": []},
        {"normalized_sample_id": "PVDF powder", "mention_text": "PVDF powder", "aliases": []},
        {"normalized_sample_id": "recycled cellulose pulp", "mention_text": "recycled cellulose pulp", "aliases": []},
    ]
    alias_map = build_sample_alias_map(mentions)
    assert alias_map["1.0 wt% CNCs"] == alias_map["PCF_1.0wtCNC"]
    assert alias_map["PVDF powder"] == "PVDF powder"
    assert alias_map["recycled cellulose pulp"] == "recycled cellulose pulp"


def test_apply_alias_map_rewrites_facts_and_cards():
    alias_map = {"1.0 wt% CNCs": "PCF_1.0wtCNC"}
    facts = [{"assigned_sample_id": "1.0 wt% CNCs", "candidate_sample_ids": ["1.0 wt% CNCs"]}]
    cards = [{"sample_id": "1.0 wt% CNCs", "sample_aliases": ""}]
    mentions = [{"normalized_sample_id": "1.0 wt% CNCs", "aliases": []}]
    mentions, facts, cards = apply_sample_alias_map(
        alias_map, sample_mentions=mentions, facts=facts, sample_cards=cards,
    )
    assert facts[0]["assigned_sample_id"] == "PCF_1.0wtCNC"
    assert cards[0]["sample_id"] == "PCF_1.0wtCNC"


def test_merge_sample_identities_is_noop_for_distinct_samples():
    mentions = [
        {"normalized_sample_id": "Sample-A", "aliases": []},
        {"normalized_sample_id": "Sample-B", "aliases": []},
    ]
    facts = [{"assigned_sample_id": "Sample-A"}]
    cards = [{"sample_id": "Sample-A"}, {"sample_id": "Sample-B"}]
    out_mentions, out_facts, out_cards = merge_sample_identities(mentions, facts, cards)
    assert len(out_cards) == 2
    assert out_facts[0]["assigned_sample_id"] == "Sample-A"
