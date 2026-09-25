"""病灶标签元数据：146 个「器官_病灶」标签、英文名映射、公开参考 AUC。

数据由 scripts 从 RADAR_inference/inference_demo.py 中的 DataFolder 精确抽取，
避免手工转录出错。参考 AUC 来自 docs/INFERENCE.md 的 MERLIN 外部测试集结果。
"""
import json
from pathlib import Path

_META_PATH = Path(__file__).resolve().parent / "findings_meta.json"
_META = json.loads(_META_PATH.read_text(encoding="utf-8"))

#: 146 个标签，形如 "肝_脂肪肝"
TEST_ITEMS: list[str] = _META["test_items"]
#: 标签 -> 英文描述，形如 "肝_脂肪肝" -> "Liver_Steatotic liver disease"
ENGLISH_MAPPING: dict[str, str] = _META["english_mapping"]
#: 标签 -> 外部验证集 ROC-AUC（仅部分标签有）
REFERENCE_AUC: dict[str, float] = _META["reference_auc"]
REFERENCE_AUC_NOTE: str = _META["reference_auc_note"]


def organ_of(item: str) -> str:
    """取标签的器官部分："肝_脂肪肝" -> "肝"。"""
    return item.split("_")[0]


def finding_of(item: str) -> str:
    """取标签的病灶部分："肝_脂肪肝" -> "脂肪肝"。"""
    return item.split("_", 1)[1] if "_" in item else item


def risk_level(prob: float) -> str:
    """依据概率给出风险分级。

    注意：论文未给出官方判定阈值，这里的 0.5 / 0.25 是保守的展示分级，
    上线前应在自有验证集上用 Youden 指数重新标定。
    """
    if prob >= 0.5:
        return "high"
    if prob >= 0.25:
        return "medium"
    return "low"


def grouped_items() -> dict[str, list[str]]:
    """按器官分组，保持标签原始顺序。"""
    groups: dict[str, list[str]] = {}
    for item in TEST_ITEMS:
        groups.setdefault(organ_of(item), []).append(item)
    return groups
