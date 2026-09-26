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
#: 需要从 HuggingFace 下载的主权重（仓库 radar-generalist/RADAR 里确实有这些文件）
HF_CKPT=(
  "checkpoint_radar_pretrain.pth"
  "bert-base-chinese/config.json"
)
#: 随本仓库一起分发的模型资产。**HuggingFace 上没有这个文件**（体积很小，
#: 约 0.33 MB，属上游发布的一部分），所以自动下载救不了它——缺了只可能是
#: 仓库内容不完整，比如 git clone 没拉全，或部署打包时漏掉了 DAMO-RADAR/ckpt/。
REPO_CKPT=(
  "infer_text_embedding_radar.pt"
)
REQUIRED_CKPT=("${HF_CKPT[@]}" "${REPO_CKPT[@]}")

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

#: 下载脚本内容，写进临时文件后由宿主机或容器执行。直接用 snapshot_download
#: 而不是仓库里的 download_checkpoints.py，是为了绕开后者在
#: huggingface_hub>=0.23 已废弃的 local_dir_use_symlinks 参数——那个脚本在新
#: 版本上会直接 TypeError。
write_download_script() {
  cat > "$1" <<'PY'
import os

# Xet 是 HuggingFace 新的内容寻址存储，大文件会改走 cas-server.xethub.hf.co。
# hf-mirror.com 这类镜像并不支持它（直接 401 Unauthorized），国内网络也基本
# 连不上那个域名。除非调用方显式指定，一律禁用，回退到传统 LFS 下载。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# HF_ENDPOINT 被设成**空串**时（`export HF_ENDPOINT=`，或 docker 的
# `-e HF_ENDPOINT=`），huggingface_hub 会拿空字符串当端点，拼出
# `/api/models/...` 这种没有协议的 URL，抛 UnsupportedProtocol——
# 真实原因埋在 traceback 最深处，极难排查。这里直接清掉让它回退到默认端点。
if not os.environ.get("HF_ENDPOINT", "").strip():
    os.environ.pop("HF_ENDPOINT", None)

# 注意：这些环境变量必须在 import huggingface_hub 之前设好，模块导入时就会求值
from huggingface_hub import snapshot_download  # noqa: E402

print(
    "HF endpoint: "
    + (os.environ.get("HF_ENDPOINT") or "https://huggingface.co")
    + " | xet: "
    + ("off" if os.environ.get("HF_HUB_DISABLE_XET") == "1" else "on"),
    flush=True,
)

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
}

pip_usable() { python3 -m pip --version >/dev/null 2>&1; }

# 让宿主机 python3 具备 pip。Debian/Ubuntu 的 python3 不带 pip、还移除了
# ensurepip（都是独立包），云主机上极常见，所以这里必须能自己救回来。
ensure_pip() {
  if pip_usable; then
    c_ok "pip 可用"
    return 0
  fi

  c_warn "宿主机 python3 没有 pip 模块，尝试自动补齐"

  python3 -m ensurepip --default-pip >/dev/null 2>&1 || true
  if pip_usable; then
    c_ok "已通过 ensurepip 装好 pip"
    return 0
  fi

  # 官方引导脚本：不依赖系统包管理器，也不需要 root
  local tmp
  tmp="$(mktemp -d)"
  if curl -fsSL --connect-timeout 20 https://bootstrap.pypa.io/get-pip.py -o "$tmp/get-pip.py" 2>/dev/null \
     || wget -q --timeout=20 -O "$tmp/get-pip.py" https://bootstrap.pypa.io/get-pip.py 2>/dev/null; then
    python3 "$tmp/get-pip.py" --user --quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"

  if pip_usable; then
    c_ok "已通过 get-pip.py 装好 pip"
    return 0
  fi

  c_err "无法自动安装 pip。请在服务器上手动执行其一后重新部署："
  c_err "  Debian/Ubuntu : sudo apt-get update && sudo apt-get install -y python3-pip"
  c_err "  RHEL / CentOS : sudo yum install -y python3-pip"
  c_err "  通用          : curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 - --user"
  return 1
}

