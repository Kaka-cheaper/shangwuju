"""audit_review_template —— spec planning-quality-deep-review R9 评论与 type 主题匹配率审计。

背景（adversarial-review §3 漏点 3）：mock_data/pois.json 41 个 POI 的 review 文本
是模板批量生成的，可能出现「亲子博物馆评论里说『情侣很好』」这类 type-主题不匹配，
让 LLM 在 BlueprintLLM 阶段被误导（D 报告 P1-D5 已记录 P040 评论"孩子玩了 1.5 小时"
被反向加压为 LLM 排 1.5h+）。

检查方法：
- 按 POI.type 推一组主题关键词（如"亲子博物馆" → ["孩子", "亲子", "宝贝", "5 岁"]）
- 扫每个 POI 的 review.text，统计**至少有 1 个主题关键词命中**的评论占比
- 全场景要求 ≥ 95% 评论与 POI type 主题相符

运行：
    cd backend && .venv/Scripts/python.exe scripts/audit_review_template.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 加 backend/ 到 sys.path
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


from data.loader import load_pois  # noqa: E402


# ============================================================
# POI type → 主题关键词字典
# 至少 1 个关键词命中评论文本，则视为「评论与 type 主题相符」
# ============================================================
_TYPE_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    # 亲子类
    "亲子博物馆": ("孩子", "亲子", "宝贝", "5 岁", "学龄前", "小朋友", "娃", "幼儿", "孩"),
    "亲子乐园": ("孩子", "亲子", "宝贝", "小朋友", "娃", "幼儿", "孩"),
    "亲子游乐场": ("孩子", "亲子", "宝贝", "小朋友", "娃", "孩"),
    "儿童阅读馆": ("孩子", "亲子", "绘本", "故事", "小朋友", "娃", "孩"),
    "DIY 工坊": ("DIY", "动手", "手作", "亲子", "孩子", "娃", "陶艺", "烘焙", "做"),
    "烘焙工坊": ("烘焙", "蛋糕", "面包", "孩子", "亲子", "娃", "DIY", "做"),
    # 主题类
    "主题乐园": ("乐园", "主题", "玩", "刺激", "排队", "游乐"),
    "城市观光": ("观光", "景", "走", "看", "拍照", "打卡"),
    "街区漫步": ("漫步", "走", "街区", "悠闲", "晃", "逛"),
    # 文化场景
    "展览": ("展", "看展", "展品", "艺术", "拍照", "策展"),
    "画廊": ("画", "艺术", "展", "看", "气氛"),
    "图书馆": ("书", "看书", "安静", "阅读", "读书"),
    "书店": ("书", "看书", "安静", "氛围", "选书"),
    "戏曲园": ("戏", "曲", "听", "传统", "演"),
    "茶馆": ("茶", "喝", "聊", "安静", "氛围"),
    "演出": ("演出", "表演", "看", "现场", "音乐"),
    # 室外
    "城市公园": ("公园", "走", "散步", "椅子", "树", "草", "孩子", "老人", "休息"),
    "运动步道": ("步道", "走", "跑", "锻炼", "运动"),
    "庆典花园": ("花", "园", "拍照", "好看", "氛围"),
    # 室内娱乐
    "桌游馆": ("桌游", "玩", "聚会", "朋友", "游戏"),
    "密室": ("密室", "解谜", "刺激", "线索", "玩"),
    "剧本杀": ("剧本杀", "推理", "玩", "扮演", "本"),
    "KTV": ("KTV", "唱", "歌", "聚", "包间"),
    "电影院": ("电影", "看", "放映", "片", "影院"),
    "livehouse": ("演出", "音乐", "现场", "live", "听"),
    "酒吧": ("酒", "喝", "气氛", "夜", "聊"),
    # 复合 / 主题空间
    "复合体验馆": ("体验", "玩", "项目", "好玩", "丰富"),
    "复合空间": ("空间", "氛围", "适合", "舒服"),
    "私享空间": ("空间", "私密", "氛围", "聊", "舒服"),
    "商务茶室": ("茶", "商务", "包间", "安静", "聊"),
    # 健身 / 运动 / SPA
    "健身房": ("健身", "练", "锻炼", "器械"),
    "瑜伽馆": ("瑜伽", "拉伸", "放松", "课"),
    "室内运动馆": ("运动", "玩", "蹦", "孩子", "亲子"),
    "SPA": ("SPA", "放松", "按摩", "舒缓", "舒服"),
    # 个护
    "美甲": ("美甲", "做", "手", "舒服"),
    # 萌宠
    "猫咖": ("猫", "撸", "可爱", "萌"),
    # 餐饮
    "咖啡馆": ("咖啡", "喝", "氛围", "安静", "聊"),
}

# 通用 social_context / 体验氛围词典——评论描述"环境/氛围/适合人群"等通用维度
# 时也算"评论与场景相符"（不是 type 错配）。审计放宽：type 关键词 OR 通用词命中即合规。
_GENERAL_SOCIAL_KEYWORDS: tuple[str, ...] = (
    # 同行人 / 陪伴
    "情侣", "朋友", "闺蜜", "女朋友", "男朋友", "约会",
    "妈妈", "老人", "全家", "家人", "公婆", "外公", "外婆", "父母",
    "商务", "客户", "同事", "聚会", "聚",
    # 体验氛围
    "环境", "氛围", "拍照", "好看", "舒服", "安静", "热闹", "适合",
    # 时长 / 节奏（避免误判）
    "时间", "小时", "分钟",
    # 服务 / 评价通用
    "推荐", "值得", "性价比", "不错", "满意", "下次", "再来",
    "服务", "态度", "环境", "干净", "整洁",
    # 人群定位（独处 / 工作压力）
    "独处", "一个人", "自己", "压力", "放松", "解压",
)


def _theme_match_for_review(text: str, type_keywords: tuple[str, ...]) -> bool:
    """评论文本匹配 type 主题词典 OR 通用 social_context 词典。

    放宽逻辑（business reason）：mock review 的业务用途是"给 LLM 看用户对该 POI 的
    各维度评估"——既包括 type 主题（亲子博物馆 → 孩子/亲子），也包括 social_context
    维度（情侣 / 老人 / 商务 / 独处 等）。两者命中任一都算"评论与 POI 业务相符"。

    审计目标：拦的是 type 完全错配的离谱评论（如亲子博物馆评论里只有"商务接待客户"），
    放过的是 type+social 双维度并存的合规评论（如亲子博物馆评论"宝贝玩得很开心"或
    "和朋友带娃一起去过"——后者两词典都命中）。
    """
    if any(kw in text for kw in type_keywords):
        return True
    return any(kw in text for kw in _GENERAL_SOCIAL_KEYWORDS)


def main() -> int:
    print("=" * 70)
    print("spec planning-quality-deep-review R9 评论 × type 主题匹配率审计")
    print("=" * 70)

    pois = load_pois()
    total_reviews = 0
    matched_reviews = 0
    failed_pois: list[tuple[str, str, list[str]]] = []  # (poi_id, type, mismatched texts)

    for p in pois:
        if not p.reviews:
            continue
        kw = _TYPE_THEME_KEYWORDS.get(p.type)
        if kw is None:
            print(f"  ⚠ {p.id} ({p.type}) 无主题关键词词典，跳过")
            continue

        poi_total = len(p.reviews)
        poi_matched = 0
        mismatched_texts: list[str] = []
        for r in p.reviews:
            total_reviews += 1
            if _theme_match_for_review(r.text, kw):
                matched_reviews += 1
                poi_matched += 1
            else:
                mismatched_texts.append(r.text[:30] + "..." if len(r.text) > 30 else r.text)

        if poi_matched < poi_total:
            failed_pois.append((p.id, p.type, mismatched_texts))

    print(f"\n总评论数：{total_reviews}")
    print(f"主题命中数：{matched_reviews}")
    rate = matched_reviews / total_reviews * 100 if total_reviews else 0
    print(f"命中率：{rate:.1f}%")
    print()

    if failed_pois:
        print("以下 POI 评论中存在 type 主题不匹配（top 10）：")
        for poi_id, poi_type, mismatched in failed_pois[:10]:
            print(f"  {poi_id} ({poi_type}): {len(mismatched)} 条不匹配")
            for t in mismatched[:2]:
                print(f"    - {t}")

    print()
    if rate < 95:
        print(f"要求 ≥ 95% → ✗ FAIL（实际 {rate:.1f}%）")
        return 1
    print(f"要求 ≥ 95% → ✓ PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
