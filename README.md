# 晌午局 shangwuju

> 一句话搞定下午行程。

美团 AI Hackathon 命题赛道 06「本地探索 · 周末闲时活动规划」参赛作品。

用户说一句话 → AI Agent 编排 Tool → 输出可执行 + 可转发的下午行程。

## 快速了解

| 你是谁 | 读哪份 | 预计时间 |
|---|---|---|
| 非技术队友 / 想快速了解产品 | [`项目说明.md`](项目说明.md) | 5 分钟 |
| 技术队友 / 想了解架构选型 | [`技术架构.md`](技术架构.md) → [`docs/01-requirements/架构选型.md`](docs/01-requirements/架构选型.md) | 15 分钟 |
| AI Agent（Cascade / Claude Code） | [`AGENTS.md`](AGENTS.md) | 自动读取 |
| 想了解完整文档体系 | [`docs/00-overview/如何使用这套文档.md`](docs/00-overview/如何使用这套文档.md) | 5 分钟 |

## 当前阶段

**文档驱动开发 · 第一阶段：文档审阅**（2026-05-08）

- 架构选型全部完成（D1-D9，共 11 项决策）
- 代码尚未开始——先让团队 3 人审阅文档、集思广益
- 详见 [`docs/00-overview/progress.md`](docs/00-overview/progress.md)

## 团队协作

- 分工方案：[`docs/00-overview/团队分工.md`](docs/00-overview/团队分工.md)
- 3 人 · 1 个月时间盒（至 2026-06-08）
- 文档驱动：先对齐设计、再动手编码

## 文档结构

```text
shangwuju/
├── AGENTS.md              # AI Agent 编码铁律
├── README.md              # 你正在读
├── 项目说明.md            # 产品逻辑（人类友好）
├── 比赛详情.md            # 赛题原文（冻结）
├── chatgpt分析.md         # 需求拆解参考（冻结）
├── 技术架构.md            # 架构候选（活文档）
└── docs/
    ├── 00-overview/       # 进度 / 分工 / 导航
    ├── 01-requirements/   # 需求 / MVP / 验收 / 选型 / 场景
    └── 03-implementation/ # 踩坑笔记
```

## 技术栈（已锁定）

- **LLM**：DeepSeek-V3 主 + 通义 Qwen-Plus 备
- **后端**：Python 3.11+ / FastAPI / Pydantic v2 / SSE
- **前端**：Next.js 14 / TypeScript / Tailwind / shadcn/ui
- **数据**：Mock JSON（不接真实 API）

## 给队友的话

这个仓库目前只有文档，没有代码。这是故意的。

**请你做 3 件事**：

1. **读 [`项目说明.md`](项目说明.md)**——5 分钟了解我们要做什么
2. **读 [`docs/00-overview/团队分工.md`](docs/00-overview/团队分工.md)**——看看你的角色
3. **带着问题来**——用你自己的 AI 助手（ChatGPT / Claude / DeepSeek）从不同角度审视这套文档，看有没有逻辑漏洞、遗漏场景、或者更好的做法

我们的目标是：**文档对齐后再开工，一次做对**。
