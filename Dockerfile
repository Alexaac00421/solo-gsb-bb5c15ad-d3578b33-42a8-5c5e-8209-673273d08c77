# 条款草案协作台 —— 纯 Python 标准库实现，无需联网安装任何第三方依赖
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLAUSE_HOST=0.0.0.0 \
    CLAUSE_PORT=8000 \
    CLAUSE_DB_PATH=/data/clauses.db

WORKDIR /app

# 零第三方依赖：仅复制应用代码
COPY app/ ./app/

# 数据目录（SQLite）由 Compose 挂载为命名卷
RUN mkdir -p /data && chmod +x /app/app/entrypoint.sh

EXPOSE 8000

# 容器内单进程 + 多线程（ThreadingHTTPServer）；写操作由应用锁串行化
ENTRYPOINT ["/app/app/entrypoint.sh"]
