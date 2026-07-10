"""Pydantic schemas for projects."""

from datetime import datetime
from pydantic import BaseModel


# --- Project ---
class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    # Stats (filled by API)
    paper_count: int = 0
    pending_count: int = 0
    approved_count: int = 0

    model_config = {"from_attributes": True}


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


# --- Project LLM Configuration ---
class ProjectLLMConfigUpdate(BaseModel):
    llm_provider: str | None = "openai"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = "gpt-4o"


class ProjectLLMConfigOut(BaseModel):
    llm_provider: str | None
    llm_api_key_masked: str | None
    llm_base_url: str | None
    llm_model: str | None

    model_config = {"from_attributes": True}
