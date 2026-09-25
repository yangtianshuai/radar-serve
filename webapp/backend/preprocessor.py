"""上传物解析与标准化：DICOM 序列 / NIfTI -> 单文件 HU 体数据 (.nii.gz)。

这里是整个服务最容易出错的一环，两个必做动作：
1. DICOM 必须按 ImagePositionPatient 在层方向上的投影排序，否则体数据是乱序的；
2. DICOM 必须应用 RescaleSlope / RescaleIntercept 转成 CT 值（HU），
   因为 RADAR 的预处理按 HU 做了 [-300, 400] 截断，不转换结果完全不可用。
"""
from __future__ import annotations

import io
import shutil
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import SimpleITK as sitk

NIFTI_SUFFIXES = (".nii", ".nii.gz", ".nii.gz")
DICOM_SUFFIXES = (".dcm", ".dicom", ".ima")


class PreprocessError(Exception):
    """输入数据无法解析为可用的体数据。"""


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def safe_extract(zip_path: Path, dest: Path) -> None:
    """解压 zip 并防御 zip-slip 路径穿越。"""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest)):
                raise PreprocessError(f"压缩包内含有非法路径: {info.filename}")
        zf.extractall(dest)


def _read_header(path: Path) -> sitk.ImageFileReader | None:
    """尝试把文件当 DICOM 读取头部信息，失败返回 None。"""
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.LoadPrivateTagsOn()
    try:
        reader.ReadImageInformation()
    except Exception:
        return None
    if not reader.HasMetaDataKey("0008|0060"):  # Modality
        return None
    return reader


def _meta_float(reader: sitk.ImageFileReader, key: str, default: float) -> float:
    if not reader.HasMetaDataKey(key):
        return default
    try:
        return float(str(reader.GetMetaData(key)).strip())
    except (ValueError, TypeError):
        return default


def _meta_str(reader: sitk.ImageFileReader, key: str, default: str = "") -> str:
    if not reader.HasMetaDataKey(key):
        return default
    return str(reader.GetMetaData(key)).strip()


def _slice_position(reader: sitk.ImageFileReader) -> float | None:
    """用 ImagePositionPatient 在层法向上的投影距离作为排序键。"""
    if not (reader.HasMetaDataKey("0020|0032") and reader.HasMetaDataKey("0020|0037")):
        return None
    try:
        ipp = [float(v) for v in _meta_str(reader, "0020|0032").split("\\")]
        iop = [float(v) for v in _meta_str(reader, "0020|0037").split("\\")]
    except ValueError:
        return None
    if len(ipp) != 3 or len(iop) != 6:
        return None
    return _dot(ipp, _cross(iop[:3], iop[3:]))


def _sort_dicom_files(files: list[Path]) -> list[Path]:
    """按层位置排序；缺失定位信息时退化为 InstanceNumber，再退化为文件名。"""
    keys: list[tuple[float, float, str]] = []
    for f in files:
        reader = _read_header(f)
        if reader is None:
            keys.append((0.0, 0.0, f.name))
            continue
        pos = _slice_position(reader)
        inst = _meta_float(reader, "0020|0013", 0.0)  # InstanceNumber
        if pos is None:
            keys.append((0.0, inst, f.name))
        else:
            keys.append((1.0, pos, f.name))
    return [f for _, f in sorted(zip(keys, files), key=lambda kv: kv[0])]


def _collect_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


@dataclass
class VolumeInfo:
    """标准化之后的体数据描述。"""

    path: Path
    source_kind: str  # "nifti" | "dicom"
    shape_zyx: tuple[int, int, int]
    spacing_zyx: tuple[float, float, float]
    hu_range: tuple[float, float]
    series_id: str | None = None
    series_description: str | None = None
    rescale_slope: float = 1.0
    rescale_intercept: float = 0.0
    warnings: list[str] = field(default_factory=list)


def classify_upload(raw_dir: Path) -> str:
    """判断上传内容是 NIfTI 还是 DICOM 序列。"""
    files = _collect_files(raw_dir)
    if not files:
        raise PreprocessError("上传内容为空")

    for p in files:
        name = p.name.lower()
        if name.endswith(".nii") or name.endswith(".nii.gz"):
            return "nifti"
    for p in files:
        if p.suffix.lower() in DICOM_SUFFIXES:
            return "dicom"
    # 无扩展名的 DICOM（常见于国产设备导出）
    for p in files[:50]:
        if _read_header(p) is not None:
            return "dicom"
    raise PreprocessError(
        "无法识别上传内容：未找到 .nii/.nii.gz 文件，也没有可用的 DICOM 序列"
    )


