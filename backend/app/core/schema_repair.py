"""Small startup schema repair for extraction runtime compatibility."""

from __future__ import annotations

from sqlalchemy import text

from app.core.database import engine
from app.models.base import Base


EXTRACTION_JOB_COLUMNS_SQLITE = {
    "requested_mode": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
    "resolved_mode": "VARCHAR(20)",
    "parser_strategy": "VARCHAR(30) NOT NULL DEFAULT 'mineru_local'",
    "step": "VARCHAR(50) NOT NULL DEFAULT 'starting'",
    "percent": "INTEGER NOT NULL DEFAULT 0",
    "error_code": "VARCHAR(50)",
    "error_detail": "TEXT",
    "updated_at": "DATETIME",
    "cancel_requested_at": "DATETIME",
}

EXTRACTION_JOB_COLUMNS_POSTGRES = {
    "requested_mode": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
    "resolved_mode": "VARCHAR(20)",
    "parser_strategy": "VARCHAR(30) NOT NULL DEFAULT 'mineru_local'",
    "step": "VARCHAR(50) NOT NULL DEFAULT 'starting'",
    "percent": "INTEGER NOT NULL DEFAULT 0",
    "error_code": "VARCHAR(50)",
    "error_detail": "TEXT",
    "updated_at": "TIMESTAMP WITH TIME ZONE",
    "cancel_requested_at": "TIMESTAMP WITH TIME ZONE",
}

FACT_CANDIDATE_COLUMNS_SQLITE = {
    "source_block_id": "VARCHAR(120)",
    "source_page": "INTEGER",
    "source_bbox_json": "TEXT",
    "evidence_item_id": "INTEGER",
}

FACT_CANDIDATE_COLUMNS_POSTGRES = {
    "source_block_id": "VARCHAR(120)",
    "source_page": "INTEGER",
    "source_bbox_json": "TEXT",
    "evidence_item_id": "INTEGER",
}

EVIDENCE_ITEM_COLUMNS_SQLITE = {
    "parse_run_id": "INTEGER",
    "block_id": "VARCHAR(120)",
    "bbox_json": "TEXT",
    "mineru_block_type": "VARCHAR(50)",
}

EVIDENCE_ITEM_COLUMNS_POSTGRES = {
    "parse_run_id": "INTEGER",
    "block_id": "VARCHAR(120)",
    "bbox_json": "TEXT",
    "mineru_block_type": "VARCHAR(50)",
}


async def _add_missing_sqlite_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    rows = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    existing = {row[1] for row in rows.fetchall()}
    for column_name, column_sql in columns.items():
        if column_name not in existing:
            await conn.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
            )


async def _add_missing_postgres_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    for column_name, column_sql in columns.items():
        await conn.execute(
            text(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_sql}"
            )
        )


async def ensure_runtime_schema() -> None:
    """Create missing tables and add runtime columns needed by the job runner."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        dialect = conn.dialect.name
        if dialect == "sqlite":
            await _add_missing_sqlite_columns(
                conn, "extraction_jobs", EXTRACTION_JOB_COLUMNS_SQLITE
            )
            await _add_missing_sqlite_columns(
                conn, "fact_candidates", FACT_CANDIDATE_COLUMNS_SQLITE
            )
            await _add_missing_sqlite_columns(
                conn, "evidence_items", EVIDENCE_ITEM_COLUMNS_SQLITE
            )
        elif dialect == "postgresql":
            await _add_missing_postgres_columns(
                conn, "extraction_jobs", EXTRACTION_JOB_COLUMNS_POSTGRES
            )
            await _add_missing_postgres_columns(
                conn, "fact_candidates", FACT_CANDIDATE_COLUMNS_POSTGRES
            )
            await _add_missing_postgres_columns(
                conn, "evidence_items", EVIDENCE_ITEM_COLUMNS_POSTGRES
            )

        await conn.execute(
            text("UPDATE extraction_jobs SET status='queued' WHERE status='pending'")
        )
        await conn.execute(
            text(
                "UPDATE extraction_jobs SET step='starting' "
                "WHERE step IS NULL OR step=''"
            )
        )
        await conn.execute(
            text("UPDATE extraction_jobs SET percent=0 WHERE percent IS NULL")
        )
        await conn.execute(
            text(
                "UPDATE extraction_jobs SET updated_at=created_at "
                "WHERE updated_at IS NULL"
            )
        )

        if dialect == "postgresql":
            await _ensure_postgres_indexes(conn)


POSTGRES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_papers_project_id ON papers (project_id)",
    "CREATE INDEX IF NOT EXISTS ix_candidate_records_project_paper ON candidate_records (project_id, source_paper_id)",
    "CREATE INDEX IF NOT EXISTS ix_candidate_records_review ON candidate_records (project_id, review_status)",
    "CREATE INDEX IF NOT EXISTS ix_extraction_jobs_status ON extraction_jobs (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_extraction_jobs_paper ON extraction_jobs (paper_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_extraction_jobs_project ON extraction_jobs (project_id, status)",
]


async def _ensure_postgres_indexes(conn) -> None:
    for stmt in POSTGRES_INDEXES:
        await conn.execute(text(stmt))
