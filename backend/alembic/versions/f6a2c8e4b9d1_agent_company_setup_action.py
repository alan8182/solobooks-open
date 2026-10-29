# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent 建账动作：agent_actions.action_type 放行迁移引导流新增的两类动作。

propose_company_setup（建账确认卡）与 propose_opening_balance（期初导入确认卡）
由迁移引导流（代账/旧账迁入）落库，但 CHECK 约束未放行导致插入 500。
"""

from alembic import op


revision = "f6a2c8e4b9d1"
down_revision = "e9c7b3d5a1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher','propose_vouchers','propose_match','propose_accrual','propose_company_setup','propose_opening_balance')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher','propose_vouchers','propose_match','propose_accrual')",
    )
