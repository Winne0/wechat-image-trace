#!/usr/bin/env bash
# 重启服务：后端 FastAPI(3000) + 前端 Vite(5173)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

pkill -f "[u]vicorn app.main" 2>/dev/null
pkill -f "[v]ite" 2>/dev/null
sleep 1

(cd "$ROOT/backend" && nohup python3 -m uvicorn app.main:app --host 0.0.0.0 \
  --port 3000 > /tmp/backend.log 2>&1 &)

(cd "$ROOT/frontend" && nohup pnpm dev > /tmp/frontend.log 2>&1 &)

sleep 7
echo "backend : $(curl -s --max-time 5 http://localhost:3000/api/health)"
echo "frontend: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:5173/)"
