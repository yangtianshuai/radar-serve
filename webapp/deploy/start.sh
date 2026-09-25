#!/usr/bin/env bash
# 非容器方式启动（已在宿主机配好 CUDA + conda/venv 时使用）
# 用法：bash webapp/deploy/start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DAMO_RADAR_DIR="${DAMO_RADAR_DIR:-$ROOT/DAMO-RADAR}"
export MODEL_ROOT="${MODEL_ROOT:-$DAMO_RADAR_DIR/ckpt}"
export CONFIGS_ROOT="${CONFIGS_ROOT:-$DAMO_RADAR_DIR/ckpt}"
export RADAR_DATA_DIR="${RADAR_DATA_DIR:-$ROOT/webapp/runtime}"
export RADAR_DEVICE="${RADAR_DEVICE:-cuda}"
export RADAR_MAX_UPLOAD_MB="${RADAR_MAX_UPLOAD_MB:-2048}"

PORT="${PORT:-8000}"

echo "==> RADAR web service"
echo "    MODEL_ROOT     = $MODEL_ROOT"
echo "    RADAR_DATA_DIR = $RADAR_DATA_DIR"
echo "    DEVICE         = $RADAR_DEVICE"

cd "$ROOT/webapp/backend"
exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --timeout-keep-alive 120
