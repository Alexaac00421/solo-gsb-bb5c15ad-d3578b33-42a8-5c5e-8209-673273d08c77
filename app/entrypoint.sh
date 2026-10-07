#!/bin/sh
# 容器启动入口：结构初始化在服务启动时幂等执行（CREATE TABLE IF NOT EXISTS）
set -e

mkdir -p "$(dirname "$CLAUSE_DB_PATH")"

echo "[entrypoint] 初始化/校验 SQLite 结构: $CLAUSE_DB_PATH"
python -m app.server
