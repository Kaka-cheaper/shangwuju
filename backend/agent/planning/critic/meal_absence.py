"""meal_absence —— 饭缺席发声的三分叉互斥编排（四条不变式批 I4 · C6）。

【这是什么问题】presence obligation vs absence explanation 的经典对立：系统有权
按设计不排某顿饭，但"不做"和"不说"是两回事（I4：缺席必须发声，诚实带出路）。
本模块是那**一个**生成函数的家——两条 advisory 码 + 一句轻确认由同一个 if/else
产出，以 `intent.explicit_dining_requested` 为唯一分叉点，结构上互斥（不是生成
后再去重）：单一分叉点、单一产出点，杜绝两条诚实同时进口播的自相矛盾
（"一边说我按设计没排、一边实际是想排没排上"）。

【三分叉（方案 1.9 红队修订 C + 1.28 tristate）】
- `True` 且最终无饭 → `MEAL_REQUESTED_UNSEATED`：显式失败——设计明明想排
  （C5a 的触发与 critic 护栏都试过了），措辞必须承认"试了没成"+给出路，
  **绝不能**说"默认你吃过来"。
- `None` 且出行窗真实压过某饭点窗且最终无饭 → `MEAL_OMITTED_BY_DESIGN`：
  常识缺席——按设计不排是合理选择不是缺陷，但不能沉默；点破 + 给路
  （"想加一顿跟我说"），与"方案卡口播 4 拍结构"第 3 拍同一哲学血脉。
- `False` → 无码轻确认句："按你说的，这次没排饭"——缺席是用户自己点的，
  发声退化为一句复述确认（防"它记住了吗"追问），不占 advisory 通道
  （不进 SSE narrationMessages 的结构化条目，只进口播 honest 段）。

【一份实现两处消费】LLM 蓝图路径（`agent.graph.nodes.narrate.narrate_node`）
与 ILS 路径（`agent.planning.planners.ils_planner._build_success_advisories`）
都 import 本函数——两路对同一 itinerary 可能都产出，靠 narrate 侧
`_merge_advisories` 的同 message 去重合并成一条（既有机制，非本批新增）。
检测窗谓词在 `meal_windows.crossed_meal_window`（饭点窗域知识单一真相源）。

不负责：
- 呈现顺序（cap 优先序在 narrate_node 组装 advisories 处排——cap 函数本体
  吃纯消息串看不到 code，排序必须发生在组装处，方案 1.34-W2）。
- 与 unmet_cuisines 通道的"同一顿饭只道歉一次"去重（在 narrate_node）。
"""

from __future__ import annotations

import re
from typing import Optional

from schemas.advisory import Advisory, AdvisoryCode

from .meal_windows import crossed_meal_window

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

MEAL_ABSENCE_LIGHT_CONFIRM = "按你说的，这次没排饭。"
"""tristate=False 的轻确认句（无码纯句，narrate 侧拼进口播 honest 段）。"""


def _actual_window_min(itinerary) -> Optional[tuple[int, int]]:
    """最终行程实际时段（分钟）：[parse(nodes[0].start_time), 同左+total_minutes]。

    防跨午夜回卷的取材公式（方案 1.18）：起点字符串永不回卷（折叠只后移到首
    活动 start 之前，凡通过 critic 的方案起点必在当日域内），span 用未回卷的
    `total_minutes`——全程不解析可能 mod-24 回卷的尾部时刻字符串。解析不出
    （空方案/异常形状）→ None（调用方按"无窗可判"处理，宁缺毋误报）。
    """
    nodes = getattr(itinerary, "nodes", None) or []
    if not nodes:
        return None
    start_str = getattr(nodes[0], "start_time", "") or ""
    if not _TIME_RE.match(start_str):
        return None
    h, m = start_str.split(":")
    start_min = int(h) * 60 + int(m)
    total = getattr(itinerary, "total_minutes", 0) or 0
    if total <= 0:
        return None
    return (start_min, start_min + total)


def build_meal_absence_signal(
    intent, itinerary
) -> tuple[Optional[Advisory], Optional[str]]:
    """三分叉互斥编排的唯一产出点。

    Returns:
        (advisory, light_confirm)——**至多一个非 None**：
        - (`MEAL_REQUESTED_UNSEATED` advisory, None)：显式要吃饭但最终无饭；
        - (`MEAL_OMITTED_BY_DESIGN` advisory, None)：没提及、出行窗真实压过
          某饭点窗、最终无饭；
        - (None, 轻确认句)：显式不要排饭且最终确实无饭；
        - (None, None)：方案里有餐厅节点（无缺席可言）/ 不满足任何分叉条件。
    """
    if intent is None or itinerary is None:
        return (None, None)

    nodes = getattr(itinerary, "nodes", None) or []
    if any(getattr(n, "target_kind", None) == "restaurant" for n in nodes):
        # 方案里有饭——不存在"缺席"，三分叉全不触发（含 False 态：方案与
        # 用户要求不符时不做虚假确认，那是 critic/prompt 层的责任）。
        return (None, None)

    edr = getattr(intent, "explicit_dining_requested", None)

    if edr is True:
        return (
            Advisory(
                code=AdvisoryCode.MEAL_REQUESTED_UNSEATED,
                message=(
                    "你说想吃饭，这顿我试了没排上——附近暂时没找到能同时满足"
                    "这些条件的合适餐厅。放宽一点条件，或告诉我想吃什么，"
                    "我再试一次。"
                ),
            ),
            None,
        )

    if edr is False:
        return (None, MEAL_ABSENCE_LIGHT_CONFIRM)

    # None（没提及）：只有出行窗真实压过某饭点窗才点破（常识缺席）
    window = _actual_window_min(itinerary)
    if window is None:
        return (None, None)
    meal_name = crossed_meal_window(window[0], window[1])
    if meal_name is None:
        return (None, None)
    return (
        Advisory(
            code=AdvisoryCode.MEAL_OMITTED_BY_DESIGN,
            message=(
                f"没有给你排{meal_name}，默认你们是吃过来的——"
                "想加一顿跟我说，我补进去。"
            ),
        ),
        None,
    )


__all__ = ["build_meal_absence_signal", "MEAL_ABSENCE_LIGHT_CONFIRM"]
