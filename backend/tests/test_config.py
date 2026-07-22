"""Config validation tests."""

import asyncio
import json

import pytest

from app.core.config import Settings
from app.models.extraction_job import ExtractionJob
from app.schemas.project import ProjectLLMConfigUpdate
from app.services.mineru_client import (
    MinerUClient,
    MinerUParseResult,
    MinerUUnavailable,
    build_cloud_upload_payload,
    build_local_parse_form_data,
)


def test_config_no_longer_requires_auth_secret():
    settings = Settings(DEBUG=False, _env_file=None)
    assert settings.APP_NAME


def test_default_parser_uses_mineru_cloud_without_legacy_fallback():
    settings = Settings(DEBUG=False, _env_file=None)
    assert settings.MINERU_ENABLED is True
    assert settings.DEFAULT_PARSER_STRATEGY == "mineru_cloud"
    assert settings.MINERU_CLOUD_TRUST_ENV is True
    assert settings.MINERU_CLOUD_MODEL_VERSION == "vlm"
    assert settings.MINERU_REUSE_PARSE_ARTIFACTS is True
    assert settings.MINERU_CLOUD_FALLBACK_LOCAL is False
    assert settings.MINERU_FALLBACK_LEGACY_PARSER is False
    assert ExtractionJob.__table__.columns["parser_strategy"].default.arg == "mineru_cloud"


def test_default_llm_uses_gpt55_gateway_with_batch_budget():
    settings = Settings(DEBUG=False, _env_file=None)
    assert settings.DEFAULT_LLM_PROVIDER == "openai"
    assert settings.DEFAULT_LLM_BASE_URL == "https://aigw.sotatts.online/v1"
    assert settings.DEFAULT_LLM_MODEL == "gpt-5.5"
    assert settings.EXTRACTION_MAX_CONCURRENT_JOBS == 2
    assert settings.STRONG_LLM_PARALLEL_CALLS == 4
    assert settings.LLM_GLOBAL_MAX_CONCURRENT_CALLS == 12
    assert settings.LLM_BATCH_MAX_CONCURRENT_CALLS == 8
    assert settings.LLM_INTERACTIVE_RESERVED_CALLS == 4


def test_strong_chunk_limits_sane():
    s = Settings(DEBUG=True, _env_file=None)
    assert s.STRONG_MAX_PRIORITY_CHUNKS >= s.WEAK_MAX_PRIORITY_CHUNKS
    assert s.STRONG_MAX_FACT_CHUNKS >= s.WEAK_MAX_FACT_CHUNKS
    assert s.WEAK_STAGE2_BATCH_SIZE >= 1
    assert s.WEAK_STAGE2_BATCH_MAX_CHARS >= 2000
    assert s.WEAK_STAGE2_BATCH_MAX_TOKENS >= 1400


def test_project_llm_config_update_is_partial_by_default():
    body = ProjectLLMConfigUpdate()
    assert body.llm_provider is None
    assert body.llm_model is None


def test_mineru_health_requires_token_only_for_cloud_default(monkeypatch):
    import app.core.health_checks as health_checks

    monkeypatch.setattr(health_checks.settings, "MINERU_ENABLED", True)
    monkeypatch.setattr(health_checks.settings, "DEFAULT_PARSER_STRATEGY", "mineru_cloud")
    monkeypatch.setattr(health_checks.settings, "MINERU_CLOUD_TOKEN", "")
    assert health_checks.check_mineru_cloud_configured() is False

    monkeypatch.setattr(health_checks.settings, "DEFAULT_PARSER_STRATEGY", "mineru_local")
    assert health_checks.check_mineru_cloud_configured() is True


