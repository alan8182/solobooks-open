# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""SoloBooks Agent 基础设施（M0）。

设计原则（见 Agent化落地计划.md 第 3 节）：
- 写即草稿：agent 写操作只产生 agent_actions 中的 proposal，人确认才生效；
- 证据链：每个动作携带 evidence，审批后与 audit_log 打通；
- 记忆即画像：company_profiles 注入每轮 system context；
- 工具即服务：工具是对现有 service 函数的薄包装。
"""
