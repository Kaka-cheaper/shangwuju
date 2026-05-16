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
