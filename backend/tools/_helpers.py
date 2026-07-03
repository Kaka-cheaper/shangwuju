"""tools._helpers —— Tool 内共享的 *只读* 辅助函数。

职责：
- 距离 / tag 求交集 / 路线查找等被多个 Tool 复用的纯函数
- 统一从 data.loader 取 mock，避免每个 Tool 重复 import
- tag 渐进放宽（Step 6；ADR-0014 决策 2 · G-2 改造为 hard/soft 分层）：
  候选 0 时按 hard/soft 分层降级——hard tag 永不放宽，soft tag 按出处
  （field_provenance）降级序渐进删除

不负责：
- 业务过滤策略（每个 Tool 自己定）
- 任何业务决策 / 调用其他 Tool（违反 AGENTS.md §4.1）
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Sequence, TypeVar

from data.loader import load_routes
from schemas.domain import Route
from schemas.tags import is_hard_tag


T = TypeVar("T")


def has_all_tags(item_tags: Iterable[str], required: Iterable[str]) -> bool:
    """item 的 tags 是否覆盖 required 全部。空 required 视为通过。"""
    required_set = {t for t in required if t}
    if not required_set:
        return True
    return required_set.issubset(set(item_tags))


def has_any_tag(item_tags: Iterable[str], required: Iterable[str]) -> bool:
    """item 的 tags 是否命中 required 中任意一个；用于 experience_tags 这类弱约束。"""
    required_set = {t for t in required if t}
    if not required_set:
        return True
    return bool(required_set & set(item_tags))


def find_route(from_loc: str, to_loc: str) -> Route | None:
    """从 routes.json 查 from→to；找不到返回 None。"""
    for r in load_routes():
        if r.from_location == from_loc and r.to_location == to_loc:
            return r
    return None


# ============================================================
# Tag 渐进放宽（Step 6；ADR-0014 决策 2 · G-2：硬/软分层改造）
# ============================================================
#
# 【硬/软两条完全不同的语义（不是同一根轴上的"优先级高低"）】
# hard tag（`schemas.tags.is_hard_tag`）= 一票否决的过滤器：从"可被放宽的
# 候选集合"里整体摘除，全程恒定过滤，绝不进入下面的渐进丢弃序列——即使因此
# 打到 0 候选，也照常返回空（不是 bug，是"没有安全的替代品"这件事本身需要
# 被诚实地看见，下游 give_up 疏导见任务报告"配套三件"）。
# soft tag = 可协商：按 2×2 出处矩阵降级（`_soft_drop_order`），出处越"轻"
# （default）越先丢、越"重"（user_stated）越后丢——但**丢的决定本身不再由
# 这里负责告知**：是否要让用户知道"这条软约束被放宽了"，收口到出口满足度
# 审计（`agent.planning.critic.exit_audit`），比对的是**最终**方案而非搜索
# 期的中间尝试，天然去重、天然覆盖三条规划路径，不必在这里插桩。
#
# 旧 `_PRIORITY_TAGS_HIGH` 私有常量（人群物理 tag 一律"优先级高"）已删除——
# 它把"安全底线"这个二元语义硬塞进一根"优先级"数轴，表达不出"一票否决"，
# 且不知道出处也无法体现"用户亲口说的该比档案里猜的更抗放宽"；现在两条轴
# （severity 二元 + soft 内部按出处的降级序）分开表达。

# 出处 → 丢弃优先级（数字越小越先丢）。default（纯 schema 默认值）最先丢、
# user_stated（用户原话）最后丢——ADR-0014 决策 2：「降级全序：default→
# prior→inferred→user_stated→hard 永不」。
_PROVENANCE_DROP_RANK: dict[str, int] = {
    "default": 0,
    "prior": 1,
    "inferred": 2,
    "user_stated": 3,
}

# 无出处数据时的默认丢弃优先级——取 "prior" 量级（既不像 default 那样抢先丢，
# 也不像 user_stated 那样被过度保护）：多数直调 `relax_tag_search` 的历史
# 调用点（测试 / 未来新增调用方）不传 `tag_provenance`，这些 tag 之间没有
# 谁比谁"更该被保护"的信号，只按调用方原始顺序做稳定排序（tie-break），
# 不再区分"物理 tag 优先级更高"（那是已删除的 `_PRIORITY_TAGS_HIGH` 的语义，
# 现在物理 tag 里真正需要保护的部分已经用 hard 表达，不需要 soft 内部再模拟一次）。
_DEFAULT_PROVENANCE_DROP_RANK = _PROVENANCE_DROP_RANK["prior"]


def _soft_drop_order(
    soft_tags: Sequence[str], tag_provenance: Mapping[str, str] | None
) -> list[str]:
    """soft tag 的丢弃顺序（先丢的排前面）。

    按出处丢弃优先级升序排序；同优先级（含无出处数据的全员同级情形）按
    `soft_tags` 原始出现顺序稳定排序（Python `sorted` 稳定性保证）。
    """
    prov = tag_provenance or {}
    indexed = list(enumerate(soft_tags))
    return [
        t
        for _i, t in sorted(
            indexed,
            key=lambda pair: (
                _PROVENANCE_DROP_RANK.get(
                    prov.get(pair[1], ""), _DEFAULT_PROVENANCE_DROP_RANK
                ),
                pair[0],
            ),
        )
    ]


def relax_tag_search(
    required_tags: Sequence[str],
    source: Sequence[T],
    *,
    extract_tags: Callable[[T], Iterable[str]],
    additional_filter: Callable[[T], bool] = lambda _x: True,
    max_relax_levels: int = 3,
    tag_provenance: Mapping[str, str] | None = None,
) -> tuple[list[T], list[str]]:
    """渐进式 tag 放宽搜索（ADR-0014 决策 2：hard 永不放宽，soft 按出处降级）。

    场景：祖孙三代需「亲子友好+适合老人+无台阶」三 tag 全命中，但 mock 数据
    可能只有 2 条同时挂；硬过滤会打到 0 集。本函数把 required_tags 拆成
    hard/soft 两组：hard 组全程恒定过滤（永不放宽）；soft 组按出处降级序
    （`_soft_drop_order`）逐级丢弃，返回首个非空候选集。

    Args:
        required_tags: 全 required tags（默认要全命中）
        source: 候选源
        extract_tags: 从候选取 tags 列表的函数（如 lambda p: p.tags）
        additional_filter: 额外过滤函数（如距离 / opening_hours），独立于 tag 之外
        max_relax_levels: soft tag 最多放宽几级（默认 3；hard 不计入这个上限，
            因为 hard 从不进入放宽序列）
        tag_provenance: `{tag值: 出处}`（出处 ∈ user_stated/prior/inferred/
            default，见 `schemas.intent.FieldProvenance`）。调用方从
            `intent.field_provenance` 摘取本次 required_tags 对应子集传入
            （见 `schemas.intent.extract_tag_provenance`）；缺省 None——
            soft tag 之间按原始出现顺序稳定丢弃，不做出处区分。

    Returns:
        (matched_candidates, relaxed_tags)
        - matched_candidates: 首个非空候选集（按严格性递减）
        - relaxed_tags: 本次实际丢弃的 **soft** tag 列表（顺序与丢弃顺序一致；
          hard tag 从不出现在这里——它们从未被"丢"，要么全程满足、要么全程
          没有安全候选）。**纯调试信息**：不再驱动任何用户可见的"哪些约束被
          放宽了"告知——该职责已收口到出口满足度审计（`agent.planning.
          critic.exit_audit`），比对最终方案而非搜索期中间尝试。

    设计纪律：
    - hard tag 优先级凌驾于任何丢弃序列之上：全程作为恒定过滤条件参与每一次
      候选筛选，从不进入 `_soft_drop_order`。
    - hard 全部满足但 soft 丢光仍空 → 保底 `additional_filter`（不含 tag
      过滤）；hard 本身不满足（无安全候选）→ 无论 soft 怎么丢都返回空，
      不做"仅 additional_filter"的兜底（一票否决不能被兜底绕过）。
    """
    required_unique = list(dict.fromkeys(t for t in required_tags if t))
    if not required_unique:
        # 无 tag 要求 → 仅过滤
        return [c for c in source if additional_filter(c)], []

    hard_required = [t for t in required_unique if is_hard_tag(t)]
    soft_required = [t for t in required_unique if not is_hard_tag(t)]
    hard_set = set(hard_required)

    def _matches(item: T, active_soft: set[str]) -> bool:
        if not additional_filter(item):
            return False
        tags = set(extract_tags(item))
        if not hard_set.issubset(tags):
            return False
        return active_soft.issubset(tags)

    # Level 0：全命中（hard + soft 全部满足）
    candidates = [c for c in source if _matches(c, set(soft_required))]
    if candidates:
        return candidates, []

    # Level 1..N：按出处降级序逐级丢 1 个 soft tag（hard 恒定过滤，不参与丢弃）
    drop_order = _soft_drop_order(soft_required, tag_provenance)
    relaxed: list[str] = []
    remaining_soft = set(soft_required)
    levels = min(max_relax_levels, len(drop_order))
    for _level in range(levels):
        dropped = drop_order[_level]
        remaining_soft.discard(dropped)
        relaxed.append(dropped)
        candidates = [c for c in source if _matches(c, remaining_soft)]
        if candidates:
            return candidates, relaxed

    # soft 全丢完仍无 → hard 仍是常量过滤条件，仅退化到 additional_filter + hard；
    # hard 本身不满足（含 hard_set 为空但 soft 也丢不出候选）→ 照常返回空
    # （hard 一票否决不接受"仅 additional_filter"的兜底降级）。
    if not remaining_soft:
        candidates = [
            c
            for c in source
            if additional_filter(c) and hard_set.issubset(set(extract_tags(c)))
        ]
        if candidates:
            return candidates, list(soft_required)

    return [], list(soft_required)
