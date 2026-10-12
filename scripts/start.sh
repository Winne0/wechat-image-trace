#!/usr/bin/env bash
# 生产/部署启动入口：单端口同时提供 API 与前端静态页面。
# 必须监听环境变量 PORT（各云平台约定），默认回落到 8000。
set -euo pipefail

cd "$(dirname "$0")/.."

# 首次启动若指纹库为空，自动灌入演示数据，保证打开即有内容可搜
if [ ! -f backend/data/index.db ]; then
  python3 scripts/seed_demo.py || true
fi

PORT="${PORT:-8000}"
exec python3 -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port "$PORT" --timeout-keep-alive 65
