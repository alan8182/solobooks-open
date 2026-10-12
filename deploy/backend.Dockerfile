FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# 只安装 ca-certificates + curl（用于健康检查），跳过 build-essential
# 当前 Python 包全有预编译 wheel，无需 gcc
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL=https://pypi.org/simple
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -i $PIP_INDEX_URL -r requirements.txt

COPY backend/ .

EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
