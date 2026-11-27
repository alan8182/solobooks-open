# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import os
import json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# 阿里云百炼平台 API Key（通义千问）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    raise RuntimeError("请设置 DASHSCOPE_API_KEY 环境变量或在 backend/.env 中配置")

# 智谱开放平台 API Key（OCR：https://open.bigmodel.cn/ 文档「OCR 服务」）
# 拍单据路由在运行时校验；未配置则返回 503，避免静默失败。
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

# 模型名称（变量名 QWEN_* 为历史遗留；实际由 .env 决定供应商与模型）
QWEN_TEXT_MODEL = os.getenv("QWEN_TEXT_MODEL", "").strip() or "qwen-plus"
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "").strip() or "qwen-vl-plus"

# 文本 LLM 统一走 OpenAI 兼容模式端点（DeepSeek/GLM 等供应商仅换 .env 四项即可）
DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL
# LLM 供应商 API Key（默认复用百炼 Key；接入 DeepSeek/GLM 等供应商时配置各自的 Key）
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip() or DASHSCOPE_API_KEY
# 是否发送 Qwen 专有的 enable_thinking 参数（extra_body）；DeepSeek 等不需要时置 0
LLM_SEND_ENABLE_THINKING = os.getenv("LLM_SEND_ENABLE_THINKING", "1").strip().lower() in (
    "1", "true", "yes", "on",
)
# 深度思考开关：默认关闭以保证日常记账低时延；需要深度分析时可通过前端动态开启或在 .env 设置
QWEN_ENABLE_THINKING = os.getenv("QWEN_ENABLE_THINKING", "false").strip().lower() == "true"

# ── 政策检索（DeepSeek Search：agent 实时核验税务政策，唯一对外检索出口）──
# 文档 https://api-docs.deepseek.com/zh-cn/guides/search：每 1000 次成功请求 5 元，
# 失败/无结果不计费。Key 默认回落 LLM_API_KEY（当前 .env 即 DeepSeek key，同账户同主机）。
# 应急停用：.env 设 POLICY_SEARCH_OFF=1，工具返回 disabled 并走"未核实"降级。
DEEPSEEK_SEARCH_BASE_URL = os.getenv("DEEPSEEK_SEARCH_BASE_URL", "").strip() or "https://api.deepseek.com/v1"
DEEPSEEK_SEARCH_API_KEY = os.getenv("DEEPSEEK_SEARCH_API_KEY", "").strip() or LLM_API_KEY
POLICY_SEARCH_DOMAINS = tuple(
    d.strip()
    for d in os.getenv(
        "POLICY_SEARCH_DOMAINS",
        "chinatax.gov.cn,mof.gov.cn,gov.cn,flk.npc.gov.cn",
    ).split(",")
    if d.strip()
)
POLICY_SEARCH_TIMEOUT_S = float(os.getenv("POLICY_SEARCH_TIMEOUT_S", "15"))
POLICY_SEARCH_OFF = os.getenv("POLICY_SEARCH_OFF", "0").strip().lower() in ("1", "true", "yes", "on")

# 主体永久删除专用连接：必须是数据库属主角色（不可变触发器只对属主放行）。
# 未配置时删除入口失败关闭，不降级到应用角色；日常业务连接始终是应用角色。
TENANT_PURGE_DATABASE_URL = (
    os.getenv("TENANT_PURGE_DATABASE_URL", "").strip()
    or os.getenv("MIGRATION_DATABASE_URL", "").strip()
)

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))
# 网页端公开地址：用于让 MCP/对话把最终锁账引导回登录后的产品页面。
APP_PUBLIC_URL = (os.getenv("APP_PUBLIC_URL", "").strip() or "https://solobooks.cn").rstrip("/")
# CORS：默认仅本地开发前端（vite 5173）。allow_credentials=True 时通配符源
# 等效全开（Starlette 回显任意 Origin），生产必须显式配置白名单。
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

# 微信小程序登录配置（个人开发者主体：仅用于 code2session 获取 openid）
WECHAT_APPID = os.getenv("WECHAT_APPID", "").strip()
WECHAT_SECRET = os.getenv("WECHAT_SECRET", "").strip()

