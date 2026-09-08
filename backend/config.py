import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# 阿里云百炼平台 API Key（通义千问）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    raise RuntimeError("请设置 DASHSCOPE_API_KEY 环境变量或在 backend/.env 中配置")

# 智谱开放平台 API Key（OCR：https://open.bigmodel.cn/ 文档「OCR 服务」）
# 拍单据路由在运行时校验；未配置则返回 503，避免静默失败。
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

# 模型名称
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen-vl-plus")
QWEN_TEXT_MODEL = os.getenv("QWEN_TEXT_MODEL", "qwen-plus")

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# 微信小程序登录配置（个人开发者主体：仅用于 code2session 获取 openid）
WECHAT_APPID = os.getenv("WECHAT_APPID", "").strip()
WECHAT_SECRET = os.getenv("WECHAT_SECRET", "").strip()

# JWT 配置
JWT_SECRET = os.getenv("JWT_SECRET", "solobooks-dev-secret")
JWT_EXPIRES_IN = int(os.getenv("JWT_EXPIRES_IN", "604800"))

# 阿里云号码认证短信（Dypnsapi）
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "").strip()
ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "").strip()
ALIYUN_SMS_SCHEME_NAME = os.getenv("ALIYUN_SMS_SCHEME_NAME", "").strip()
ALIYUN_SMS_CODE_VALID_SECONDS = int(os.getenv("ALIYUN_SMS_CODE_VALID_SECONDS", "300"))
ALIYUN_SMS_INTERVAL_SECONDS = int(os.getenv("ALIYUN_SMS_INTERVAL_SECONDS", "60"))
