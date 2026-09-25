# RADAR Web 服务镜像（多阶段：构建 React 前端 + GPU 推理后端）
# 构建上下文必须是项目根目录：
#   docker build -f webapp/deploy/Dockerfile -t radar-web .

# ---------------- 阶段 1：前端 ----------------
FROM node:20-slim AS frontend
WORKDIR /fe
COPY webapp/frontend/package.json webapp/frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY webapp/frontend/ ./
RUN npm run build

# ---------------- 阶段 2：后端 ----------------
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DAMO_RADAR_DIR=/app \
    MODEL_ROOT=/ckpt \
    CONFIGS_ROOT=/ckpt \
    RADAR_DATA_DIR=/data \
    RADAR_DEVICE=cuda

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 镜像内保持 DAMO_RADAR_DIR=/app，使 RADAR_inference 落在 /app/RADAR_inference
COPY DAMO-RADAR/RADAR_inference ./RADAR_inference
COPY webapp/backend ./backend
COPY --from=frontend /fe/dist ./frontend/dist

# torch 需要与基础镜像 CUDA 版本匹配的轮子，换 CUDA 版本时同步修改这里
RUN python3 -m pip install --no-cache-dir --upgrade pip \
 && python3 -m pip install --no-cache-dir \
      torch==2.4.0+cu121 --index-url https://download.pytorch.org/whl/cu121 \
 && python3 -m pip install --no-cache-dir -r backend/requirements.txt

EXPOSE 8000
VOLUME ["/ckpt", "/data"]

CMD ["python3", "-m", "uvicorn", "main:app", \
     "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
