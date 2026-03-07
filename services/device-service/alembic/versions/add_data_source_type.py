"""Add data_source_type to devices table

Revision ID: add_data_source_type
Revises: add_device_performance_trends
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa


revision = "add_data_source_type"
down_revision = "add_device_performance_trends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("data_source_type", sa.String(length=20), nullable=False, server_default="metered"),
    )
    op.create_index("ix_devices_data_source_type", "devices", ["data_source_type"])


def downgrade() -> None:
    op.drop_index("ix_devices_data_source_type", table_name="devices")
    op.drop_column("devices", "data_source_type")
