"""Schemas for NAS ingestion and platform delivery."""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, Field, SecretStr, field_validator


class NasScanRequest(BaseModel):
    source_id: str = Field(min_length=8, max_length=64)
    relative_directory: str = Field(default="", max_length=1_000)
    filename_query: str | None = Field(default=None, max_length=200)
    recursive: bool = True

    @field_validator("filename_query")
    @classmethod
    def normalize_filename_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip()
        if len(normalized) > 200:
            raise ValueError("filename_query 不能超过 200 个字符")
        return normalized or None


class NasSelectedFile(BaseModel):
    id: str = Field(min_length=8, max_length=64)
    relative_path: str = Field(min_length=1, max_length=2_000)
    size: int = Field(gt=0)
    modified_ns: str = Field(min_length=1, max_length=32, pattern=r"^[1-9]\d*$")


class NasImportRequest(BaseModel):
    source_id: str = Field(min_length=8, max_length=64)
    files: list[NasSelectedFile] = Field(min_length=1, max_length=500)
    start_extraction: bool = True
    model_mode: str = "strong"
    parser_strategy: str = "mineru_cloud"

    @field_validator("model_mode")
    @classmethod
    def validate_model_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"auto", "weak", "strong"}:
            raise ValueError("model_mode 必须是 auto、weak 或 strong")
        return normalized

    @field_validator("parser_strategy")
    @classmethod
    def validate_parser_strategy(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {
            "mineru_cloud",
            "mineru_local",
            "mineru_local_sync",
            "legacy",
        }
        if normalized not in allowed:
            raise ValueError(
                "parser_strategy 必须是 mineru_cloud、mineru_local、"
                "mineru_local_sync 或 legacy"
            )
        return normalized


class PlatformConnectRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr
    captcha_code: str = Field(default="", max_length=50)
    captcha_uuid: str = Field(default="", max_length=200)


class PlatformBatchRequest(BaseModel):
    paper_ids: list[int] | None = Field(default=None, max_length=500)
    include_unmapped: bool = True

    @field_validator("paper_ids")
    @classmethod
    def unique_paper_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(item <= 0 for item in value):
            raise ValueError("paper_ids 必须是正整数")
        return list(dict.fromkeys(value))


class PlatformImportRequest(PlatformBatchRequest):
    force: bool = False