# ── 微信开放平台网站应用扫码登录（与小程序登录、微信支付相互独立）──────────
# 网站应用 APPID 不能用于微信支付；支付仍走 WECHAT_PAY_APP_ID。
WECHAT_WEB_APPID = os.getenv("WECHAT_WEB_APPID", "").strip()
WECHAT_WEB_SECRET = os.getenv("WECHAT_WEB_SECRET", "").strip()
# 微信 OAuth 回调：必须落在开放平台「授权回调域」之下（生产 solobooks.cn）
WECHAT_WEB_REDIRECT_URI = f"{APP_PUBLIC_URL}/api/auth/wechat/web/callback"
# state cookie 与一次性 ticket 有效期（秒）
WECHAT_WEB_STATE_TTL_SECONDS = int(os.getenv("WECHAT_WEB_STATE_TTL_SECONDS", "600"))
WECHAT_WEB_TICKET_TTL_SECONDS = int(os.getenv("WECHAT_WEB_TICKET_TTL_SECONDS", "600"))
# 后端回调 302 到的前端路由（ticket 走 HttpOnly cookie，不进 URL）
WECHAT_WEB_FRONTEND_CALLBACK_PATH = "/wechat-login-callback"


def wechat_web_login_enabled() -> bool:
    """网站应用 APPID + AppSecret 齐全时扫码登录可用。"""
    return bool(WECHAT_WEB_APPID and WECHAT_WEB_SECRET)


# JWT 配置
# 生产环境必须显式配置高强度随机串（deploy/docker-compose.yml 已强制 :?）；
# 仅 ACCOUNTING_TEST_MODE=1 的测试进程回落到固定 dev 值，缺失即拒绝启动，
# 防止弱默认密钥被用于离线伪造任意用户/公司身份的 JWT。
_JWT_SECRET_ENV = os.getenv("JWT_SECRET", "").strip()
if _JWT_SECRET_ENV:
    JWT_SECRET = _JWT_SECRET_ENV
elif os.getenv("ACCOUNTING_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
    JWT_SECRET = "solobooks-dev-secret"
else:
    raise RuntimeError(
        "JWT_SECRET 未配置：请在 backend/.env 或环境变量中设置高强度随机串"
        "（测试进程由 conftest 自动设置 ACCOUNTING_TEST_MODE=1）"
    )
JWT_EXPIRES_IN = int(os.getenv("JWT_EXPIRES_IN", "604800"))

# 阿里云号码认证短信（Dypnsapi）
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "").strip()
ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "").strip()
ALIYUN_SMS_SCHEME_NAME = os.getenv("ALIYUN_SMS_SCHEME_NAME", "").strip()
ALIYUN_SMS_CODE_VALID_SECONDS = int(os.getenv("ALIYUN_SMS_CODE_VALID_SECONDS", "300"))
ALIYUN_SMS_INTERVAL_SECONDS = int(os.getenv("ALIYUN_SMS_INTERVAL_SECONDS", "60"))

# 密码登录（PBKDF2-SHA256，标准库实现；失败锁定为进程内计数，多实例部署需换集中存储）
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))

# 待办生成：低于该金额（绝对值，元）的条目不生成"补充票据/开票确认"待办
# （小额微信手续费/收款逐条催票据不现实；0 表示不豁免）
TODO_SMALL_AMOUNT_THRESHOLD = float(os.getenv("TODO_SMALL_AMOUNT_THRESHOLD", "10"))
PASSWORD_PBKDF2_ITERATIONS = int(os.getenv("PASSWORD_PBKDF2_ITERATIONS", "390000"))
PASSWORD_LOGIN_MAX_FAILURES = int(os.getenv("PASSWORD_LOGIN_MAX_FAILURES", "5"))
PASSWORD_LOCKOUT_SECONDS = int(os.getenv("PASSWORD_LOCKOUT_SECONDS", "900"))

