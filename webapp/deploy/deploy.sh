#!/usr/bin/env bash
# =============================================================================
# RADAR 一键部署脚本（Linux GPU 服务器 / macOS）
#
#   bash webapp/deploy/deploy.sh                    # Docker 模式（Linux 推荐）
#   bash webapp/deploy/deploy.sh native             # 裸机模式（Linux + CUDA）
#   bash webapp/deploy/deploy.sh macos              # macOS 裸机模式（仅 CPU）
#   bash webapp/deploy/deploy.sh docker --download-ckpt   # 缺权重时自动下载
#
# macOS 没有 NVIDIA 直通，docker 模式会被自动改判为 macos 模式。
# 本文件必须以 LF 换行保存。若在 Windows 上编辑过，先执行：
#   sed -i 's/\r$//' webapp/deploy/deploy.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="docker"
DOWNLOAD_CKPT=0

for arg in "$@"; do
  case "$arg" in
    docker|native|macos) MODE="$arg" ;;
    --download-ckpt)   DOWNLOAD_CKPT=1 ;;
    -h|--help)         sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $arg（可用: docker | native | macos | --download-ckpt）" >&2; exit 2 ;;
  esac
done

PORT="${PORT:-8000}"
#: 官方开源项目目录（RADAR_inference / ckpt / download_scripts 都在其下）
DAMO_RADAR_DIR="${DAMO_RADAR_DIR:-$ROOT/DAMO-RADAR}"
CKPT_DIR="${MODEL_ROOT:-$DAMO_RADAR_DIR/ckpt}"
REQUIRED_CKPT=(
  "checkpoint_radar_pretrain.pth"
  "infer_text_embedding_radar.pt"
  "bert-base-chinese/config.json"
)

# ---------------------------------------------------------------- 输出工具
c_ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
c_info() { printf '\033[36m[ .. ]\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; }
die()    { c_err "$*"; exit 1; }
step()   { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 权重检查
missing_ckpt() {
  local f
  for f in "${REQUIRED_CKPT[@]}"; do
    [[ -f "$CKPT_DIR/$f" ]] || echo "$f"
  done
}

ensure_ckpt() {
  step "检查模型权重"
  local missing
  missing="$(missing_ckpt)"

  if [[ -z "$missing" ]]; then
    c_ok "权重齐全（$CKPT_DIR）"
    return 0
  fi

  c_warn "缺少以下文件："
  echo "$missing" | sed 's/^/         /'

  if [[ "$DOWNLOAD_CKPT" -eq 0 ]]; then
    cat <<EOF

请选择其一：
  1) 自动下载：bash webapp/deploy/deploy.sh $MODE --download-ckpt
  2) 手动下载：
       cd DAMO-RADAR/download_scripts && python download_checkpoints.py
     （脚本默认 --local-dir ../ckpt，指向 DAMO-RADAR/ckpt，须在该目录内执行）

EOF
    die "权重不完整，已中止"
  fi

  step "从 HuggingFace 下载权重"
  command -v python3 >/dev/null 2>&1 || die "未找到 python3"
  python3 -m pip install --quiet --upgrade "huggingface_hub" \
    || die "huggingface_hub 安装失败"

  # 直接内联调用 snapshot_download，绕开 download_checkpoints.py 中
  # 在 huggingface_hub>=0.23 已废弃的 local_dir_use_symlinks 参数
  ( cd "$DAMO_RADAR_DIR" && python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="radar-generalist/RADAR",
    repo_type="model",
    local_dir="./ckpt",
    allow_patterns=[
        "checkpoint_radar_pretrain.pth",
        "infer_text_embedding_radar.pt",
        "bert-base-chinese/*",
    ],
)
print("download done")
PY
  ) || die "权重下载失败，请检查网络或改用代理"

  missing="$(missing_ckpt)"
  [[ -z "$missing" ]] || die "下载后仍缺少：$missing"
  c_ok "权重下载完成"
}

# ---------------------------------------------------------------- 前置检查
check_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    c_ok "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
  else
    c_warn "未检测到 nvidia-smi。服务会退化到 CPU，单例耗时可能长达数十分钟。"
    c_warn "如确认只用 CPU，请设置 RADAR_DEVICE=cpu。"
  fi
}

