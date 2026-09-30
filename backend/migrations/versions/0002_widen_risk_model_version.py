"""Widen risk_scores.model_version.

The registry reports one version per target, comma-joined ("continuation:…,distraction:…,doomscroll:…"), which is
101 characters with three models and overflowed the original VARCHAR(64) on PostgreSQL. SQLite does not enforce
varchar length, so this only appeared when the trained models were served against PostgreSQL.

Revision ID: 0002_widen_model_version
Revises: 0001_initial
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_widen_model_version"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("risk_scores", "model_version", existing_type=sa.String(64), type_=sa.String(255),
                    existing_nullable=True)


def downgrade() -> None:
    op.execute("UPDATE risk_scores SET model_version = left(model_version, 64) WHERE length(model_version) > 64")
    op.alter_column("risk_scores", "model_version", existing_type=sa.String(255), type_=sa.String(64),
                    existing_nullable=True)
