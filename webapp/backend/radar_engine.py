"""RADAR 推理引擎：模型单例 + 单体数据推理。

推理逻辑与 RADAR_inference/inference_demo.py 保持一致（同样的滑窗、拼接、
器官注意力池化与图文对比），只做了三处工程化改造：
1. 从「扫目录批量写 CSV」改为「单个文件 -> dict」，供 HTTP 接口直接返回；
2. 增加进度回调与体积校验，避免原实现中超大体积被静默跳过；
3. 模型常驻显存 + 互斥锁，保证并发请求下 GPU 串行执行。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# 让 dynamic_network_architectures 可以被 import。
# 路径统一由 config 推导，避免目录结构调整时漏改这里。
import config  # noqa: E402

if str(config.RADAR_INFER_DIR) not in sys.path:
    sys.path.insert(0, str(config.RADAR_INFER_DIR))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from monai import transforms  # noqa: E402
from monai.data.utils import dense_patch_slices  # noqa: E402
from transformers import BertTokenizer  # noqa: E402

from findings_meta import TEST_ITEMS, organ_of  # noqa: E402

# XBertEncoder.from_config 内部读取 CONFIGS_ROOT 环境变量，必须在 import 之后、
# 实例化之前设置好
os.environ.setdefault("CONFIGS_ROOT", str(config.CONFIGS_ROOT))
os.environ.setdefault("MODEL_ROOT", str(config.MODEL_ROOT))

from dynamic_network_architectures.med import XBertEncoder  # noqa: E402
from dynamic_network_architectures.vision_branch import VisionBranch  # noqa: E402

ROI_SIZE = (96, 256, 384)  # (D, W, H)，与训练一致
OVERLAP = 0.25
SW_BATCH_SIZE = 1
NUM_ORGAN_CLASSES = 37  # 36 个器官 + 背景
REF_SPACING = (1.0, 1.0, 5.0)
#: 参与评测的器官集合，等价于原实现中 DataFolder 的 test_organ_names
TEST_ORGANS = sorted({organ_of(item) for item in TEST_ITEMS})

ORGANS = [
    '肾上腺', '主动脉', '竖脊肌', '脑', '锁骨', '大肠', '十二指肠', '食管', '面部', '股骨',
    '胆囊', '臀肌', '心脏', '髋关节', '肱骨', '髂动脉', '髂静脉', '髂腰肌', '下腔静脉', '肾',
    '肝', '肺', '胰腺', '门静脉', '肺动脉', '肋骨', '骶骨', '肩胛骨', '小肠', '脾',
    '胃', '气管', '膀胱', '颈椎', '腰椎', '胸椎',
]


class VolumeTooLargeError(Exception):
    pass


@dataclass(frozen=True)
class InferenceStats:
    """单次推理的资源开销，用于容量规划（判断需要几张卡、单卡可承载多少并发）。

    peak_gpu_mem_mb 取自 torch.cuda.max_memory_allocated，是本次推理的峰值
    分配量而非整卡占用，不含 CUDA context 与碎片。
    """

    elapsed_sec: float
    num_windows: int
    refine_count: int
    peak_gpu_mem_mb: float | None
    device_name: str


def _get_scan_interval(image_size, roi_size, num_spatial_dims, overlap):
    if len(image_size) != num_spatial_dims or len(roi_size) != num_spatial_dims:
        raise ValueError("image coord different from spatial dims.")
    scan_interval = []
    for i in range(num_spatial_dims):
        if roi_size[i] == image_size[i]:
            scan_interval.append(int(roi_size[i]))
        else:
            interval = int(roi_size[i] * (1 - overlap))
            scan_interval.append(interval if interval > 0 else 1)
    return tuple(scan_interval)


def masks_to_boxes_3d(masks):
    """[N, D, H, W] -> [N, 6]，box 为 min_x, min_y, min_z, max_x, max_y, max_z。"""
    if masks.numel() == 0:
        return torch.zeros((0, 6), device=masks.device)

    d, h, w = masks.shape[-3:]
    z = torch.arange(0, d, dtype=torch.float, device=masks.device)
    y = torch.arange(0, h, dtype=torch.float, device=masks.device)
    x = torch.arange(0, w, dtype=torch.float, device=masks.device)
    z, y, x = torch.meshgrid(z, y, x, indexing='ij')

    x_mask = masks * x.unsqueeze(0)
    x_max = x_mask.flatten(1).max(-1).values
    x_min = x_mask.masked_fill(~masks.bool(), float('inf')).flatten(1).min(-1).values

    y_mask = masks * y.unsqueeze(0)
    y_max = y_mask.flatten(1).max(-1).values
    y_min = y_mask.masked_fill(~masks.bool(), float('inf')).flatten(1).min(-1).values

    z_mask = masks * z.unsqueeze(0)
    z_max = z_mask.flatten(1).max(-1).values
    z_min = z_mask.masked_fill(~masks.bool(), float('inf')).flatten(1).min(-1).values

    return torch.stack([x_min, y_min, z_min, x_max, y_max, z_max], dim=1)


def center_crop(image, mask, crop_size):
    x_min, y_min, z_min, x_max, y_max, z_max = masks_to_boxes_3d(mask)[0].long()
    crop_d = max(crop_size[0], z_max - z_min)
    crop_h = max(crop_size[1], y_max - y_min)
    crop_w = max(crop_size[2], x_max - x_min)

    cx, cy, cz = (x_min + x_max) // 2, (y_min + y_max) // 2, (z_min + z_max) // 2
    d, h, w = image.shape[-3:]

    x_start = max(0, cx - crop_w // 2)
    x_end = min(w, x_start + crop_w)
    if x_end - x_start < crop_w:
        x_start = max(0, x_end - crop_w)

    y_start = max(0, cy - crop_h // 2)
    y_end = min(h, y_start + crop_h)
    if y_end - y_start < crop_h:
        y_start = max(0, y_end - crop_h)

    z_start = max(0, cz - crop_d // 2)
    z_end = min(d, z_start + crop_d)
    if z_end - z_start < crop_d:
        z_start = max(0, z_end - crop_d)

    return (
        image[..., z_start:z_end, y_start:y_end, x_start:x_end],
        mask[..., z_start:z_end, y_start:y_end, x_start:x_end],
    )


class RADAR(nn.Module):
    """与 inference_demo.py 中的 RADAR 完全一致，仅把路径来源换成 config。"""

    def __init__(
        self,
        image_encoder,
        text_encoder,
        queue_size=1234,
        alpha=0.4,
        embed_dim=256,
        momentum=0.995,
        tie_enc_dec_weights=True,
        max_txt_len=175,
    ):
        super().__init__()
        self.tokenizer = BertTokenizer.from_pretrained(
            str(config.CONFIGS_ROOT / config.BERT_DIR_NAME)
        )
        text_encoder.resize_token_embeddings(len(self.tokenizer))
        self.visual_encoder = image_encoder
        self.text_encoder = text_encoder

        text_width = text_encoder.config.hidden_size
        vision_width = 256

        self.text_proj = nn.Linear(text_width, embed_dim)
        self.queue_size = queue_size
        self.momentum = momentum
        self.temp = nn.Parameter(0.07 * torch.ones([]))
        self.alpha = alpha
        self.max_txt_len = max_txt_len
        self.organs = ORGANS

        self.attention = nn.MultiheadAttention(
            embed_dim=vision_width, num_heads=4, dropout=0.1, batch_first=True
        )
        self.vision_projs = nn.ModuleList(
            [nn.Linear(vision_width, embed_dim) for _ in range(len(self.organs))]
        )
        self.query_tokens = nn.Parameter(torch.zeros(len(self.organs), vision_width))

    @torch.inference_mode()
    def forward_test_win(
        self,
        images,
        masks,
        organ_logits,
        test_organs,
        text_feat_dict,
        organ_feat_dict,
        whole_organ_sizes,
        skip_organ=None,
    ):
        (
            seg_probs, seg, image_embeds1, image_embeds2, image_embeds3,
            organ_token_flags1, organ_token_flags2, organ_token_flags3,
        ) = self.visual_encoder(images, None)

        margin = 2
        for i, (embed1, embed2, embed3, mask) in enumerate(
            zip(image_embeds1, image_embeds2, image_embeds3, seg)
        ):
            boundaries = []
            for d in range(mask.dim()):
                start_slice = [slice(None)] * mask.dim()
                end_slice = [slice(None)] * mask.dim()
                start_slice[d] = slice(None, margin)
                end_slice[d] = slice(-margin, None)
                boundaries.append(mask[tuple(start_slice)][mask[tuple(start_slice)] > 0])
                boundaries.append(mask[tuple(end_slice)][mask[tuple(end_slice)] > 0])
            boundaries = torch.cat(boundaries)

            boundary_values = boundaries[boundaries > 0].flatten()
            boundary_organs = torch.unique(boundary_values)

            if skip_organ is not None:
                boundary_organs = boundary_organs[boundary_organs != skip_organ + 1]

            organ_ids, organ_counts = torch.unique(mask, return_counts=True)
            organ_ids = organ_ids.long()
            organ_counts = organ_counts[organ_ids != 0]
            organ_ids = organ_ids[organ_ids != 0]

            intact_organ_ids = [
                oid for oid, _ in zip(organ_ids, organ_counts)
                if oid not in boundary_organs
            ]
            if not intact_organ_ids:
                continue
            intact_organ_ids = torch.tensor(intact_organ_ids, device=mask.device).long() - 1

            for organ_id in intact_organ_ids:
                organ_name = self.organs[organ_id.item()]
                if organ_name not in test_organs:
                    continue
                if organ_name in organ_feat_dict:
                    continue

                tokens1 = organ_token_flags1[i, organ_id, :]
                tokens2 = organ_token_flags2[i, organ_id, :]
                tokens3 = organ_token_flags3[i, organ_id, :]

                query = self.query_tokens[organ_id].unsqueeze(0).unsqueeze(0)
                key = value = torch.cat(
                    [embed1[tokens1].unsqueeze(0), embed2[tokens2].unsqueeze(0),
                     embed3[tokens3].unsqueeze(0)],
                    dim=1,
                )
                updated_query_token, _ = self.attention(query, key, value)
                updated_query_token = updated_query_token.squeeze(0)

                image_feat = F.normalize(self.vision_projs[organ_id](updated_query_token), dim=-1)
                organ_feat_dict[organ_name] = image_feat.cpu().tolist()

                for item in organ_logits.keys():
                    item_organ_name = item.split('_')[0] if isinstance(item, str) else item[0]
                    if item_organ_name != organ_name:
                        continue
                    text_feat = text_feat_dict[item]
                    logits = image_feat @ text_feat.t() / self.temp
                    probs = logits.softmax(-1)
                    organ_logits[item].append(probs.cpu().tolist())

        return organ_logits, seg_probs


def load_and_preprocess(volume_path: str | Path) -> torch.Tensor:
    """读取 .nii.gz -> 重采样 -> HU 截断 -> 裁非零区 -> pad，返回 [C, D, W, H]。"""
    pad_func = transforms.SpatialPadd(
        keys=["image"], spatial_size=ROI_SIZE, mode="constant",
        constant_values=0, method="end",
    )
    data = {"image": str(volume_path)}
    res = transforms.LoadImaged(
        keys=["image"], image_only=False, ensure_channel_first=True
    )(data)

    affine = np.asarray(res["image_meta_dict"]["affine"], dtype=np.float64)
    spacing = (abs(affine[0, 0]), abs(affine[1, 1]), abs(affine[2, 2]))
    _, h, w, d = res["image"].shape

    scale = [spacing[i] / REF_SPACING[i] for i in range(3)]
    target_size = [int(h * scale[1]), int(w * scale[0]), int(d * scale[2])]

    trans = transforms.Compose([
        transforms.Resized(spatial_size=target_size, keys=["image"], mode="trilinear"),
        transforms.Transposed(keys=["image"], indices=(0, 3, 2, 1)),
    ])
    resized = trans(res)

    img = resized["image"]
    img[img > 400] = 400
    img[img < -300] = -300
    img = (img - img.min()) / (img.max() - img.min() + 1e-8)

    roi_coords = np.nonzero(img[0].cpu().numpy())
    min_dhw = torch.from_numpy(np.min(roi_coords, axis=1))
    max_dhw = torch.from_numpy(np.max(roi_coords, axis=1))

    extend_d, extend_hw = 5, 20
    min_dhw = torch.max(
        min_dhw - torch.tensor([extend_d, extend_hw, extend_hw]), torch.tensor([0, 0, 0])
    )
    max_dhw = torch.min(
        max_dhw + torch.tensor([extend_d, extend_hw, extend_hw]),
        torch.tensor([img.shape[1], img.shape[2], img.shape[3]]),
    )

    cropped = img[:, min_dhw[0]:max_dhw[0], min_dhw[1]:max_dhw[1], min_dhw[2]:max_dhw[2]]
    data["image"] = cropped
    data = pad_func(data)
    return data["image"].as_tensor()


class RadarEngine:
    """模型单例。加载权重耗时较长，服务启动时预热一次即可。"""

    _instance: "RadarEngine | None" = None
    _lock = threading.Lock()

    @staticmethod
    def _resolve_device() -> torch.device:
        """决定实际使用的设备，并把结论打到日志里。

        `RADAR_DEVICE=cuda` 但 CUDA 不可用时**绝不能静默退回 CPU**：3D 滑窗在
        CPU 上单例要跑几十分钟，用户只会觉得"怎么这么慢"，而不会想到是 GPU
        压根没启用。所以这里把原因和排查方向直接说出来。
        """
        requested = config.DEVICE

        if requested == "cpu":
            print("[radar] RADAR_DEVICE=cpu，按配置使用 CPU", flush=True)
            return torch.device("cpu")

        if torch.cuda.is_available():
            print(
                f"[radar] 使用 GPU：{torch.cuda.get_device_name(0)}"
                f"（torch {torch.__version__}, CUDA {torch.version.cuda}）",
                flush=True,
            )
            return torch.device(requested)

        print(
            f"[radar] 警告：RADAR_DEVICE={requested}，但 torch 看不到可用 GPU，"
            "已回退到 CPU。\n"
            "[radar] 3D 滑窗在 CPU 上单例可能耗时数十分钟，请按顺序排查：\n"
            "[radar]   1) 容器是否拿到 GPU：docker exec <容器名> nvidia-smi\n"
            "[radar]   2) 宿主机是否装了 NVIDIA Container Toolkit，且 compose 里的\n"
            "[radar]      deploy.resources.reservations.devices 生效\n"
            "[radar]   3) 驱动版本是否支持 CUDA 12.1（需要 >= 525）\n"
            f"[radar]   torch={torch.__version__}, 编译时 CUDA={torch.version.cuda}",
            flush=True,
        )
        return torch.device("cpu")

    def __init__(self) -> None:
        self.device = self._resolve_device()
        self.pad_func = transforms.DivisiblePadd(
            keys=["image", "label"], k=32, mode="constant",
            constant_values=0, method="end",
        )

        vision_encoder = VisionBranch()
        text_encoder = XBertEncoder.from_config({}, from_pretrained=True)
        model = RADAR(image_encoder=vision_encoder, text_encoder=text_encoder)

        ckpt_path = config.MODEL_ROOT / config.CHECKPOINT_NAME
        if not ckpt_path.exists():
            raise FileNotFoundError(f"未找到模型权重: {ckpt_path}")
        # 仅加载官方权重；weights_only=False 会执行 pickle，不要指向不可信来源
        ckpt = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model"], strict=False)
        model.eval()
        model.to(self.device)

        text_emb_path = config.MODEL_ROOT / config.TEXT_EMBEDDING_NAME
        if not text_emb_path.exists():
            raise FileNotFoundError(f"未找到文本 embedding: {text_emb_path}")
        text_feat_dict = torch.load(str(text_emb_path), map_location="cpu", weights_only=False)

        self.model = model
        self.text_feat_dict = text_feat_dict
        # GPU 推理串行化，避免并发请求导致显存溢出
        self.infer_lock = threading.Lock()

    @classmethod
    def get(cls) -> "RadarEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def peek(cls) -> "RadarEngine | None":
        return cls._instance

    @torch.inference_mode()
    def predict(self, volume_path: str | Path, progress_cb=None) -> dict[str, float | None]:
        """对单个 .nii.gz 推理，返回 {标签: 阳性概率}，未检出的器官为 None。"""
        return self.predict_with_stats(volume_path, progress_cb=progress_cb)[0]

    @torch.inference_mode()
    def predict_with_stats(
        self, volume_path: str | Path, progress_cb=None
    ) -> tuple[dict[str, float | None], InferenceStats]:
        """同 predict，额外返回耗时 / 显存 / 滑窗数等统计，供容量规划使用。"""
        started = time.time()
        on_gpu = self.device.type == "cuda"
        if on_gpu:
            torch.cuda.reset_peak_memory_stats()

        def report(stage: str, frac: float) -> None:
            if progress_cb:
                progress_cb(stage, min(max(frac, 0.0), 1.0))

        report("loading", 0.02)
        image = load_and_preprocess(volume_path)

        for s in image.shape[1:]:
            if int(s) > config.MAX_SPATIAL_DIM:
                raise VolumeTooLargeError(
                    f"体数据尺寸 {tuple(int(x) for x in image.shape[1:])} 超过上限 "
                    f"{config.MAX_SPATIAL_DIM}，请提供更小范围或更厚层的重建数据"
                )

        image = image[None].to(self.device)

        if self.device.type == "cuda":
            torch.cuda.empty_cache()

        image_size = list(image.shape[2:])
        num_spatial_dims = len(image_size)
        scan_interval = _get_scan_interval(image_size, ROI_SIZE, num_spatial_dims, OVERLAP)
        slices = dense_patch_slices(image_size, ROI_SIZE, scan_interval)
        num_win = len(slices)

        organ_logits: dict[str, list] = {k: [] for k in TEST_ITEMS}
        full_mask = torch.zeros((1, NUM_ORGAN_CLASSES) + tuple(image_size), device=self.device)
        count_map = torch.zeros_like(full_mask)
        organ_feat_dict: dict[str, list] = {}

        report("segmenting", 0.05)
        for slice_g in range(0, num_win, SW_BATCH_SIZE):
            slice_range = range(slice_g, min(slice_g + SW_BATCH_SIZE, num_win))
            unravel_slice = [
                [slice(0, 1), slice(None)] + list(slices[idx % num_win])
                for idx in slice_range
            ]
            window_patches = torch.cat([image[win] for win in unravel_slice]).to(self.device)

            organ_logits, pred_window_seg_prob = self.model.forward_test_win(
                window_patches, None, organ_logits, TEST_ORGANS,
                self.text_feat_dict, organ_feat_dict, None,
            )

            interpolated = F.interpolate(
                pred_window_seg_prob, size=window_patches.shape[2:], mode="trilinear"
            )
            for ii, slice_idx in enumerate(slice_range):
                full_slice = unravel_slice[ii]
                full_mask[full_slice] += interpolated[ii]
                count_map[full_slice] += 1

            if num_win:
                report("segmenting", 0.05 + 0.65 * (slice_g + SW_BATCH_SIZE) / num_win)

        count_map = torch.clamp(count_map, min=1)
        stitched_mask = (full_mask / count_map).argmax(1).unsqueeze(0)

        # 未被滑窗覆盖的器官，用其分割结果做一次中心裁剪补推理
        pending = [k for k, v in organ_logits.items() if not v]
        refined = 0
        report("refining", 0.72)
        for n_done, item in enumerate(pending):
            organ_name = item.split('_')[0]
            if organ_name not in ORGANS:
                continue
            organ_id = ORGANS.index(organ_name)
            organ_mask = torch.eq(stitched_mask, organ_id + 1)
            if int(torch.count_nonzero(organ_mask)) == 0:
                continue  # 该器官在图像中不存在，保持为 None

            window_patch, window_mask = center_crop(image, organ_mask, crop_size=ROI_SIZE)
            window_mask = window_mask.float()
            window_mask[window_mask == 1] = organ_id + 1
            refined += 1

            pad_data = self.pad_func({"image": window_patch[0], "label": window_mask[0]})
            window_patch, window_mask = pad_data["image"], pad_data["label"]

            organ_logits, _ = self.model.forward_test_win(
                window_patch[None], None, organ_logits, TEST_ORGANS,
                self.text_feat_dict, organ_feat_dict, None, skip_organ=organ_id,
            )
            if pending:
                report("refining", 0.72 + 0.23 * (n_done + 1) / len(pending))

        report("aggregating", 0.97)
        results: dict[str, float | None] = {}
        for item in TEST_ITEMS:
            probs = organ_logits.get(item) or []
            if not probs:
                results[item] = None
            else:
                results[item] = float(np.concatenate(probs).mean(0)[1])

        report("done", 1.0)
        if on_gpu:
            # 前向是异步的，不同步就统计不到真实耗时
            torch.cuda.synchronize()
        stats = InferenceStats(
            elapsed_sec=round(time.time() - started, 2),
            num_windows=num_win,
            refine_count=refined,
            peak_gpu_mem_mb=(
                round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1) if on_gpu else None
            ),
            device_name=torch.cuda.get_device_name(self.device) if on_gpu else "cpu",
        )
        return results, stats
