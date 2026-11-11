# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent 循环：qwen function calling + 工具执行 + SSE 事件流。

事件契约（routers/agent.py 以 SSE data: JSON 下发）：
  meta        会话元信息（工具清单、步数上限）
  token       正文增量
  tool_call   开始调用工具 {step, name, arguments}
  tool_result 工具返回 {step, name, ok, summary}
  action      产生了待确认动作（含完整 action 卡片数据）
  final       回合最终回复全文
  done        会话结束
  error       出错（会话终止）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from agent.memory import get_profile
from agent.prompts import AGENT_SYSTEM_PROMPT, MAX_STEPS, TOOL_RESULT_MAX_CHARS
from config import APP_PUBLIC_URL
from agent.tools import (
    PRODUCT_AGENT_DISABLED_TOOLS,
    PRODUCT_AGENT_TOOL_SCHEMAS,
    ToolContext,
    execute_tool,
)
from services.llm import chat_tools_stream_async
from services.tax_policy_card import POLICY_CARD_MD

logger = logging.getLogger(__name__)


@dataclass
class LoopContext:
    user_id: str
    company_id: str
    conversation_id: Optional[str] = None
    workspace_view: Optional[str] = None

    def tool_ctx(self) -> ToolContext:
        return ToolContext(
            user_id=self.user_id,
            company_id=self.company_id,
            source="product",
            conversation_id=self.conversation_id,
        )


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


async def _build_system_prompt(ctx: LoopContext) -> str:
    now = datetime.now()
    try:
        profile = await get_profile(ctx.company_id)
    except Exception:
        logger.exception("load company profile failed")
        profile = {}

    profile_lines = []
    if isinstance(profile, dict):
        if profile.get("industry") or profile.get("taxpayerType"):
            profile_lines.append(
                f"行业：{profile.get('industry') or '未知'}；纳税人身份：{profile.get('taxpayerType') or '未知'}"
            )
        if profile.get("taxpayerType") == "general":
            profile_lines.append(
                "注意：该公司为增值税一般纳税人，小规模纳税人减免政策（1% 征收率、月销售额 10 万元以下免征）不适用；"
                "涉及增值税优惠按「政策要点卡」中适用项回答，无对应项时明确提示需核实。"
            )
        prefs = profile.get("categoryPreferences") or {}
        if isinstance(prefs, dict) and prefs:
            ranked = sorted(
                prefs.items(),
                key=lambda kv: int((kv[1] or {}).get("count") if isinstance(kv[1], dict) else 0),
                reverse=True,
            )[:10]
            mappings = "；".join(f"{k}→{v.get('category')}" for k, v in ranked if isinstance(v, dict))
            if mappings:
                profile_lines.append(f"本公司已确认的分类习惯（尽量沿用）：{mappings}")
        if profile.get("notes"):
            profile_lines.append(f"其他偏好：{profile['notes']}")

    profile_block = ("\n\n## 公司画像（长期记忆）\n" + "\n".join(profile_lines)) if profile_lines else ""
    if ctx.workspace_view == "boss":
        view_block = (
            "\n\n## 当前视图\n"
            "用户正在老板视图。优先用经营语言回答：赚了多少、钱在哪里、欠谁、要交多少税、下一步做什么。"
            "除非用户主动询问会计处理，不要先展开借贷分录、科目编码或凭证状态。"
        )
    elif ctx.workspace_view == "accounting":
        view_block = (
            "\n\n## 当前视图\n"
            "用户正在会计视图。可以给出科目编码、借贷方向、凭证状态、勾稽差异和五项检查细节；"
            "但仍然必须遵守 AI 起草、规则校验、用户确认的边界。"
        )
    else:
        view_block = ""
    return AGENT_SYSTEM_PROMPT.format(
        today=now.strftime("%Y-%m-%d"),
        weekday=WEEKDAYS[now.weekday()],
        month=now.strftime("%Y-%m"),
        card=POLICY_CARD_MD,
        opening_url=f"{APP_PUBLIC_URL}/mine/opening",
    ) + view_block + profile_block


def _summarize_result(result: object) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = str(result)
    if len(text) > 200:
        text = text[:200] + "…"
    return text


