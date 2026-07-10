"""Config validation tests."""

import pytest

from app.core.config import Settings
from app.models.extraction_job import ExtractionJob
from app.schemas.project import ProjectLLMConfigUpdate
from app.services.mineru_client import MinerUClient, MinerUUnavailable


def test_config_no_longer_requires_auth_secret():
    settings = Settings(DEBUG=False, _env_file=None)
    assert settings.APP_NAME


def test_default_parser_uses_mineru_cloud_without_legacy_fallback():
    settings = Settings(DEBUG=False, _env_file=None)
    assert settings.MINERU_ENABLED is True
    assert settings.DEFAULT_PARSER_STRATEGY == "mineru_cloud"
    assert settings.MINERU_CLOUD_FALLBACK_LOCAL is False
    assert settings.MINERU_FALLBACK_LEGACY_PARSER is False
    assert ExtractionJob.__table__.columns["parser_strategy"].default.arg == "mineru_cloud"


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
