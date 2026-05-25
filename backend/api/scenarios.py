"""演示场景集端点 + 常量（来源：docs/01-requirements/演示场景集.md §二）。

顺序约定（小团 App 命题方对齐）：青年向场景置首位（小团主力用户群），
原家庭/朋友/情侣等顺延；原「带父母散步」与「跨代际纪念日」两条长辈向
场景从演示按钮中下线（mock 数据 + 回归测试仍保留覆盖证明 D9 无感）。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["演示场景"])


SCENARIOS: list[dict[str, str]] = [
    {
        "id": "S1",
        "title": "学生党 KTV 局",
        "input": "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
        "icon": "🎤",
    },
    {
        "id": "S2",
        "title": "兄弟撸串夜宵",
        "input": "今晚和兄弟出来撸串喝点酒，人均 50 左右就行",
        "icon": "🍢",
    },
    {
        "id": "S3",
        "title": "家庭主线",
        "input": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        "icon": "👨‍👩‍👧",
    },
    {
        "id": "S4",
        "title": "朋友 4 人",
        "input": "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
        "icon": "👫",
    },
    {
        "id": "S5",
        "title": "情侣看展",
        "input": "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
        "icon": "💑",
    },
    {
        "id": "S6",
        "title": "闺蜜下午茶",
        "input": "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        "icon": "👯",
    },
    {
        "id": "S7",
        "title": "商务接待",
        "input": "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        "icon": "💼",
    },
    {
        "id": "S8",
        "title": "独处放空",
        "input": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "icon": "🌿",
    },
]


@router.get("/scenarios", summary="拉 8 个演示场景的输入文案")
def scenarios() -> dict[str, list[dict[str, str]]]:
    return {"scenarios": SCENARIOS}
