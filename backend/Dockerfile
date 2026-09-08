# 使用轻量级 Python 镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，确保 Python 输出直接打印到日志中
ENV PYTHONUNBUFFERED=1

# 安装系统依赖（如果以后需要处理图片或数据库驱动，可以在这里添加）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements.txt 以利用 Docker 缓存
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目所有文件
COPY . .

# 微信云托管默认监听 80 端口
EXPOSE 80

# 启动 FastAPI 应用
# main 是文件名 (main.py)，app 是 FastAPI 实例名
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
