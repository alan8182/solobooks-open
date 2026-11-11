FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# 只安装 ca-certificates + curl（用于健康检查），跳过 build-essential
# 当前 Python 包全有预编译 wheel，无需 gcc
# Debian 官方源先换清华镜像（D-018：服务器到 deb.debian.org 实测 13KB/s，
# builder prune 后全量重建会卡死在 apt 层）。slim 镜像（trixie 起）源文件为
# /etc/apt/sources.list.d/debian.sources，老基底为 /etc/apt/sources.list，
# 两处都尝试替换；main 与 security 同域，一条替换全覆盖。
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 默认清华源：服务器在国内，官方 pypi 直连极慢（2026-10-21 曾因下载
# 卡死把 3.9G 内存小机拖到整机无响应）。海外构建可用 --build-arg 覆盖。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -i $PIP_INDEX_URL -r requirements.txt

COPY backend/ .


EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
