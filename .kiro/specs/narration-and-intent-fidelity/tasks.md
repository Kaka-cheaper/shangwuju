# Implementation Plan

## Overview

两块改动：块A（narration 完整复述）+ 块B（明示诉求轻量词法重排治本 + 诚实告知兜底）。块A 独立；块B 内部 B2 重排函数与 B3 检测函数共用同一词法 helper（先建 helper），B1 镜像 prompt 独立，B4 接线依赖 B3。全部完成后真 LLM 实测 S3/S5 + 回归。

## Task Dependency Graph

```text
块A（narration）─ Task1 prompt + Task2 模板 ──────────────┐
块B helper ─ Task3 共享词法 helper ─┬─ Task4 POI重排(R3) ──┤
                                    └─ Task5 未满足检测(R4)─┤
块B B1 镜像 prompt ─ Task6 ────────────────────────────────┤
块B B4 接线 ─ Task7（依赖 Task5）──────────────────────────┤
                                                            ├─→ Task8 真LLM实测+回归 ─→ Task9 收尾
```

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2", "3", "6"], "rationale": "块A prompt/模板、块B 共享词法 helper、块B 镜像 prompt 互相独立可并行" },
    { "wave": 2, "tasks": ["4", "5"], "rationale": "POI 重排与未满足检测都依赖 Task3 的共享词法 helper" },
    { "wave": 3, "tasks": ["7"], "rationale": "narrate_node 接线依赖 Task5 检测函数" },
    { "wave": 4, "tasks": ["8"], "rationale": "真 LLM 实测 S3/S5 + 全量回归(依赖全部代码改动)" },
    { "wave": 5, "tasks": ["9"], "rationale": "收尾(problem.md + commit + CodeSee)" }
  ]
}
```

## Tasks

- [ ] 1. 块A-1：narrator_prompt 字数按节点弹性 + 餐后活动必讲 + 三节点 few-shot（R1.1/R1.2/R1.3）
  - narrator_prompt.py：字数上限改弹性（1-2 活动≤80/3 活动≤120/4+≤150）+ 硬规则「行程有几个活动讲几个，用餐排中间时餐后活动必须讲、不能在用餐处收尾」
  - 加 1 条 few-shot：猫咖→甜品店(餐)→电影院 三节点 → 输出含电影院
  - 先写/扩 test_narrator_full_nodes.py：断言 prompt 含「餐后」「必须讲」类规则 + 三节点 few-shot 关键词
  - 验证：pytest tests/test_narrator_full_nodes.py + 既有 narrator 测试不回归
  - _需求: R1.1, R1.2, R1.3, R1.5_

- [ ] 2. 块A-2：_template_narration 去 phrases[:3] 截断（R1.4）
  - narrator.py：body 改为复述全部活动节点（去 [:3]；>6 活动温和截断 + 「等」）
  - test_narrator_full_nodes.py：构造 3 节点 itinerary → _template_narration 输出含全部 3 个地点（先红后绿）
  - 验证：pytest tests/test_narrator_full_nodes.py
  - _需求: R1.4_

- [ ] 3. 块B-helper：共享词法匹配 helper（R3/R4 同源 SoT）
  - narrator.py（或 search_adapter 可 import 的位置）：实现 `_poi_desire_match(desire, poi_type, poi_name, poi_tags) -> bool`（双向 substring 命中 type/name/tags 任一）
  - 决定放置位置：放 narrator.py 则 search_adapter import；或放一个轻量 helper 模块。优先放 search_adapter（检索侧），narrator 复用——避免 narrator import 检索层。最终：放 search_adapter.py，detect 函数 import 它（检索→文案单向依赖，无环）
  - 先写 test 覆盖 helper：看展命中 tags、展览命中 type、攀岩命中 name、无关诉求不命中
  - 验证：pytest 该 helper 测试
  - _需求: R3.1, R3.3, R4.2_

- [ ] 4. 块B-2：search_pois_for_intent POI 诉求重排 + 扩池（R3 治本核心）
  - search_adapter.py：`_rerank_by_preferred_poi_types(pois, preferred_poi_types)`（复用 Task3 helper，命中前置稳定排序）
  - search_pois_for_intent：有 preferred_poi_types 时 fetch_limit=max(limit,15) → 搜 → 重排 → [:limit]；无诉求原序（零回归）
  - 先写 test_search_adapter_poi_rerank.py：看展 POI 前置 / KTV 前置 / 空诉求原序 / 扩池不丢命中
  - 验证：pytest tests/test_search_adapter_poi_rerank.py
  - _需求: R3.1, R3.2, R3.3, R3.4, R3.5_

- [ ] 5. 块B-3：detect_unmet_poi_preference 纯函数（R4 兜底）
  - narrator.py：detect_unmet_poi_preference(preferred_poi_types, itin_poi_types, itin_poi_names, itin_poi_tags)（复用 Task3 helper；fail-safe；含明显餐饮 token 的诉求交给 cuisine 版不重复计）
  - 先写 test_detect_unmet_poi.py：看展未满足命中 / 已满足不命中(type 或 tags) / KTV 未满足命中 / 空诉求返[] / 餐饮 token 不重复计
  - 跑红→绿
  - 验证：pytest tests/test_detect_unmet_poi.py
  - _需求: R4.1, R4.2, R4.3, R4.4_

- [ ] 6. 块B-1：intent prompt 镜像通道（R2）
  - intent_parser_prompt.py：在「明示餐饮/活动品类必须保留」段补一句——词典内活动品类（如「看展」）被点名时也镜像写进 preferred_poi_types（说明：高信号重排通道）
  - 守既有约束：禁止凭空添加、没点名则空数组；可选加 1 条「看展」镜像 few-shot
  - 加断言：intent prompt 含「镜像」/「preferred_poi_types」相关规则关键词
  - 验证：pytest 相关 intent prompt 测试 + 既有 intent 测试不回归
  - _需求: R2.1, R2.2, R2.3, R2.4_

- [ ] 7. 块B-4：narrate_node 接线 + 诚实告知泛化（R4.1）
  - narrate.py：新增 _detect_unmet_poi（查 mock pois 取 type/name/tags）→ 调 detect_unmet_poi_preference；与 unmet_cuisines 合并为 unmet_desires
  - narrator.py：generate_narration/_call_llm_narrator/_template_narration/stream_llm_narrator 形参合并为 unmet_desires（语义泛化）
  - narrator_prompt.py：诚实告知规则从「品类」泛化为「诉求」+ 保留 cuisine/活动各 1 条 few-shot
  - 验证：pytest tests/test_narrator_honest_substitution.py（不回归）+ 接线手测
  - _需求: R4.1_

- [ ] 8. 真 LLM 实测 S3/S5 + 全量回归（R1/R2/R3/R4/R5）
  - 临时脚本真 LLM 跑 S5 看展：抽出 preferred_poi_types=["看展"] + 候选预览含展 + 方案含展（或诚实告知）+ narration 复述全部活动
  - S3 家庭：narration 复述全部 3 段（含探索乐园）
  - S1 KTV / S6 闺蜜：诉求满足不误报；S4/S7/S8 不回归
  - 全量 backend pytest 全过 + 前端 verify:all 4/4
  - 用完即删临时脚本
  - 验证：脚本输出 + pytest + verify
  - _需求: R1.1-R1.4, R2.1, R3.1, R3.2, R4.1, R5.1, R5.3, R5.4, R5.5_

- [ ] 9. 收尾：problem.md + commit + CodeSee sync
  - problem.md 追加本 spec 会话日志（按全局格式）
  - git commit（中文 message，精确 stage，用 -F 文件传 message）
  - CodeSee features.json sync（intent/检索/narrate feature 补「明示诉求重排」step）+ 校验
  - _需求: 全部_

## Notes

- **核心治本是 Task4 的词法重排**（让对味候选进 top_k 预览），R4 诚实告知是兜底，R2 镜像通道是给重排喂干净信号——三者构成「检索相关性优先」闭环
- 不引入活动诉求白名单 / type 映射字典 / `if type==` 分支（D9 意图开放铁律）——只用纯字符串双向 substring
- 不引入 embedding / LLM 打分（60 个 mock POI 杀鸡用牛刀 + 拖慢 demo）
- 不把 preferred_types 硬传进 search_pois 工具（精确匹配会归零）；召回仍走宽松 has_any_tag
- 不动 graph/build.py 拓扑；不动 search_pois 工具（守 §3.4 Tool 对场景无感）
- 词法 helper 单一 SoT（Task3），R3 重排与 R4 检测共用，保证 Property 4 同源一致性
- Out of scope（单独修）：kind 标签错配 / dock 收起按钮 / planner 默认值对齐；blueprint prompt 加诉求规则作为可选锦上添花（不动 2200 cap 风险）
