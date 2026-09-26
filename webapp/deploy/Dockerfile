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

# 依赖清单先单独拷进来安装，之后才拷代码——这个顺序很关键。
# torch 的 cu121 轮子有 2 GB 以上，只要它所在那层能命中缓存，改一次业务代码
# 就只重建后面几个 COPY 层（秒级）；反过来先拷代码的话，任何改动都会让 pip
# 层失效，于是每次部署都重新下载几个 GB。
COPY webapp/backend/requirements.txt ./backend/requirements.txt

# torch 需要与基础镜像 CUDA 版本匹配的轮子，换 CUDA 版本时同步修改这里
RUN python3 -m pip install --no-cache-dir --upgrade pip \
 && python3 -m pip install --no-cache-dir \
      torch==2.4.0+cu121 --index-url https://download.pytorch.org/whl/cu121 \
 && python3 -m pip install --no-cache-dir -r backend/requirements.txt

# 代码放最后。这几个 COPY 变得最频繁，重活都由上面的层缓存兜住了。
# 镜像内保持 DAMO_RADAR_DIR=/app，使 RADAR_inference 落在 /app/RADAR_inference
COPY DAMO-RADAR/RADAR_inference ./RADAR_inference
COPY webapp/backend ./backend
COPY --from=frontend /fe/dist ./frontend/dist

EXPOSE 8000
VOLUME ["/ckpt", "/data"]

CMD ["python3", "-m", "uvicorn", "main:app", \
     "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