#: 容器下载通道用的镜像
DOWNLOAD_IMAGE="python:3.11-slim"

# 在容器里跑一次下载。endpoint 为空时**不传** HF_ENDPOINT，让容器用官方默认值：
# 传空串会让 huggingface_hub 拼出缺协议的 URL（见上面 write_download_script 的说明）。
container_download_once() {
  local script="$1" endpoint="${2:-}"
  local -a env_args=()
  if [[ -n "$endpoint" ]]; then
    env_args+=(-e "HF_ENDPOINT=$endpoint")
  fi

  # --user 让容器以当前用户身份写文件：否则 ckpt 归 root，之后服务以非 root
  # 运行时读不了。HOME 指到 /tmp，pip --user 才有地方落盘。
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    ${env_args[@]+"${env_args[@]}"} \
    -v "$DAMO_RADAR_DIR:/work" \
    -v "$script:/dl.py:ro" \
    -w /work \
    "$DOWNLOAD_IMAGE" \
    bash -c 'pip install -q --user huggingface_hub && python /dl.py'
}

# 备用通道：借容器里的 pip 下载权重，宿主机一个包都不用装
download_via_container() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 || return 1

  local tmp
  tmp="$(mktemp -d)"
  write_download_script "$tmp/dl.py"

  c_info "改用容器下载权重（镜像 $DOWNLOAD_IMAGE，宿主机无需 pip）"
  if [[ -n "${HF_ENDPOINT:-}" ]]; then
    c_info "容器内 HF 端点：$HF_ENDPOINT"
  fi

  if container_download_once "$tmp/dl.py" "${HF_ENDPOINT:-}"; then
    rm -rf "$tmp"
    return 0
  fi

  # 官方端点失败就换镜像再试一次。容器与宿主机的网络可达性未必一致
  # （比如宿主机配了代理而容器没有），所以不能只凭前面那次探测下结论。
  if [[ "${HF_ENDPOINT:-}" != *"hf-mirror"* ]]; then
    c_warn "容器下载失败，改用镜像重试：$HF_MIRROR_ENDPOINT"
    if container_download_once "$tmp/dl.py" "$HF_MIRROR_ENDPOINT"; then
      rm -rf "$tmp"
      return 0
    fi
  fi

  rm -rf "$tmp"
  return 1
}

#: 社区维护的 HuggingFace 镜像。国内直连 huggingface.co 基本不可达，而且失败
#: 方式是长时间挂起而不是立刻报错，用户很容易误判成"卡死"。
HF_MIRROR_ENDPOINT="https://hf-mirror.com"

# 没显式指定端点时先探一次连通性，不通就自动切镜像
pick_hf_endpoint() {
  if [[ -n "${HF_ENDPOINT:-}" ]]; then
    c_info "HF 端点（外部指定）：$HF_ENDPOINT"
    return 0
  fi

  if curl -fsS --connect-timeout 6 -o /dev/null https://huggingface.co/ 2>/dev/null; then
    c_ok "huggingface.co 直连可用"
    return 0
  fi

  export HF_ENDPOINT="$HF_MIRROR_ENDPOINT"
  c_warn "huggingface.co 连不上，已自动改用镜像：$HF_ENDPOINT"
  c_warn "若镜像也不通，说明这台机器访问不了公网，请配好代理再试"
}