def list_dicom_series(dicom_dir: Path) -> list[dict]:
    """列出目录内所有 DICOM 序列，供前端选择期相。"""
    reader = sitk.ImageSeriesReader()
    try:
        series_ids = list(reader.GetGDCMSeriesIDs(str(dicom_dir)))
    except Exception as exc:  # pragma: no cover - 依赖具体数据
        raise PreprocessError(f"读取 DICOM 目录失败: {exc}") from exc

    series: list[dict] = []
    for sid in series_ids:
        files = [Path(p) for p in reader.GetGDCMSeriesFileNames(str(dicom_dir), sid)]
        if not files:
            continue
        first = _read_header(files[0])
        desc = _meta_str(first, "0008|103e") if first else ""
        number = _meta_float(first, "0020|0011", 0.0) if first else 0.0
        series.append(
            {
                "series_id": sid,
                "series_number": int(number),
                "series_description": desc or "(无描述)",
                "num_slices": len(files),
            }
        )
    series.sort(key=lambda s: (-s["num_slices"], s["series_number"]))
    return series


def _convert_dicom(dicom_dir: Path, series_id: str | None, out_path: Path) -> VolumeInfo:
    reader = sitk.ImageSeriesReader()
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()

    if series_id is None:
        available = list_dicom_series(dicom_dir)
        if not available:
            raise PreprocessError("DICOM 目录中未找到可用序列")
        series_id = available[0]["series_id"]

    raw_files = [Path(p) for p in reader.GetGDCMSeriesFileNames(str(dicom_dir), series_id)]
    if len(raw_files) < 5:
        raise PreprocessError(f"序列层数过少（{len(raw_files)} 层），无法构成腹部 CT 体数据")

    files = _sort_dicom_files(raw_files)
    reader.SetFileNames([str(p) for p in files])

    try:
        image = reader.Execute()
    except Exception as exc:
        raise PreprocessError(f"DICOM 序列读取失败: {exc}") from exc

    head = _read_header(files[0])
    slope = _meta_float(head, "0028|1053", 1.0) if head else 1.0
    intercept = _meta_float(head, "0028|1054", 0.0) if head else 0.0
    description = _meta_str(head, "0008|103e") if head else ""

    warnings: list[str] = []

    # ---- 关键：像素值 -> HU
    image = sitk.Cast(image, sitk.sitkFloat32)
    if abs(slope - 1.0) > 1e-6 or abs(intercept) > 1e-6:
        image = sitk.Add(sitk.Multiply(image, slope), intercept)
    else:
        warnings.append(
            "DICOM 未声明 RescaleSlope/Intercept，已假定像素值即为 HU，请确认设备导出设置"
        )

    array = sitk.GetArrayFromImage(image)  # [Z, Y, X]
    if array.size == 0:
        raise PreprocessError("DICOM 序列转换后为空")

    hu_min, hu_max = float(array.min()), float(array.max())
    # HU 合理性检查：空气约 -1000，骨皮质可达 +1000 以上
    if hu_min > -500 or hu_max < 200:
        warnings.append(
            f"CT 值范围异常（{hu_min:.0f} ~ {hu_max:.0f} HU），"
            "可能不是增强腹部 CT 或 HU 标定不正确，结果仅供参考"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(out_path), True)

    size = image.GetSize()  # (X, Y, Z)
    spacing = image.GetSpacing()
    return VolumeInfo(
        path=out_path,
        source_kind="dicom",
        shape_zyx=(int(size[2]), int(size[1]), int(size[0])),
        spacing_zyx=(float(spacing[2]), float(spacing[1]), float(spacing[0])),
        hu_range=(hu_min, hu_max),
        series_id=series_id,
        series_description=description,
        rescale_slope=slope,
        rescale_intercept=intercept,
        warnings=warnings,
    )


