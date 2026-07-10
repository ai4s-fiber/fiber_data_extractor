"""Config validation tests."""

from app.core.config import Settings


def test_config_no_longer_requires_auth_secret():
    settings = Settings(DEBUG=False)
    assert settings.APP_NAME


def test_strong_chunk_limits_sane():
    s = Settings(DEBUG=True)
    assert s.STRONG_MAX_PRIORITY_CHUNKS >= s.WEAK_MAX_PRIORITY_CHUNKS
    assert s.STRONG_MAX_FACT_CHUNKS >= s.WEAK_MAX_FACT_CHUNKS
