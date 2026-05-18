# 踩坑笔记

> 实现过程中**踩到一次的坑**记下来，不要让自己 / 队友 / 后来 AI 再踩。
>
> 优先级：**P1 = 必须避免（会让 demo 跑不起来）/ P2 = 应该避免（会浪费时间）/ P3 = 可以了解（背景知识）**

## 一、记录格式

每条踩坑按以下结构（参考 Polisim `pitfalls.md`）：

```markdown
### [Px] YYYY-MM-DD <一句话标题>

- **现象**：看到了什么
- **根因**：为什么会这样
- **解法**：怎么绕 / 修
- **相关文件**：`path/to/file.py:L10-L20`
- **防再犯**：是否需要加进 `AGENTS.md` 的 MUST NOT
```

## 二、预埋已知坑（hackathon 通用，未实现就先记下）

### [P2-预埋] LLM Function Calling 的参数名 / 类型 hallucination

- **现象**：LLM 调 `search_pois` 时把 `distance_max_km` 写成 `max_distance` 或 `5km`（字符串）
- **根因**：LLM 对 JSON Schema 描述不严格遵守；中文模型尤甚
- **解法**：
  - Tool 定义里给参数加详细 description（含示例值）
  - Pydantic 校验输入参数，校验失败 → Agent 把校验错误回灌给 LLM 让它重试
  - System prompt 里加一句"严格按 schema 调用，不要发明字段"
- **防再犯**：进入 Tool 实现阶段后第一时间在 `pyproject.toml`（或 `requirements.txt`）固定 `pydantic` 版本，避免 v1/v2 字段定义差异

### [P2-预埋] 中文 LLM 输出 JSON 时混入 markdown 代码块

- **现象**：LLM 返回 `\`\`\`json\n{...}\n\`\`\``，直接 `json.loads()` 会失败
- **根因**：很多模型把"输出 JSON"理解为"输出 markdown 形式的 JSON"
- **解法**：
  - 解析前用 regex 剥掉 \`\`\` fence
  - System prompt 强调"直接输出 JSON，不要 ```json"
  - 用 OpenAI 兼容的 `response_format={"type": "json_object"}`（DeepSeek / 通义都支持）
- **防再犯**：写 LLM 调用 wrapper 时统一加 fence 剥离

### [P3-预埋] hackathon 现场网络不稳

- **现象**：现场跑 demo 时 LLM API 超时 / 限流
- **根因**：hackathon 现场可能多人共享网络 / 主办方限速
- **解法**：
  - 准备录屏兜底
  - 部署本地 mock LLM（用固定 response 模拟，纯演示用）
  - 提前测试 1 次完整 demo 在现场网络环境下
- **防再犯**：在 `MVP定义.md` §六的"评委 5 分钟观察清单"里加"录屏兜底"项

### [P2-预埋] Mock 数据缺业务约束 → 异常分支不触发

- **现象**：写完所有 Tool + 跑 demo，发现没有任何餐厅返回 `available=false`，异常分支演示不出来
- **根因**：Mock 数据是手写的，写的时候忘了埋失败案例
- **解法**：
  - Mock 数据一开始就**显式标记**至少 2 条 `available=false` / `sold_out=true`
  - 写 Mock 数据时同步起一个 `mock_data/README.md` 标注哪些条目用于触发哪个异常
- **防再犯**：`AGENTS.md` §3.4 已写"至少 2 个 Tool 在 Mock 数据里埋失败案例"，写 Mock 时即检查

### [P3-预埋] LLM 倾向"过度规划"——加冗余 Tool 调用

- **现象**：LLM 已经拿到答案了，仍然调一遍 `check_weather` / `estimate_route_time` / 其他无关 Tool
- **根因**：模型有"想多调几次显得严谨"的倾向；System prompt 没限制调用次数
- **解法**：
  - System prompt 明确"每个 Tool 在 1 次规划循环里最多调 2 次，重复调用必须有新约束"
  - Agent 编排层加调用次数计数器，超限直接打断
- **防再犯**：Agent 实现时加 `max_tool_calls=10` 上限

### [P2-预埋] Streamlit / Gradio 的 stateful 重跑陷阱

- **现象**：每次用户点按钮，整个页面重跑一遍，LLM 调用重复触发
- **根因**：Streamlit 默认每次交互重跑整个 script
- **解法**：
  - 用 `st.session_state` 缓存上次 LLM 调用结果
  - 关键状态用 `@st.cache_data` 缓存
  - 区分"首次输入"和"中间状态展示"
