"""Dry-run V7 strong/weak logic without sending paper content to an LLM."""
from __future__ import annotations

import asyncio

from app.services.extractor_v7 import V7ExtractorService
from app.services.grouping import assign_fact_to_sample, build_sample_cards, group_samples


class FakeClient:
    def generate_json_tolerant(self, prompt: str, user_text: str, max_tokens: int = 1000):
        _ = max_tokens
        if "sample_mentions" in prompt:
            return {
                "sample_mentions": [
                    {
                        "mention_text": "S1",
                        "normalized_sample_id": "S1",
                        "aliases": [],
                        "context_text": "Samples S1 and S2 were prepared with 1 wt% and 2 wt% CNT.",
                        "confidence": 0.92,
                    },
                    {
                        "mention_text": "S2",
                        "normalized_sample_id": "S2",
                        "aliases": [],
                        "context_text": "Samples S1 and S2 were prepared with 1 wt% and 2 wt% CNT.",
                        "confidence": 0.92,
                    },
                ]
            }, None
        if "variable_candidates" in prompt:
            return {
                "variable_candidates": [
                    {
                        "sample_id": "S1",
                        "variable_name_raw": "CNT content",
                        "variable_value_raw": "1",
                        "variable_unit_raw": "wt%",
                        "context_text": "S1 with 1 wt% CNT",
                        "confidence": 0.9,
                    },
                    {
                        "sample_id": "S2",
                        "variable_name_raw": "CNT content",
                        "variable_value_raw": "2",
                        "variable_unit_raw": "wt%",
                        "context_text": "S2 with 2 wt% CNT",
                        "confidence": 0.9,
                    },
                ]
            }, None
        if "原子事实" in prompt:
            method = "weak"
        else:
            method = "strong"
        if "Experimental" in user_text:
            return {
                "facts": [
                    {
                        "fact_type": "composition",
                        "candidate_sample_ids": ["S1"],
                        "metric_or_parameter": "CNT loading",
                        "value": "1",
                        "unit": "wt%",
                        "evidence_text": "S1 with 1 wt% CNT",
                        "source_location": "p.2, Experimental section",
                        "confidence": 0.86,
                    },
                    {
                        "fact_type": "process",
                        "candidate_sample_ids": ["S1"],
                        "metric_or_parameter": "electrospinning voltage",
                        "value": "20",
                        "unit": "kV",
                        "evidence_text": "electrospinning voltage was 20 kV",
                        "source_location": "p.2, Experimental section",
                        "confidence": 0.86,
                    },
                ]
            }, None
        return {
            "facts": [
                {
                    "fact_type": "performance",
                    "candidate_sample_ids": ["S2"],
                    "metric_or_parameter": "tensile strength",
                    "value": "25",
                    "unit": "MPa",
                    "evidence_text": f"S2 showed tensile strength of 25 MPa ({method})",
                    "source_location": "p.5, Fig. 2a",
                    "confidence": 0.86,
                },
                {
                    "fact_type": "performance",
                    "candidate_sample_ids": ["S2"],
                    "metric_or_parameter": "XPS N 1s binding energy",
                    "value": "400.2",
                    "unit": "eV",
                    "evidence_text": "S2 showed an XPS N 1s binding energy of 400.2 eV",
                    "source_location": "p.5, Fig. 2b",
                    "confidence": 0.86,
                },
                {
                    "fact_type": "performance",
                    "candidate_sample_ids": ["S2"],
                    "metric_or_parameter": "simulation temperature",
                    "value": "298",
                    "unit": "K",
                    "evidence_text": "The simulation temperature was 298 K",
                    "source_location": "p.5, text",
                    "confidence": 0.86,
                },
                {
                    "fact_type": "performance",
                    "candidate_sample_ids": ["S2"],
                    "metric_or_parameter": "reaction fraction",
                    "value": "65",
                    "unit": "%",
                    "evidence_text": "The reaction fraction of the side-group pathway was 65%",
                    "source_location": "p.5, text",
                    "confidence": 0.86,
                }
            ]
        }, None


