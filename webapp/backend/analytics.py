"""科研统计：ROC / AUC 与阈值标定。

刻意只用 numpy 手算，不引入 scikit-learn——内网私有化部署里少一个依赖就少
一份运维负担，而这几个统计量本身都是十几行的事。
"""
from __future__ import annotations

import math

import numpy as np

# numpy 2.0 把 trapz 改名为 trapezoid，这里同时兼容两代
_trapz = getattr(np, "trapezoid", None) or np.trapz


def risk_with_thresholds(
    probability: float | None, high: float, medium: float
) -> str:
    """按调用方给定的阈值分级，用于「用户标定过的阈值」下的导出与统计。

    后端默认的 0.5 / 0.25 只是保守展示（见 findings_meta.risk_level），
    科研上必须能按自有数据重算，所以这里显式接收阈值。
    """
    if probability is None:
        return "na"
    if probability >= high:
        return "high"
    if probability >= medium:
        return "medium"
    return "low"


def roc_curve(
    scores: np.ndarray, labels: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算 ROC 曲线，返回 (fpr, tpr, thresholds)，三者等长且从「全判负」开始。

    每个不同的分数都作为候选切点，而不是用人为设定的阈值网格——这样得到的
    曲线是精确的（等价于 Mann-Whitney U 统计量的排序算法）。
    """
    if scores.size == 0 or labels.size == 0:
        return np.array([]), np.array([]), np.array([])

    order = np.argsort(-scores, kind="mergesort")
    scores = scores[order]
    labels = labels[order]

    positives = int(labels.sum())
    negatives = int(labels.size - positives)
    if positives == 0 or negatives == 0:
        return np.array([]), np.array([]), np.array([])

    tps = np.cumsum(labels)
    fps = np.cumsum(1 - labels)

    # 分数相同的样本必须落在同一个切点，否则曲线会出现无意义的锯齿
    distinct = np.where(np.diff(scores) != 0)[0]
    cut = np.r_[distinct, scores.size - 1]

    tpr = np.r_[0.0, tps[cut] / positives]
    fpr = np.r_[0.0, fps[cut] / negatives]
    # 首个阈值取「比最高分还高一点」，对应「全部判负」那个点
    thresholds = np.r_[scores[0] + 1e-12, scores[cut]]
    return fpr, tpr, thresholds


def auc(fpr: np.ndarray, tpr: np.ndarray) -> float:
    """梯形法积分求 ROC 曲线下面积。"""
    if fpr.size < 2:
        return float("nan")
    return float(_trapz(tpr, fpr))


def best_youden(
    fpr: np.ndarray, tpr: np.ndarray, thresholds: np.ndarray
) -> tuple[float, float, float]:
    """按 Youden 指数（敏感性 + 特异性 - 1）取最优切点。

    返回 (阈值, 敏感性, 特异性)。
    """
    if fpr.size == 0:
        return float("nan"), float("nan"), float("nan")
    j = tpr - fpr
    idx = int(np.argmax(j))
    return float(thresholds[idx]), float(tpr[idx]), float(1.0 - fpr[idx])


def bootstrap_auc_ci(
    scores: np.ndarray,
    labels: np.ndarray,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 20260925,
) -> tuple[float, float] | None:
    """Bootstrap 法估计 AUC 的置信区间，算不出来时返回 None。

    小样本下 AUC 的点估计波动极大（5 阳 7 阴时区间可能横跨 0.4~1.0），
    只报一个数字会让人高估把握度。这里用有放回重采样给出区间。

    固定随机种子是刻意的：同一批数据每次刷新都必须得到同一个区间，
    否则用户会以为数据变了——科研结果首先要可复现。
    """
    n = int(scores.size)
    if n < 4:
        return None

    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        sampled_scores = scores[idx]
        sampled_labels = labels[idx]
        positives = int(sampled_labels.sum())
        # 重采样后只剩一类，这个样本算不出 AUC，跳过
        if positives == 0 or positives == n:
            continue
        fpr, tpr, _ = roc_curve(sampled_scores, sampled_labels)
        value = auc(fpr, tpr)
        if not math.isnan(value):
            values.append(value)

    # 有效重采样太少说明样本极度不均衡，此时给区间反而是误导
    if len(values) < n_boot // 4:
        return None

    low = float(np.percentile(values, 100 * alpha / 2))
    high = float(np.percentile(values, 100 * (1 - alpha / 2)))
    return low, high