def _compact_tool_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    depth: int = 0,
) -> Any:
    """递归裁剪工具结果，但始终保持为可 JSON 序列化的结构。"""
    if depth >= 8:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit] + "…（已截断）"
    if isinstance(value, list):
        out = [
            _compact_tool_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for item in value[:list_limit]
        ]
        if len(value) > list_limit:
            out.append({"_omittedItems": len(value) - list_limit})
        return out
    if isinstance(value, dict):
        return {
            str(key): _compact_tool_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _tool_result_content(result: object) -> str:
    """生成可安全回填给模型的工具结果。

    旧实现直接按字符截断 JSON，较长结果会以残缺的 `{...` 进入下一轮，
    模型只能猜测缺失字段，容易出现金额、笔数等幻觉。这里改为结构化裁剪，
    并在必要时返回带 preview 的合法 JSON 信封。
    """
    for string_limit, list_limit in ((800, 80), (400, 30), (200, 10), (100, 5)):
        compacted = _compact_tool_value(
            result,
            string_limit=string_limit,
            list_limit=list_limit,
        )
        text = json.dumps(compacted, ensure_ascii=False, default=str)
        if len(text) <= TOOL_RESULT_MAX_CHARS:
            return text

    try:
        raw = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        raw = str(result)
    preview_limit = max(200, TOOL_RESULT_MAX_CHARS // 2)
    envelope = {
        "truncated": True,
        "message": "工具结果过大，已按结构裁剪；不得猜测被省略的金额、笔数或流水 ID。",
        "preview": raw[:preview_limit],
    }
    text = json.dumps(envelope, ensure_ascii=False, default=str)
    while len(text) > TOOL_RESULT_MAX_CHARS and preview_limit > 100:
        preview_limit //= 2
        envelope["preview"] = raw[:preview_limit]
        text = json.dumps(envelope, ensure_ascii=False, default=str)
    return text


def _normalize_history(messages: List[dict]) -> List[dict]:
    """仅保留 user/assistant 消息，各取最近一半，防止历史膨胀。"""
    valid = [
        {"role": m["role"], "content": str(m.get("content") or "")}
        for m in messages
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and str(m.get("content") or "").strip()
    ]
    keep = 12
    return valid[-keep:]


async def run_agent_stream(
    ctx: LoopContext, messages: List[dict], enable_thinking: bool = False
) -> AsyncIterator[dict]:
    """运行 agent 循环，逐个产出 SSE 事件。"""
    yield {
        "type": "meta",
        "tools": [t["function"]["name"] for t in PRODUCT_AGENT_TOOL_SCHEMAS],
        "maxSteps": MAX_STEPS,
        "companyId": ctx.company_id,
        "enableThinking": enable_thinking,
    }

    system_prompt = await _build_system_prompt(ctx)
    convo: List[dict] = _normalize_history(messages)
    tool_ctx = ctx.tool_ctx()
    bank_statement_context = False

    for step in range(1, MAX_STEPS + 1):
        content_parts: List[str] = []
        final_content = ""
        tool_calls: List[dict] = []

        try:
            async for chunk in chat_tools_stream_async(
                system_prompt,
                convo,
                tools=PRODUCT_AGENT_TOOL_SCHEMAS,
                scene="agent_chat",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
                enable_thinking=enable_thinking,
            ):
                if chunk["type"] == "delta":
                    content_parts.append(chunk["content"])
                    yield {"type": "token", "content": chunk["content"]}
                elif chunk["type"] == "reasoning":
                    yield {"type": "reasoning", "content": chunk["content"]}
                elif chunk["type"] == "final":
                    final_content = chunk["content"]
                    tool_calls = chunk["tool_calls"]
        except Exception as exc:  # LLM 网络层失败
            logger.exception("agent llm round failed at step %s", step)
            yield {"type": "error", "message": f"AI 服务暂时不可用，请稍后重试（{exc.__class__.__name__}）"}
            return

        if tool_calls:
            convo.append({"role": "assistant", "content": final_content or "", "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in tool_calls
            ]})

            batch_queries_bank = any(
                tc["name"] in ("list_unmatched", "list_bank_statements")
                for tc in tool_calls
            )
            for tc in tool_calls:
                name = tc["name"]
                try:
                    arguments = json.loads(tc["arguments"] or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {}
                except json.JSONDecodeError:
                    arguments = {}

                yield {"type": "tool_call", "step": step, "name": name, "arguments": arguments}

                try:
                    if name in PRODUCT_AGENT_DISABLED_TOOLS:
                        result = {
                            "error": (
                                "月度结账只能在「月度结账」向导中按系统规则执行。"
                                "AI CFO 不能运行结账检查、生成结账动作或锁账；"
                                "请引导用户打开 /closing-wizard 对应月份继续。"
                            )
                        }
                        ok = False
                    elif (
                        name in ("propose_voucher", "propose_vouchers")
                        and (bank_statement_context or batch_queries_bank)
                    ):
                        result = {
                            "error": (
                                "本轮已查询银行流水，不能用通用记账工具改写金额。"
                                "请改用 propose_statement_vouchers，并逐笔传 statementId 与 category。"
                            )
                        }
                        ok = False
                    else:
                        result = await execute_tool(name, arguments, tool_ctx)
                        ok = True
                except Exception as exc:
                    logger.exception("tool %s failed", name)
                    result = {"error": f"工具执行失败：{exc}"}
                    ok = False

                if ok and name in ("list_unmatched", "list_bank_statements"):
                    bank_statement_context = True

                summary = _summarize_result(result)
                yield {"type": "tool_result", "step": step, "name": name, "ok": ok, "summary": summary}

                if ok and name == "run_close_checks" and isinstance(result, dict):
                    guide = result.get("manualClose")
                    if (
                        isinstance(guide, dict)
                        and str(guide.get("url") or "").startswith(("http://", "https://"))
                    ):
                        yield {
                            "type": "closing_link",
                            "closingLink": {
                                "url": str(guide["url"]),
                                "label": str(guide.get("label") or "打开月度结账向导"),
                                "status": str(guide.get("status") or ""),
                                "requiresLogin": bool(guide.get("requiresLogin")),
                                "message": str(guide.get("message") or ""),
                                "month": str(result.get("period") or ""),
                            },
                        }

                convo.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": _tool_result_content(result),
                })

                if (
                    ok
                    and name in (
                        "propose_voucher",
                        "propose_vouchers",
                        "propose_statement_vouchers",
                        "propose_match",
                        "propose_accrual",
                        "propose_company_setup",
                    )
                    and isinstance(result, dict)
                    and result.get("actionId")
                ):
                    try:
                        from agent.actions import get_action

                        action = await get_action(str(result["actionId"]), company_id=ctx.company_id)
                        yield {"type": "action", "action": action}
                    except Exception:
                        logger.exception("emit action event failed")

            continue  # 工具结果已回填，进入下一回合

        # 无工具调用：本轮即最终回复（token 已流出）
        yield {"type": "final", "content": final_content or "".join(content_parts)}
        break
    else:
        yield {
            "type": "final",
            "content": (final_content or "") + "\n\n（本次任务步骤较多，已暂停。你可以让我继续。）",
        }

    yield {"type": "done"}
