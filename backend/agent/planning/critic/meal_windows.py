"""meal_windows —— 饭点时间窗单一真相源（ADR-0010 D-1）。

【为什么抽出来】

饭点窗口常量（午餐 11:00-13:30 / 晚餐 17:00-20:00 / 夜宵 21:00 起）原来只有
`critic/_rules/checks.py:check_meal_time` 一处消费。ADR-0010 D-1 引入第二个
消费者——`planners/activity_pool.py` 要为餐厅候选构建默认时间窗，同一组域知识
（"什么时候算饭点"）不能有两份拷贝（重蹈 ADR-0008 诊断过的 age_caps 45/90 两处
独立编码、后来分叉的教训）。

本模块把这组常量单独收口，`check_meal_time` 与 `activity_pool` 两边共读：
- `check_meal_time`：判"已排定的 start_time 是否落在饭点" —— 消费开区间左右端点
- `activity_pool`：给餐厅活动构建默认候选时间窗 —— 同样消费这组端点，另加一个
  仅供窗口构造用的夜宵收尾时刻（`check_meal_time` 本身对夜宵不设上限，见下）

【范围声明】

- `TEAHOUSE_CUISINES` / `LUNCH_START_MIN` / `LUNCH_END_MIN` / `DINNER_START_MIN` /
  `DINNER_END_MIN` / `SUPPER_START_MIN`：`check_meal_time` 逐字节沿用的原始常量，
  搬家不改值——只是导入路径变了，`test_meal_time_critic.py` 全绿即钉住行为不变
  （characterization）。
- `SUPPER_END_MIN`：`check_meal_time` 里夜宵窗本来是开区间判断（`start_min >=
  SUPPER_START_MIN`，无上限），因为它只需要判断"够不够晚"。但 `activity_pool`
  要给 D-2 调度器一个可求交集的**有限**窗口，不能真开区间到无穷——按 checks.py
  原注释"夜宵窗口 21:00-次日 2:00"补一个仅供窗口构造用的收尾常量。
  **`check_meal_time` 不导入这个常量，其原有判断逻辑不受影响、不因此改变行为**。

不负责：
- 判定逻辑本身（在 `checks.py:check_meal_time` / `activity_pool.py` 各自实现；
  **例外**：`crossed_meal_window` 完整区间重叠谓词住本模块——它是"饭点窗
  域知识"的第三个消费面（I4 缺席发声），ILS 与 LLM 蓝图两条路径共读同一份
  实现，若放在任一路径内部就是本仓已有的"饭窗判定两处独立实现"（
  `dining_soft_anchored` × blueprint prompt 决策 3/10）的第三份复制）。
- opening_hours 解析（在 `critic._rules.helpers._is_in_business_hours` /
  `activity_pool` 共读同一份正则）。
"""

from __future__ import annotations

from typing import Optional

# 茶点类 cuisine：可落午后非饭点时段（下午茶 / 咖啡 / 甜品）
TEAHOUSE_CUISINES: frozenset[str] = frozenset({"下午茶", "咖啡", "烘焙甜品"})

# 午餐窗口 11:00-13:30；晚餐窗口 17:00-20:00；夜宵窗口 21:00 起
LUNCH_START_MIN = 11 * 60  # 11:00
LUNCH_END_MIN = 13 * 60 + 30  # 13:30
DINNER_START_MIN = 17 * 60  # 17:00
DINNER_END_MIN = 20 * 60  # 20:00
SUPPER_START_MIN = 21 * 60  # 21:00（夜宵；含烧烤/火锅等夜宵正餐）

# 仅供 activity_pool 窗口构造用（check_meal_time 不读）：夜宵窗收尾于次日 02:00，
# 与 critic._rules.helpers.parse_hhmm/fmt_hhmm 的跨日分钟表示法（0-29h）同一记法。
SUPPER_END_MIN = 26 * 60  # 次日 02:00


