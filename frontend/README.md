# frontend —— Next.js 14 App Router

> P3 W3 启动时由 B 同学初始化。本文件先占位 + 锁定与后端的契约引用。

## 启动前必读

1. **后端契约**：`backend/api_contract.md`（HTTP 路径 + SSE 事件序列）
2. **类型来源**：`backend/schemas/sse.py` + `backend/schemas/itinerary.py` + `backend/schemas/intent.py`
3. **演示场景**：`docs/01-requirements/演示场景集.md`（8 个场景的输入文案 + 调性）
4. **环境变量**：`.env.local` 内填 `NEXT_PUBLIC_API_BASE=http://localhost:8000`

## TypeScript 类型同步策略

后端 Pydantic 模型 → 前端 TS 类型，三选一：

- **A. 手抄一份**（最稳，本项目首选）：前端 `lib/types.ts` 手写，每次后端改 schema 时 grep 同步
- **B. 用 `datamodel-codegen` 自动生成**：从 `model_json_schema()` 输出转 TS（可选）
- **C. 用 OpenAPI**：`/openapi.json` → openapi-typescript 生成 client（FastAPI 自带）

P3 启动后由 B 拍板。在那之前，前端 TS 类型按本目录 `lib/types.ts`（待创建）规范。

## 与后端的硬契约

```text
| 字段            | 来源                  | 改动纪律                             |
|-----------------|-----------------------|--------------------------------------|
| SseEvent        | schemas/sse.py        | 双方同时改，不能单边                 |
| Itinerary       | schemas/itinerary.py  | 同上                                 |
| IntentExtraction| schemas/intent.py     | §5.7 D-SoT，绝对禁止前端发明字段     |
| /chat/stream    | api_contract.md §2    | 路径/方法/事件序列固定               |
```

## 开发模式

- B 同学开发时把后端跑在 stub 模式（`LLM_PROVIDER=stub`），不需要 LLM API key
- 8 个演示场景的快捷按钮 → 调 `GET /scenarios` 拉数据，**不写死在前端**

## 暂未决定

- 状态管理库：Zustand vs React 19 useActionState 二选一（B 拍板）
- 主题色：与系统整体风格一致（agent steering 规则：避免刺眼紫色/粉色）
- 组件库：shadcn/ui + Tailwind（D2 已锁定）