def _copy_nifti(raw_dir: Path, out_path: Path) -> VolumeInfo:
    candidates = [
        p
        for p in _collect_files(raw_dir)
        if p.name.lower().endswith(".nii") or p.name.lower().endswith(".nii.gz")
    ]
    if not candidates:
        raise PreprocessError("未找到 NIfTI 文件")
    candidates.sort(key=lambda p: -p.stat().st_size)

    src = candidates[0]
    warnings: list[str] = []
    if len(candidates) > 1:
        warnings.append(f"目录内有 {len(candidates)} 个 NIfTI 文件，已使用体积最大的 {src.name}")

    image = sitk.ReadImage(str(src))
    array = sitk.GetArrayFromImage(image)

    hu_min, hu_max = float(array.min()), float(array.max())
    if 0.0 <= hu_min and hu_max <= 1.0:
        warnings.append(
            "体数据已归一化到 [0, 1]，RADAR 期望 HU 值输入（会按 [-300, 400] 截断），结果不可信"
        )
    elif 0.0 <= hu_min and hu_max <= 255.0:
        warnings.append("体数据疑似 8 位灰度图而非 CT 值，结果不可信")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if src.name.lower().endswith(".gz"):
        shutil.copyfile(src, out_path)
    else:
        sitk.WriteImage(sitk.Cast(image, sitk.sitkFloat32), str(out_path), True)

    size = image.GetSize()
    spacing = image.GetSpacing()
    return VolumeInfo(
        path=out_path,
        source_kind="nifti",
        shape_zyx=(int(size[2]), int(size[1]), int(size[0])),
        spacing_zyx=(float(spacing[2]), float(spacing[1]), float(spacing[0])),
        hu_range=(hu_min, hu_max),
        warnings=warnings,
    )


def prepare_volume(raw_dir: Path, out_path: Path, series_id: str | None = None) -> VolumeInfo:
    """把上传目录标准化为单个 .nii.gz。"""
    kind = classify_upload(raw_dir)
    if kind == "dicom":
        return _convert_dicom(raw_dir, series_id, out_path)
    return _copy_nifti(raw_dir, out_path)


def make_preview_png(volume_path: Path, out_png: Path, num_slices: int = 3) -> list[str]:
    """生成若干等距轴向切片 PNG（腹部窗 W400/L50），供前端预览。"""
    image = sitk.ReadImage(str(volume_path))
    array = sitk.GetArrayFromImage(image).astype(np.float32)  # [Z, Y, X]
    depth = array.shape[0]
    if depth == 0:
        return []

    from PIL import Image

    out_png.parent.mkdir(parents=True, exist_ok=True)
    lo, hi = -150.0, 250.0  # 腹部软组织窗
    idxs = np.linspace(0, depth - 1, num=min(num_slices, depth)).astype(int)

    paths: list[str] = []
    for i, z in enumerate(idxs):
        sl = array[z]
        # 部分设备在 z 轴上是上下翻转的，这里保持与 SimpleITK 写出一致
        sl = np.clip((sl - lo) / (hi - lo), 0.0, 1.0) * 255.0
        img = Image.fromarray(sl.astype(np.uint8))
        p = out_png.parent / f"{out_png.stem}_{i}.png"
        img.save(p)
        paths.append(p.name)
    return paths


# ---------------------------------------------------------------- 切片浏览
#: 窗宽窗位预设，(窗宽 WW, 窗位 WL)，单位 HU
WINDOW_PRESETS: dict[str, tuple[float, float]] = {
    "abdomen": (400.0, 50.0),     # 腹部软组织（与预览图一致）
    "liver": (150.0, 30.0),       # 肝脏
    "mediastinum": (350.0, 50.0),  # 纵隔
    "lung": (1500.0, -600.0),     # 肺
    "bone": (2000.0, 400.0),      # 骨窗
    "wide": (2000.0, 0.0),        # 全窗，便于观察整体轮廓
}

#: 已解析体数据的内存缓存。翻页时若每次都重读几十 MB 的 .nii.gz 会明显卡顿。
_VOLUME_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_VOLUME_CACHE_LIMIT = 3
_VOLUME_CACHE_LOCK = threading.Lock()


def load_volume_cached(volume_path: Path) -> np.ndarray | None:
    """读取体数据并缓存（LRU，最多保留若干例）。文件不存在返回 None。"""
    key = str(volume_path)

    with _VOLUME_CACHE_LOCK:
        cached = _VOLUME_CACHE.get(key)
        if cached is not None:
            _VOLUME_CACHE.move_to_end(key)
            return cached

    if not volume_path.exists():
        return None

    # 读盘放在锁外，避免大文件 IO 阻塞其他请求
    image = sitk.ReadImage(str(volume_path))
    array = sitk.GetArrayFromImage(image).astype(np.float32)  # [Z, Y, X]

    with _VOLUME_CACHE_LOCK:
        _VOLUME_CACHE[key] = array
        _VOLUME_CACHE.move_to_end(key)
        while len(_VOLUME_CACHE) > _VOLUME_CACHE_LIMIT:
            _VOLUME_CACHE.popitem(last=False)
    return array


