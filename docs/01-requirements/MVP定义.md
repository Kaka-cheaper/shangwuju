# MVP 定义

> "做完哪些算最小可演示版本"。MVP = Minimum Viable Product。
>
> 评委演示的下限不是"代码跑起来"，而是"能讲出完整故事"。

## 一、MVP 的目标

**1 个用户、1 个场景、1 条主路径、1 个异常分支** —— 跑通从输入到转发文案的完整闭环。

如果时间盒只够做 50% 的功能，**先保证 MVP 100% 闭环**，再考虑加场景 / 加 UI / 加更多 Tool。

## 二、MVP 必做项（MVP-1）

### MVP-1.1 输入

- 主路径接收一句话固定文案（家庭场景，题目原文）：「今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。」
- **意图解析层从一开始就走开放路径**（D9 决议）：不论输入什么都走 LLM 动态抽取约束、**不**写 `if scene_type == "family"` 分支
- MVP-1 验收仅要求主输入能跑通；MVP-2 验收「意图解析的开放鲁棒性」

### MVP-1.2 Tool 集（最小 6 个）

按 `需求分析.md` §M3，MVP 只需 6 个：

- `get_user_profile`——返硬编码用户画像（家庭位置 / 默认预算 / 交通偏好）；**不包含「场景类型」字段**（场景仅从输入意图抽取）
- `search_pois`——返 5km 内亲子 POI 列表（≥ 8 条）
- `search_restaurants`——返 5km 内健康餐厅列表（≥ 10 条）
- `check_restaurant_availability`——返某餐厅指定时段是否有位
- `reserve_restaurant`——返预约成功 + 订单号
- `generate_share_message`——返可转发文案字符串

> `buy_ticket` / `estimate_route_time` / `order_extra_service` 进 MVP-2。

### MVP-1.3 规划循环

实现 ReAct 循环：

```text
用户输入
  → LLM Function Calling 解析意图（Mock 直接抽出约束）
  → 调 get_user_profile（拿位置/偏好）
  → 调 search_pois（拿 8 个候选）
  → LLM 筛选 1 个最适合 5 岁的 POI
  → 调 search_restaurants（拿 10 个候选）
  → LLM 筛选 2 个候选餐厅
  → 调 check_restaurant_availability(候选 1, 17:00)
    → 假设满 → 触发异常分支
  → 调 check_restaurant_availability(候选 2, 17:00)
    → 有位
  → 调 reserve_restaurant
  → 调 generate_share_message
  → 输出最终方案
```

### MVP-1.4 异常分支（必触发）

- **E1. 餐厅没位**：第一家餐厅 17:00 满 → 系统切换到候选餐厅 2
- 这一步**必须在演示中体现**——评分硬性项（参考 `比赛详情.md` §赛道 06 → "异常处理机制"）

### MVP-1.5 输出

3 段输出（按顺序展示给评委）：

1. **Tool 调用日志**（中间过程，字段名按 `需求分析.md` §5.7 schema）：

   ```text
   [1] 解析意图（IntentExtraction）:
       start_time=today_afternoon / duration_hours=[4,6] / distance_max_km=5
       companions=[{role:妻子,count:1},{role:孩子,age:5,count:1}]
       physical_constraints=[亲子友好, 适合 5-10 岁]
       dietary_constraints=[低脂, 健康轻食]
       social_context=家庭日常
       parse_confidence=0.88
   [2] search_pois(distance_max_km=5, physical_constraints=[亲子友好]) → 8 条候选
   [3] 筛选 age_range 覆盖 5 岁 → 4 条
   [4] search_restaurants(dietary_constraints=[低脂, 健康轻食], distance_max_km=5) → 10 条
   [5] check_restaurant_availability(restaurant_id=R001, time=17:00) → available=false → 切换 17:30
   [6] reserve_restaurant(restaurant_id=R001, time=17:30, people=3) → 订单号 R20260507_001
   [7] generate_share_message(...) → 口语化文案
   ```

2. **行程方案**（结构化卡片）：

   ```text
   家庭半日方案：
     14:00 出发
     14:25 抵达「森林儿童探索乐园」
     14:30-16:30 亲子游玩
     16:30-17:00 转场
     17:00-18:20 「轻食研究所」晚餐（已预约 17:00，3 人位）
     19:30 回家
   已为你预留：轻食研究所 17:00 三人位（订单号 R20260507_001）
   ```

3. **转发文案**（口语化）：

   > "搞定了，下午 2 点出发。先去森林儿童探索乐园，孩子能玩两个小时；晚饭订了附近的轻食研究所，有低脂餐和儿童餐，不会太累。大概 7 点半前能回家。"