- **防再犯**：进入 Streamlit 实现阶段时，先看 [Streamlit caching docs](https://docs.streamlit.io/library/advanced-features/caching)；首个 demo 后即测一次"二次点击是否重跑 LLM"

## 三、实际踩坑（实现开始后追加）

> 暂无。实现开始后按 §一 格式追加。

## 四、跨项目沉淀（来自 Polisim 经验，hackathon 也适用）

### [P3-跨项目] 文档与 schema enum 漂移

- **来源**：Polisim 项目踩坑（pitfalls.md 2026-04-29）
- **教训**：写 client 代码涉及枚举时，**先 grep schema 看实际值**，不要从文档文字猜
- **本项目应用**：写前端 / Agent 代码引用 Tool 名 / 参数名时，**先看 Tool 定义代码**，不要从 mockup / 设计文档猜（mockup 文字可能与代码 drift）

### [P3-跨项目] dotenv 双重保险加载

- **来源**：Polisim 项目（pitfalls.md 2026-05-06）
- **教训**：CLI 入口 + 服务入口都要 `load_dotenv()`——uvicorn `--reload` 子进程会跳过 CLI 入口
- **本项目应用**：如果选 FastAPI + uvicorn，主入口 `backend/main.py` 顶部加 `from dotenv import load_dotenv; load_dotenv()`；如果选 Streamlit，单文件加一次即可

### [P3-跨项目] API 限流 / 重试

- **来源**：Polisim 项目 D-011 LLM 错误处理设计
- **教训**：LLM 调用必带 timeout + 至少 1 次重试；429 限流要指数退避
- **本项目应用**：写 `LLMClient` wrapper 时统一加 timeout=30s + retry=2，避免 hackathon 现场单次卡死

### [P1-预埋] 意图解析翻车：评委扔不常见词、LLM 抽取到错类型

> D9 后新增预埋坑（走全开放路径后这是最可能的翻车点）。

- **现象**：评委输入「带个老师出去聊聊」（亲近场景？商务场景？），LLM 可能抽为 `companions: [师生]` + `experience: [体验业务]`——Tool 查不到匹配餐厅
- **根因**：全开放意图解析的代价是「抽取不到低频词」；中文多义（老师 = 老酒友？老师傅？会计师？）造成错类型抽取
- **解法**：
  - System prompt 里加「词典出口」：列出 `social_context` 可选值（家庭日常 / 朋友热闹 / 情侣亲密 / ...），告诉 LLM「从这些里选一个最接近的，不要发明」
  - 同时约束 tag 词典（架构选型 D4）：人物 / 饮食 / 体验 三类仅接受预定 tag
  - 下游 Tool 查询返空集时要优雅降级：返「未找到完全匹配，为你推荐 X」而不是报错
  - 现场 Demo 提供「重新描述」按钮：让评委能重试
- **防再犯**：在 `演示场景集.md` 预埋词典；同时准备 5 句「词典边界输入」（多义词 / 方言）提前测试

### [P2-预埋] D9 后代码处需避免场景枚举隐性名称

- **现象**：Tool 参数里出现 `relation: "family" | "friends" | ...`——实质上仍是枚举型分支
- **根因**：开发者习惯从场景名称出发设计参数
- **解法**：Tool 参数只接受三类约束 tag + 同行人结构 + 距离 / 时长 三组参数，不接受 `relation` / `scene_type` 这种顶层名称
- **防再犯**：验收 A17 的“反向检查”项 + Tool JSON Schema review

### [P2-预埋] Mock 数据只覆盖 2 场景→开放输入返空集

- **现象**：评委输入「一个人安静呆会」，Tool 返 0 条餐厅——因为 Mock 里没有任何 `独处舒缓` tag 的餐厅
- **根因**：Mock 数据准备时只看主场景需求，未按 `架构选型.md` D4 跨场景覆盖表补全
- **解法**：在 Mock 数据提交前跑 `演示场景集.md` §四 自检查表，每条查询必须达到下限
- **防再犯**：`演示场景集.md` §四 自检查表是交付 gate

## 五、不要踩的元规则坑（项目流程层面）

### [P1] 不要在选型未定时就开始写代码

- **现象**：D1 / D2 都没决定，但有人已经在写 `backend/agent/planner.py`
- **后果**：选型变了 → 代码全部重写
- **解法**：选型阶段只能改 `docs/`，不能 `mkdir backend`
- **防再犯**：`AGENTS.md` §4.3 已写"在没决定选型前新建 `backend/` 或 `frontend/` 顶层目录禁止"

### [P1] 不要为多场景复制两套 Tool 代码 / 写场景枚举分支

> 2026-05-07 D9 决议后重写：原「不要并行做 2 个场景」现面向「代码底层设计」，不再限制「演示范围」。

- **现象**：两人各抱一个场景独立写代码，联调发现 Tool 接口 / Mock 数据格式不统一；或者代码里出现 `if scene_type == "family": ... else: ...`
- **后果**：
  - 合并成本 = 重写一半代码
  - 评委面试时任意输入（情侣 / 商务 / 独处）会直接跳进 else 分支走错路径——A17 / A18 验收项直接拿不到分
- **解法**：
  - **唯一路径**：意图解析层仅输出约束 JSON（参考 `需求分析.md` §五 词典），Tool / Agent 仅看约束字段、不看场景名称
  - **场景扩展走数据**：新场景只补 Mock 数据的 tag，不改代码
  - **代码 grep gate**：提交前 grep `scene_type` `relation_type` `if scene ==` 必须为空
- **防再犯**：`AGENTS.md` §3.5 + §4.2 + 架构选型 D9；验收标准 A17 反向检查项

### [P1] 不要演示前才发现异常分支没触发

- **现象**：上台前最后一遍 dry run，发现餐厅没位的异常没触发，临时改 mock 数据来不及
- **后果**：评分项 A6 / A7 拿不到分
- **解法**：MVP-1.4 完成后第一时间录屏一次"异常分支触发"作为基线证据
- **防再犯**：`验收标准.md` §六 的 5 分钟观察清单已要求

## 六、给后来 AI 的提示

- 进入项目第一件事：读 `AGENTS.md` § 五的 30 秒恢复通道
- 读完 §二的"预埋已知坑"6 条，避免重复踩
- 实现过程中踩了**新坑**：写到 §三 "实际踩坑" 段落，不要写到 §二
- 不确定要不要记：**记**。比"该记没记"安全 10 倍

### [P2] 2026-05-16 RestaurantCapacity alias dump 漂移导致 invoke_tool 二次校验崩

- **现象**：意图解析家庭主场景跑通，search_restaurants 直接调函数也返候选 2 条，但通过 `invoke_tool("search_restaurants", ...)` 调用就返 `UPSTREAM_FAILURE`，end-to-end 测试连续报 "餐厅候选为空"。
- **根因**：`RestaurantCapacity` 用了 alias（`"2"/"4"/"6"/"8"`）配合 Python 不允许数字开头变量名的 workaround（字段名 `two/four/six/eight`）。`invoke_tool` 二次校验时先 `output.model_dump()`（默认输出字段名 `two/four/...`）再 `output_model.model_validate(...)`（默认期待 alias `"2"/"4"/..`）→ 字段名 vs alias 不匹配 → ValidationError → 上层包装为 `UPSTREAM_FAILURE`。
- **解法**：在 `RestaurantCapacity.model_config` 加 `populate_by_name=True`，让 dump 输出的字段名也能反向 validate。**不**改字段名 / **不**改 mock_data JSON 写法（依然用 `"2": true`）。
- **相关文件**：`backend/schemas/domain.py:84-99`、`backend/tools/registry.py:158-167`
- **防再犯**：
  - 任何带 alias 的 BaseModel **必须** `populate_by_name=True`，否则 `model_dump → model_validate` 链路会断
  - 把这条加进 schema 设计 checklist：但凡用 `Field(..., alias=...)` 就要同步设 `populate_by_name=True`
  - 可选改进：把字段名直接改成 `seats_2/seats_4/...` 避开 alias，但需要 mock_data JSON 也跟改（暂不改）
- **优先级**：P2（不会让 demo 跑不起来，但会让多 Tool 链路联调时间炸 30 分钟以上）

### [P2] 2026-05-16 multi-agent 场景下 AI 越界 sync 别人的 feature

- **现象**：A 角色（W2）完成 P2 后跑 CodeSee sync，发现 12 个 feature 还挂 `tags: ['planned']`（W1 真 Tool / W3 前端确实已写完测试也过，但他们没 commit 没 sync）。AI 自作主张写了批量升级脚本，把 10 个不属于自己 owner 的 feature 都升级了。用户当场指出"multi-agent 场景下应该是各 agent 只 sync 自己的"，触发 revert。
- **根因**：AI 把"仓库整体实现度真实"误当成"我应该负责修正"。multi-agent 范式下：
  - 每个窗口只 sync 自己 owner 的 feature
  - 别人的 feature 由别人那个窗口的 agent 自己 sync
  - 即使别人没 commit、features.json 暂时失真，也是别人窗口的责任，不是当前窗口的事
  - "失真"是分布式系统的合理中间状态，**不是 bug**
- **错误连锁**：随后又用 `git revert --no-commit` 后 commit 时没指定文件清单，把 14 个 W1 owner 的 untracked 文件也带进 revert commit——**第二次越界**。
- **解法**：
  - revert 越界 commit；用 `git reset --soft + git reset HEAD <他人文件>` 精准 unstage
  - 越界产生的"长效解法"提议（auto-detect-implemented.mjs 之类）一律撤回——这些是把越界合理化的工具
- **相关文件**：本次 revert 涉及 `.codesee/features.json` / `docs/03-implementation/pitfalls.md` / `problem.md`
- **防再犯**：
  - sync 前先问「这个 feature 谁 owner？」owner 不是自己 → 不动
  - commit 前用 `git diff --cached --stat` 看清晰范围；untracked 文件不应进 revert commit
  - 看到 features.json 失真不要本能去"修复"——多窗口异步是正常状态
- **优先级**：P2（不影响 demo 跑通；但破坏 multi-agent 协作边界，留下错误的"长效解法"会污染后续 prompts 设计）

### [P2] 2026-05-17 Phase 0.7 persona prior 注入策略迭代（保守补全 vs 过严候选）

- **现象**：方案 C 个性化首版 prompt 写「prior 含相关 tag → 把 top 1-2 个补进去」。商务白领 persona 输入「今天下午想出去玩」时，LLM 把 physical/dietary/experience 三类 prior tag 全塞，加上 social_context=商务接待 + capacity 默认 → search_pois **empty_candidates**（mock 数据商务走向 POI 仅 1 条）。
- **根因**：双重过滤陷阱——prior 把 tag 全塞进 IntentExtraction，下游 search_pois 用「全部命中」过滤，候选必空。
- **二次踩坑**：第一版改成「保守补全（默认空）」后，LLM 把模糊输入误判为「独处放空」，因为没有 social_context prior 引导。
- **第三版**（最终）：
  - **social_context 必注**：persona.suitable_for_priority[0] 是 user 身份核心标识，**必须**注入
  - **distance 必注**：persona.default_distance_max_km 也必注
  - **physical/dietary/experience 默认空**：除非用户输入有明确暗示
  - **planner 五级降级兜底**：即使 prior 过严，逐级剥离重试到候选非空
- **解法**：
  - `backend/agent/prompts/system_prompt.py` `build_intent_parser_system_prompt_with_priors`
  - `backend/agent/planner.py` `_query_pois` / `_query_restaurants` 多级降级 + Tool quota 提升（3→5）
- **相关文件**：
  - 第一版 prompt：commit bb7c43c
  - 第三版迭代：本次 commit
  - 浏览器实测：商务白领与新手爸爸同句不同方案演示通过
- **防再犯**：
  - prior 注入「开放性维度（distance / social_context）必注，封闭性维度（tag）保守补」
  - 任何 prior 影响下游过滤的设计，必带 fallback 兜底链
  - mock 数据稀疏的 social_context 走向（商务、独处、跨代际）需要在 prior 注入时给「降级开关」
- **优先级**：P2（不影响 demo 跑通；但首版会让评委演示时碰到 empty_candidates，影响印象）


### [P1] 2026-05-17 行程"5 段写死"反模式（架构级根因）

- **现象**：用户反馈"我只有一个小时"，refiner 把 `intent.duration_hours` 改到 [1,1] 后，下游 planner 仍强塞「出发 + 主活动 + 转场 + 用餐 + 返回」5 段，导致总时长仍 5+ 小时（用户截图复现）。
- **根因（架构级）**：
  - `演示场景集.md §三` 把 5 段当模板期待结构 → `planner._assemble_itinerary` 把 5 段写进 list 字面量 → `critics.HardConstraintCritic` 把"5 段缺失"作硬违规 → `test_8_scenarios.py` 等多处断言 `len(stages) >= 5`
  - 文档→代码→测试三层都把 5 段当默认，导致**段集合**不是 IntentExtraction 的函数，而是死常量
  - 即使 refiner 改对了 duration，下游不消费段数维度的变化
- **解法**：
  - 新增 `agent/segment_decider.py`：`decide_segments(intent) -> frozenset[str]`，按 duration / social / dietary 推导
  - `planner._assemble_itinerary(segments=...)` 按段集合选段拼装
  - `critics.HardConstraintCritic` 改成「按 intent 决定的 segments 判段缺失」
  - `planner_hybrid.plan_hybrid` 检测到 segments != FULL_SEGMENTS 直接 fallback rule（ILS 假设 POI×餐厅 笛卡尔积，削段下不适用）
  - 改写 4 处测试断言（test_8_scenarios / test_e2e_refinement / test_llm_planner / test_segment_decider）从「硬要 5 段」改为「按 intent 期望段数」
- **相关文件**：
  - `backend/agent/segment_decider.py`（新）
  - `backend/agent/planner.py` `_assemble_itinerary` / `plan_itinerary`
  - `backend/agent/planner_hybrid.py` `plan_hybrid`
  - `backend/agent/critics.py` `_hard_constraint_critic`
  - `backend/tests/test_segment_decider.py`（新，22 项参数化）
  - `演示场景集.md` §三（语义需对齐：5 段是「典型」而非「必要」）
- **防再犯**（必读）：
  1. **任何"行程结构"相关字段必须是 IntentExtraction 的函数**——不要在代码字面量里硬写段名清单
  2. **写 `_assemble_*` / `_render_*` 类函数前先检查依赖什么 intent 字段**：缺一个就回头补 decider 层（不要在拼装层做条件 if）
  3. **测试断言 `len(stages) >= N` 是可疑信号**：写之前问"如果用户说 N=1 呢？" 测试反映的是**典型场景**而不是**所有场景**
  4. **Critic 的硬违规列表反映"什么算合规"**——这等同于隐式 schema，必须随 intent 变
  5. 引申潜伏场景（每条都可能被同一根因影响，按 P1 处理）：
     - 用户说"想吃下午茶"→ 应少 POI 多餐厅
     - 用户说"独处去图书馆"→ 应只 POI 不餐厅
     - 用户说"全家粤菜"→ 应直接餐厅 + 蛋糕加购
     - 用户说"city walk 半天"→ 应多个 POI 串成路线
     - 用户说"先吃饭再去看展"→ 应反序（餐厅 → POI）
- **优先级**：P1（直接影响 demo 现场反馈环；任何"反馈削减约束"场景都被这个反模式拖垮）

### [P2] 2026-05-17 段被削后餐厅可订时段反向卡死总时长（P1 引申问题）

- **现象**：reduce 到「出发+用餐+返回」3 段后，若用户原 social=老人伴助 + dietary=软烂，候选餐厅时段最早 17:30（mock 数据），导致 14:00 出发后等到 17:30 才用餐——总时长 248min ≠ 用户期望的 60min。
- **根因**：段决策只看 intent，不看候选物理约束。candidate.dining_time 由 `_negotiate_dining` 在受限时段池（如 17:00/17:30/18:00）里选首个可订的，与"压缩到 1 小时"目标冲突。
- **临时解**：暂不修复——demo 当前重点是段决策本身工作；二级时段问题属于"约束优先级排序"问题，需改 `_resolve_time_window` 让 dining_slots 跟 depart_time 紧贴（不再用全局默认晚餐时段）。
- **防再犯**：实现"反馈→削段"链路时，**同时**审视该反馈是否还要影响 *时段池*——段数与时段池是双维度，不能只改一个。
- **优先级**：P2（不影响 demo 跑通，但削段场景的总时长仍可能偏离用户期望；需要二次修复）

### [P3] 2026-05-17 测试断言中的"硬常量集"是隐式 schema

- **现象**：测试 `for required in ("出发", "主活动", "转场", "用餐", "返回")` 这种硬字符串集合在多个测试文件出现 → 同一字面量被多处独立维护 → segment_decider 落地后必须改 4 处。
- **根因**：测试期望被「直觉式 5 段」绑架；没有用统一的 `decide_segments(intent)` 当真值。
- **解法**：现已统一从 `agent.segment_decider` 导入；新测试不要再硬列段名。
- **防再犯**：未来测试段数相关行为时，**强制**用 `decide_segments(intent)` 算 expected，而非字面量。
- **优先级**：P3（不影响功能；规范开发习惯）


### [P1] 2026-05-17 反馈精度约束未传到下游（截图 4.7h bug 第二次复发）

- **现象**：用户在前端反馈"只有一个小时"，IntentSummary 已显示 [3,5] → [1,2] 小时（注意是 [1,2] 不是 [1,1]），但下方时间轴仍 4.7 小时（5 段）。第一次修（问题 11）只接了 `_enforce_duration_consistency` 在 refiner 出口，但没解决三层下游问题。
- **根因（多层）**：
  1. **reFiner LLM 漂移**：5 次跑同一反馈，第 4 次 changed_fields 出现 `[1,2] → [1,1]`（LLM 把"一个小时"先理解成 [1,2] 再调到 [1,1]）。这暴露**纯 LLM 路径不稳定**——必须有结构化兜底
  2. **raw_input 反馈丢失**：refiner 强制保留 `original.raw_input`，反馈只进 `changed_fields`，下游所有路径都不知道原始反馈数字
  3. **`MIN_MAIN_ACTIVITY_MINUTES = 30` / `MIN_DINING_MINUTES = 30` 硬下限**：1h 总池下，main+dining=60 已用满，加 30min 路程 buffer = 总跨度 1.5h+，违反用户 1h 约束
  4. **dining_slots 起点写死 `main_minutes + 30`**：1h + 无主活动场景下，dining 应从 14:15 起算，但代码始终从 14:00+main+30 起算
  5. **餐厅 mock 数据物理约束**：下午茶餐厅最早 14:30 起预约，14:00→ 等到 15:00 用餐造成的等待时间无法压缩，需要"二次裁段"主动放弃用餐段
- **解法**（5 层防御，缺一不可）：
  1. **入口防线**：`planner._enforce_intent_duration_from_raw(intent)` 在 plan 跑前从 `intent.raw_input` 提取精确小时数，不一致就强制覆盖。这是「反馈作为最高优先级约束」的硬实现
  2. **raw_input 携带反馈**：refiner 把 feedback 拼到 raw_input 末尾（`原句（反馈：...）`），让下游所有路径都能从单一来源消费反馈
  3. **`_resolve_time_window` 接受 segments**：仅含主活动 / 仅含用餐时，对应时长 0；含两者时按 4:3 分配；下限改 15min（而非 30min）
  4. **dining_slots 起点跟着 segments**：仅用餐场景从 depart+15 起；含主活动从 depart+main+30 起
  5. **二次裁段**：duration ≤ 2h 的短场景，若估算总跨度 > duration_hours[1] + 15min，主动剔掉用餐段，改为只去 POI
- **测试矩阵**（218 项全过）：
  - `test_screenshot_bug_one_hour_feedback_caps_total_minutes`：1h 反馈 → ≤ 90min / ≤ 3 段
  - `test_two_hour_feedback_caps_total_within_2_5_hours`：2h 反馈 → ≤ 150min
  - `test_long_duration_unaffected_by_dining_cut`：4h 场景仍 5 段不被误裁
  - `test_extract_duration_from_feedback`：11 项参数化
- **相关文件**：
  - `backend/agent/refiner.py`（`_enforce_duration_consistency` + `_extract_duration_from_feedback` + raw_input 拼接）
  - `backend/agent/planner.py`（`_enforce_intent_duration_from_raw` 入口防线 + `_resolve_time_window` 接受 segments + 二次裁段）
  - `backend/tests/test_refiner_duration_consistency.py`（21 项 + 3 项 e2e）
- **防再犯**：
  1. **「反馈优先级」必须在数据流最上游**：raw_input 是唯一可靠的反馈载体，下游各层都从这里读
  2. **测试断言"硬段数"是 P3 已记的反模式**：用 `decide_segments(intent)` 算期望
  3. **修 LLM 漂移不能只接出口校验**：要在多层（refiner 出口 + planner 入口）独立兜底，因为各层都可能被旁路
  4. **MIN_* 下限常量是潜在硬约束**：写代码时问"用户给的 duration 比这小怎么办？"
  5. **dining_slots 起点逻辑写死 main_minutes + 30 是另一种"5 段假设"残余**——任何"假设主活动一定存在"的代码都要按段数检查
- **优先级**：P1（直接影响 demo 现场反馈环 + 第二次复发已暴露第一次修不彻底；前后两次问题都进 pitfalls 是为了记住"分多层防御"原则）


### [P1] 2026-05-17 行程"段决策耦合 LLM 主观与算法客观"反模式（架构级根因）

> 与 P1-2026-05-17 「5 段写死反模式」是同一根问题不同侧面：那条主要谈段集合，本条主要谈段决策的责任归属。

- **现象**：用户说"只想吃饭"/"今晚夜宵"/"24h 营业餐厅"/"先吃饭再看展" → 现有 hybrid + rule planner 不论怎么调启发式都强加主活动 / 强行下午 14:00 起 / 段顺序写死 POI→餐厅。每出一个反例就在 `decide_segments` / `_resolve_time_window` 里加 if 分支，越改越 spaghetti。
- **根因（架构级）**：
  - 段决策（哪些段 / 段顺序 / 段时长 / 选哪个 target_id）是**主观的需求理解**，应由 LLM 决定
  - 段验证（时序无重叠 / 营业时间覆盖 / 总时长不超）是**客观的物理约束**，应由算法决定
  - 旧实现把段决策也放算法层（`decide_segments` 用 if/elif 枚举场景），违反 LLM-Modulo (Kambhampati NeurIPS 2024) 的「LLM 决主观、算法决客观」分工
  - LLM 仅出意图 + 出权重，根本没机会决定段维度 → 启发式必然遗漏边界场景
- **解法**：引入 PlanBlueprint 中间数据结构（`agent/blueprint.py`），让 LLM 出蓝图（段集合/顺序/时长/target_id 全开放），算法只跑 critic 验证客观约束。具体实现见 `agent/planner_llm_first.py` + `agent/prompts/blueprint_prompt.py`，详见 problem.md 问题 15。
- **相关文件**：
  - `backend/agent/blueprint.py`（PlanBlueprint + 3 critic）
  - `backend/agent/blueprint_llm.py`（LLM 蓝图生成器）
  - `backend/agent/planner_llm_first.py`（主流程 + critic backprompt 重试 + fallback 链）
  - `backend/agent/assemble_blueprint.py`（蓝图→Itinerary 拼装）
  - `backend/agent/prompts/blueprint_prompt.py`（蓝图生成 prompt）
  - `backend/agent/planner.py` `_plan_with_llm_first` 适配器
- **防再犯**（每条都是产品级要求）：
  1. **新增任何"行程结构相关"维度前，先问"这是主观还是客观？"**：主观（哪些段 / 选哪个 target / 用什么文案）→ LLM；客观（时序重叠 / 营业时间 / 距离上限）→ algo
  2. **绝不在算法层加 `if scene == "夜宵": ...` 类启发式**：所有场景区分由 LLM 看 raw_input + 候选预览自主决定
  3. **Critic 反馈消息必须是自然语言**：让 LLM 能针对性修改（"段 X 与 Y 时序重叠：前者结束于 A，后者开始于 B"），不是 `OVERLAP_CODE_42`
  4. **Fallback 链必须四级（LLM 重试 → hybrid → rule → 错误推流）**：每层都推 `agent_thought` 让评委可见
  5. **PlanBlueprint 是 LLM 决策的所有维度**：段集合 / 段顺序 / 每段时长 / target_id 必须全在蓝图里。如果有维度没进蓝图（如餐厅时段、POI 物理 tag）就说明该维度被算法越权决定了
  6. **测试覆盖必须含"反 5 段"场景**：单段 / 反序 / 24h 营业 / 极短时长 / 极长时长，每条至少 1 个 e2e 用例（参考 `backend/scripts/verify_llm_first.py` 4 场景）
  7. **`PLANNER_LLM_STRATEGY` 切换默认值时要保证 fallback 链向后兼容**：本次 hybrid → llm_first 时，hybrid 仍可显式指定且通过 verify_planning 4 场景
- **优先级**：P1（架构级；任何"段决策耦合主客观"的反模式都归到本条防再犯清单；hackathon 评分项 2 规划链路 25% 的核心）


### [P1] 2026-05-17 dock 直接反馈无上下文持久化（Phase 0.11 主修）

- **现象**：用户在 dock 输入框打字"太远了 3 公里以内"，前端走 /chat/stream 触发**完整新规划**——LLM 看不到上一次给用户的方案，把反馈当成新需求重新意图解析。结果意图字段 distance_max_km 仍是默认 5km，POI 候选不变，重规划失败。
- **根因（架构级）**：缺 conversation_id / message_history 概念；session 在内存只持单次方案，跨 turn 无 messages 累积。
- **解法**：
  - `agent/v2/conversation.py` 引入 ConversationState + ConversationStore（commit 81141cb）；后续重构成 ConversationRepository Protocol（commit e3767ca）+ InMemoryRepository（默认）+ RedisRepositoryStub（Milestone 2 接入点）
  - `agent/v2/orchestrator.py` 加 looks_like_feedback / decide_turn_kind（旧路径用，启发式判断反馈 vs 新需求）
  - `backend/main.py` 新增 POST `/chat/turn` 单一入口
  - **彻底解**：D-react-unified 用 ReAct 单一 Agent，把 message_history 喂给 LLM，由 LLM 自主判断（无需启发式）
- **相关文件**：
  - `backend/agent/v2/conversation.py`（ConversationRepository Protocol + 向后兼容 ConversationStore 别名）
  - `backend/agent/v2/orchestrator.py`（looks_like_feedback / decide_turn_kind / run_react_turn）
  - `backend/main.py:461-540`（/chat/turn 端点 + USE_REACT_AGENT flag）
  - `backend/agent/v2/react_agent.py`（unified_agent: Agent + message_history 跨 turn 持久化）
- **防再犯**：
  1. **任何"对话型"产品上线前先想 message_history 持久化**——session 不只持业务快照，还要持 ModelMessage list
  2. **判断"反馈 vs 新需求"不应是启发式 if 关键词**——LLM 看到 raw_input + tool 结果就能自主分类
  3. **持久化抽象一开始就上 Protocol**——demo 用 InMemory，MVP 切 Redis 时业务代码 0 改动
  4. **跨 turn 字段不要静默丢失**：refiner 漂走 raw_input 已经是 P1 老坑（pitfalls 2026-05-17 P1）；ConversationState.messages 同等重要
  5. **/chat/turn 必须有 fallback**：探活失败（import 错 / 配置错）自动回旧 router→planner / refiner 双路径，让 demo 不翻车
- **优先级**：P1（直接影响 demo 现场反馈环；用户场景里"对话框输反馈"是最自然的交互路径）

### [P2] 2026-05-17 MiMo Function Calling 嵌套 array of objects 边界（Phase 0.12 调试期）

- **现象**：Pydantic AI 框架自动把 Tool Input 模型转成 OpenAI Function Schema，含嵌套 array of objects（如 `companions: [{age: 5, dietary: [...]}]`）。MiMo v2.5 Pro 在 ReAct 循环里偶尔把数组参数序列化成 JSON 字符串而不是真数组，导致 Pydantic AI 校验失败 → ModelRetry → 浪费 1-2 次重试预算。
- **根因**：
  - 不同 LLM 厂商对 nested array 的支持稳定性不一致；MiMo 文档声称支持但实际边界 case 会漂
  - Pydantic AI 默认 strict 校验，看到 `"[1,2,3]"` 字符串而不是 `[1,2,3]` 数组直接抛
- **解法**：
  - `react_agent.py` 在 8 个 `@unified_agent.tool` 装饰函数顶部加 `_coerce_*` helper：检测到字符串入参先 `json.loads()` 再喂给 Pydantic 模型
  - prompt 里加显式警示「数组参数必须用 JSON 数组形式 [...] 不是字符串 "[...]"」
  - `_FlexibleItineraryResponse` 子类放宽 dietary / experience tag 词典外值的校验（log warning 而非 raise）
- **相关文件**：
  - `backend/agent/v2/react_agent.py` 8 个工具入参 `_coerce_*` helper
  - `backend/agent/v2/output_types.py` `_FlexibleItineraryResponse` 子类
  - `backend/agent/v2/react_agent.py` system prompt 嵌套数组警示段
- **防再犯**：
  1. **任何 LLM Function Calling 实现前先看 nested array of objects 是否稳**——单层数组通常稳，嵌套数组要测
  2. **Pydantic AI / OpenAI SDK 默认 strict 校验**：写工具入参前看入参类型，给可能漂的字段加 `_coerce_*` 入口
  3. **三层 MiMo 容错原则**：prompt 警示 + 入参 coerce + 输出 Flexible 子类，缺一不可
  4. **ModelRetry 预算有限（默认 retries=3）**：每次解析失败就消耗一次，意味着 critic 真违规时可能只剩 1 次修复机会——尽量在前置层挡住不该走 retry 的失败
- **优先级**：P2（不直接挂 demo，但会让 LLM 看似"傻"——本来一次能完成的规划要重试 2 次）

### [P2] 2026-05-17 Pydantic AI ToolOutput vs PromptedOutput 区分（Phase 0.12 重构期）

- **现象**：Agent E 第一版用 `output_type=ItineraryResponse` 单类型，发现 LLM 在闲聊场景被强行套用 ItineraryResponse schema 导致输出空 stages 列表（structurally valid 但语义失败）。
- **根因**：Pydantic AI 的 `output_type` 是「最终回复的形态」契约，单类型时 LLM 必须填满所有必填字段；闲聊场景没有 stages / orders 等业务数据，但 schema 强制要求它们。
- **解法**：
  - `agent/v2/output_types.py` 用 `AgentOutput = Union[ChatResponse, ItineraryResponse]`
  - LLM 在生成最终回复时自主选分支：闲聊/Q&A/拒答 → ChatResponse；完整规划 → ItineraryResponse
  - 不需要外部分类器（router）来决定走哪个分支
- **相关文件**：
  - `backend/agent/v2/output_types.py`（ChatResponse / ItineraryResponse Union 定义）
  - `backend/agent/v2/react_agent.py` `unified_agent: Agent[AgentDeps, AgentOutput]`
- **防再犯**：
  1. **Pydantic AI output_type 用 Union 避免单类型 over-constraint**——多形态对话产品天然就该是 Union
  2. **LLM 自己选分支 > 外部分类器路由**：让 LLM 看 raw_input + tool 结果自主决策；router 只在「LLM 真不可用时走启发式 fallback」
  3. **dc2fdae commit 教训**：第一版漏建 output_types.py 导致 react_agent.py import 失败——多文件 PR 时先建 stub 再填实现
- **优先级**：P2（不影响 demo，但会让闲聊场景看上去很别扭）

### [P2] 2026-05-17 verify_v2_turn 与 USE_REACT_AGENT flag 冲突（Phase 0.12 联调期）

- **现象**：Agent C 提交 ConversationRepository（commit e3767ca）时 verify_v2_turn 通过，但 Agent G /chat/turn 接 ReAct 后（commit 330cc80）verify_v2_turn 在默认 USE_REACT_AGENT=1 路径下偶尔超时——因为旧 verify 期待的 stub 路径事件序列与 ReAct 路径不一致。
- **根因**：multi-agent 并行重构时，C/G 各自基于自己的 owner 跑 verify，但端到端 verify 跨 owner 边界。C 的 verify_v2_turn 是基于旧 router→planner 双路径写的；G 的 USE_REACT_AGENT=1 改了底层路径，verify 没同步更新。
- **解法**：
  - 用户实测发现后，临时把 verify_v2_turn 设 `USE_REACT_AGENT=0` 显式跑旧路径再过
  - 长期：写新 verify_v2_react.py（4 场景，跑 ReAct 路径），旧 verify_v2_turn 保留作为 fallback 路径回归测
- **相关文件**：
  - `backend/scripts/verify_v2_turn.py`（旧路径回归测，用 USE_REACT_AGENT=0）
  - `backend/scripts/verify_v2_react.py`（新增，ReAct 路径 4 场景）
- **防再犯**：
  1. **端到端 verify 脚本必须显式声明依赖的 feature flag**：环境变量值变了 verify 行为可能完全不同
  2. **multi-agent 并行重构时，跨 owner 的端到端 verify 应由协调者（用户/PM）维护**——不是某个 owner 私有
  3. **新增 feature flag 默认 ON 时**：必须同步更新所有依赖该路径的 verify 脚本，否则 CI 会偶发挂
- **优先级**：P2（multi-agent 并行重构特有问题；不影响 demo 跑通）

### [P3] 2026-05-17 multi-agent 并行 ReAct 重构的协作纪律

- **现象**：Phase 0.11/0.12 一次性开 7 个 Agent（A 改 schema/B 改抽象/C 改持久化/D 写文档/E 写 ReAct/F 写 critic/G 接 main.py）。Agent 边界严格独占（git diff --cached --stat 校验）但仍有冲突点：
  - Agent E 漏建 output_types.py（commit dc2fdae 补漏）
  - Agent D 写商业文档时引用 ToolProvider/observability 措辞超前于 B 实际实现
  - Agent F critics_v2.py 与 Agent E react_agent.py 互相 try/import 兜底（防对方未合流时挂）
- **根因**：multi-agent 时序耦合——A 完成顺序 vs commit 顺序 vs 用户合流顺序可能不同；下游 Agent 读上游产出时若上游还没 commit 就只能 stub。
- **解法**：
  - **接口先行**：每个 Agent 提示词内显式声明「我导出什么 / 我消费什么」；下游 Agent 用 try/import 兜底处理上游未合流
  - **commit 范围闸**：每个 Agent commit 前 `git diff --cached --stat` 自查只动 owner 范围
  - **写文档的 Agent 措辞「已就绪」需谨慎**：Agent D 第一版写 ToolProvider「已就绪」，发现实际由 Agent B 同步实现——若 B 失败会留空头支票。改为「Phase 0.11 落地」+ 文件路径，便于评委 grep 验证
- **相关文件**：
  - `docs/06-business/01-数据源切换路径.md`（措辞修正过）
  - `backend/agent/v2/react_agent.py` critics_v2 try/import 兜底
- **防再犯**：
  1. **multi-agent 并行重构 ≥ 5 个 Agent 时**：必须先画出依赖图，按 topological 顺序合流
  2. **「commit 顺序 ≠ 实现顺序」**：先 commit 的 Agent 不一定先实现完；下游用 try/import 兜底
  3. **跨 owner 引用文档**：写「已实现」时附文件路径让评委可验证；写「Phase X 落地」时同步说明在哪个 commit
  4. **Agent E/F 互相依赖的特殊处理**：双方 try/import 兜底；commit 时哪个先合都不破
- **优先级**：P3（流程优化；不影响代码功能）


### [P2] 2026-05-18 子元素 z-index 被父级 stacking context 困住

- **现象**：UserSwitcher 顶栏下拉面板用 `position: absolute z-30`，但仍被全局 `z-30` 的 ChatDock 盖住——明明 z-index 数字相同，却显示在 dock 下面。
- **根因（CSS stacking context）**：
  - `<header className="sticky top-0 z-20">` 创建了 z-20 stacking context
  - 下拉面板作为 header 的子元素，它的 z-30 仅在 header stacking context **内部**生效
  - 实际堆叠层级是「z-20 内部的 z-30」，与全局 z-30 dock 比较时永远输
  - 任何带 z-index 的 sticky / fixed 容器都会创建 stacking context（同样适用于 transform / opacity < 1 / will-change 等 CSS 特性）
- **解法**：跨越 stacking context 时，子元素**必须**用 `position: fixed`（或用 React Portal 渲染到 body），脱离父级容器的层叠上下文。配合按钮 ref + `getBoundingClientRect()` 动态计算位置：
  ```typescript
  const rect = buttonRef.current.getBoundingClientRect();
  setPanelPos({
    top: rect.bottom + 6,
    right: window.innerWidth - rect.right,
    maxHeight: Math.max(160, window.innerHeight - rect.bottom - 18),
  });
  ```
- **相关文件**：`frontend/components/UserSwitcher.tsx`（fix commit 2df8b6c）
- **防再犯**（必读）：
  1. **任何下拉菜单 / tooltip / popover / context menu** 类组件，**默认采用 `position: fixed`**，不要图省事用 `position: absolute`
  2. 即使 absolute 在简单场景能工作，未来父级加 `transform / opacity / sticky` 任何一个都会创建 stacking context，下拉立刻被遮挡
  3. fixed 定位需要监听 `resize` + `scroll`（capture phase）保持位置同步
  4. 跨 stacking context 的 z-index 体系建议显式分层：
     - z-20: Header / 固定导航
     - z-30: 主要 fixed 容器（dock / dialog）
     - z-40: 瞬时通知（toast）
     - z-45: 下拉菜单（高于 dock，低于命令面板）
     - z-50: 顶级模态（command palette / 关键确认弹窗）
  5. 写新 popover 类组件时**默认从 fixed 开始**，遇到「为什么我的 z-index 不生效」立刻怀疑 stacking context
- **优先级**：P2（典型 CSS 层级陷阱；前端开发常踩）
