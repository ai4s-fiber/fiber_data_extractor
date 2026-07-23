"""Baseline schema marker for existing deployments.

Production databases created via ensure_runtime_schema() should stamp this revision:
  alembic stamp head
"""

from typing import Sequence, Union

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema is managed by SQLAlchemy models + ensure_runtime_schema on startup.
    # Use autogenerate for future incremental migrations.
    pass


def downgrade() -> None:
    pass