check_docker() {
  command -v docker >/dev/null 2>&1 || die "未安装 Docker"
  docker compose version >/dev/null 2>&1 || die "未安装 docker compose 插件"
  c_ok "Docker $(docker --version | awk '{print $3}' | tr -d ,)"

  if docker info 2>/dev/null | grep -qi nvidia; then
    c_ok "NVIDIA Container Toolkit 已就绪"
  else
    c_warn "未检测到 nvidia runtime。若容器内跑不了 GPU，请安装："
    c_warn "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
  fi
}

check_native() {
  command -v python3 >/dev/null 2>&1 || die "未找到 python3"
  local pyver
  pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  case "$pyver" in
    3.9|3.10|3.11|3.12) c_ok "Python $pyver" ;;
    *) c_warn "Python $pyver 未经验证，建议 3.10" ;;
  esac
  check_gpu
}

check_macos() {
  step "检查运行环境（macOS 裸机模式）"
  command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装：brew install python@3.11"
  local pyver
  pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  case "$pyver" in
    3.9|3.10|3.11) c_ok "Python $pyver" ;;
    # requirements.txt 固定 transformers==4.25，过新的 Python 可能没有对应轮子
    *) c_warn "Python $pyver 未经验证，建议 3.10 / 3.11" ;;
  esac

  command -v node >/dev/null 2>&1 || die "未安装 Node.js（构建前端需要）：brew install node"
  command -v npm  >/dev/null 2>&1 || die "未安装 npm"
  c_ok "Node $(node --version) / npm $(npm --version)"

  c_warn "macOS 无 NVIDIA GPU，服务将以 CPU 运行。RADAR 是 3D 滑窗推理，"
  c_warn "单例耗时可能长达数十分钟——仅用于功能验证与界面联调，勿用于真实批量分析。"
  c_warn "若 HuggingFace 直连不通，下载权重前先：export HF_ENDPOINT=https://hf-mirror.com"
}

check_node() {
  command -v npm >/dev/null 2>&1 || die "未安装 Node.js / npm（构建前端需要）"
  c_ok "Node $(node --version) / npm $(npm --version)"
}

# ---------------------------------------------------------------- 前端构建
build_frontend() {
  step "构建前端"
  cd "$ROOT/webapp/frontend"
  if [[ ! -d node_modules ]]; then
    c_info "安装 npm 依赖..."
    npm install --no-audit --no-fund
  else
    c_info "复用已有 node_modules"
  fi
  c_info "vite build..."
  npm run build
  [[ -f dist/index.html ]] || die "前端构建产物缺失（webapp/frontend/dist/index.html）"
  c_ok "前端已构建到 webapp/frontend/dist"
  cd "$ROOT"
}

# ---------------------------------------------------------------- 启动
start_docker() {
  step "构建并启动容器"
  cd "$ROOT/webapp/deploy"
  docker compose up -d --build
  cd "$ROOT"
  c_ok "容器已启动"
}

install_torch() {
  if python -c "import torch" >/dev/null 2>&1; then
    c_ok "torch 已安装：$(python -c 'import torch; print(torch.__version__)')"
    return 0
  fi

  if [[ "$MODE" == "macos" ]]; then
    # macOS 上 PyPI 官方 wheel 自带 CPU 支持，指定 cuXXX 源会直接找不到发行版
    c_info "安装 torch（macOS 版，约数百 MB）..."
    python -m pip install --quiet torch==2.4.0 || die "torch 安装失败"
    return 0
  fi

  # torch 需按服务器 CUDA 版本选择轮子，装错版本会在启动时才暴露
  c_info "安装 torch（CUDA 12.1 轮子）..."
  c_warn "若服务器 CUDA 版本不是 12.1，请自行替换下方 index-url，例如 cu118 / cu124"
  python -m pip install --quiet torch==2.4.0+cu121 \
    --index-url https://download.pytorch.org/whl/cu121
}

