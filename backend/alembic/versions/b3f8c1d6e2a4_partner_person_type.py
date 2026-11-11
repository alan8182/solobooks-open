# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""往来单位增加「个人」类型（股东/员工垫付的对手方）。

垫付业务的对手方是自然人，既不是客户也不是供应商。把它建成
``type='person'`` 的往来单位，凭证行才能通过辅助核算挂到具体的人，
而不是靠「其他应付款-股东」这种不落库的科目名区分。
"""

import sqlalchemy as sa
from alembic import op


revision = "b3f8c1d6e2a4"
down_revision = "a7c3e9f1b5d2"
branch_labels = None
depends_on = None

_OLD = "type IN ('customer','supplier','both')"
_NEW = "type IN ('customer','supplier','both','person')"


def _alter(expression: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite 不支持直接 DROP CONSTRAINT，走整表重建（本地开发库/迁移测试）
        with op.batch_alter_table("partners", recreate="always") as batch:
            batch.drop_constraint("ck_partner_type", type_="check")
            batch.create_check_constraint("ck_partner_type", expression)
    else:
        op.drop_constraint("ck_partner_type", "partners", type_="check")
        op.create_check_constraint("ck_partner_type", "partners", expression)


def upgrade() -> None:
    _alter(_NEW)


def downgrade() -> None:
    # 回退前先把个人档案收敛成 both，否则新约束会拒绝现有数据
    op.execute(sa.text("UPDATE partners SET type = 'both' WHERE type = 'person'"))
    _alter(_OLD)
