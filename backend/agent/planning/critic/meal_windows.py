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
- 判定逻辑本身（在 `checks.py:check_meal_time` / `activity_pool.py` 各自实现）。
- opening_hours 解析（在 `critic._rules.helpers._is_in_business_hours` /
  `activity_pool` 共读同一份正则）。
"""

from __future__ import annotations

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
