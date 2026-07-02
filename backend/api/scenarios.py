"""演示场景集端点（来源：docs/01-requirements/演示场景集.md §二）。

顺序约定（小团 App 命题方对齐）：青年向场景置首位（小团主力用户群），
原家庭/朋友/情侣等顺延；原「带父母散步」与「跨代际纪念日」两条长辈向
场景从演示按钮中下线（mock 数据 + 回归测试仍保留覆盖证明 D9 无感）。

SCENARIOS 单一真相源已迁至 `agent.routing.canonical_shortcut.DEMO_SCENARIOS`
（ADR-0011 决策 2 / E-1）：这 8 个 input 文案同时是壳2 canonical 字面短路的
匹配表——断网/stub 演示下"任意输入→引导气泡→点场景 chip→正常规划"全靠它。
本文件只做端点 adapter，不再自己维护一份数据（防止两处漂移）。
"""

from __future__ import annotations

from fastapi import APIRouter

from agent.routing.canonical_shortcut import DEMO_SCENARIOS as SCENARIOS

router = APIRouter(tags=["演示场景"])


@router.get("/scenarios", summary="拉 8 个演示场景的输入文案")
def scenarios() -> dict[str, list[dict[str, str]]]:
    return {"scenarios": SCENARIOS}