# 下载权重；直连失败时自动换镜像重试一次
download_weights() {
  local script="$1"

  if ( cd "$DAMO_RADAR_DIR" && python3 "$script" ); then
    return 0
  fi

  if [[ "${HF_ENDPOINT:-}" != *"hf-mirror"* ]]; then
    export HF_ENDPOINT="$HF_MIRROR_ENDPOINT"
    c_warn "下载失败，改用镜像重试：$HF_ENDPOINT"
    ( cd "$DAMO_RADAR_DIR" && python3 "$script" )
    return $?
  fi
  return 1
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

  # 先把「随仓库分发」的那几个挑出来单独处理：它们在 HuggingFace 上不存在，
  # 提示用户去下载只会白折腾一轮（下完仍然缺，报错也看不懂为什么）
  local repo_missing="" f
  for f in "${REPO_CKPT[@]}"; do
    [[ -f "$CKPT_DIR/$f" ]] || repo_missing="$repo_missing $f"
  done

  if [[ -n "$repo_missing" ]]; then
    c_err "其中$repo_missing 属于**随本仓库分发**的模型资产，HuggingFace 上没有，"
    c_err "下载无法补齐。请从项目仓库的 DAMO-RADAR/ckpt/ 拷贝到 $CKPT_DIR/"
    c_err "（用「图形化部署助手」上传代码会自动带上它，见 webapp/doc/DEPLOYMENT.md §3.2）"
    die "仓库内容不完整，已中止"
  fi

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
  pick_hf_endpoint

  local dl_script
  dl_script="$(mktemp)"
  write_download_script "$dl_script"

  if pip_usable || ensure_pip; then
    # --user 优先：不需要 root，也不污染系统 site-packages；装不上再退回
    # 系统级（少数发行版禁用了 --user）。pypi 慢的话可先设：
    #   export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
    if ! python3 -m pip install --quiet --user --upgrade huggingface_hub >/dev/null 2>&1 \
       && ! python3 -m pip install --quiet --upgrade huggingface_hub >/dev/null 2>&1; then
      rm -f "$dl_script"
      c_err "huggingface_hub 安装失败，可手动执行后重试："
      c_err "  python3 -m pip install --user huggingface_hub"
      die "依赖安装失败"
    fi

    download_weights "$dl_script" \
      || { rm -f "$dl_script"; die "权重下载失败。上方 traceback 的最后一行才是真实原因"; }
  elif download_via_container; then
    : # 容器通道已完成下载
  else
    rm -f "$dl_script"
    die "无法下载权重：宿主机没有 pip，容器通道也不可用（原因见上方提示）"
  fi
  rm -f "$dl_script"

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
  # venv 在 Debian/Ubuntu 上同样是独立包（python3-venv），缺失时会在建虚拟环境
  # 那一步才报错，而那时已经走到依赖安装中途，提前查出来更省事
  python3 -m venv --help >/dev/null 2>&1 \
    || die "python3 缺少 venv 模块，请先安装：sudo apt-get install -y python3-venv"
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
  c_info "镜像构建日志里出现 CACHED 就说明那一层复用了缓存，没有重新下载"
  cd "$ROOT/webapp/deploy"
  docker compose up -d --build
  cd "$ROOT"
  c_ok "容器已启动"

  # 容器起来后确认它真的拿到了 GPU。只看宿主机的 nvidia-smi 是不够的：
  # compose 的 deploy.resources.reservations 在旧版 compose 上会被**静默忽略**，
  # 结果就是"明明有 GPU 却在 CPU 上慢慢跑"，而且一路没有任何报错。
  local i
  for i in $(seq 1 10); do
    if docker exec radarserve nvidia-smi >/dev/null 2>&1; then
      c_ok "容器内 GPU 可用"
      return 0
    fi
    sleep 2
  done

  c_warn "容器内看不到 GPU，推理会退化到 CPU。请确认："
  c_warn "  1) 宿主机已装 NVIDIA Container Toolkit"
  c_warn "  2) docker-compose.yml 的 deploy.resources.reservations.devices 生效"
  c_warn "     （compose 版本过旧会被静默忽略，可改用 gpus: all，需 v2.30+）"
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
  c_warn "若服务器 CUDA 版本不是 12.1，请自行替换下方 index-url，例如 cu118 / cu124"
  c_info "安装 torch 2.4.0+cu121（轮子约 2.5 GB，视网络可能数分钟到数十分钟）"
  # 刻意不加 --quiet：这是整个部署里最大的一笔下载，必须让进度露出来，
  # 否则界面长时间毫无动静，会被当成卡死
  python -m pip install torch==2.4.0+cu121 \
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
