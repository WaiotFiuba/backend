"""partition data_level by day

Recrea data_level particionada por día de reading_date. Las lecturas se
descartan (igual que al iniciar una simulación): la tabla nueva arranca vacía y
las particiones de cada día las crea la ingesta a demanda.

Sin particiones, los índices de data_level crecen con toda la historia y cada
lote de la ingesta toca miles de páginas dispersas: cuando superan la memoria
de Postgres, cada lote tarda más que el anterior. Con particiones diarias, cada
lote escribe solo en los índices del día, que son chicos.

Además no se recrea el índice (imei, reading_date): ninguna consulta lo usa.

Revision ID: d2f7a9c4e1b8
Revises: b8d4e2f6a1c3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2f7a9c4e1b8"
down_revision: str | Sequence[str] | None = "b8d4e2f6a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    # La secuencia de id pertenece a la tabla vieja: se suelta para que no se
    # borre con ella y se vuelve a asociar a la nueva.
    op.execute("ALTER SEQUENCE data_level_id_seq OWNED BY NONE")
    op.execute(
        "CREATE TABLE data_level_new (LIKE data_level INCLUDING DEFAULTS) "
        "PARTITION BY RANGE (reading_date)"
    )
    op.execute("DROP TABLE data_level")
    op.execute("ALTER TABLE data_level_new RENAME TO data_level")
    # La columna de partición tiene que estar en la clave primaria.
    op.execute("ALTER TABLE data_level ADD PRIMARY KEY (id, reading_date)")
    op.execute(
        "ALTER TABLE data_level ADD CONSTRAINT data_level_container_id_fkey "
        "FOREIGN KEY (container_id) REFERENCES containers (id)"
    )
    op.execute(
        "CREATE INDEX idx_data_level_container_reading_date "
        "ON data_level (container_id, reading_date)"
    )
    op.execute("CREATE INDEX idx_data_level_reading_date ON data_level (reading_date)")
    op.execute("ALTER SEQUENCE data_level_id_seq OWNED BY data_level.id")


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("ALTER SEQUENCE data_level_id_seq OWNED BY NONE")
    op.execute("CREATE TABLE data_level_plain (LIKE data_level INCLUDING DEFAULTS)")
    op.execute("DROP TABLE data_level")  # borra también las particiones
    op.execute("ALTER TABLE data_level_plain RENAME TO data_level")
    op.execute("ALTER TABLE data_level ADD PRIMARY KEY (id)")
    op.execute(
        "ALTER TABLE data_level ADD CONSTRAINT data_level_container_id_fkey "
        "FOREIGN KEY (container_id) REFERENCES containers (id)"
    )
    op.execute(
        "CREATE INDEX idx_data_level_container_reading_date "
        "ON data_level (container_id, reading_date)"
    )
    op.execute("CREATE INDEX idx_data_level_reading_date ON data_level (reading_date)")
    op.execute(
        "CREATE INDEX idx_data_level_imei_reading_date ON data_level (imei, reading_date)"
    )
    op.execute("ALTER SEQUENCE data_level_id_seq OWNED BY data_level.id")