### MVP-1.6 Demo 形态

- **首选**：极简 Web 页面（输入框 + 提交 + 三段输出展示）
- **兜底**：命令行（`python main.py "<用户输入>"` → 打印三段输出）

> 选哪个看 `架构选型.md` §2 决策。

## 三、MVP-2（行有余力做）

按时间盒优先级排序：

### MVP-2.1 加 Tool（剩余 2-4 个）

- `buy_ticket`——加门票购买 → 触发 E2 售罄异常分支
- `estimate_route_time`——加路线时间 → 体现"距离合理"约束
- `order_extra_service`——加蛋糕/鲜花 → 锦上添花

### MVP-2.2 演示场景集（6-8 个开放场景，本赛最大加分项）

> 代替原「朋友场景」单独项。详见 `演示场景集.md`。

- 准备 6-8 个覆盖关系多样性的场景：家庭 / 朋友 / 情侣 / 带父母 / 闺蜜 / 同事 / 独处 / 跨代际
- 每个场景仅**扩充 Mock 数据 + 输入用例**，**不**改 Tool 代码、**不**写 if-else 分支
- Demo 现场两种入口并存：
  - 6-8 个「快捷输入」按钮（已压力测试过，演示稳定）
  - 输入框（评委可即兴扔任意输入，体现开放性）
- 评分目的：评委直观感受到 Agent 能跨场景泛化，不是模板系统
- Mock 数据规模随之扩大（见 `架构选型.md` D4 更新后版本）

### MVP-2.3 用户确认 → 执行

- 在"输出方案"和"调用 reserve_restaurant"之间插入确认步骤
- 用户点「确认」→ 系统才调执行类 Tool（vs MVP-1 一气呵成）
- 体现"Agent 不是擅作主张，而是辅助决策"

### MVP-2.4 加分项 UI

- 行程时间轴卡片（带图标 / 缩略图）
- Tool 调用链路可视化（带连线 / 状态标记）
- 转发文案"复制按钮"

## 四、MVP-3（如果时间还有余）

- **开放鲁棒性压力测（D9 加分项）**：准备 ≥10 句未预演练过的输入在 demo 后现场扔进去，Agent 走主路径不报错
- 用户画像可选输入——让用户自定义 home 位置 / 预算上限
- 第 3 个异常分支（E3 距离超限 / E4 总时长超 6h）
- 多 LLM 切换（DeepSeek vs 通义对比）
- 在 Tool 调用日志中可见「意图抽取后的结构化约束」，让评委看到 Agent 思考过程

## 五、MVP 不做项

明确不做（即使时间够也不做，**避免分散注意力**）：

- ❌ 用户账号系统
- ❌ 行程历史记录
- ❌ 多 city 适配
- ❌ 个性化学习
- ❌ 真实 API 集成
- ❌ 移动端适配（桌面浏览器够用）

## 六、MVP 验收点速查

| MVP 项 | 完成判据 |
|---|---|
| MVP-1.1 输入 | 主输入能跑；意图解析层走开放路径（代码中无 `if scene_type` 分支） |
| MVP-1.2 Tool | 6 个 Tool 全部按 JSON Schema 定义且有 Mock 实现 |
| MVP-1.3 规划循环 | LLM 能基于 Tool 输出做下一步决策 |
| MVP-1.4 异常分支 | E1 触发可见，且系统正确切换备选 |
| MVP-1.5 输出 | 三段输出齐全 |
| MVP-1.6 Demo 形态 | 端到端能跑通（Web 或 CLI） |
| MVP-2.2 演示场景集 | 至少 4 个场景能跑不报错（家庭主场景 + 3 个跨关系场景） |
| MVP-3 开放鲁棒 | 现场扔任意输入能跑主路径（可在预留输入池外） |

具体每项的验收方式见 `验收标准.md`。

## 七、时间盒估算（待 user 输入团队人数后填）

> Hackathon 一般 24-48h；具体时间盒待 user 给出。

参考估时（单人）：

| 阶段 | 估时 |
|---|---|
| 架构选型决策 | 1-2h |
| Mock 数据 + 6 个 Tool | 4-6h |
| Agent 编排循环（含异常分支） | 4-6h |
| Web UI（如做） | 4-8h |
| 设计文档（≤ 2 页） | 1-2h |
| Demo 排练 + 录屏 | 2-3h |
| **合计（单人 MVP）** | **16-27h** |

→ 单人 24h hackathon 做完 MVP-1 + MVP-2 部分项；3 人队可能做完 MVP-1 + MVP-2 全部 + 部分 MVP-3。
