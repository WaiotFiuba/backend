"""create users table

Revision ID: c4a8d2f1b9e6
Revises: b7e3c1a9d2f4
Create Date: 2026-10-09 00:00:00.000000

La tabla users no estaba en ninguna migración: la creaba la app al arrancar
(init_db en app/main.py), solo con AUTO_CREATE_DB=true y APP_ENV distinto de
production. En producción nunca se creaba sola.

Si la tabla ya existe (bases donde se creó a mano o con init_db), la migración
no hace nada: los índices y restricciones son los mismos que crea init_db.

Corre contra MAP_DATABASE_URL, como todas las migraciones. Sirve cuando
DATABASE_URL apunta a la misma base, que es el caso de producción.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8d2f1b9e6"
down_revision: Union[str, Sequence[str], None] = "b7e3c1a9d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("users"):
        return

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("remember_token", sa.String(length=255), nullable=True),
        sa.Column("photo", sa.String(length=255), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("qr_token", sa.String(length=80), nullable=True),
        sa.Column("email_notifications", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qr_token"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role_id", "users", ["role_id"])
    op.create_index("ix_users_created_by", "users", ["created_by"])
    op.create_index("ix_users_provider_id", "users", ["provider_id"])


def downgrade() -> None:
    # Borra la tabla aunque existiera antes de esta migración: se pierden los
    # usuarios.
    op.drop_table("users")