start_native() {
  step "安装后端依赖（仅推理所需，不含训练依赖）"
  cd "$ROOT"

  if [[ ! -d .venv ]]; then
    c_info "创建虚拟环境 .venv ..."
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate

  python -m pip install --quiet --upgrade pip

  install_torch

  python -m pip install --quiet -r webapp/backend/requirements.txt
  c_ok "依赖安装完成"

  step "启动服务"
  export MODEL_ROOT="${MODEL_ROOT:-$CKPT_DIR}"
  export CONFIGS_ROOT="${CONFIGS_ROOT:-$CKPT_DIR}"
  export RADAR_DATA_DIR="${RADAR_DATA_DIR:-$ROOT/webapp/runtime}"
  if [[ "$MODE" == "macos" ]]; then
    # 没有 CUDA，显式指定 CPU，避免误导性的 "cuda" 状态显示在 /api/health 上
    export RADAR_DEVICE="${RADAR_DEVICE:-cpu}"
  else
    export RADAR_DEVICE="${RADAR_DEVICE:-cuda}"
  fi

  mkdir -p "$RADAR_DATA_DIR"
  echo "     日志: $RADAR_DATA_DIR/server.log"
  cd "$ROOT/webapp/backend"
  nohup python -m uvicorn main:app --host 0.0.0.0 --port "$PORT" \
    --timeout-keep-alive 120 > "$RADAR_DATA_DIR/server.log" 2>&1 &
  echo "$!" > "$RADAR_DATA_DIR/server.pid"
  cd "$ROOT"
  c_ok "服务已后台启动（PID $(cat "$RADAR_DATA_DIR/server.pid")）"
}

# ---------------------------------------------------------------- 健康检查
wait_healthy() {
  step "等待服务就绪（模型首次加载需要时间）"
  local url="http://127.0.0.1:$PORT/api/health"
  local i status=""
  for i in $(seq 1 90); do
    sleep 4
    # 用 python3 探测而非 curl，避免宿主未装 curl 时误判为未就绪
    status="$(python3 - "$url" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as resp:
        print(json.load(resp).get("status", ""))
except Exception:
    pass
PY
)"
    case "$status" in
      ready)     c_ok "服务就绪（用时约 $((i * 4))s）"; return 0 ;;
      loading)   printf '.' ;;
      ckpt_missing) die "权重缺失，请检查 MODEL_ROOT=$CKPT_DIR" ;;
      "")        printf '.' ;;
      *)         printf '.' ;;
    esac
  done
  echo
  c_warn "等待超时（$((i * 4))s）。服务可能仍在加载模型，请稍后手动确认："
  echo "        curl $url"
  return 1
}

# ---------------------------------------------------------------- 主流程
main() {
  # macOS 上 Docker 既没有 NVIDIA 直通，基础镜像又只有 amd64（要靠 Rosetta 模拟），
  # 直接跑会卡在 "could not select device driver nvidia"，故自动改走裸机 CPU 路线
  if [[ "$MODE" == "docker" && "$(uname -s)" == "Darwin" ]]; then
    c_warn "检测到 macOS：Docker 模式无 NVIDIA 直通，已自动改用 macos 模式（CPU 推理）"
    MODE="macos"
  fi

  echo "============================================================"
  echo " RADAR 部署  |  模式=$MODE  端口=$PORT"
  echo " 项目根: $ROOT"
  echo "============================================================"

  ensure_ckpt

  if [[ "$MODE" == "docker" ]]; then
    step "检查运行环境"
    check_docker
    # 前端在镜像内构建，宿主机无需 Node
    start_docker
  elif [[ "$MODE" == "macos" ]]; then
    check_macos
    build_frontend
    start_native
  else
    step "检查运行环境"
    check_native
    check_node
    build_frontend
    start_native
  fi

  wait_healthy || true

  cat <<EOF

============================================================
 部署完成
============================================================
 访问地址   http://<服务器IP>:$PORT
 接口文档   http://<服务器IP>:$PORT/docs
 健康检查   curl http://127.0.0.1:$PORT/api/health

 查看日志   $( [[ "$MODE" == docker ]] && echo "cd webapp/deploy && docker compose logs -f" || echo "tail -f webapp/runtime/server.log" )
 停止服务   $( [[ "$MODE" == docker ]] && echo "cd webapp/deploy && docker compose down" || echo "kill \$(cat webapp/runtime/server.pid)" )
 重启服务   bash webapp/deploy/deploy.sh $MODE

 文档       webapp/doc/DEPLOYMENT.md（部署）/ webapp/doc/OPS.md（运维）
============================================================
EOF

  if [[ "$MODE" == "macos" ]]; then
    cat <<EOF

 注意  本机无 NVIDIA GPU，以 CPU 推理，单例可能耗时数十分钟。
       只适合功能验证与界面联调，真实分析请部署到 Linux GPU 服务器。
EOF
  fi
}

main
