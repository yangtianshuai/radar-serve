"""RADAR Web 服务配置。

所有路径都可以通过环境变量覆盖，便于在 Linux GPU 服务器上容器化部署。
"""
import os
from pathlib import Path

# ---------------------------------------------------------------- 目录布局
# webapp/backend/config.py -> parents[0]=webapp/backend, [1]=webapp, [2]=仓库根
BACKEND_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BACKEND_DIR.parent
PROJECT_ROOT = WEBAPP_DIR.parent

#: 官方开源项目（RADAR_inference / RADAR_train / ckpt / download_scripts 等）位于仓库根的子目录。
#: 如需调整目录名，改这里一处即可（也可用 DAMO_RADAR_DIR 环境变量覆盖）。
DAMO_RADAR_DIR = Path(
    os.environ.get("DAMO_RADAR_DIR") or (PROJECT_ROOT / "DAMO-RADAR")
).expanduser().resolve()
RADAR_INFER_DIR = DAMO_RADAR_DIR / "RADAR_inference"
DOWNLOAD_SCRIPTS_DIR = DAMO_RADAR_DIR / "download_scripts"


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


# 模型权重 / tokenizer 目录（HuggingFace 下载的内容）
MODEL_ROOT = _env_path("MODEL_ROOT", DAMO_RADAR_DIR / "ckpt")
CONFIGS_ROOT = _env_path("CONFIGS_ROOT", DAMO_RADAR_DIR / "ckpt")

def _default_data_dir() -> Path:
    """运行时数据根目录的默认值。

    Windows 上 SimpleITK 读写**含非 ASCII 字符的绝对路径**会直接失败
    （相对路径不受影响，所以现象很隐蔽）。仓库一旦落在中文目录下，
    上传预处理、切片渲染整条链路都不可用，因此这里回退到用户目录下的
    纯 ASCII 路径。Linux 部署路径一般都是 ASCII，不会触发回退。
    """
    preferred = WEBAPP_DIR / "runtime"
    if preferred.as_posix().isascii():
        return preferred

    fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "radar-runtime"
    print(
        f"[radar] 数据目录 {preferred} 含非 ASCII 字符，SimpleITK 无法读写，"
        f"运行时数据改存 {fallback}（可用 RADAR_DATA_DIR 指定其他位置）",
        flush=True,
    )
    return fallback


# 推理运行时数据（上传、转换后的体数据、结果）
DATA_DIR = _env_path("RADAR_DATA_DIR", _default_data_dir())
UPLOAD_DIR = DATA_DIR / "uploads"
CASE_DIR = DATA_DIR / "cases"
RESULT_DIR = DATA_DIR / "results"

for _d in (UPLOAD_DIR, CASE_DIR, RESULT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 模型文件
CHECKPOINT_NAME = os.environ.get("RADAR_CHECKPOINT", "checkpoint_radar_pretrain.pth")
TEXT_EMBEDDING_NAME = os.environ.get(
    "RADAR_TEXT_EMBEDDING", "infer_text_embedding_radar.pt"
)
BERT_DIR_NAME = os.environ.get("RADAR_BERT_DIR", "bert-base-chinese")

# 推理设备：cuda（推荐）/ cpu（极慢，仅用于连通性自测）
DEVICE = os.environ.get("RADAR_DEVICE", "cuda")

# ---------------------------------------------------------------- 服务参数
# 单个体数据任一空间维度超过该值直接拒绝（对应原实现中的静默 skip）
MAX_SPATIAL_DIM = int(os.environ.get("RADAR_MAX_SPATIAL_DIM", "1000"))
# 上传体积上限（MB）
MAX_UPLOAD_MB = int(os.environ.get("RADAR_MAX_UPLOAD_MB", "2048"))
# 允许同时存在的任务数上限（超出的排队）
MAX_QUEUE_SIZE = int(os.environ.get("RADAR_MAX_QUEUE_SIZE", "64"))
# 结果保留天数，0 表示不清理
RESULT_TTL_DAYS = int(os.environ.get("RADAR_RESULT_TTL_DAYS", "7"))

# 允许的前端来源（生产环境请改成实际域名）
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("RADAR_CORS_ORIGINS", "*").split(",")
    if o.strip()
]


def image_reader_status() -> dict:
    """检查 MONAI 有没有可用的 NIfTI 读取后端。

    这类"可选依赖缺失"的表现特别隐蔽：服务能启动、能上传、预处理也正常
    （那条链路直接走 SimpleITK），直到推理要加载体数据才报
    `LoadImage cannot find a suitable reader`——而错误信息里只列出
    NumpyReader / PILReader，很难联想到是少装了一个包。所以这里启动时就查，
    并挂到 /api/health 上。
    """
    try:
        from monai.transforms import LoadImage
    except Exception as exc:
        return {"ok": False, "readers": [], "detail": f"MONAI 不可用：{exc}"}

    try:
        readers = [type(r).__name__ for r in LoadImage().readers]
    except Exception as exc:
        return {"ok": False, "readers": [], "detail": f"初始化 LoadImage 失败：{exc}"}

    # NumpyReader / PILReader 是恒定存在的兜底，只剩它们说明读不了 NIfTI
    usable = [r for r in readers if r not in ("NumpyReader", "PILReader")]
    if usable:
        return {"ok": True, "readers": readers, "detail": ""}

    return {
        "ok": False,
        "readers": readers,
        "detail": "没有可用于 NIfTI 的 reader，请安装：pip install nibabel",
    }


def ensure_ckpt_ready() -> list[str]:
    """返回缺失的必要模型文件列表，为空表示就绪。"""
    required = [
        MODEL_ROOT / CHECKPOINT_NAME,
        MODEL_ROOT / TEXT_EMBEDDING_NAME,
        CONFIGS_ROOT / BERT_DIR_NAME / "config.json",
    ]
    return [str(p) for p in required if not p.exists()]
