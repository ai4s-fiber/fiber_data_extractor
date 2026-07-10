"""Minimal tests for extraction runtime helper functions."""

from types import SimpleNamespace

from app.services.extraction_jobs import (
    classify_extraction_error,
    normalize_model_mode,
    resolve_model_mode,
)
from app.services.llm_diagnostics import normalize_openai_base_url_candidates


def main() -> None:
    assert normalize_model_mode(None) == "auto"
    assert normalize_model_mode(" STRONG ") == "strong"

    assert resolve_model_mode(SimpleNamespace(llm_model="gpt-4o"), "auto") == "strong"
    assert resolve_model_mode(SimpleNamespace(llm_model="deepseek-chat"), "auto") == "weak"
    assert resolve_model_mode(SimpleNamespace(llm_model="anything"), "weak") == "weak"

    assert normalize_openai_base_url_candidates("https://how88.top") == [
        "https://how88.top",
        "https://how88.top/v1",
    ]
    assert normalize_openai_base_url_candidates("https://api.example.com/v1") == [
        "https://api.example.com/v1",
    ]
    assert normalize_openai_base_url_candidates(
        "https://api.example.com/v1/chat/completions"
    ) == ["https://api.example.com/v1"]

    assert classify_extraction_error("401 unauthorized") == "llm_auth_failed"
    assert classify_extraction_error("Expecting value") == "llm_non_json_response"
    assert classify_extraction_error("PDF file not found") == "pdf_parse_failed"
    print("runtime helper tests passed")


if __name__ == "__main__":
    main()
