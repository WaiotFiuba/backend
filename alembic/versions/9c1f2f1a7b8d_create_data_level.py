"""create_data_level

Revision ID: 9c1f2f1a7b8d
Revises: bc51307a5d47
Create Date: 2026-06-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9c1f2f1a7b8d"
down_revision: Union[str, Sequence[str], None] = "bc51307a5d47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_level",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("imei", sa.String(), nullable=True),
        sa.Column("m_id", sa.String(), nullable=True),
        sa.Column("reading_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reported_height", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("echo", sa.Text(), nullable=True),
        sa.Column("fire_alarm", sa.Boolean(), nullable=False),
        sa.Column("freeze_alarm", sa.Boolean(), nullable=False),
        sa.Column("reported_temperature", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("crash_alarm", sa.Boolean(), nullable=False),
        sa.Column("garbage_collection_alarm", sa.Boolean(), nullable=False),
        sa.Column(
            "reported_collection_date", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("reported_low_consumption_voltage", sa.Boolean(), nullable=False),
        sa.Column("reported_high_consumption_voltage", sa.Boolean(), nullable=False),
        sa.Column("container_current_level_old", sa.Integer(), nullable=True),
        sa.Column("site_pickup_status", sa.String(), nullable=True),
        sa.Column("client_id", sa.BigInteger(), nullable=True),
        sa.Column("device_id", sa.BigInteger(), nullable=True),
        sa.Column("device_name", sa.String(), nullable=True),
        sa.Column("container_id", sa.BigInteger(), nullable=True),
        sa.Column("container_name", sa.String(), nullable=True),
        sa.Column("container_current_level", sa.Integer(), nullable=True),
        sa.Column("container_type_id", sa.BigInteger(), nullable=True),
        sa.Column("container_type_name", sa.String(), nullable=True),
        sa.Column("container_type_height", sa.Integer(), nullable=True),
        sa.Column("site_id", sa.String(), nullable=True),
        sa.Column("site_name", sa.String(), nullable=True),
        sa.Column("zone_id", sa.BigInteger(), nullable=True),
        sa.Column("zone_name", sa.String(), nullable=True),
        sa.Column("waste_type_id", sa.BigInteger(), nullable=True),
        sa.Column("waste_type_name", sa.String(), nullable=True),
        sa.Column(
            "waste_type_pickup_alert_threshold", sa.DOUBLE_PRECISION(), nullable=True
        ),
        sa.Column(
            "waste_type_pickup_warning_threshold", sa.DOUBLE_PRECISION(), nullable=True
        ),
        sa.Column("waste_type_max_days_between_pickup", sa.Integer(), nullable=True),
        sa.Column("waste_type_alert_rule_category_id", sa.BigInteger(), nullable=True),
        sa.Column("waste_type_alert_rule_category_name", sa.String(), nullable=True),
        sa.Column("rssi", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("container_current_m3", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("container_current_t", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("provider_id", sa.BigInteger(), nullable=True),
        sa.Column("message_version", sa.String(), nullable=True),
        sa.Column("device_serial_id", sa.String(), nullable=True),
        sa.Column("device_model_id", sa.BigInteger(), nullable=True),
        sa.Column("device_model_name", sa.String(), nullable=True),
        sa.Column("device_category", sa.String(), nullable=True),
        sa.Column("container_serie_id", sa.String(), nullable=True),
        sa.Column("load_side_category", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["container_id"], ["containers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_data_level_reading_date", "data_level", ["reading_date"])
    op.create_index(
        "idx_data_level_container_reading_date",
        "data_level",
        ["container_id", "reading_date"],
    )
    op.create_index(
        "idx_data_level_imei_reading_date", "data_level", ["imei", "reading_date"]
    )


def downgrade() -> None:
    op.drop_index("idx_data_level_imei_reading_date", table_name="data_level")
    op.drop_index("idx_data_level_container_reading_date", table_name="data_level")
    op.drop_index("idx_data_level_reading_date", table_name="data_level")
    op.drop_table("data_level")
