"""age_caps —— 年龄 → 单段时长上限 单一真相源（ADR-0008 红队 X-1 / R1+R2）。

【为什么需要单一表】

重构前年龄上限表在两处独立编码：`critic/_rules/checks.py:check_age_aware_duration`
（内联 45/75/120/60）与 `blueprint/blueprint.py:_resolve_age_caps`（同表 + 多一档
未触发的 60-74→90）。两处独立维护即漂移温床——ADR-0008 背景诊断已指出这正是
critic 与 ILS grounding `45` vs `90` 分歧导致 thrash 的根源之一。

本模块把 critic 实际用到的四档收口成单一表，`check_age_aware_duration` 改读本表。

【范围声明（ADR-0008 B-2b）】

只编码 check 实际触发的四档：≤3 / 4-6 / 7-12 / ≥75。**不**收编 blueprint.py 里
`_AGE_CAP_ELDER_60_74 = 90`——那一档从未被任何 check 触发（60-74 岁不进任何分支），
是否要让 60-74 岁也约束、约束到多少，是 Phase C「critic 与 ILS grounding 对齐」
的决策范围，B-2b 不动。blueprint.py 自己的 `_resolve_age_caps` / `_AGE_CAP_*`
保持不变（Phase C 随整个 blueprint 死层一起删）。
"""

from __future__ import annotations

# 单段时长上限（分钟）。业界基线（Smithsonian SEEC 等，见 blueprint.py 原注释）：
TODDLER_CAP_MIN = 45  # ≤3 岁：婴幼儿（注意力 ≤30，余量给过渡）
PRESCHOOL_CAP_MIN = 75  # 4-6 岁：学龄前
SCHOOL_AGE_CAP_MIN = 120  # 7-12 岁：学童
SENIOR_CAP_MIN = 60  # ≥75 岁：高龄（含台阶/长走再砍，但 critic 不感知场地坡度，给统一 cap）


def cap_for_age(age: int) -> tuple[int, str] | None:
    """按年龄分桶返回 (cap_min, tier_label)。

    分桶落在 check_age_aware_duration 实际使用的四档之外（如 13-74 岁）→ 返回 None
    （不约束）。tier_label 是人话分级名，供调用方拼 `f"含 {age} 岁{role}（{tier_label}
    ≤{cap}min）"` 这类 message——本函数不拼具体 age/role，保持纯查表职责。
    """
    if age <= 3:
        return TODDLER_CAP_MIN, "婴幼儿"
    if age <= 6:
        return PRESCHOOL_CAP_MIN, "学龄前"
    if age <= 12:
        return SCHOOL_AGE_CAP_MIN, "学童"
    if age >= 75:
        return SENIOR_CAP_MIN, "高龄"
    return None