async def run_mode(mode: str) -> dict:
    chunks = [
        {
            "page_number": 2,
            "section_name": "experimental",
            "source_type": "text",
            "raw_text": "Experimental: Samples S1 and S2 were prepared with 1 wt% and 2 wt% CNT. electrospinning voltage was 20 kV.",
        },
        {
            "page_number": 5,
            "section_name": "results",
            "source_type": "figure_caption",
            "raw_text": "Fig. 2a. S2 showed tensile strength of 25 MPa.",
            "has_figure_image": True,
        },
    ]
    client = FakeClient()
    mentions = await V7ExtractorService._stage1_sample_mentions(client, chunks, mode)
    variables = await V7ExtractorService._stage1_variable_candidates(client, chunks, mentions, mode)
    groups = group_samples(mentions, variables)
    facts = await V7ExtractorService._stage2_fact_candidates(client, chunks, mode)
    for fact in facts:
        assignment = assign_fact_to_sample(fact, mentions, groups)
        fact["assigned_sample_id"] = assignment["sample_id"] or None
        fact["assignment_confidence"] = assignment["confidence"]
        fact["assignment_status"] = assignment["status"]
    cards = build_sample_cards(mentions, variables, groups, facts)
    records, report = V7ExtractorService._stage4_generate_records(
        1,
        1,
        {
            "paper_id_biz": "P0001",
            "paper_title": "Dry-run paper",
            "doi_or_url": "10.0000/dryrun",
            "year": "2026",
            "journal": "Dry Run Journal",
        },
        cards,
        facts,
        sample_mentions=mentions,
        variable_candidates=variables,
        sample_groups=groups,
    )
    assert all(group["sample_group_id"].startswith("G") for group in groups)
    assert all("temperature-series" not in group["sample_group_id"] for group in groups)
    assert not any(f.get("extraction_method") == "AI_sample_card" and f.get("confidence", 1) > 0.45 for f in facts)
    assert records and records[0]["sample_group_id"] == "G001"
    assert records[0]["performance_metric"] == "tensile_strength"
    assert len(records) == 1
    assert not any("binding" in r["performance_metric"].lower() for r in records)
    assert report["qa_result_fact_count"] >= 3
    return {
        "mode": mode,
        "sample_mentions": len(mentions),
        "variable_candidates": len(variables),
        "sample_groups": len(groups),
        "sample_group_ids": [g["sample_group_id"] for g in groups],
        "facts": len(facts),
        "records": len(records),
        "quality_conclusions": report["quality_conclusions"],
    }


async def assert_vision_skips_when_core_facts_are_sufficient() -> None:
    class ExplodingVisionClient:
        def generate_vision_json_tolerant(self, *args, **kwargs):
            raise AssertionError("vision should not be called")

    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "tensile strength",
            "value": str(20 + idx),
            "unit": "MPa",
            "evidence_text": f"S2 tensile strength {20 + idx} MPa",
            "source_location": "p.5, Fig. 2a",
        }
        for idx in range(8)
    ]
    returned = await V7ExtractorService._stage5_vision_enhancement(
        ExplodingVisionClient(), "missing.pdf", [], facts,
    )
    assert returned is facts


async def main() -> None:
    metadata = V7ExtractorService._fill_paper_metadata_fallback(
        {},
        "\n".join([
            "[page 1]",
            "A real paper title about polymer aerogels",
            "b Beijing Zeqiao Medical Technology Co., Ltd., Beijing, China",
            "Abstract",
        ]),
        "fallback.pdf",
    )
    assert metadata.get("journal", "") == ""
    await assert_vision_skips_when_core_facts_are_sufficient()
    strong = await run_mode("strong")
    weak = await run_mode("weak")
    print("strong dry-run:", strong)
    print("weak dry-run:", weak)


if __name__ == "__main__":
    asyncio.run(main())
