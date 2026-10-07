FROM python:3.12-slim

WORKDIR /app

# 依赖安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY app ./app

# SQLite 数据目录（首次启动时自动建表并写入演示数据）
ENV APP_DB=/data/app.db \
    APP_PORT=8000 \
    SEED_DEMO=1
RUN mkdir -p /data

EXPOSE 8000

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "app.server:app"]