# ── 社保公积金口径（P3 修复：费率可配、基数限高、公积金独立口径）────────
# 默认按杭州常见口径；各地差异大，通过 .env 按当地标准调整。
# 单位社保（养老16%+医疗8%+失业0.5%+工伤0.5%，不含公积金）
SOCIAL_COMPANY_RATE = float(os.getenv("SOCIAL_COMPANY_RATE", "0.25") or 0.25)
# 个人社保（养老8%+医疗2%+失业0.5%）
SOCIAL_PERSONAL_RATE = float(os.getenv("SOCIAL_PERSONAL_RATE", "0.105") or 0.105)
# 住房公积金单位/个人比例（杭州常见 5%-12%，默认 8%）
HF_COMPANY_RATE = float(os.getenv("HF_COMPANY_RATE", "0.08") or 0.08)
HF_PERSONAL_RATE = float(os.getenv("HF_PERSONAL_RATE", "0.08") or 0.08)
# 社保公积金缴费基数上下限（当地社平的 60%-300%）；0 表示不限制
SOCIAL_SECURITY_BASE_MIN = float(os.getenv("SOCIAL_SECURITY_BASE_MIN", "0") or 0)
SOCIAL_SECURITY_BASE_MAX = float(os.getenv("SOCIAL_SECURITY_BASE_MAX", "0") or 0)
# LLM 成本估算单价表（管理后台用量页用）：JSON 字符串环境变量，键为模型名，
# 值为 {"prompt": 元/千tokens, "completion": 元/千tokens}。未配置或模型缺价时只显示 token 数。
LLM_PRICE_TABLE: dict = {}
_llm_price_raw = os.getenv("LLM_PRICE_TABLE", "").strip()
if _llm_price_raw:
    try:
        _parsed = json.loads(_llm_price_raw)
        LLM_PRICE_TABLE = {
            str(model): {"prompt": float(price["prompt"]), "completion": float(price["completion"])}
            for model, price in _parsed.items()
            if isinstance(price, dict)
        }
    except (ValueError, TypeError, AttributeError):
        LLM_PRICE_TABLE = {}

# 研发部门名单（逗号分隔）——这些部门的员工计提时必须登记研发项目工时
RD_DEPARTMENTS = tuple(
    part.strip() for part in os.getenv("RD_DEPARTMENTS", "研发部").split(",") if part.strip()
)

# ── 支付宝电脑网站支付（收款，docs/alipay-payment-integration.md）────────────
# 企业自研接入：公钥模式 RSA2（只收款不支出，无需证书加签三件套）。
# 三项配齐 alipay_enabled() 才为真，未配置时支付路由运行时 503（对齐 ZHIPU_API_KEY 惯例）。
ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "").strip()
# 应用私钥：PKCS8 单行 base64 或完整 PEM（\n 转义）均可，alipay_client 会归一化
ALIPAY_PRIVATE_KEY = os.getenv("ALIPAY_PRIVATE_KEY", "").strip()
# 支付宝公钥（平台「接口加签方式」配置后由支付宝生成，不是应用公钥）
ALIPAY_PUBLIC_KEY = os.getenv("ALIPAY_PUBLIC_KEY", "").strip()
# 生产网关；沙箱联调在 .env 覆盖为 https://openapi-sandbox.dl.alipaydev.com/gateway.do
ALIPAY_GATEWAY = (
    os.getenv("ALIPAY_GATEWAY", "").strip() or "https://openapi.alipay.com/gateway.do"
)
# 异步通知/同步跳转地址：挂在公网 HTTPS 入口下（宿主 nginx → 容器后端）
ALIPAY_NOTIFY_URL = f"{APP_PUBLIC_URL}/api/billing/alipay/notify"
ALIPAY_RETURN_URL = f"{APP_PUBLIC_URL}/api/billing/alipay/return"
# 订单支付有效期（支付宝 timeout_express 与本地惰性关单共用）
ALIPAY_ORDER_TIMEOUT = os.getenv("ALIPAY_ORDER_TIMEOUT", "2h")


def alipay_enabled() -> bool:
    """APP_ID/应用私钥/支付宝公钥齐全时支付链路可用。"""
    return bool(ALIPAY_APP_ID and ALIPAY_PRIVATE_KEY and ALIPAY_PUBLIC_KEY)