#: 支持的成像平面。冠状面固定行号、矢状面固定列号，轴位固定层号
PLANES = ("axial", "coronal", "sagittal")

#: MPR 显示图的最长边上限（像素）。各向异性缩放是**必须**等比的，
#: 但厚层数据放大后可能非常大（1000 层 × 5mm = 5000 像素高），这里只做
#: 输出尺寸兜底，不限制比例本身——否则冠状面测量会出现系统性偏差。
_MAX_MPR_SIDE = 1600


def _spacing_pair(
    spacing_zyx: list[float] | None, row_axis: int, col_axis: int
) -> tuple[float, float]:
    """取某个平面的（行间距, 列间距）；没有可信间距时退化为 1:1。"""
    if spacing_zyx and len(spacing_zyx) == 3:
        row = float(spacing_zyx[row_axis])
        col = float(spacing_zyx[col_axis])
        if row > 0 and col > 0:
            return row, col
    return 1.0, 1.0


def _resize_isotropic(img, row_spacing: float, col_spacing: float):
    """把切片按体素间距缩放成等比显示。

    各向异性数据直接显示会严重变形：冠状面上 Z 方向是 5mm、X 方向是 1mm，
    不缩放的话一个 87 层的体数据会画成 87 像素高的细条。
    """
    if abs(row_spacing - col_spacing) < 1e-6:
        return img

    from PIL import Image  # 延迟导入，仅浏览影像时用到

    width, height = img.size
    scale = max(row_spacing, col_spacing) / min(row_spacing, col_spacing)
    if row_spacing > col_spacing:
        new_size = (width, int(round(height * scale)))
    else:
        new_size = (int(round(width * scale)), height)

    longest = max(new_size)
    if longest > _MAX_MPR_SIDE:
        shrink = _MAX_MPR_SIDE / longest
        new_size = (max(1, int(new_size[0] * shrink)), max(1, int(new_size[1] * shrink)))

    return img.resize(new_size, Image.BILINEAR)


def render_slice_png(
    array: np.ndarray,
    index: int,
    window: str = "abdomen",
    ww: float | None = None,
    wl: float | None = None,
    plane: str = "axial",
    spacing_zyx: list[float] | None = None,
) -> bytes | None:
    """把体数据的某一层渲染为 PNG 字节；索引越界返回 None。

    - `ww` / `wl` 显式给出时优先于 `window` 预设；
    - `plane` 为 axial / coronal / sagittal，冠状与矢状面按体素间距等比缩放，
      避免各向异性数据被拉伸变形；
    - `spacing_zyx` 即 [层厚, 行间距, 列间距]，缺失时按 1:1 渲染。
    """
    shape = array.shape
    if plane == "coronal":
        if not 0 <= index < shape[1]:
            return None
        gray = array[:, index, :]  # (Z, X)
        row_spacing, col_spacing = _spacing_pair(spacing_zyx, 0, 2)
    elif plane == "sagittal":
        if not 0 <= index < shape[2]:
            return None
        gray = array[:, :, index]  # (Z, Y)
        row_spacing, col_spacing = _spacing_pair(spacing_zyx, 0, 1)
    else:
        if not 0 <= index < shape[0]:
            return None
        gray = array[index]  # (Y, X)
        row_spacing, col_spacing = _spacing_pair(spacing_zyx, 1, 2)

    if ww is None or wl is None:
        ww, wl = WINDOW_PRESETS.get(window, WINDOW_PRESETS["abdomen"])

    hi = wl + ww / 2.0
    lo = wl - ww / 2.0
    if hi <= lo:
        hi = lo + 1.0

    from PIL import Image  # 延迟导入，仅浏览影像时用到

    scaled = np.clip((gray - lo) / (hi - lo), 0.0, 1.0) * 255.0
    img = Image.fromarray(scaled.astype(np.uint8))
    img = _resize_isotropic(img, row_spacing, col_spacing)

    buf = io.BytesIO()
    # 压缩级别调低：翻页是高频操作，优先保证响应速度
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def volume_depth(volume_path: Path) -> int:
    """只读取元数据获取层数，不加载整个体数据。"""
    if not volume_path.exists():
        return 0
    try:
        return int(sitk.ReadImage(str(volume_path)).GetSize()[2])
    except Exception:
        return 0
