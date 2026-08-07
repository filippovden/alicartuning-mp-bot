"""Добавить значение 'partial' в enum publishstatus

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE не может выполняться внутри обычной транзакции
    # Alembic — используем autocommit-блок (см. документацию Alembic по
    # изменению Postgres-энумов).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE publishstatus ADD VALUE IF NOT EXISTS 'partial'")


def downgrade() -> None:
    # Postgres не поддерживает удаление значения из enum — откат недоступен.
    pass
