"""Agent M2：agent_actions 支持对账建议与计提确认动作类型。

Revision ID: d2f4a6b8c1e9
Revises: c7a9d1e4b5f8
"""

from alembic import op


revision = "d2f4a6b8c1e9"
down_revision = "c7a9d1e4b5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher','propose_vouchers','propose_match','propose_accrual')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_action_type", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_action_type",
        "agent_actions",
        "action_type IN ('propose_voucher','propose_vouchers')",
    )
