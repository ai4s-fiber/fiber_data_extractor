from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from app.services.bulk_preflight import (
    classify_document_relevance,
    inspect_pdf,
    select_stratified_documents,
    stable_config_fingerprint,
    validate_storage_capacity,
)


def _write_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as target:
        writer.write(target)


def test_pdf_inspection_accepts_real_pdf_and_rejects_fake_extension(tmp_path):
    valid = tmp_path / "valid.pdf"
    invalid = tmp_path / "invalid.pdf"
    _write_pdf(valid, pages=2)
    invalid.write_bytes(b"not a pdf")

    valid_result = inspect_pdf(valid)
    invalid_result = inspect_pdf(invalid)

    assert valid_result.page_count == 2
    assert valid_result.rejection_reason == ""
    assert invalid_result.rejection_reason == "invalid_pdf_header"


def test_relevance_prefilter_keeps_fiber_papers_and_rejects_clear_noise():
    fiber = classify_document_relevance(
        "Electrospun PAN nanofibers for structural composites",
        "The tensile behavior of the fibers was measured.",
    )
    book_review = classify_document_relevance(
        "Book Review: Advances in Analytical Chemistry",
        "This volume surveys recent analytical methods. " * 40,
    )
    clinical = classify_document_relevance(
        "Randomized placebo-controlled clinical trial",
        "Patients received therapy in the hospital. " * 50,
    )
    ligature = classify_document_relevance(
        "Aramid materials",
        "The aramid nanoﬁber aerogel reached 682 kPa. " * 20,
    )
    review_article = classify_document_relevance(
        "Aramid triboelectric materials",
        "REVIEW www.example.test This review discusses aramid ﬁbers. " * 20,
    )

    assert fiber == ("eligible", "fiber_signal")
    assert book_review == ("irrelevant", "excluded_document_type")
    assert clinical == (
        "irrelevant",
        "clinical_document_without_material_signal",
    )
    assert ligature == ("eligible", "fiber_signal")
    assert review_article == ("irrelevant", "review_article")


def test_relevance_prefilter_keeps_ambiguous_or_unreadable_documents_for_review():
    ambiguous = classify_document_relevance(
        "A new processing method",
        "Short preview with no reliable abstract.",
    )
    material_without_early_fiber_term = classify_document_relevance(
        "Mechanical behavior of reinforced polymer composites",
        (
            "The material system and tensile response were investigated. "
            "Manufacturing parameters and thermal properties are reported. "
        ) * 30,
    )
    assert ambiguous == ("review", "insufficient_local_text")
    assert material_without_early_fiber_term == (
        "review",
        "material_without_fiber_signal",
    )


def test_pdf_inspection_accepts_header_within_first_kib(tmp_path):
    path = tmp_path / "prefixed.pdf"
    path.write_bytes(b"\x00" * 32 + b"%PDF-1.4\n")

    result = inspect_pdf(path, inspect_relevance=False)

    assert result.rejection_reason == ""


def test_relevance_prefilter_does_not_treat_process_withdrawal_as_retraction():
    decision = classify_document_relevance(
        (
            "3D printing of continuous fiber-reinforced smart molds with "
            "a filament-withdrawal demolding strategy"
        ),
        (
            "Continuous fiber-reinforced specimens were printed and tested "
            "for extreme forming performance. "
        ) * 20,
    )

    assert decision == ("eligible", "fiber_signal")


def test_storage_preflight_checks_writes_and_hardlink_capacity(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output"
    uploads = tmp_path / "uploads"
    _write_pdf(source)

    result = validate_storage_capacity(
        source_paths=[source],
        output_directories=[output, uploads],
        upload_directory=uploads,
        copy_mode="hardlink",
        artifact_factor=1.0,
        minimum_free_bytes=0,
    )

    assert result.source_bytes == source.stat().st_size
    assert result.available_bytes > result.estimated_output_bytes
    assert output.is_dir()
    assert not list(output.glob(".bulk-*-probe-*"))


def test_config_fingerprint_is_order_independent_and_secret_free():
    first = stable_config_fingerprint({"model": "gpt-5.5", "jobs": 3})
    second = stable_config_fingerprint({"jobs": 3, "model": "gpt-5.5"})

    assert first == second
    assert len(first) == 64
    assert "gpt" not in first


def test_stratified_pilot_is_deterministic_and_covers_source_folders(tmp_path):
    documents = []
    for folder_index, folder_name in enumerate(("paper1", "paper2", "paper3")):
        folder = tmp_path / folder_name
        folder.mkdir()
        for index in range(6):
            path = folder / f"{index}.pdf"
            path.write_bytes(b"%PDF-1.4")
            documents.append(SimpleNamespace(
                path=path,
                sha256=f"{folder_index:02d}{index:02d}".ljust(64, "0"),
                size_bytes=(index + 1) * 1000,
                page_count=(folder_index + 1) * (index + 2),
            ))

    first, first_manifest = select_stratified_documents(
        documents,
        source_root=tmp_path,
        sample_size=9,
        seed="stable-seed",
    )
    second, second_manifest = select_stratified_documents(
        documents,
        source_root=tmp_path,
        sample_size=9,
        seed="stable-seed",
    )

    assert [item.sha256 for item in first] == [item.sha256 for item in second]
    assert first_manifest == second_manifest
    assert len(first) == 9
    assert {
        Path(item["relative_path"]).parts[0] for item in first_manifest
    } == {"paper1", "paper2", "paper3"}
