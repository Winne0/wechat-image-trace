#!/usr/bin/env bash
# 生产/部署启动入口：单端口同时提供 API 与前端静态页面。
# 必须监听环境变量 PORT（各云平台约定），默认回落到 8000。
set -euo pipefail

cd "$(dirname "$0")/.."

# 库不存在，或存在但被清空（异常中断会导致空表）时，自动灌入演示数据，
# 保证打开即有内容可搜
NEED_SEED=$(python3 - <<'PY' 2>/dev/null || echo yes
import os, sqlite3
db = "backend/data/index.db"
if not os.path.exists(db):
    print("yes"); raise SystemExit
try:
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM images").fetchone()[0]
    print("no" if n > 0 else "yes")
except Exception:
    print("yes")
PY
)
if [ "$NEED_SEED" = "yes" ]; then
  python3 scripts/seed_demo.py || true
fi

PORT="${PORT:-8000}"
exec python3 -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port "$PORT" --timeout-keep-alive 65
