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

    # Controlled NAS ingestion. Configure one or more server-side roots as a
    # JSON array or semicolon-separated string; the API never accepts an
    # arbitrary absolute path from the browser.
    NAS_SOURCE_ROOTS: str = ""
    NAS_SCAN_MAX_FILES: int = 5_000
    NAS_MAX_FILE_BYTES: int = 100 * 1024 * 1024

    # New Materials Big Data Center integration. The bundled template binding
    # is pinned to the private canary dataset created during compatibility
    # verification. Override all binding values together when the target
    # dataset or template changes.
    PLATFORM_BASE_URL: str = "http://192.168.2.101/database-code"
    PLATFORM_BATCH_TEMPLATE_PATH: str = str(
        Path(__file__).resolve().parents[3]
        / "platform_templates"
        / "canary"
        / "platform-batch-canary.json"
    )
    PLATFORM_EXPECTED_DATASET_ID: int = 2_081_660_157_305_163_778
    PLATFORM_EXPECTED_TEMPLATE_ID: int = 2_081_658_374_180_704_257
    PLATFORM_BATCH_TEMPLATE_SHA256: str = (
        "d001d4d70df42a34644cf8704dd77ede99dc6eb7e89626b8778cd353d34465a2"
    )
    # Ordered v0.3.2 material-chain binding.  The legacy MATERIAL_FACT setting
    # names remain for deployment compatibility, but one platform record now
    # represents one actual sample and follows the user's original workbook
    # order: composition → process → structure → performance → evidence.
    PLATFORM_MATERIAL_FACT_TEMPLATE_PATH: str = str(
        Path(__file__).resolve().parents[3]
        / "platform_templates"
        / "ai4s-material-chain-template-v0.3.2.json"
    )
    PLATFORM_MATERIAL_FACT_TEMPLATE_SHA256: str = (
        "5ca1f62e96681fe30e5936738daed802d67c6fa6034e81cbbd90bab4ac4fa279"
    )
    PLATFORM_MATERIAL_FACT_DATASET_ID: int = 2_082_071_264_142_430_210
    PLATFORM_MATERIAL_FACT_TEMPLATE_ID: int = 2_082_071_243_661_643_777
    PLATFORM_MATERIAL_FACT_DATASET_NAME: str = (
        "AI4S材料数据链_成分工艺结构性能_v0.3.2_20260728"
    )
    PLATFORM_SESSION_TTL_SECONDS: int = 4 * 60 * 60
    PLATFORM_PARSE_TIMEOUT_SECONDS: int = 180
    PLATFORM_POLL_INTERVAL_SECONDS: float = 2.0

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
    MINERU_CLOUD_TRUST_ENV: bool = True
    MINERU_CLOUD_MODEL_VERSION: str = "vlm"
    MINERU_CLOUD_PAGE_RANGES: str = ""
    MINERU_CLOUD_ENABLE_FORMULA: bool = True
    MINERU_CLOUD_ENABLE_TABLE: bool = True
    MINERU_CLOUD_IS_OCR: bool = False
    MINERU_CLOUD_BATCH_SIZE: int = 20
    MINERU_CLOUD_UPLOAD_CONCURRENCY: int = 8
    MINERU_CLOUD_MAX_RETRIES: int = 4
    MINERU_CLOUD_RETRY_BASE_SECONDS: float = 2.0
    MINERU_REUSE_PARSE_ARTIFACTS: bool = True
    MINERU_CLOUD_FALLBACK_LOCAL: bool = False
    MINERU_FALLBACK_LEGACY_PARSER: bool = False
    MINERU_FORMULA_ENABLE: bool = True
    MINERU_TABLE_ENABLE: bool = True
    MINERU_IMAGE_ANALYSIS_ENABLE: bool = True
    MINERU_HYBRID_EFFORT: str = "medium"

    # Extraction runtime
    EXTRACTION_MAX_CONCURRENT_JOBS: int = 3
    EXTRACTION_JOB_POLL_INTERVAL_SECONDS: int = 2
    EXTRACTION_MAX_ATTEMPTS: int = 2
    EXTRACTION_RETRY_BASE_SECONDS: float = 5.0
    EXTRACTION_PIPELINE_TIMEOUT_SECONDS: int = 1800
    EXTRACT_REVIEW_ARTICLES: bool = False
    DEFAULT_PARSER_STRATEGY: str = "mineru_cloud"
    DEFAULT_LLM_PROVIDER: str = "openai"
    DEFAULT_LLM_BASE_URL: str = "https://aigw.sotatts.online/v1"
    DEFAULT_LLM_MODEL: str = "gpt-5.5"
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
    STRONG_STAGE2_PARTIAL_FAILURE_MIN_FACTS: int = 3
    STRONG_LLM_TIMEOUT_SECONDS: int = 180
    WEAK_LLM_PARALLEL_CALLS: int = 2
    STRONG_LLM_PARALLEL_CALLS: int = 4
    STRONG_VISION_MAX_PAGES: int = 4
    STRONG_HOLISTIC_ENABLED: bool = True
    STRONG_HOLISTIC_SAMPLE_MAX_CHARS: int = 16000
    STRONG_HOLISTIC_CATALOG_REASONING_EFFORT: str = "low"
    STRONG_HOLISTIC_CATALOG_RETRY_ENABLED: bool = True
    STRONG_HOLISTIC_CATALOG_RETRY_MAX_CHARS: int = 8000
    STRONG_HOLISTIC_CATALOG_RETRY_MAX_TOKENS: int = 1800
    STRONG_HOLISTIC_PERFORMANCE_MAX_TOKENS: int = 6000
    STRONG_HOLISTIC_PERFORMANCE_TIMEOUT_SECONDS: int = 180
    STRONG_HOLISTIC_RESULTS_MAX_CHARS: int = 35000
    STRONG_HOLISTIC_PERFORMANCE_WINDOW_CHARS: int = 6000
    STRONG_HOLISTIC_WINDOW_OVERLAP_BLOCKS: int = 1
    STRONG_HOLISTIC_RESULT_MIN_SCORE: int = 4
    STRONG_HOLISTIC_MAX_RESULT_BLOCKS: int = 80
    STRONG_HOLISTIC_RESULT_NEIGHBOR_BLOCKS: int = 1
    STRONG_HOLISTIC_SKIP_EMPTY_PERFORMANCE: bool = True
    STRONG_HOLISTIC_PARALLEL_CALLS: int = 3
    STRONG_HOLISTIC_BACKGROUND_TIMEOUT_SECONDS: int = 60
    STRONG_HOLISTIC_BACKGROUND_MAX_CHARS: int = 9000
    STRONG_HOLISTIC_BACKGROUND_MAX_TOKENS: int = 1400
    STRONG_TABLE_LLM_TIMEOUT_SECONDS: int = 75
    STRONG_HOLISTIC_SENSING_ENABLED: bool = False
    LLM_DISABLE_THINKING: bool = True
    LLM_DEFAULT_REASONING_EFFORT: str = "low"
    LLM_REQUEST_MAX_RETRIES: int = 3
    LLM_RETRY_BASE_SECONDS: float = 1.0
    LLM_RETRY_MAX_SECONDS: float = 20.0
    LLM_MAX_OUTPUT_TOKENS_PER_CALL: int = 6000
    LLM_GLOBAL_MAX_CONCURRENT_CALLS: int = 16
    LLM_BATCH_MAX_CONCURRENT_CALLS: int = 12
    LLM_INTERACTIVE_RESERVED_CALLS: int = 4
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
