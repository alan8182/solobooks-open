# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""bank_statements 补列：交易流水号 + 文件行序。

背景：流水表只有 日期/摘要/金额/余额，判重只能靠"复合指纹"，同秒多笔也只能
靠 id 倒序猜顺序（SQLite 的 uuid4 路径下是噪声），期初/期末/流水连续差额会算错。
引用号是银行给的身份（中国银行「交易流水号」12 位，精确可用），行序是文件给的
顺序（同一秒内按行号升序即真实时序）——两者都不能从现有列推导。

_ensure_schema() 的 DDL 在 PostgreSQL 上被 PostgresCompatConnection.execute
短路（CREATE/ALTER 一律返回空 cursor），所以 PG 侧必须在这里补列；SQLite 侧
由 bank_service._ensure_schema 的 PRAGMA 补列覆盖。与 d9f5b7e2c4a1 同因同法。

索引不唯一：流水表没有账户列，同公司多账户理论上可撞号；且唯一冲突会让整批
导入事务回滚（import_statements_core 只按行捕获业务异常）。

Revision ID: b7e3d1c8a4f2
Revises: d4b7e2a9c6f1
"""

from alembic import op
import sqlalchemy as sa


revision = "b7e3d1c8a4f2"
down_revision = "d4b7e2a9c6f1"
branch_labels = None
depends_on = None


# (列名, Column) — 与 bank_service._ensure_schema() 的增量列 DDL 对齐。
_COLUMNS = [
    ("transaction_reference", sa.Column("transaction_reference", sa.Text, nullable=True)),
    ("source_row_index", sa.Column("source_row_index", sa.Integer, nullable=True)),
]
_INDEX_NAME = "idx_bank_reference"


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("bank_statements"):
        return
    existing = _column_names(bind, "bank_statements")
    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column("bank_statements", column)
    if _INDEX_NAME not in _index_names(bind, "bank_statements"):
        op.create_index(
            _INDEX_NAME, "bank_statements",
            ["company_id", "transaction_reference"], unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("bank_statements"):
        return
    if _INDEX_NAME in _index_names(bind, "bank_statements"):
        op.drop_index(_INDEX_NAME, table_name="bank_statements")
    existing = _column_names(bind, "bank_statements")
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("bank_statements", name)
