# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""生产反代链路的回归测试。

背景：生产是「宿主 nginx → 容器 nginx → uvicorn」。uvicorn 默认只信任
127.0.0.1 的转发头，而实际 peer 是 Docker 网关 IP，于是
`request.client.host` 对所有用户都是同一个地址：

- 短信发送的 60 秒同 IP 限流变成全站共享（一个人发过，其他人 60 秒内都被 429）；
- 登录/step-up 的失败锁定按「手机号 + 共享 IP」计数，5 次错误会锁住所有人。

修复依赖两件事同时成立：nginx 透传 X-Forwarded-For，uvicorn 信任 Docker
私网段的转发头。任何一处被删掉都会让问题复现，所以这里把配置钉住。
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"


def _read(relative: str) -> str:
    path = DEPLOY_DIR / relative
    if not path.exists():
        pytest.skip(f"{relative} 不在当前检出中（非完整仓库时跳过）")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    ["docker-compose.yml", "docker-compose.app.yml", "backend.Dockerfile"],
)
def test_uvicorn_trusts_forwarded_client_ip(relative: str):
    text = _read(relative)
    assert "--proxy-headers" in text, f"{relative} 必须显式开启 proxy-headers"
    assert "--forwarded-allow-ips" in text, f"{relative} 必须信任 nginx 转发头"
    assert "172.16.0.0/12" in text, f"{relative} 需信任 Docker 私网段"


@pytest.mark.parametrize(
    "relative",
    ["nginx/solobooks.cn.host.conf", "nginx/default.conf"],
)
def test_nginx_passes_x_forwarded_for(relative: str):
    text = _read(relative)
    assert "X-Forwarded-For" in text, f"{relative} 必须透传 X-Forwarded-For"
    assert "X-Real-IP" in text, f"{relative} 必须透传 X-Real-IP"