# ============================================================
# 完整区间重叠谓词（四条不变式批 I4 · C6，2026-07-11）
# ============================================================

MEAL_ABSENCE_MIN_OVERLAP_MIN = 45
"""缺席发声的最小重叠阈值（分钟，拍板项 P4）：出行窗与饭点窗重叠不足 45 分钟
时连吃饭的物理时间都不够，点破反而矫情——45 取自最短 typical_dining_min 量级。"""

_MEAL_WINDOWS_FOR_ABSENCE: tuple[tuple[str, int, int], ...] = (
    ("午饭", LUNCH_START_MIN, LUNCH_END_MIN),
    ("晚饭", DINNER_START_MIN, DINNER_END_MIN),
    ("夜宵", SUPPER_START_MIN, SUPPER_END_MIN),
)


def crossed_meal_window(start_min: int, end_min: int) -> Optional[str]:
    """完整区间重叠谓词：出行窗 [start_min, end_min] 真实压过哪个饭点窗？

    返回**时间序最早**的达标饭点窗名（"午饭"/"晚饭"/"夜宵"），全不达标返 None。

    【这是什么问题（I4 缺席发声的检测谓词，方案 1.9 红队修订 A）】判"计划
    没排饭要不要说一声"需要**完整**的区间重叠：标准公式
    `overlap = min(end, w_end) - max(start, w_start)`，重叠 ≥45 分钟
    （`MEAL_ABSENCE_MIN_OVERLAP_MIN`）才算"这顿饭真的在你的时段里"。

    【与 `dining_soft_anchored`（route_builder）是两把尺子，勿"顺手统一"】
    该函数判"要不要**强制排**饭"——高门槛判定，规则**有意收窄**（只认"出行
    窗结束点落在饭点窗内"或"完整跨过"两种，出行窗**起点**落在饭点窗中间的
    情形不算），防误锚。本谓词判"没排饭要不要**说一声**"——低门槛告知，必须
    完整防漏说：S1（周五 19:00 出发 3-4h 晚 K）在收窄尺下晚饭窗两条件都不中
    （结束点 22:00+ 不在窗内、起点 19:00 > 17:00 完整跨过不成立），反而经
    夜宵窗误命中——原型场景要么检测不到晚饭缺席、要么报错饭名。两个谓词
    分开实现是语义使然，不是重复建设。

    【规则细节（拍板项 P4）】
    - 多窗同时达标（S1 同时压晚饭窗 60min 与夜宵窗 120min+）→ 报**时间序
      最早**的达标窗：用户的常识预期是"这个点出门该吃晚饭了"，晚饭缺席才是
      他会奇怪的那一顿；夜宵本来就是可选项。一次只点破一顿。
    - **夜宵窗仅当整个出行窗都落在夜宵时段**（start_min >= 21:00）才参与
      判定——傍晚出发的局压到夜宵窗尾巴不构成"该吃夜宵"的常识预期；
      午/晚窗恒参与。
    - 取材纪律（调用方义务，方案 1.18）：出行窗用**最终行程实际时段**
      `[parse(nodes[0].start_time), 同左 + total_minutes]`——起点字符串永不
      回卷（折叠只后移到首活动 start 之前）、span 用未回卷的 total_minutes，
      全程不解析可能 mod-24 回卷的尾部时刻字符串；夜宵窗上界 26:00（次日
      02:00 记法）与未回卷的 end_min 直接可比。**不用 intent 声明窗**——
      本地人 3 秒常识审视的对象是摆在眼前的这份计划，不是当初的意图。
    """
    if end_min <= start_min:
        return None
    for name, w_start, w_end in _MEAL_WINDOWS_FOR_ABSENCE:
        if name == "夜宵" and start_min < SUPPER_START_MIN:
            continue  # 夜宵仅整窗落夜宵时段才点破（P4）
        overlap = min(end_min, w_end) - max(start_min, w_start)
        if overlap >= MEAL_ABSENCE_MIN_OVERLAP_MIN:
            return name
    return None