def test_mineru_payload_builders_follow_runtime_settings(monkeypatch):
    import app.services.mineru_client as mineru_client

    monkeypatch.setattr(mineru_client.settings, "MINERU_CLOUD_MODEL_VERSION", "pipeline")
    monkeypatch.setattr(mineru_client.settings, "MINERU_CLOUD_PAGE_RANGES", "1-5")
    monkeypatch.setattr(mineru_client.settings, "MINERU_CLOUD_ENABLE_FORMULA", False)
    monkeypatch.setattr(mineru_client.settings, "MINERU_CLOUD_ENABLE_TABLE", True)
    monkeypatch.setattr(mineru_client.settings, "MINERU_CLOUD_IS_OCR", True)
    payload = build_cloud_upload_payload("paper.pdf", "data-1")
    assert payload["model_version"] == "pipeline"
    assert payload["enable_formula"] is False
    assert payload["files"][0]["page_ranges"] == "1-5"
    assert payload["files"][0]["is_ocr"] is True

    monkeypatch.setattr(mineru_client.settings, "MINERU_BACKEND", "hybrid-engine")
    monkeypatch.setattr(mineru_client.settings, "MINERU_HYBRID_EFFORT", "medium")
    monkeypatch.setattr(mineru_client.settings, "MINERU_FORMULA_ENABLE", False)
    form = build_local_parse_form_data()
    assert form["formula_enable"] == "false"
    assert form["table_enable"] == "true"
    assert form["response_format_zip"] == "false"
    assert form["effort"] == "medium"


def test_mineru_artifact_cache_round_trip(tmp_path, monkeypatch):
    import app.services.document_context as document_context

    monkeypatch.setattr(document_context.settings, "PARSE_ARTIFACT_DIR", str(tmp_path))
    result = MinerUParseResult(
        task_id="task-1",
        backend="vlm",
        version="cloud_v4",
        document_name="paper.pdf",
        md_content="# Title\n\nbody",
        content_list=[{"type": "text", "text": "body"}],
        content_list_v2=[],
        middle_json={"pages": 1},
        raw_result={"extract_result": []},
        elapsed_seconds=1.0,
    )
    raw_path, markdown_path = document_context._write_parse_artifacts(
        1,
        2,
        result,
        "mineru_cloud",
    )
    raw = json.loads((tmp_path / "1" / "2" / "mineru_result.json").read_text(encoding="utf-8"))
    assert raw["_fiber_extractor_mineru_artifact"]["task_id"] == "task-1"

    loaded = document_context._load_mineru_parse_result_from_artifacts(
        raw_result_path=raw_path,
        markdown_path=markdown_path,
        expected_cache_key=document_context._mineru_parse_cache_key("mineru_cloud"),
    )
    assert loaded is not None
    assert loaded.task_id == "task-1"
    assert loaded.md_content.startswith("# Title")
    assert loaded.content_list[0]["text"] == "body"


@pytest.mark.asyncio
async def test_llm_concurrency_guard_and_per_job_budget(monkeypatch):
    import app.services.llm_concurrency as llm_concurrency

    monkeypatch.setattr(llm_concurrency.settings, "LLM_GLOBAL_MAX_CONCURRENT_CALLS", 2)
    monkeypatch.setattr(llm_concurrency.settings, "LLM_BATCH_MAX_CONCURRENT_CALLS", 8)
    monkeypatch.setattr(llm_concurrency.settings, "EXTRACTION_MAX_CONCURRENT_JOBS", 2)
    assert llm_concurrency.per_job_llm_parallel_limit(10) == 4
    assert llm_concurrency.per_job_llm_parallel_limit(3) == 3

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def worker():
        nonlocal active, max_active
        async with llm_concurrency.llm_call_slot():
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert max_active == 2


@pytest.mark.asyncio
async def test_mineru_cloud_requires_token_before_network(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(MinerUUnavailable, match="MINERU_CLOUD_TOKEN"):
        await MinerUClient(token="").parse_pdf_cloud(pdf_path)


@pytest.mark.asyncio
async def test_mineru_client_rejects_unknown_strategy_before_network(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(ValueError, match="Unsupported MinerU parser strategy"):
        await MinerUClient().parse_pdf(pdf_path, strategy="unknown")


@pytest.mark.asyncio
async def test_mineru_local_sync_strategy_is_routed(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    async def fake_sync(self, path):
        return "sync-result"

    monkeypatch.setattr(MinerUClient, "parse_pdf_local_sync", fake_sync)
    assert await MinerUClient().parse_pdf(pdf_path, strategy="mineru_local_sync") == "sync-result"
