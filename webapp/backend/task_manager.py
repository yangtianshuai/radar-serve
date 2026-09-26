"""病例任务管理：上传解析 -> 排队 -> GPU 串行推理 -> 结果落盘。

推理耗时在数十秒到数分钟量级，因此走「异步任务 + 前端轮询」而非同步请求。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import preprocessor
from analytics import risk_with_thresholds
from findings_meta import (
    ENGLISH_MAPPING,
    REFERENCE_AUC,
    TEST_ITEMS,
    finding_of,
    organ_of,
    risk_level,
)
from schemas import (
    CaseAnnotation,
    CaseBrief,
    CaseResult,
    CaseStatus,
    Finding,
    InferenceStats,
    SeriesOption,
    VolumeMeta,
)

STATUS_PENDING = "pending"          # 已创建，等待确认序列或直接入队
STATUS_QUEUED = "queued"            # 已入队
STATUS_PREPROCESSING = "preprocessing"
STATUS_INFERENCING = "inferencing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_TERMINAL = {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED}

#: 金标准判读结论的合法取值；空串在 PATCH 里表示删除该标签的标注
ANNOTATION_LABELS = ("positive", "negative", "uncertain")

_STAGE_TEXT = {
    "loading": "读取体数据",
    "segmenting": "器官分割滑窗推理",
    "refining": "补充未覆盖器官",
    "aggregating": "汇总病灶概率",
    "done": "完成",
}


def _elapsed_sec(rec: dict) -> float:
    """任务已耗时（秒），从创建算起（含排队）。

    运行中用「现在 - 创建时间」实时增长；到了终态就改用 updated_at 定格——
    否则它会在每次轮询时继续变大，看起来像任务还没结束。前端拿这个值之后
    还会按秒自己补差值，所以这里的精度只影响校准频率。
    """
    start = _parse_iso(rec.get("created_at", ""))
    if start is None:
        return 0.0

    if rec.get("status") in _TERMINAL:
        end = _parse_iso(rec.get("updated_at", "")) or start
    else:
        end = datetime.now(timezone.utc).timestamp()

    return max(end - start, 0.0)

#: 过期结果清理的扫描间隔（秒）
_CLEANUP_INTERVAL_SEC = 3600


class QueueFullError(Exception):
    """排队任务数达到 RADAR_MAX_QUEUE_SIZE 上限。"""


class CaseCancelled(Exception):
    """用户主动取消；与执行失败区分开，不计入失败。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> float | None:
    """把 ISO8601 时间戳解析为 epoch 秒，解析失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TaskManager:
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        #: 入队顺序快照，用于告诉前端「排在第几位」
        #: asyncio.Queue 只能给出长度，拿不到具体位置，所以自己记一份
        self._pending_order: list[str] = []
        self._worker: asyncio.Task | None = None
        self._cleaner: asyncio.Task | None = None
        self._restore_history()

    # ------------------------------------------------------------ 持久化
    def _meta_path(self, case_id: str) -> Path:
        return config.RESULT_DIR / f"{case_id}.json"

    def _restore_history(self) -> None:
        for p in config.RESULT_DIR.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(rec, dict) and rec.get("case_id"):
                self._records[rec["case_id"]] = rec

    def _save(self, rec: dict) -> None:
        rec["updated_at"] = _now()
        self._meta_path(rec["case_id"]).write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._loop())
        if self._cleaner is None and config.RESULT_TTL_DAYS > 0:
            self._cleaner = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        for attr in ("_worker", "_cleaner"):
            task: asyncio.Task | None = getattr(self, attr)
            if task is not None:
                task.cancel()
                setattr(self, attr, None)

    async def _loop(self) -> None:
        while True:
            case_id = await self._queue.get()
            if case_id in self._pending_order:
                self._pending_order.remove(case_id)

            # 排队期间就被取消的话，没必要再进推理流程
            rec = self._records.get(case_id)
            if rec is not None and rec.get("cancel_requested"):
                rec.update(
                    status=STATUS_CANCELLED,
                    stage="cancelled",
                    message="已取消",
                    progress=0.0,
                )
                self._save(rec)
                self._queue.task_done()
                continue

            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._run_case, case_id
                )
            except Exception:
                rec = self._records.get(case_id)
                if rec is not None:
                    rec.update(status=STATUS_FAILED, error="内部错误")
                    self._save(rec)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------ 创建
    def create_case(self, filename: str) -> tuple[str, Path]:
        case_id = uuid.uuid4().hex[:16]
        upload_dir = config.UPLOAD_DIR / case_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "case_id": case_id,
            "status": STATUS_PENDING,
            "stage": "",
            "progress": 0.0,
            "filename": filename,
            "created_at": _now(),
            "updated_at": _now(),
            "message": "已上传，正在解析",
            "error": None,
            "available_series": [],
            "volume": None,
            "preview_count": 0,
            "result": None,
        }
        self._records[case_id] = rec
        self._save(rec)
        return case_id, upload_dir

    def set_pending_series(self, case_id: str, series: list[dict]) -> None:
        rec = self._records[case_id]
        rec.update(
            status=STATUS_PENDING,
            available_series=series,
            message="检测到多个 DICOM 序列，请选择用于推理的期相",
        )
        self._save(rec)

    def enqueue(self, case_id: str, series_id: str | None = None) -> None:
        if self._queue.qsize() >= config.MAX_QUEUE_SIZE:
            raise QueueFullError(
                f"任务队列已满（上限 {config.MAX_QUEUE_SIZE}），请稍后重试"
            )
        rec = self._records[case_id]
        rec.update(
            status=STATUS_QUEUED,
            progress=0.0,
            stage="queued",
            message="已排队，等待 GPU 空闲",
            error=None,
            series_id=series_id,
            # 重新入队等于一次新的尝试，清掉上一次的取消标记
            cancel_requested=False,
        )
        self._save(rec)
        self._pending_order.append(case_id)
        self._queue.put_nowait(case_id)

    def queue_size(self) -> int:
        """当前排队（尚未开始执行）的任务数。"""
        return self._queue.qsize()

    def queue_position(self, case_id: str) -> int | None:
        """返回 1 起算的排队位次；不在队列里则返回 None。"""
        try:
            return self._pending_order.index(case_id) + 1
        except ValueError:
            return None

    def cancel(self, case_id: str) -> bool:
        """请求取消任务。

        排队中的由 worker 取出时直接跳过，推理中的由进度回调抛 CaseCancelled
        中断。已结束的任务返回 False——取消与删除是两回事，取消只改状态，
        产出的文件留给用户自己处置。
        """
        rec = self._records.get(case_id)
        if rec is None or rec.get("status") in _TERMINAL:
            return False
        rec.update(cancel_requested=True, message="已请求取消，等待当前步骤结束")
        self._save(rec)
        return True

    def requeue(self, case_id: str) -> None:
        """把已失败或已取消的病例重新入队。

        体数据还在的话标记 `skip_preprocess`，让 `_run_case` 直接跳过 DICOM
        转换——那是整条链路里最耗时的一段（大序列要几分钟），没理由重跑。
        """
        if self._queue.qsize() >= config.MAX_QUEUE_SIZE:
            raise QueueFullError(
                f"任务队列已满（上限 {config.MAX_QUEUE_SIZE}），请稍后重试"
            )

        rec = self._records.get(case_id)
        if rec is None:
            raise KeyError(case_id)

        volume_path = config.CASE_DIR / case_id / "volume.nii.gz"
        rec.update(
            status=STATUS_QUEUED,
            stage="queued",
            progress=0.0,
            message="已重新排队",
            error=None,
            result=None,
            cancel_requested=False,
            # 只有元数据也在时才复用：写入中途被打断可能留下半个文件
            skip_preprocess=volume_path.exists() and bool(rec.get("volume")),
        )
        self._save(rec)
        self._pending_order.append(case_id)
        self._queue.put_nowait(case_id)

    def _ensure_not_cancelled(self, rec: dict) -> None:
        """阶段边界上的取消检查。

        诚实的限制：SimpleITK 的 DICOM 读写、torch 的权重加载都是原子的 C++
        调用，中途打断不了。所以检查点只能设在阶段之间——能覆盖「排队等锁」
        「预处理刚结束还没进推理」这类间隙，真正跑起来的 sitk 调用得等它自己
        跑完。这不是偷懒，是不给用户「点了取消却立刻中断」的错觉。
        """
        if rec.get("cancel_requested"):
            raise CaseCancelled()

    # ------------------------------------------------------------ 查询
    def get(self, case_id: str) -> dict | None:
        return self._records.get(case_id)

    def list_cases(
        self,
        limit: int = 50,
        offset: int = 0,
        keyword: str | None = None,
        status: str | None = None,
    ) -> tuple[list[CaseBrief], int]:
        """按创建时间倒序返回历史病例，支持文件名模糊搜索、状态筛选与分页。

        返回 (当前页条目, 过滤后的总条数)。记录全部常驻内存，过滤成本可忽略。
        """
        recs = sorted(
            self._records.values(), key=lambda r: r.get("created_at", ""), reverse=True
        )

        kw = (keyword or "").strip().lower()
        if kw:
            recs = [r for r in recs if kw in r.get("filename", "").lower()]

        if status:
            wanted = {s.strip() for s in status.split(",") if s.strip()}
            recs = [r for r in recs if r.get("status") in wanted]

        total = len(recs)
        start = max(offset, 0)
        page = recs[start : start + max(limit, 1)]
        return [_brief(r) for r in page], total

    def delete(self, case_id: str) -> bool:
        rec = self._records.pop(case_id, None)
        if rec is None:
            return False
        shutil.rmtree(config.UPLOAD_DIR / case_id, ignore_errors=True)
        shutil.rmtree(config.CASE_DIR / case_id, ignore_errors=True)
        self._meta_path(case_id).unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------ 金标准
    def set_annotation(
        self,
        case_id: str,
        labels: dict[str, str] | None = None,
        reader: str | None = None,
        remark: str | None = None,
    ) -> dict | None:
        """合并式更新参考标准，返回更新后的 annotation；病例不存在返回 None。

        labels 与已有标注做合并，值为空串表示删除该条——这样前端可以逐条
        提交（点一下存一次），不必每次回传全量 146 项。
        """
        rec = self._records.get(case_id)
        if rec is None:
            return None

        ann = dict(rec.get("annotation") or {})
        merged: dict[str, str] = dict(ann.get("labels") or {})
        for item, value in (labels or {}).items():
            if value:
                merged[item] = value
            else:
                merged.pop(item, None)

        ann["labels"] = merged
        if reader is not None:
            ann["reader"] = reader
        if remark is not None:
            ann["remark"] = remark
        ann["updated_at"] = _now()

        rec["annotation"] = ann
        self._save(rec)
        return ann

    def labeled_dataset(self) -> dict[str, dict[str, list]]:
        """按标签聚合已标注样本，返回 {item: {"scores": [...], "labels": [...]}}。

        只纳入 done 病例中「概率存在且判读结论为阳性/阴性」的样本：
        「不确定」是判读者的弃权、「未判读」是压根没看，两者都构不成
        有效标签——把它们算进 ROC 会直接污染 AUC。
        """
        out: dict[str, dict[str, list]] = {}
        for rec in self._records.values():
            if rec.get("status") != STATUS_DONE:
                continue
            labels = (rec.get("annotation") or {}).get("labels") or {}
            if not labels:
                continue
            findings = (rec.get("result") or {}).get("findings") or []
            probs = {f.get("item"): f.get("probability") for f in findings}
            for item, value in labels.items():
                if value not in ("positive", "negative"):
                    continue
                prob = probs.get(item)
                if prob is None:
                    continue
                bucket = out.setdefault(item, {"scores": [], "labels": []})
                bucket["scores"].append(float(prob))
                bucket["labels"].append(1 if value == "positive" else 0)
        return out

    def labeled_case_count(self) -> int:
        """已录入过金标准的病例数。"""
        return sum(
            1 for rec in self._records.values() if (rec.get("annotation") or {}).get("labels")
        )

    def counts(self) -> dict[str, int]:
        """病例总体情况，供统计面板展示。"""
        total = len(self._records)
        done = sum(1 for r in self._records.values() if r.get("status") == STATUS_DONE)
        return {"total": total, "done": done, "labeled": self.labeled_case_count()}

    def export_rows(
        self,
        keyword: str | None = None,
        status: str | None = None,
        high: float = 0.5,
        medium: float = 0.25,
    ) -> list[list]:
        """把病例展开成长表：一行 = 一个标签，供统计软件直接使用。

        只导出有结果的病例——排队中和失败的没有概率，放进长表只会让下游
        多一步过滤。`risk` 按调用方给定的阈值重算，保证与界面所见一致。
        """
        wanted = {s.strip() for s in status.split(",") if s.strip()} if status else None
        kw = (keyword or "").strip().lower()

        rows: list[list] = []
        for rec in sorted(self._records.values(), key=lambda r: r.get("created_at", "")):
            if wanted and rec.get("status") not in wanted:
                continue
            if kw and kw not in (rec.get("filename") or "").lower():
                continue

            result = rec.get("result")
            if not result:
                continue

            annotation = rec.get("annotation") or {}
            labels = annotation.get("labels") or {}
            for finding in result.get("findings") or []:
                item = finding.get("item", "")
                prob = finding.get("probability")
                reference_auc = finding.get("reference_auc")
                rows.append(
                    [
                        rec.get("case_id", ""),
                        rec.get("filename", ""),
                        rec.get("created_at", ""),
                        rec.get("status", ""),
                        annotation.get("reader", ""),
                        item,
                        finding.get("organ", ""),
                        finding.get("finding", ""),
                        finding.get("english", ""),
                        "" if prob is None else prob,
                        risk_with_thresholds(prob, high, medium),
                        high,
                        medium,
                        labels.get(item, ""),
                        "" if reference_auc is None else reference_auc,
                        annotation.get("remark", ""),
                    ]
                )
        return rows

    def to_status(self, rec: dict) -> CaseStatus:
        result = rec.get("result")
        annotation = rec.get("annotation")
        return CaseStatus(
            case_id=rec["case_id"],
            status=rec["status"],
            stage=_STAGE_TEXT.get(rec.get("stage", ""), rec.get("stage", "")),
            progress=float(rec.get("progress", 0.0)),
            filename=rec.get("filename", ""),
            created_at=rec.get("created_at", ""),
            updated_at=rec.get("updated_at", ""),
            message=rec.get("message", ""),
            error=rec.get("error"),
            available_series=[SeriesOption(**s) for s in rec.get("available_series", [])],
            volume=VolumeMeta(**rec["volume"]) if rec.get("volume") else None,
            preview_count=int(rec.get("preview_count", 0)),
            elapsed_sec=_elapsed_sec(rec),
            result=CaseResult(**result) if result else None,
            annotation=CaseAnnotation(**annotation) if annotation else None,
            queue_position=self.queue_position(rec["case_id"]),
            cancel_requested=bool(rec.get("cancel_requested")),
        )

    # ------------------------------------------------------------ 执行
    def _run_case(self, case_id: str) -> None:
        from radar_engine import RadarEngine, VolumeTooLargeError

        rec = self._records[case_id]
        started = time.time()

        def progress(stage: str, frac: float) -> None:
            # 推理是同步长任务，只能靠进度回调中途响应取消请求
            self._ensure_not_cancelled(rec)
            rec.update(stage=stage, progress=round(frac, 4))
            self._save(rec)

        try:
            rec.update(status=STATUS_PREPROCESSING, message="解析并标准化体数据")
            self._save(rec)
            self._ensure_not_cancelled(rec)

            raw_dir = config.UPLOAD_DIR / case_id
            case_dir = config.CASE_DIR / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            volume_path = case_dir / "volume.nii.gz"

            # 重跑且上次的体数据还在：跳过 DICOM 转换，直接进推理
            reuse_volume = bool(rec.get("skip_preprocess")) and volume_path.exists()
            if reuse_volume:
                rec.update(message="复用已有体数据，直接进入推理")
                self._save(rec)
                self._ensure_not_cancelled(rec)
            else:
                info = preprocessor.prepare_volume(
                    raw_dir, volume_path, series_id=rec.get("series_id")
                )
                # DICOM 读写本身打断不了，但结束之后就不必再往下走了
                self._ensure_not_cancelled(rec)
                rec["volume"] = {
                    "source_kind": info.source_kind,
                    "shape_zyx": list(info.shape_zyx),
                    "spacing_zyx": [round(float(x), 4) for x in info.spacing_zyx],
                    "hu_range": [round(float(x), 2) for x in info.hu_range],
                    "series_id": info.series_id,
                    "series_description": info.series_description,
                    "rescale_slope": info.rescale_slope,
                    "rescale_intercept": info.rescale_intercept,
                    "warnings": info.warnings,
                }
                previews = preprocessor.make_preview_png(
                    volume_path, case_dir / "preview.png", num_slices=3
                )
                rec["preview_count"] = len(previews)

            rec.update(status=STATUS_INFERENCING, message="模型推理中")
            self._save(rec)
            self._ensure_not_cancelled(rec)

            engine = RadarEngine.get()
            # 首次加载权重可能几十秒，加载完先确认用户还没反悔
            self._ensure_not_cancelled(rec)

            # 同一时刻只允许一个推理任务占用 GPU
            with engine.infer_lock:
                # 等锁期间同样可以取消，否则排队时点取消毫无反应
                self._ensure_not_cancelled(rec)
                probs, istats = engine.predict_with_stats(volume_path, progress_cb=progress)

            # 打点落日志，便于部署后统计真实耗时分布与显存峰值
            print(
                f"[radar] case={case_id} device={istats.device_name} "
                f"infer={istats.elapsed_sec}s windows={istats.num_windows} "
                f"refine={istats.refine_count} peak={istats.peak_gpu_mem_mb}MB",
                flush=True,
            )

            findings = _build_findings(probs)
            result = CaseResult(
                case_id=case_id,
                findings=findings,
                evaluated_count=sum(1 for f in findings if f.probability is not None),
                top_findings=sorted(
                    [f for f in findings if f.probability is not None],
                    key=lambda f: f.probability,
                    reverse=True,
                )[:10],
                volume=VolumeMeta(**rec["volume"]),
                stats=InferenceStats(
                    elapsed_sec=istats.elapsed_sec,
                    num_windows=istats.num_windows,
                    refine_count=istats.refine_count,
                    peak_gpu_mem_mb=istats.peak_gpu_mem_mb,
                    device_name=istats.device_name,
                ),
            )
            rec.update(
                status=STATUS_DONE,
                stage="done",
                progress=1.0,
                message=f"推理完成，耗时 {time.time() - started:.1f}s",
                error=None,
                result=result.model_dump(),
            )
            self._save(rec)
            # 原始上传内容不再需要，释放磁盘
            shutil.rmtree(raw_dir, ignore_errors=True)

        except CaseCancelled:
            rec.update(
                status=STATUS_CANCELLED,
                stage="cancelled",
                message=f"已取消（已运行 {time.time() - started:.1f}s）",
                error=None,
            )
            self._save(rec)
        except VolumeTooLargeError as exc:
            rec.update(status=STATUS_FAILED, error=str(exc), message="体数据超出可处理范围")
            self._save(rec)
        except preprocessor.PreprocessError as exc:
            rec.update(status=STATUS_FAILED, error=str(exc), message="输入数据解析失败")
            self._save(rec)
        except Exception as exc:  # noqa: BLE001 - 记录完整栈便于排障
            traceback.print_exc()
            rec.update(
                status=STATUS_FAILED,
                error=f"{type(exc).__name__}: {exc}",
                message="推理失败",
            )
            self._save(rec)

    # ------------------------------------------------------------ 过期清理
    async def _cleanup_loop(self) -> None:
        """定期清理超过 RADAR_RESULT_TTL_DAYS 的终态病例，避免磁盘无限增长。"""
        while True:
            try:
                await asyncio.sleep(_CLEANUP_INTERVAL_SEC)
                await asyncio.get_event_loop().run_in_executor(
                    None, self._purge_expired
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 清理失败不应影响推理主流程
                traceback.print_exc()

    def _purge_expired(self) -> None:
        ttl_days = config.RESULT_TTL_DAYS
        if ttl_days <= 0:
            return
        cutoff = time.time() - ttl_days * 86400
        removed = 0
        for path in list(config.RESULT_DIR.glob("*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            case_id = rec.get("case_id")
            # 只清理终态病例，避免误删正在排队的任务
            if not case_id or rec.get("status") not in _TERMINAL:
                continue
            ts = _parse_iso(rec.get("updated_at") or rec.get("created_at") or "")
            if ts is None or ts >= cutoff:
                continue
            self._records.pop(case_id, None)
            shutil.rmtree(config.UPLOAD_DIR / case_id, ignore_errors=True)
            shutil.rmtree(config.CASE_DIR / case_id, ignore_errors=True)
            path.unlink(missing_ok=True)
            removed += 1
        if removed:
            print(f"[radar] purged {removed} expired case(s)", flush=True)


def _brief(rec: dict) -> CaseBrief:
    """从完整记录里抽出列表页需要的字段，避免前端为每条记录再请求一次详情。"""
    result = rec.get("result") or {}
    findings = result.get("findings") or []
    top = (result.get("top_findings") or [None])[0] or {}
    annotation = rec.get("annotation") or {}
    labels = annotation.get("labels") or {}

    organ = top.get("organ", "")
    finding = top.get("finding", "")
    return CaseBrief(
        case_id=rec["case_id"],
        status=rec.get("status", ""),
        filename=rec.get("filename", ""),
        created_at=rec.get("created_at", ""),
        updated_at=rec.get("updated_at", ""),
        progress=float(rec.get("progress", 0.0)),
        evaluated_count=int(result.get("evaluated_count") or 0),
        high_risk_count=sum(1 for f in findings if f.get("risk") == "high"),
        labeled_count=len(labels),
        reader=annotation.get("reader", ""),
        top_finding=f"{organ} · {finding}" if organ and finding else "",
        top_probability=top.get("probability"),
        error=rec.get("error"),
    )


def _build_findings(probs: dict[str, float | None]) -> list[Finding]:
    findings: list[Finding] = []
    for item in TEST_ITEMS:
        p = probs.get(item)
        findings.append(
            Finding(
                item=item,
                organ=organ_of(item),
                finding=finding_of(item),
                english=ENGLISH_MAPPING.get(item, ""),
                probability=None if p is None else round(float(p), 6),
                risk="na" if p is None else risk_level(p),
                reference_auc=REFERENCE_AUC.get(item),
            )
        )
    # 有结果的按概率降序，未评估的排在最后
    findings.sort(key=lambda f: (f.probability is None, -(f.probability or 0.0)))
    return findings


task_manager = TaskManager()
