"""Application configuration using pydantic-settings."""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # App
    APP_NAME: str = "Fiber Data Extractor V6"
    APP_VERSION: str = "6.0.0"
    DEBUG: bool = False
    ALLOW_SQLITE_FALLBACK: bool = False

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./fiber_data.db"

    # File storage
    UPLOAD_DIR: str = "./uploads"

    # Export
    EXPORT_DIR: str = "./exports"

    # Document parsing (MinerU)
    PARSE_ARTIFACT_DIR: str = "./parse_artifacts"
    MINERU_ENABLED: bool = True
    MINERU_API_URL: str = "http://127.0.0.1:8001"
    MINERU_CLOUD_TOKEN: str = ""
    MINERU_BACKEND: str = "pipeline"
    MINERU_PARSE_METHOD: str = "auto"
    MINERU_LANG: str = "ch"
    MINERU_TASK_TIMEOUT_SECONDS: int = 1800
    MINERU_POLL_INTERVAL_SECONDS: float = 2.0
    MINERU_CLOUD_FALLBACK_LOCAL: bool = False
    MINERU_FALLBACK_LEGACY_PARSER: bool = False

    # Extraction runtime
    EXTRACTION_MAX_CONCURRENT_JOBS: int = 2
    EXTRACTION_JOB_POLL_INTERVAL_SECONDS: int = 2
    DEFAULT_PARSER_STRATEGY: str = "mineru_cloud"
    # Weak-mode throughput limits (batch literature extraction)
    WEAK_MAX_PRIORITY_CHUNKS: int = 8
    WEAK_MAX_FACT_CHUNKS: int = 35
    WEAK_STAGE2_BATCH_SIZE: int = 3
    WEAK_STAGE2_BATCH_MAX_CHARS: int = 9000
    WEAK_STAGE2_BATCH_MAX_TOKENS: int = 1800
    WEAK_LLM_TIMEOUT_SECONDS: int = 90
    STRONG_MAX_PRIORITY_CHUNKS: int = 40
    STRONG_STAGE1_BATCH_SIZE: int = 5
    STRONG_STAGE2_BATCH_SIZE: int = 4
    STRONG_MAX_TABLE_CHUNKS: int = 60
    STRONG_MAX_FACT_CHUNKS: int = 100
    STRONG_STAGE2_HOLISTIC_SLIM_THRESHOLD: int = 12
    STRONG_STAGE2_HOLISTIC_SLIM_MAX_CHUNKS: int = 30
    STRONG_LLM_TIMEOUT_SECONDS: int = 180
    WEAK_LLM_PARALLEL_CALLS: int = 2
    STRONG_LLM_PARALLEL_CALLS: int = 4
    STRONG_VISION_MAX_PAGES: int = 4
    STRONG_HOLISTIC_ENABLED: bool = True
    STRONG_HOLISTIC_PERFORMANCE_MAX_TOKENS: int = 6000
    STRONG_HOLISTIC_RESULTS_MAX_CHARS: int = 35000
    STRONG_HOLISTIC_SENSING_ENABLED: bool = False
    LLM_DISABLE_THINKING: bool = True
    LLM_MAX_OUTPUT_TOKENS_PER_CALL: int = 6000
    LLM_METRICS_LOCAL_ENABLED: bool = True
    LLM_METRICS_DIR: str = "./reports/llm_metrics"
    BENCHMARK_REPORT_DIR: str = "./reports/benchmarks"

    # Redis (optional progress pub/sub, cache, job queue)
    REDIS_ENABLED: bool = True
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL_SECONDS: int = 30

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @model_validator(mode="after")
    def _apply_dev_defaults(self) -> "Settings":
        if self.DEBUG:
            self.ALLOW_SQLITE_FALLBACK = True
        return self


settings = Settings()

# Ensure directories exist
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.EXPORT_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.PARSE_ARTIFACT_DIR).mkdir(parents=True, exist_ok=True)
