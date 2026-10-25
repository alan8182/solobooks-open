# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent M1：agent_actions 支持批量记账动作类型 propose_vouchers。

Revision ID: c7a9d1e4b5f8
Revises: b3f8a1c2d9e7
"""

from alembic import op


revision = "c7a9d1e4b5f8"
down_revision = "b3f8a1c2d9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher','propose_vouchers')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher')",
    )
