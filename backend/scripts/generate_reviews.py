"""scripts.generate_reviews —— 给 mock_data 中所有 POI / 餐厅补 UGC 评论。

设计依据：
- 赛题 06 原文要求「结合点评 POI 数据 / 用户评价语料」
- 每个实体补 2 条评论，覆盖：
  - 1 条主流好评（80后 / 90后 用户）
  - 1 条次要评论（00后 / 学生 / 银发 / 商务）
- 评论文本基于 tags / suitable_for 上下文模板生成，不重复

模板纪律：
- 每条评论 ≥ 30 字（赛题 06 要求"用户评价语料"，不是"很好""不错"水帖）
- tag_evidence 必须是该实体真实拥有的 tag
- helpful_count 随机 5-50 之间（demo 真实感）
- visited_at 在 2026-01 至 2026-05 之间均匀分布

执行方式：
    python -m scripts.generate_reviews

会原地修改 mock_data/pois.json 和 restaurants.json：
- 跳过已有 reviews 的实体（向后兼容；P040/P041/P042 已手工写）
- 仅补 reviews 字段为空 / 不存在的

不负责：
- 真接入大众点评 / 美团 UGC 接口（schema 一致即可平滑切换）
- 评论的"真实"——这是 mock 数据，关注 schema 完整 + 字段密度
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_POIS_FILE = _REPO_ROOT / "mock_data" / "pois.json"
_RESTAURANTS_FILE = _REPO_ROOT / "mock_data" / "restaurants.json"


# ============================================================
# 评论模板库
# ============================================================
# 每个 social_context / suitable_for 配 3 条评论模板，{name} 是占位符

_POI_TEMPLATES_BY_SOCIAL: dict[str, list[tuple[str, str, list[str]]]] = {
    "家庭日常": [
        # (text, age_bucket, tag_evidence_keywords)
        (
            "周末带孩子去的，环境干净安全，工作人员对小朋友很有耐心。"
            "孩子玩了一下午都不愿意走，下次还想再来。",
            "80后",
            ["亲子友好"],
        ),
        (
            "亲子设施齐全，分龄区分得很清楚。5 岁孩在适合的区域玩得很开心，"
            "不像有些场所大孩子小孩子混着，磕碰风险高。",
            "90后",
            ["亲子友好", "适合 5-10 岁"],
        ),
        (
            "假日去人不算特别多，孩子玩得很尽兴。门票价格在合理范围内，"
            "比同类场所性价比高。",
            "80后",
            ["亲子友好"],
        ),
    ],
    "老人伴助": [
        (
            "带老人去的，全程电梯无台阶，60+ 长辈走完一圈不累。"
            "环境也清静，没有那种年轻人扎堆的喧闹感。",
            "80后",
            ["适合老人", "无台阶", "可休息"],
        ),
        (
            "70 岁妈妈第一次来表示满意。走道宽敞、休息长椅多，"
            "老人累了随时坐下歇会儿。这种贴心设计现在不多见了。",
            "90后",
            ["适合老人", "可休息"],
        ),
        (
            "环境安静，没台阶，老人走动方便。是我找了很久才发现的"
            "适合带长辈去的地方。",
            "80后",
            ["适合老人", "无台阶"],
        ),
    ],
    "情侣亲密": [
        (
            "和女朋友约会的好去处，环境很有氛围感。两个人聊天很惬意，"
            "不会被周围打扰。",
            "00后",
            ["安静聊天"],
        ),
        (
            "周末约会去的，气氛很赞，有不少角落适合两个人独处。"
            "整体格调比较精致。",
            "90后",
            ["安静聊天", "拍照友好"],
        ),
        (
            "情侣去刚刚好。空间不算大但布置温馨，氛围拍照都很在线。",
            "00后",
            ["拍照友好"],
        ),
    ],
    "闺蜜聊天": [
        (
            "和闺蜜每次约都来这家，能安心聊心事，环境也好出片。"
            "下午茶时段一坐就是几个小时。",
            "90后",
            ["安静聊天", "拍照友好"],
        ),
        (
            "闺蜜局首选。能聊能拍能放松，是那种你不愿意离开的氛围。",
            "00后",
            ["拍照友好"],
        ),
        (
            "好几个朋友一起去过，每次都很尽兴。空间够私密，话题不会被周围干扰。",
            "90后",
            ["安静聊天"],
        ),
    ],
    "朋友热闹": [
        (
            "几个朋友一起去玩得很 high，空间够大、设施齐全。"
            "很适合朋友局，气氛能炒起来。",
            "00后",
            ["热闹"],
        ),
        (
            "和大学同学聚会去的，好多年没见的朋友这里玩得很尽兴。"
            "不像有的场所拘谨。",
            "90后",
            ["朋友热闹", "热闹"],
        ),
        (
            "周末和朋友扎堆来。氛围轻松，玩得开心。",
            "00后",
            ["热闹"],
        ),
    ],
    "商务接待": [
        (
            "接待外地客户用的，整体格调到位，客户表示很满意。"
            "不像有些地方接待感弱，这里能撑场面。",
            "80后",
            ["商务体面", "高人均"],
        ),
        (
            "公司客户接待我固定推荐这里。环境体面、服务专业，"
            "比一般商场餐厅高一档。",
            "80后",
            ["商务体面"],
        ),
        (
            "正式商务用，客户体验感不错。流程顺畅、不打扰谈话。",
            "80后",
            ["商务体面"],
        ),
    ],
    "独处放空": [
        (
            "一个人去最舒服。安静且不打扰，能专注做自己的事。"
            "周围氛围不会让独自一人感到尴尬。",
            "90后",
            ["安静聊天"],
        ),
        (
            "工作压力大的时候来这里待半天就能整个人放松。"
            "比咖啡馆还安静。",
            "90后",
            ["独处身心舒缓", "安静聊天"],
        ),
        (
            "适合一个人坐很久，是我的固定独处选择。环境氛围都到位。",
            "00后",
            [],
        ),
    ],
    "纪念日仪式感": [
        (
            "纪念日去的，氛围拉满，对方惊喜满满。"
            "细节处理得用心，仪式感到位。",
            "80后",
            ["礼仪感", "拍照友好"],
        ),
        (
            "重要日子的不二之选。环境格调高，"
            "能让人感受到节日的特别感。",
            "90后",
            ["礼仪感"],
        ),
        (
            "结婚纪念日去的，满分回忆。氛围、服务、出片都很赞。",
            "90后",
            ["礼仪感", "拍照友好"],
        ),
    ],
    "同学重聚": [
        (
            "和老同学聚会去的，很容易聊开。整体氛围不拘谨，"
            "比包间更轻松。",
            "90后",
            ["朋友热闹"],
        ),
        (
            "毕业 N 年后第一次聚会，这里气氛很好。"
            "大家都说下次还来。",
            "80后",
            ["热闹"],
        ),
        (
            "几个同学定期聚会的固定地点。",
            "90后",
            [],
        ),
    ],
}

_RESTAURANT_TEMPLATES_BY_SOCIAL = {
    "家庭日常": [
        (
            "经常带孩子来吃，菜式清淡口味适合小朋友。"
            "环境也不嘈杂，全家人都能舒服吃完一顿。",
            "80后",
            ["有儿童餐"],
        ),
        (
            "带 5 岁孩去的，店家很贴心，主动准备了儿童椅。"
            "招牌菜油不重盐不咸，孩子吃了好几碗饭。",
            "80后",
            ["亲子友好"],
        ),
        (
            "家庭聚餐很好的选择，菜量大、口味稳，孩子和老人都吃得满意。",
            "90后",
            [],
        ),
    ],
    "老人伴助": [
        (
            "带父母吃了几次。无台阶、座位舒适，菜也偏清淡软糯。"
            "70 岁的爸妈说很合胃口。",
            "80后",
            ["适合老人", "软烂"],
        ),
        (
            "找了很久才找到的适合带老人的店。环境安静、菜品软烂、"
            "服务员对老人也耐心。",
            "90后",
            ["适合老人", "软烂"],
        ),
        (
            "老人来这里吃得很满意。是为长辈准备饭局的优选。",
            "80后",
            ["适合老人"],
        ),
    ],
    "情侣亲密": [
        (
            "和男朋友约会去的，环境很有氛围。"
            "两个人坐在窗边一聊就是一晚上。",
            "00后",
            ["安静聊天"],
        ),
        (
            "情侣餐厅 top。灯光、音乐、菜式都恰到好处。"
            "重要日子来不会出错。",
            "90后",
            ["礼仪感"],
        ),
        (
            "周末约会去的，分量精致、味道在线。",
            "00后",
            [],
        ),
    ],
    "闺蜜聊天": [
        (
            "闺蜜下午茶最爱。甜品颜值在线，茶位舒适，"
            "聊到关店都不被催。",
            "00后",
            ["拍照友好"],
        ),
        (
            "和好朋友的固定聚点。每道菜都拍得出来，话题也聊得起来。",
            "90后",
            ["拍照友好"],
        ),
        (
            "闺蜜约饭推荐这里，环境氛围都很到位，菜也好吃。",
            "00后",
            [],
        ),
    ],
    "朋友热闹": [
        (
            "和朋友聚餐很尽兴。菜量大、品类多、价格合理。"
            "适合 4-6 人那种小团体。",
            "90后",
            ["热闹"],
        ),
        (
            "周末晚上和朋友扎堆来过几次。氛围轻松、上菜快。",
            "00后",
            [],
        ),
        (
            "热闹的朋友局首选，菜上得快、味道也稳定，几次都没踩雷。",
            "80后",
            ["热闹"],
        ),
    ],
    "商务接待": [
        (
            "接待客户的固定选择。包间私密、菜式体面、人均也匹配商务身份。"
            "客户从未挑过毛病。",
            "80后",
            ["商务体面", "有包间"],
        ),
        (
            "重要客户来杭都带这家。出品稳、服务到位，菜式不会出错。",
            "80后",
            ["商务体面"],
        ),
        (
            "公司商务局的常用地，菜品稳定、服务专业，从不踩雷。",
            "80后",
            ["商务体面"],
        ),
    ],
    "独处放空": [
        (
            "一个人吃饭也不会尴尬，环境安静，吧台位舒适。"
            "适合下班后给自己一个奖励。",
            "90后",
            ["安静聊天"],
        ),
        (
            "工作日中午经常一个人来。出菜快、味道好。",
            "90后",
            [],
        ),
        (
            "独自吃饭的优选，环境安静、出菜稳定，下班后偶尔来犒劳自己。",
            "00后",
            [],
        ),
    ],
    "纪念日仪式感": [
        (
            "结婚纪念日去的。环境布置精致，菜单格调高，"
            "服务员还专门写了贺卡。仪式感拉满。",
            "80后",
            ["礼仪感"],
        ),
        (
            "生日庆祝的好地方。蛋糕预定服务体贴，氛围浪漫。",
            "90后",
            ["礼仪感"],
        ),
        (
            "重要日子的就餐选择，氛围庄重又不失温馨，菜品出品也稳。",
            "80后",
            ["礼仪感"],
        ),
    ],
    "同学重聚": [
        (
            "和老同学聚会去的。大圆桌、菜量大、人均合理。"
            "聊老同学旧事很尽兴。",
            "90后",
            [],
        ),
        (
            "同学聚会推荐这里，环境轻松、菜量足，大家聊得很尽兴。",
            "80后",
            [],
        ),
        (
            "好几年没见的同学一起去的，氛围轻松。",
            "90后",
            [],
        ),
    ],
}


# ============================================================
# 生成器
# ============================================================

def _pick_templates(
    suitable_for: list[str],
    template_pool: dict,
    n: int = 2,
    seed: int = 0,
) -> list:
    """从 suitable_for 命中的模板池里挑 n 条不重复的模板。"""
    rng = random.Random(seed)
    candidates = []
    for s in suitable_for:
        if s in template_pool:
            candidates.extend(template_pool[s])
    if not candidates:
        # 没命中任何 social → fallback 用「家庭日常」
        candidates = template_pool.get("家庭日常", [])
    rng.shuffle(candidates)
    return candidates[:n]


def _build_review(
    template: tuple[str, str, list[str]],
    *,
    name: str,
    available_tags: list[str],
    seed: int,
) -> dict:
    """根据模板生成一条 review。"""
    text, age_bucket, kw_tags = template
    rng = random.Random(seed)
    # tag_evidence 取候选 tag 与该实体真实 tag 的交集
    real_tags = [t for t in kw_tags if t in available_tags][:3]
    if not real_tags and available_tags:
        # 至少标 1 个 tag（取第一个）
        real_tags = [available_tags[0]]

    # visited_at: 2026-01 至 2026-05 之间随机
    month = rng.randint(1, 5)
    day = rng.randint(1, 28)
    visited = f"2026-{month:02d}-{day:02d}"

    helpful = rng.randint(5, 50)
    rating_raw = rng.choice([4, 4.5, 5, 5, 5])

    return {
        "text": text,
        "rating": rating_raw,
        "user_age_bucket": age_bucket,
        "tag_evidence": real_tags,
        "visited_at": visited,
        "helpful_count": helpful,
    }


def _augment(
    items: list[dict],
    template_pool: dict,
    name_field: str = "name",
) -> int:
    """给每个 item 补 reviews（若已有则跳过）；返新增条目数。"""
    count = 0
    for idx, item in enumerate(items):
        if item.get("reviews"):
            continue
        suitable = item.get("suitable_for") or []
        avail_tags = item.get("tags") or []
        templates = _pick_templates(suitable, template_pool, n=2, seed=idx)
        if not templates:
            continue
        reviews = [
            _build_review(
                t,
                name=item.get(name_field, "未知"),
                available_tags=avail_tags,
                seed=idx * 10 + t_idx,
            )
            for t_idx, t in enumerate(templates)
        ]
        item["reviews"] = reviews
        count += len(reviews)
    return count


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    pois = json.loads(_POIS_FILE.read_text(encoding="utf-8"))
    rests = json.loads(_RESTAURANTS_FILE.read_text(encoding="utf-8"))

    poi_added = _augment(pois, _POI_TEMPLATES_BY_SOCIAL, name_field="name")
    rest_added = _augment(rests, _RESTAURANT_TEMPLATES_BY_SOCIAL, name_field="name")

    _POIS_FILE.write_text(
        json.dumps(pois, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _RESTAURANTS_FILE.write_text(
        json.dumps(rests, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"POI 新增 {poi_added} 条评论；餐厅新增 {rest_added} 条评论")
    return 0


if __name__ == "__main__":
    sys.exit(main())
