"""Stateful section classification tests for MinerU blocks."""

from app.services.chunking import (
    classify_section_in_sequence,
    classify_section_transition,
    detect_numbered_major_heading,
    detect_section_heading,
)


def test_detect_section_heading_requires_heading_like_text():
    assert detect_section_heading("3. Results and discussion") == "results"
    assert detect_section_heading("4. Conclusion") == "conclusion"
    assert detect_section_heading("References") == "references"
    assert detect_section_heading("The results show a tensile strength of 20 MPa.") is None


def test_section_sequence_inherits_numbered_subsections_and_stops_at_references():
    section = classify_section_in_sequence("Abstract", 1, None)
    assert section == "title_abstract"
    section = classify_section_in_sequence("1. Introduction", 2, section)
    assert section == "introduction"
    section = classify_section_in_sequence("2. Materials and methods", 2, section)
    assert section == "experimental"
    assert classify_section_in_sequence("2.1 Monotonic tensile tests", 2, section) == "experimental"
    section = classify_section_in_sequence("3. Results and discussion", 3, section)
    assert section == "results"
    assert classify_section_in_sequence("3.2 Cyclic response", 4, section) == "results"
    section = classify_section_in_sequence("References", 8, section)
    assert section == "references"
    assert classify_section_in_sequence("Smith et al. 2020", 8, section, block_type="ref_text") == "references"


def test_back_matter_heading_is_not_results():
    assert detect_section_heading("CRediT authorship contribution statement") == "back_matter"
    assert detect_section_heading("Declaration of competing interest") == "back_matter"


def test_numbered_domain_headings_advance_major_section_state():
    state: tuple[str | None, int | None] = (None, None)
    headings = (
        ("1. Introduction", "introduction", 1),
        (
            "2. Geometrical Structure and Equivalent Mechanical Parameters",
            "experimental",
            2,
        ),
        (
            "3. Mechanical Compressive Properties under Quasi-Static Conditions",
            "results",
            3,
        ),
        ("4. Band Structure and Vibration Isolation Characteristics", "results", 4),
        ("5. Energy Absorption Property under Impact Condition", "results", 5),
        ("6. Conclusion", "conclusion", 6),
    )

    for text, expected_section, expected_major in headings:
        state = classify_section_transition(
            text,
            2,
            state[0],
            state[1],
            block_type="paragraph",
            heading_level=2,
        )
        assert state == (expected_section, expected_major)


def test_numbered_subsections_and_prose_do_not_advance_major_state():
    assert detect_numbered_major_heading("2.1 Monotonic tensile tests", heading_level=3) is None
    assert detect_numbered_major_heading("3. This is a numbered result sentence.") is None

    state = classify_section_transition(
        "2. Materials and methods",
        2,
        "introduction",
        1,
        heading_level=2,
    )
    assert state == ("experimental", 2)
    assert classify_section_transition(
        "2.1 Monotonic tensile tests",
        3,
        state[0],
        state[1],
        heading_level=3,
    ) == ("experimental", 2)
    assert classify_section_transition(
        "3. This is a numbered result sentence.",
        3,
        state[0],
        state[1],
    ) == ("experimental", 2)


def test_terminal_sections_cannot_be_reopened_by_numbered_text():
    assert classify_section_transition(
        "1. Introduction",
        8,
        "references",
        6,
        heading_level=2,
    ) == ("references", 6)
