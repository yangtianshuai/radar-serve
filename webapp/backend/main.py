"""RADAR 腹部 CT 推理服务（FastAPI）。

启动（Linux GPU 服务器）：
    cd webapp/backend
    pip install -r requirements.txt
    MODEL_ROOT=/path/to/ckpt uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import csv
import io
import math
import mimetypes
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import config
import numpy as np
import preprocessor
import torch
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analytics import auc as roc_auc
from analytics import best_youden, bootstrap_auc_ci, roc_curve
from findings_meta import (
    ENGLISH_MAPPING,
    REFERENCE_AUC,
    REFERENCE_AUC_NOTE,
    TEST_ITEMS,
    finding_of,
    grouped_items,
    organ_of,
)
from schemas import (
    CaseAnnotation,
    CaseCreateResponse,
    CaseListResponse,
    CaseStatus,
    FindingsMeta,
    HealthResponse,
    SeriesOption,
)
from task_manager import (
    ANNOTATION_LABELS,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    QueueFullError,
    task_manager,
)

DISCLAIMER = (
    "本系统输出的是 AI 预测的病灶阳性概率，仅用于科研与辅助阅片，"
    "不构成任何临床诊断意见，不能替代执业医师的判断。"
)


def _warmup() -> None:
    """后台预热模型，避免首个请求等待几十秒。"""
    try:
        from radar_engine import RadarEngine

        RadarEngine.get()
        print("[radar] model warmup done")
    except Exception as exc:  # noqa: BLE001
        print(f"[radar] model warmup failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 图像读取后端缺失时服务照样能起、能上传，直到推理加载体数据才炸，
    # 而且报错信息很难懂。所以在启动日志里先讲清楚。
    readers = config.image_reader_status()
    if readers["ok"]:
        print(f"[radar] image readers: {', '.join(readers['readers'])}", flush=True)
    else:
        print(f"[radar] 警告：{readers['detail']}", flush=True)

    await task_manager.start()
    threading.Thread(target=_warmup, daemon=True).start()
    yield
    await task_manager.stop()


app = FastAPI(title="RADAR Abdominal CT Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def cache_headers(request, call_next):
    """给静态资源补上缓存头。

    StaticFiles 默认不发 Cache-Control，浏览器就按 Last-Modified 走启发式缓存，
    一旦某次响应头不对（例如 Windows 上 JS 曾被发成 text/plain），
    错误响应会被长时间复用，刷新也白屏。这里显式区分：
    - /assets/* 文件名自带内容 hash，可以长期强缓存；
    - index.html 必须每次回源校验，否则前端更新后用户拿不到新版本。
    """
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and response.status_code == 200:
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
    return response


def _safe_name(name: str) -> str:
    name = Path(name or "upload").name
    return name or "upload.bin"


def _flat_index_name(index: int, filename: str) -> str:
    """多文件上传时把目录结构拍平并加序号前缀。

    两个原因：
    1. SimpleITK 的 DICOM 序列扫描不递归子目录，拍平后才能被识别；
    2. 加序号避免不同子目录下的同名文件互相覆盖。
    序号与影像内容无关——DICOM 排序依赖的是 ImagePositionPatient。
    """
    return f"{index:05d}_{_safe_name(filename)}"


async def _save_upload(upload: UploadFile, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with dest.open("wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {config.MAX_UPLOAD_MB} MB 上限",
                )
            f.write(chunk)
    if written == 0:
        raise HTTPException(status_code=400, detail="上传内容为空")
    return written


@app.post("/api/cases", response_model=CaseCreateResponse)
async def create_case(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] = File(default=[]),
    series_id: str | None = Form(default=None),
):
    """上传影像并排队推理，返回 case_id 供轮询。

    支持三种输入方式：
    1. 单个 `.nii` / `.nii.gz`（`file` 字段）
    2. 打包 DICOM 序列的 `.zip`（`file` 字段）
    3. 直接多选的 DICOM 文件或整个文件夹（`files` 字段），免去手工打包
    """
    uploads: list[UploadFile] = ([file] if file is not None else []) + list(files)
    if not uploads:
        raise HTTPException(status_code=400, detail="未收到任何文件")

    case_id, upload_dir = task_manager.create_case(_safe_name(uploads[0].filename))
    raw_dir = upload_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        if len(uploads) == 1:
            dest = raw_dir / _safe_name(uploads[0].filename)
            await _save_upload(uploads[0], dest)

            if zipfile.is_zipfile(dest):
                try:
                    preprocessor.safe_extract(dest, raw_dir)
                except preprocessor.PreprocessError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                dest.unlink(missing_ok=True)
        else:
            # 文件夹上传时浏览器会带上相对路径，按文件名排序保证落盘顺序稳定
            ordered = sorted(uploads, key=lambda u: u.filename or "")
            for i, item in enumerate(ordered):
                await _save_upload(item, raw_dir / _flat_index_name(i, item.filename or ""))

        kind = preprocessor.classify_upload(raw_dir)
        chosen_series: str | None = None

        if kind == "dicom":
            series = preprocessor.list_dicom_series(raw_dir)
            if not series:
                raise HTTPException(status_code=400, detail="DICOM 目录中未找到可用序列")
            if series_id is not None and all(s["series_id"] != series_id for s in series):
                raise HTTPException(status_code=400, detail=f"序列 {series_id} 不存在")
            if len(series) > 1 and series_id is None:
                task_manager.set_pending_series(case_id, series)
                return CaseCreateResponse(
                    case_id=case_id,
                    status=STATUS_PENDING,
                    message="检测到多个 DICOM 序列，请选择期相后继续",
                    available_series=[SeriesOption(**s) for s in series],
                )
            chosen_series = series_id or series[0]["series_id"]

        task_manager.enqueue(case_id, chosen_series)
        return CaseCreateResponse(case_id=case_id, status="queued", message="已排队")

    except QueueFullError as exc:
        # 队列已满，丢弃本次上传，避免占用磁盘
        task_manager.delete(case_id)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except HTTPException:
        task_manager.delete(case_id)
        raise
    except preprocessor.PreprocessError as exc:
        task_manager.delete(case_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        task_manager.delete(case_id)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


class SeriesSelectBody(BaseModel):
    series_id: str


@app.post("/api/cases/{case_id}/series", response_model=CaseCreateResponse)
async def select_series(case_id: str, body: SeriesSelectBody):
    """多期相 DICOM 时确认使用哪个序列。"""
    rec = task_manager.get(case_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="病例不存在")
    if rec["status"] != STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"当前状态 {rec['status']} 不可再选序列")

    valid = {s["series_id"] for s in rec.get("available_series", [])}
    if valid and body.series_id not in valid:
        raise HTTPException(status_code=400, detail="序列不存在")

    try:
        task_manager.enqueue(case_id, body.series_id)
    except QueueFullError as exc:
        # 已上传的体数据保留，用户可稍后重试入队
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return CaseCreateResponse(case_id=case_id, status="queued", message="已排队")


@app.get("/api/cases/{case_id}", response_model=CaseStatus)
async def get_case(case_id: str):
    rec = task_manager.get(case_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="病例不存在")
    return task_manager.to_status(rec)


@app.delete("/api/cases/{case_id}")
async def delete_case(case_id: str):
    if not task_manager.delete(case_id):
        raise HTTPException(status_code=404, detail="病例不存在")
    return {"ok": True}


@app.post("/api/cases/{case_id}/cancel")
async def cancel_case(case_id: str):
    """请求取消排队中或推理中的任务。

    取消只改状态、保留已产出的文件（用户可自行删除）；
    已结束的任务返回 409。
    """
    if task_manager.get(case_id) is None:
        raise HTTPException(status_code=404, detail="病例不存在")
    if not task_manager.cancel(case_id):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")
    return {"ok": True}


class AnnotationBody(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)
    reader: str | None = None
    remark: str | None = None


@app.patch("/api/cases/{case_id}/annotation", response_model=CaseAnnotation)
async def update_annotation(case_id: str, body: AnnotationBody):
    """录入科研参考标准（金标准）。

    `labels` 是**增量合并**：`{"肝_脂肪肝": "positive"}` 只改这一条，
    传空串则删除该条标注。未出现的标签视为**尚未判读**，与阴性是两回事——
    只有已判读的标签才构成 AUC 计算的有效样本。
    """
    if task_manager.get(case_id) is None:
        raise HTTPException(status_code=404, detail="病例不存在")

    bad_values = sorted({v for v in body.labels.values() if v and v not in ANNOTATION_LABELS})
    if bad_values:
        raise HTTPException(
            status_code=400,
            detail=f"判读结论只能是 {'/'.join(ANNOTATION_LABELS)}，收到：{bad_values}",
        )

    unknown = [item for item in body.labels if item not in TEST_ITEMS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知标签：{unknown[:5]}")

    ann = task_manager.set_annotation(case_id, body.labels, body.reader, body.remark)
    return CaseAnnotation(**ann)


@app.get("/api/cases", response_model=CaseListResponse)
async def list_cases(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    keyword: str | None = Query(default=None, description="按文件名模糊搜索"),
    status: str | None = Query(default=None, description="状态筛选，逗号分隔，如 done,failed"),
):
    """历史病例列表：支持搜索、状态筛选与分页，条目自带推理结果摘要。"""
    items, total = task_manager.list_cases(
        limit=limit, offset=offset, keyword=keyword, status=status
    )
    return CaseListResponse(items=items, total=total, offset=offset, limit=limit)


@app.get("/api/cases/{case_id}/preview/{index}")
async def get_preview(case_id: str, index: int):
    path = config.CASE_DIR / case_id / f"preview_{index}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="预览不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cases/{case_id}/slice/{index}")
def get_slice(
    case_id: str,
    index: int,
    window: str = Query(default="abdomen", description="窗宽窗位预设名"),
    ww: float | None = Query(default=None, description="自定义窗宽，给出时覆盖预设"),
    wl: float | None = Query(default=None, description="自定义窗位，给出时覆盖预设"),
    plane: str = Query(default="axial", description="成像平面：axial / coronal / sagittal"),
):
    """按需渲染单层切片（PNG），供前端翻页浏览与 MPR 多平面重建。

    - 轴位（axial）：index 为层号，范围 0 ~ Z-1
    - 冠状（coronal）：index 为行号，范围 0 ~ Y-1
    - 矢状（sagittal）：index 为列号，范围 0 ~ X-1

    默认腹部窗 W400/L50；`window` 的可用取值见 `GET /api/meta/windows`，
    也可直接用 `ww` / `wl` 指定任意窗。冠状与矢状面会按体素间距等比缩放，
    否则各向异性数据会严重变形。

    刻意写成同步 `def`：FastAPI 会把它丢到线程池，读盘不会阻塞事件循环。
    """
    if plane not in preprocessor.PLANES:
        raise HTTPException(
            status_code=400, detail=f"plane 只能是 {'/'.join(preprocessor.PLANES)}"
        )

    volume_path = config.CASE_DIR / case_id / "volume.nii.gz"
    array = preprocessor.load_volume_cached(volume_path)
    if array is None:
        raise HTTPException(status_code=404, detail="体数据不存在或尚未处理完成")

    limit = int(array.shape[{"axial": 0, "coronal": 1, "sagittal": 2}[plane]])
    if not 0 <= index < limit:
        raise HTTPException(
            status_code=416, detail=f"{plane} 索引超出范围（0 - {limit - 1}）"
        )

    record = task_manager.get(case_id)
    spacing = (record or {}).get("volume", {}).get("spacing_zyx") if record else None

    png = preprocessor.render_slice_png(
        array, index, window, ww, wl, plane=plane, spacing_zyx=spacing
    )
    if png is None:
        raise HTTPException(status_code=416, detail="层索引超出范围")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@app.get("/api/meta/windows")
async def get_windows():
    """可用的窗宽窗位预设，供前端下拉选择。"""
    return {
        "items": [
            {"key": key, "window_width": ww, "window_level": wl}
            for key, (ww, wl) in preprocessor.WINDOW_PRESETS.items()
        ]
    }


@app.get("/api/meta/findings", response_model=FindingsMeta)
async def get_findings_meta():
    return FindingsMeta(
        total=len(TEST_ITEMS),
        organs=list(grouped_items().keys()),
        items=[
            {
                "item": item,
                "organ": organ_of(item),
                "english": ENGLISH_MAPPING.get(item, ""),
                "reference_auc": None,
            }
            for item in TEST_ITEMS
        ],
        reference_auc_note=REFERENCE_AUC_NOTE,
        disclaimer=DISCLAIMER,
    )


_EXPORT_HEADER = [
    "case_id",
    "filename",
    "created_at",
    "status",
    "reader",
    "item",
    "organ",
    "finding",
    "english",
    "probability",
    "risk",
    "threshold_high",
    "threshold_medium",
    "label",
    "reference_auc",
    "remark",
]


@app.get("/api/export/cases.csv")
async def export_cases_csv(
    keyword: str | None = Query(default=None, description="按文件名模糊筛选"),
    status: str | None = Query(default=None, description="状态筛选，逗号分隔，如 done"),
    high: float = Query(default=0.5, ge=0, le=1, description="高风险阈值"),
    medium: float = Query(default=0.25, ge=0, le=1, description="中风险阈值"),
):
    """把符合条件的病例导成长表 CSV：一行 = 一个标签。

    这是科研统计的入口——SPSS / R / Python 拿到就能直接跑。`risk` 按传入阈值
    重算，保证与界面上标定的阈值一致；`label` 为空表示该标签尚未判读。
    """
    rows = task_manager.export_rows(keyword=keyword, status=status, high=high, medium=medium)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_HEADER)
    writer.writerows(rows)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return Response(
        # 前置 BOM，Excel 打开中文表头才不会乱码
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="radar_cases_{stamp}.csv"'},
    )


def _collect_label_stats() -> list[dict]:
    """逐标签计算自有数据的 AUC、置信区间与 Youden 最优切点。

    只有同时存在阳性与阴性样本的标签才能算出 AUC，其余返回 null——
    样本量不足时硬报一个数字反而是误导。置信区间同理：算不出来就不给，
    给一个宽到 0.4~1.0 的区间比不给更容易被误读成「模型有效」。
    """
    dataset = task_manager.labeled_dataset()
    items: list[dict] = []

    for item, bucket in dataset.items():
        scores = np.asarray(bucket["scores"], dtype=float)
        labels = np.asarray(bucket["labels"], dtype=int)
        positives = int(labels.sum())
        negatives = int(labels.size - positives)

        entry: dict = {
            "item": item,
            "organ": organ_of(item),
            "finding": finding_of(item),
            "english": ENGLISH_MAPPING.get(item, ""),
            "positives": positives,
            "negatives": negatives,
            "auc": None,
            "auc_ci_low": None,
            "auc_ci_high": None,
            "best_threshold": None,
            "sensitivity": None,
            "specificity": None,
            "reference_auc": REFERENCE_AUC.get(item),
        }

        if positives and negatives:
            fpr, tpr, thresholds = roc_curve(scores, labels)
            area = roc_auc(fpr, tpr)
            cut, sensitivity, specificity = best_youden(fpr, tpr, thresholds)
            if not math.isnan(area):
                entry["auc"] = round(area, 4)
            if not math.isnan(cut):
                entry["best_threshold"] = round(cut, 4)
                entry["sensitivity"] = round(sensitivity, 4)
                entry["specificity"] = round(specificity, 4)

            ci = bootstrap_auc_ci(scores, labels)
            if ci is not None:
                entry["auc_ci_low"] = round(ci[0], 4)
                entry["auc_ci_high"] = round(ci[1], 4)

        items.append(entry)

    # AUC 高的排前面；算不出 AUC 的沉到最后按名字排，避免它们插在中间干扰阅读
    items.sort(key=lambda e: (e["auc"] is None, -(e["auc"] or 0.0), e["item"]))
    return items


@app.get("/api/stats/labels")
async def stats_labels():
    """按标签汇总自有数据的 AUC（含 95% 置信区间）与 Youden 最优切点。"""
    return {"counts": task_manager.counts(), "items": _collect_label_stats()}


@app.get("/api/stats/roc")
async def stats_roc(item: str = Query(..., description="标签，如 肝_脂肪肝")):
    """返回单个标签的 ROC 曲线点，供前端绘图。"""
    bucket = task_manager.labeled_dataset().get(item)
    if not bucket:
        raise HTTPException(status_code=404, detail="该标签还没有判读记录")

    scores = np.asarray(bucket["scores"], dtype=float)
    labels = np.asarray(bucket["labels"], dtype=int)
    positives = int(labels.sum())
    negatives = int(labels.size - positives)
    if not positives or not negatives:
        raise HTTPException(
            status_code=400,
            detail=f"该标签目前只有{'阳性' if positives else '阴性'}样本，无法计算 ROC",
        )

    fpr, tpr, thresholds = roc_curve(scores, labels)
    area = roc_auc(fpr, tpr)
    cut, sensitivity, specificity = best_youden(fpr, tpr, thresholds)
    ci = bootstrap_auc_ci(scores, labels)

    return {
        "item": item,
        "organ": organ_of(item),
        "finding": finding_of(item),
        "positives": positives,
        "negatives": negatives,
        "auc": None if math.isnan(area) else round(area, 4),
        "auc_ci_low": None if ci is None else round(ci[0], 4),
        "auc_ci_high": None if ci is None else round(ci[1], 4),
        "best_threshold": None if math.isnan(cut) else round(cut, 4),
        "sensitivity": None if math.isnan(sensitivity) else round(sensitivity, 4),
        "specificity": None if math.isnan(specificity) else round(specificity, 4),
        "reference_auc": REFERENCE_AUC.get(item),
        "points": [
            {
                "fpr": round(float(f), 6),
                "tpr": round(float(t), 6),
                "threshold": round(float(th), 6),
            }
            for f, t, th in zip(fpr, tpr, thresholds)
        ],
    }


_STATS_HEADER = [
    "item",
    "organ",
    "finding",
    "english",
    "positives",
    "negatives",
    "auc",
    "auc_ci_low",
    "auc_ci_high",
    "best_threshold",
    "sensitivity",
    "specificity",
    "reference_auc",
]


@app.get("/api/export/stats.csv")
async def export_stats_csv():
    """导出逐标签统计表：自有数据 AUC、95% 置信区间、Youden 最优切点。

    论文表格的原始来源。置信区间一并给出——只报点估计是投稿时最常被
    审稿人挑的地方，小样本下这一步不能省。
    """
    rows = _collect_label_stats()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_STATS_HEADER)
    for row in rows:
        writer.writerow([row[key] for key in _STATS_HEADER])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="radar_label_stats_{stamp}.csv"'
        },
    )


@app.post("/api/cases/{case_id}/rerun")
async def rerun_case(case_id: str):
    """重新推理已失败或已取消的病例。

    预处理产物（`volume.nii.gz`）还在时直接复用，省掉几分钟的 DICOM 转换——
    让用户重新上传同一个文件再等一遍是没有意义的。排队中或正在跑的病例
    不允许重跑，避免同一份数据被并发推理。
    """
    rec = task_manager.get(case_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="病例不存在")
    if rec.get("status") not in (STATUS_FAILED, STATUS_CANCELLED):
        raise HTTPException(
            status_code=409, detail=f"当前状态「{rec.get('status')}」不需要重跑推理"
        )
    try:
        task_manager.requeue(case_id)
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"ok": True, "case_id": case_id}


@app.get("/api/health", response_model=HealthResponse)
async def health():
    from radar_engine import RadarEngine

    missing = config.ensure_ckpt_ready()
    readers = config.image_reader_status()
    loaded = RadarEngine.peek() is not None
    if missing:
        status = "ckpt_missing"
        detail = "模型文件缺失，请先执行 DAMO-RADAR/download_scripts/download_checkpoints.py"
    elif not readers["ok"]:
        # 权重齐全但读不了影像。这个组合比缺权重更隐蔽：服务一切正常，
        # 直到用户上传完点分析才失败
        status = "reader_missing"
        detail = readers["detail"]
    elif loaded:
        status = "ready"
        detail = ""
    else:
        status = "loading"
        detail = "模型正在加载，请稍后重试"
    return HealthResponse(
        status=status,
        device=config.DEVICE,
        cuda_available=torch.cuda.is_available(),
        model_loaded=loaded,
        queue_size=task_manager.queue_size(),
        missing_files=missing,
        detail=detail,
        image_readers=readers["readers"],
        image_reader_ok=readers["ok"],
    )


def _ensure_web_mime_types() -> None:
    """修正静态资源的 MIME 类型。

    Windows 上 mimetypes 会读注册表，`.js` 常被登记成 `text/plain`，
    浏览器按 HTML 规范对 module script 做严格 MIME 检查，直接拒绝执行 -> 白屏。
    Linux 下发 /etc/mime.types 一般是对的，这里显式覆盖同样无害。
    """
    mimetypes.init()
    for ext, mime in (
        (".js", "text/javascript"),
        (".mjs", "text/javascript"),
        (".css", "text/css"),
        (".json", "application/json"),
        (".svg", "image/svg+xml"),
        (".woff2", "font/woff2"),
    ):
        mimetypes.add_type(mime, ext)


# 前端构建产物（若存在）直接由后端托管，生产环境可改为 nginx 托管
_ensure_web_mime_types()
_frontend_dist = config.WEBAPP_DIR / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
