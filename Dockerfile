# ---------------------------------------------------------------------------
# 微信图片溯源 · 单容器部署
# 阶段 1：构建前端静态产物
# 阶段 2：Python 运行时同时提供 API 与静态页面（单端口）
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@9 --activate && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DATA_DIR=/app/backend/data

# OpenCV headless 运行所需的最小系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p ${DATA_DIR}/images

EXPOSE 8000
CMD ["sh", "-c", "python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
