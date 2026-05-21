"""一次性脚本：丰富 mock 数据字段。

修改内容：
1. user_profile.json → 多用户结构（按 persona 的 user_id 索引）
2. pois.json → 每个 POI 加 suggested_duration_minutes
3. restaurants.json → 每个餐厅加 signature_dishes + recommendation_reason

运行：python scripts/enrich_mock_data.py
"""

import json
from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parents[2] / "mock_data"


# ============================================================
# 1. 多用户 profile
# ============================================================

def enrich_user_profiles():
    """把单一 user_profile.json 改成多用户字典结构。"""
    profiles = {
        "demo_user": {
            "user_id": "demo_user",
            "home_location": {"name": "西溪诚园（用户家）", "lat": 30.275, "lng": 120.075},
            "default_budget": 300.0,
            "transport_preference": "taxi"
        },
        "u_dad": {
            "user_id": "u_dad",
            "home_location": {"name": "嘉绿苑", "lat": 30.272, "lng": 120.098},
            "default_budget": 280.0,
            "transport_preference": "taxi"
        },
        "u_biz": {
            "user_id": "u_biz",
            "home_location": {"name": "钱江新城", "lat": 30.245, "lng": 120.212},
            "default_budget": 800.0,
            "transport_preference": "taxi"
        },
        "u_grandma": {
            "user_id": "u_grandma",
            "home_location": {"name": "西湖区文一路", "lat": 30.278, "lng": 120.112},
            "default_budget": 200.0,
            "transport_preference": "bus"
        },
        "u_solo": {
            "user_id": "u_solo",
            "home_location": {"name": "城西银泰", "lat": 30.268, "lng": 120.088},
            "default_budget": 150.0,
            "transport_preference": "walking"
        },
        "u_couple": {
            "user_id": "u_couple",
            "home_location": {"name": "湖滨银泰", "lat": 30.252, "lng": 120.165},
            "default_budget": 400.0,
            "transport_preference": "taxi"
        },
    }
    path = MOCK_DIR / "user_profiles.json"
    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 写入 {path}（{len(profiles)} 个用户）")

    # 保留旧文件兼容（demo_user 单对象）
    old_path = MOCK_DIR / "user_profile.json"
    old_path.write_text(
        json.dumps(profiles["demo_user"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 保留旧 {old_path}（兼容）")


# ============================================================
# 2. POI 加 suggested_duration_minutes
# ============================================================

# 按 POI 类型给默认推荐时长
_POI_TYPE_DURATION = {
    "亲子乐园": 120,
    "展览": 75,
    "亲子博物馆": 90,
    "儿童阅读馆": 60,
    "城市公园": 60,
    "茶馆": 90,
    "戏曲园": 90,
    "画廊": 60,
    "图书馆": 90,
    "SPA": 120,
    "书店": 75,
    "咖啡馆": 60,
    "密室": 90,
    "桌游馆": 120,
    "街区漫步": 90,
    "商务茶室": 90,
    "亲子游乐场": 90,
    "城市观光": 90,
    "DIY 工坊": 90,
    "运动步道": 45,
    "演出": 150,
    "庆典花园": 60,
    "猫咖": 75,
    "剧本杀": 150,
    "KTV": 120,
    "电影院": 120,
    "美甲": 90,
    "瑜伽馆": 75,
    "健身房": 90,
    "主题乐园": 180,
    "室内运动馆": 90,
    "livehouse": 120,
    "酒吧": 90,
    "烘焙工坊": 90,
}


def enrich_pois():
    path = MOCK_DIR / "pois.json"
    pois = json.loads(path.read_text(encoding="utf-8"))
    for poi in pois:
        poi_type = poi.get("type", "")
        duration = _POI_TYPE_DURATION.get(poi_type, 60)
        poi["suggested_duration_minutes"] = duration
    path.write_text(json.dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 更新 {path}（{len(pois)} 个 POI 加 suggested_duration_minutes）")


# ============================================================
# 3. 餐厅加 signature_dishes + recommendation_reason
# ============================================================

_RESTAURANT_ENRICHMENT = {
    "R001": {
        "signature_dishes": ["牛油果藜麦碗", "低卡鸡胸沙拉", "蛋白奶昔"],
        "recommendation_reason": "低脂健康，有专门的儿童餐区，适合带娃家庭"
    },
    "R002": {
        "signature_dishes": ["白切鸡", "蒸凤爪", "叉烧包"],
        "recommendation_reason": "老字号粤菜，菜品软烂适合老人，有独立包间"
    },
    "R003": {
        "signature_dishes": ["鹰嘴豆沙拉", "全麦三明治", "鲜榨果蔬汁"],
        "recommendation_reason": "纯素轻食，热量标注清晰，减脂人群首选"
    },
    "R004": {
        "signature_dishes": ["草莓千层塔", "玫瑰拿铁", "马卡龙拼盘"],
        "recommendation_reason": "ins 风装修超出片，闺蜜下午茶首选"
    },
    "R005": {
        "signature_dishes": ["剁椒鱼头", "小炒黄牛肉", "农家小炒肉"],
        "recommendation_reason": "分量足、味道正，6 人大桌适合朋友聚餐"
    },
    "R006": {
        "signature_dishes": ["鹅肝慕斯", "黑松露意面", "焦糖布蕾"],
        "recommendation_reason": "烛光氛围感满分，适合纪念日或情侣约会"
    },
    "R007": {
        "signature_dishes": ["红烧狮子头", "清蒸鲈鱼", "桂花糯米藕"],
        "recommendation_reason": "菜品软糯入味，无台阶方便轮椅进出"
    },
    "R008": {
        "signature_dishes": ["A5 和牛刺身", "鳗鱼饭", "海胆寿司"],
        "recommendation_reason": "顶级日料会所，独立包间适合商务宴请"
    },
    "R009": {
        "signature_dishes": ["手冲埃塞俄比亚", "肉桂卷", "提拉米苏"],
        "recommendation_reason": "单人座位充足，安静适合独处发呆或看书"
    },
    "R010": {
        "signature_dishes": ["脆皮烧鹅", "避风塘虾", "杨枝甘露"],
        "recommendation_reason": "家宴级粤菜，8 人大桌 + 包间，适合家庭聚餐"
    },
    "R011": {
        "signature_dishes": ["招牌舒芙蕾", "抹茶千层", "焦糖布丁"],
        "recommendation_reason": "网红甜品颜值高，适合拍照打卡"
    },
    "R012": {
        "signature_dishes": ["龙井虾仁", "东坡肉", "西湖醋鱼"],
        "recommendation_reason": "私房菜级别，环境雅致适合商务宴请"
    },
    "R013": {
        "signature_dishes": ["冰滴咖啡", "伯爵红茶", "柠檬磅蛋糕"],
        "recommendation_reason": "书房式空间，适合一个人安静待一下午"
    },
    "R014": {
        "signature_dishes": ["法式蜗牛", "红酒炖牛肉", "舒芙蕾"],
        "recommendation_reason": "正统法餐，有包间，适合情侣纪念日"
    },
    "R015": {
        "signature_dishes": ["桃花酥", "杭式卤鸭", "龙井茶香鸡"],
        "recommendation_reason": "中式园林装修，拍照出片率极高"
    },
    "R016": {
        "signature_dishes": ["芒果班戟", "椰子冻", "水果茶"],
        "recommendation_reason": "花园露台座位，适合闺蜜下午茶拍照"
    },
    "R017": {
        "signature_dishes": ["蒸蛋羹", "清炖排骨", "南瓜粥"],
        "recommendation_reason": "专为老人设计的软食菜单，无台阶无门槛"
    },
    "R018": {
        "signature_dishes": ["鹅肝配无花果", "龙虾浓汤", "巧克力熔岩"],
        "recommendation_reason": "私密包间，适合情侣或小型纪念日聚餐"
    },
    "R019": {
        "signature_dishes": ["松露蒸蛋", "黑金鲍鱼", "XO 酱炒时蔬"],
        "recommendation_reason": "顶级商务粤菜，8 人包间适合正式宴请"
    },
    "R020": {
        "signature_dishes": ["彩虹沙拉碗", "鸡胸肉卷饼", "酸奶水果杯"],
        "recommendation_reason": "距家最近的轻食店，有儿童餐和宝宝椅"
    },
    "R021": {
        "signature_dishes": ["五谷杂粮饭", "黑椒鸡胸", "时蔬拼盘"],
        "recommendation_reason": "高蛋白低脂，健身人群和减脂家庭都适合"
    },
    "R022": {
        "signature_dishes": ["凯撒沙拉", "牛油果吐司", "冷萃咖啡"],
        "recommendation_reason": "单人友好，有吧台座位，适合独自用餐"
    },
    "R023": {
        "signature_dishes": ["三文鱼牛油果碗", "低卡意面", "鲜榨橙汁"],
        "recommendation_reason": "颜值与健康兼顾，有儿童餐区"
    },
    "R024": {
        "signature_dishes": ["蒸汽海鲜拼盘", "原味蒸蔬菜", "糙米饭"],
        "recommendation_reason": "蒸汽烹饪保留营养，无油烟更健康"
    },
    "R025": {
        "signature_dishes": ["全麦贝果", "燕麦奶拿铁", "能量棒"],
        "recommendation_reason": "全麦主打，适合对碳水有要求的减脂人群"
    },
    "R026": {
        "signature_dishes": ["田园沙拉", "鸡肉卷", "鲜果酸奶"],
        "recommendation_reason": "社区店氛围亲切，有儿童餐和涂鸦墙"
    },
    "R027": {
        "signature_dishes": ["三文鱼 poke 碗", "虾仁牛油果碗", "海藻沙拉"],
        "recommendation_reason": "海鲜轻食，高蛋白低脂，食材新鲜"
    },
    "R028": {
        "signature_dishes": ["素食八宝饭", "莲藕汤", "山药糕"],
        "recommendation_reason": "素食养生，菜品软烂适合老人，有包间"
    },
    "R029": {
        "signature_dishes": ["彩虹碗", "牛油果吐司", "气泡水果茶"],
        "recommendation_reason": "网红装修超出片，轻食 + 下午茶一站式"
    },
    "R030": {
        "signature_dishes": ["红烧肉盖饭", "番茄蛋汤", "炒时蔬"],
        "recommendation_reason": "食堂风格实惠量大，8 人桌适合同学聚会"
    },
    "R031": {
        "signature_dishes": ["秘制烤羊排", "蒜蓉生蚝", "烤玉米"],
        "recommendation_reason": "路边烧烤氛围感，适合朋友喝酒撸串"
    },
    "R032": {
        "signature_dishes": ["黑松露烤牛肉", "芝士焗扇贝", "精酿啤酒"],
        "recommendation_reason": "精致烧烤有包间，适合小型聚会"
    },
    "R033": {
        "signature_dishes": ["五花肉拼盘", "芝士年糕", "石锅拌饭"],
        "recommendation_reason": "韩式烤肉互动感强，适合朋友聚餐"
    },
    "R034": {
        "signature_dishes": ["鸳鸯锅底", "手切牛肉", "虾滑"],
        "recommendation_reason": "鸳鸯锅照顾不同口味，有包间适合家庭"
    },
    "R035": {
        "signature_dishes": ["毛肚", "鸭血", "麻辣牛油锅底"],
        "recommendation_reason": "正宗川味火锅，辣度可调，有包间"
    },
    "R036": {
        "signature_dishes": ["椰子鸡锅底", "鲜虾滑", "港式甜品"],
        "recommendation_reason": "不辣的港式火锅，适合不吃辣的家庭"
    },
    "R037": {
        "signature_dishes": ["水煮鱼", "宫保鸡丁", "麻婆豆腐"],
        "recommendation_reason": "正宗川菜，有包间，适合朋友聚餐"
    },
    "R038": {
        "signature_dishes": ["椒麻鸡", "干锅牛蛙", "冰粉"],
        "recommendation_reason": "商务级川菜会所，不辣可选，有独立包间"
    },
    "R039": {
        "signature_dishes": ["冬阴功汤", "芒果糯米饭", "咖喱蟹"],
        "recommendation_reason": "东南亚风情装修，拍照出片，适合情侣"
    },
    "R040": {
        "signature_dishes": ["泰式炒河粉", "青木瓜沙拉", "椰汁西米露"],
        "recommendation_reason": "街头风格热闹，适合朋友聚餐打卡"
    },
    "R041": {
        "signature_dishes": ["DIY 奶油蛋糕", "曲奇饼干", "提拉米苏"],
        "recommendation_reason": "可以亲手做蛋糕，适合亲子或闺蜜体验"
    },
    "R042": {
        "signature_dishes": ["原味舒芙蕾", "抹茶铜锣烧", "珍珠奶茶"],
        "recommendation_reason": "日式甜品精致小巧，适合下午茶拍照"
    },
    "R043": {
        "signature_dishes": ["安格斯牛排", "凯撒沙拉", "熔岩巧克力"],
        "recommendation_reason": "美式牛排分量足，适合商务或情侣"
    },
    "R044": {
        "signature_dishes": ["意式薄底披萨", "奶油蘑菇汤", "提拉米苏"],
        "recommendation_reason": "意式简餐轻松氛围，适合朋友小聚"
    },
    "R045": {
        "signature_dishes": ["湘味小龙虾", "口味虾尾", "凉拌毛豆"],
        "recommendation_reason": "夜宵氛围感，适合朋友深夜聚餐"
    },
}


def enrich_restaurants():
    path = MOCK_DIR / "restaurants.json"
    restaurants = json.loads(path.read_text(encoding="utf-8"))
    for rest in restaurants:
        rid = rest["id"]
        if rid in _RESTAURANT_ENRICHMENT:
            rest["signature_dishes"] = _RESTAURANT_ENRICHMENT[rid]["signature_dishes"]
            rest["recommendation_reason"] = _RESTAURANT_ENRICHMENT[rid]["recommendation_reason"]
        else:
            # 兜底：没有手写的就给通用值
            rest["signature_dishes"] = ["招牌菜品", "时令推荐"]
            rest["recommendation_reason"] = f"{rest.get('cuisine', '')}风味，评分 {rest.get('rating', 4.5)}"
    path.write_text(json.dumps(restaurants, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 更新 {path}（{len(restaurants)} 家餐厅加 signature_dishes + recommendation_reason）")


if __name__ == "__main__":
    enrich_user_profiles()
    enrich_pois()
    enrich_restaurants()
    print("\n全部完成！")
