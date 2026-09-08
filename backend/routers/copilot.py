"""V3 Copilot 路由：经营助手 + 税务顾问双模式"""
from fastapi import APIRouter, Depends
from models.schemas import CopilotChatRequest, CopilotChatResponse
from services.llm import chat_text_json
from services.classifier import COPILOT_V3_PROMPT, TAX_ADVISOR_PROMPT, SECRETARY_PROMPT, COACH_PROMPT
from deps.auth import require_auth

router = APIRouter(prefix="/api/copilot", tags=["copilot"], dependencies=[Depends(require_auth)])


@router.post("/chat", response_model=CopilotChatResponse)
async def copilot_chat(req: CopilotChatRequest):
    """AI 经营助手 / 税务顾问"""
    # 选择系统提示词
    if req.mode == "tax_advisor":
        system_prompt = TAX_ADVISOR_PROMPT
    elif req.mode == "secretary":
        system_prompt = SECRETARY_PROMPT
    elif req.mode == "coach":
        system_prompt = COACH_PROMPT
    else:
        system_prompt = COPILOT_V3_PROMPT

    # 构建上下文
    context_text = ""
    if req.context:
        income = req.context.get("monthIncome", 0)
        expense = req.context.get("monthExpense", 0)
        profit = income - expense
        todo_count = req.context.get("todoCount", 0)
        risk_count = req.context.get("riskCount", 0)

        parts = [f"本月收入 {income}，支出 {expense}，净利润 {profit}"]
        if todo_count:
            parts.append(f"待处理事项 {todo_count} 项")
        if risk_count:
            parts.append(f"风险提醒 {risk_count} 项")
        context_text = "；".join(parts)

    # 过滤有效消息
    valid = [
        m for m in req.messages
        if isinstance(m, dict) and m.get("role") and m.get("content")
    ]
    recent = valid[-6:] if len(valid) > 6 else valid

    if not recent:
        history = "暂无对话"
    else:
        history = "\n".join(
            f"User: {m['content']}" if m['role'] == 'user' else f"Assistant: {m['content']}"
            for m in recent
        )

    user_msg = f"对话:\n{history}\n\n经营数据: {context_text or '暂无'}\n请用中文回复，只输出 JSON。"

    try:
        result = chat_text_json(system_prompt, user_msg)
    except Exception:
        return CopilotChatResponse(
            reply="抱歉，暂时无法回复，请稍后再试。",
            bookPayload=None,
        )

    return CopilotChatResponse(
        reply=result.get("reply", ""),
        bookPayload=result.get("bookPayload"),
        disclaimer=result.get("disclaimer"),
    )