# ── 微信支付 APIv3（Native 扫码收款，第二支付渠道；商户号与支付宝同一主体）──
# 协议薄层自实现（services/wechat_pay_client.py，仿 alipay_client 惯例，无官方 SDK）。
# 商户配置与 visanote 项目同商户号，变量名口径一致便于直接拷贝 .env 值。
WECHAT_PAY_MCH_ID = os.getenv("WECHAT_PAY_MCH_ID", "").strip()
WECHAT_PAY_APP_ID = os.getenv("WECHAT_PAY_APP_ID", "").strip()
# APIv3 密钥：32 位原始串或 64 位 hex（wechat_pay_client._resolve_api_v3_key 归一化）
WECHAT_PAY_API_V3_KEY = os.getenv("WECHAT_PAY_API_V3_KEY", "").strip()
# 商户 API 证书序列号（下载的 apiclient_cert.pem 内 certificate_serial_no）
WECHAT_PAY_SERIAL_NO = os.getenv("WECHAT_PAY_SERIAL_NO", "").strip()
# 商户私钥：apiclient_key.pem 全文（\n 转义成单行）或单行 base64，client 内归一化
WECHAT_PAY_PRIVATE_KEY = os.getenv("WECHAT_PAY_PRIVATE_KEY", "").strip()
# 微信支付公钥模式（新版商户平台默认）：公钥 PEM + 公钥编号 PUB_KEY_ID_…。
# 未配置时回退平台证书模式（运行时拉 /v3/certificates，内存缓存 1h，不落盘）
WECHAT_PAY_PUBLIC_KEY = os.getenv("WECHAT_PAY_PUBLIC_KEY", "").strip()
WECHAT_PAY_PUBLIC_KEY_ID = os.getenv("WECHAT_PAY_PUBLIC_KEY_ID", "").strip()
# 异步通知地址：挂在公网 HTTPS 入口下（与支付宝同口径，由 APP_PUBLIC_URL 派生）
WECHAT_PAY_NOTIFY_URL = f"{APP_PUBLIC_URL}/api/billing/wechat/notify"
WECHAT_PAY_REFUND_NOTIFY_URL = f"{APP_PUBLIC_URL}/api/billing/wechat/refund-notify"
# 订单支付有效期（喂微信 time_expire 与本地惰性关单，口径同 ALIPAY_ORDER_TIMEOUT）
WECHAT_PAY_ORDER_TIMEOUT = os.getenv("WECHAT_PAY_ORDER_TIMEOUT", "2h")


def wechat_pay_enabled() -> bool:
    """商户号/appid/APIv3 密钥/证书序列号/商户私钥五项齐备时微信渠道可用。

    微信支付公钥可选（回退平台证书模式），故不参与 enabled 判定。
    """
    return bool(
        WECHAT_PAY_MCH_ID
        and WECHAT_PAY_APP_ID
        and WECHAT_PAY_API_V3_KEY
        and WECHAT_PAY_SERIAL_NO
        and WECHAT_PAY_PRIVATE_KEY
    )


# ── 邮件单据通道（专属收件地址，catch-all 邮箱 IMAP 拉取）────────────────
# 邮箱侧是中转站：拉取入库后即删信，长期存储在本地 data/inbox/。
# 换邮箱服务商只需改这里的 IMAP 配置与 DNS MX 指向，地址码与前端零改动。
# 未配置 IMAP 三项时轮询不启动（功能入口仍可见，提示未开通）。
MAIL_INBOUND_IMAP_HOST = os.getenv("MAIL_INBOUND_IMAP_HOST", "").strip()
MAIL_INBOUND_IMAP_PORT = int(os.getenv("MAIL_INBOUND_IMAP_PORT", "993"))
MAIL_INBOUND_IMAP_USER = os.getenv("MAIL_INBOUND_IMAP_USER", "").strip()
MAIL_INBOUND_IMAP_PASSWORD = os.getenv("MAIL_INBOUND_IMAP_PASSWORD", "")
# 专属地址展示用域名（收件地址 = address_code@域名），须与 catch-all 域名一致
MAIL_INBOUND_DOMAIN = os.getenv("MAIL_INBOUND_DOMAIN", "solobooks.cn").strip().lstrip("@").lower()
# 轮询间隔（秒）；0 或未配置 IMAP 时不启动后台收取
MAIL_POLL_SECONDS = max(int(os.getenv("MAIL_POLL_SECONDS", "180") or 0), 0)
# 单附件大小上限（MB）：超出仍留档可下载，但不进入待导入
MAIL_INBOUND_MAX_ATTACHMENT_MB = int(os.getenv("MAIL_INBOUND_MAX_ATTACHMENT_MB", "50") or 50)


def mail_inbound_enabled() -> bool:
    """IMAP 配置齐全且域名已设置时，邮件收取链路可用。"""
    return bool(
        MAIL_INBOUND_IMAP_HOST
        and MAIL_INBOUND_IMAP_USER
        and MAIL_INBOUND_IMAP_PASSWORD
        and MAIL_INBOUND_DOMAIN
    )


# 商业化套餐 Token 额度卡口开关：
# 开源自部署默认关闭（0），使用用户自有 Key 不受月 10 万体验额度限制；
# 官方云端 SaaS 在部署环境变量中设 QUOTA_ENFORCEMENT_ENABLED=1 开启体验版月额度控制。
QUOTA_ENFORCEMENT_ENABLED = os.getenv("QUOTA_ENFORCEMENT_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

