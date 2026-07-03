#!/usr/bin/env python3
"""build_index —— 把 docs/map/*.mmd 组装成可点击导航的单页系统地图 index.html。

设计约定(与系统地图计划一致):
- 每张图一个 <div id="<文件名去后缀>">,L1 图内的 click "#..." 跳转到对应区块;
- L0 层(产品故事+术语表+走读)内嵌在本脚本常量里,与图同批演化;
- mermaid 库用本地 vendor/mermaid11.min.js(断网可开;版本与画图代理的
  校验引擎对齐,v10 与 v11 的 stateDiagram 语法宽容度不同,勿降级);
- 逐图独立渲染,单图失败不拖垮整页,失败清单显示在页首。

用法: python docs/map/build_index.py  → 重新生成 docs/map/index.html
另有独立发布物: --standalone <输出路径> 生成库内联的自包含单文件(发人用)。
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

ORDER = [
    "L1-turn-pipeline", "L1-modules",
    "L2-routing-cascade", "L2-constraint-dataflow", "L2-schema-relations",
    "L2-planning-pipeline", "L2-node-swap", "L2-session-substrate",
    "L2-confirm-execution", "L2-collab-room",
    "L3-episode-lifecycle", "L3-ledger-lifecycle", "L3-room-lifecycle",
]

TITLES = {
    "L1-turn-pipeline": "L1-a · 回合流水线全景 —— 用户的一次动作,系统从头到尾做了什么(节点可点击跳子图)",
    "L1-modules": "L1-b · 模块地图 —— 代码分成哪几大块,谁依赖谁",
    "L2-routing-cascade": "L2 · 分诊台内部 —— 一句话进来,怎么决定它是什么义务",
    "L2-constraint-dataflow": "L2 · 约束数据流 —— 诉求怎么变成数据、被谁消费(❌=已知断链,ADR-0014 治理对象)",
    "L2-schema-relations": "L2 · 数据结构关系 —— 核心结构谁包含谁",
    "L2-planning-pipeline": "L2 · 规划求解管线 —— 一份需求单怎么变成一份过检方案",
    "L2-node-swap": "L2 · 换菜引擎 —— 点一站下面的按钮之后发生什么",
    "L2-session-substrate": "L2 · 会话底座 —— 系统靠什么记住整场对话,谁是真相源",
    "L2-confirm-execution": "L2 · 确认执行流 —— 点「确认并预约」之后发生什么",
    "L2-collab-room": "L2 · 房间协作 —— 多人房间里一条消息/一次点击怎么被处理",
    "L3-episode-lifecycle": "L3 · 状态机:对话记忆字段何时生何时清(TURN/EPISODE/SESSION)",
    "L3-ledger-lifecycle": "L3 · 状态机:诉求台账一条记录的一生",
    "L3-room-lifecycle": "L3 · 状态机:一个房间从建到销毁的一生",
}

INTRO = """
<h2>L0 · 这是个什么产品</h2>
<p class="note">「晌午局」是一个<b>半日出行规划助手</b>:你说一句话(比如「下午想带爸妈出去走走」),
它自动排出一条下午的行程时间轴——从家出发、逛哪个景点、几点在哪家餐厅吃饭、几点到家。
你可以对方案<b>提意见</b>(「太远了」)、<b>点按钮换掉某一站</b>(换一家/更便宜的),也可以<b>拉朋友进房间一起商量</b>;
满意后点「确认并预约」,它才真正去订座买票。下面的图讲的就是这套流程在系统里怎么跑。</p>

<h2>L0 · 先认识 10 个词(全站的图都用这套词)</h2>
<table class="gloss">
<tr><th>词</th><th>意思</th><th>代码里叫</th></tr>
<tr><td><b>方案</b></td><td>最终产出:一条下午的行程时间轴,每一站有时间、地点、理由</td><td>Itinerary</td></tr>
<tr><td><b>站</b></td><td>方案里的一个安排(某个景点/某家餐厅),每站下面有调整按钮</td><td>ActivityNode</td></tr>
<tr><td><b>需求单</b></td><td>系统把你一句话翻译成的结构化清单:几点/几人/忌口/想要什么氛围——查店、排方案全靠它</td><td>IntentExtraction</td></tr>
<tr><td><b>场景卡</b></td><td>首页 8 张预设卡片(「带爸妈逛西湖」),点一下=替你说一句典型需求,演示的起手式</td><td>DEMO_SCENARIOS</td></tr>
<tr><td><b>反馈</b></td><td>已有方案后你说的修改意见(「太远了」)。系统不推倒重来,把意见合并进需求单重排</td><td>feedback→refiner</td></tr>
<tr><td><b>诉求台账</b></td><td>「谁提过什么要求」的记名小账本(尤其点按钮提的),重排时系统主动照顾旧账,不翻脸不认</td><td>demand_ledger</td></tr>
<tr><td><b>确认</b></td><td>点「确认并预约」后系统才真正订座买票;确认前随便改,确认后想改需说「重新规划」</td><td>confirm</td></tr>
<tr><td><b>房间</b></td><td>多人协作模式:建房邀朋友,谁的发言和点击都记名,一起商量出一个方案,闲置自动销毁</td><td>Room</td></tr>
<tr><td><b>质检</b></td><td>方案排完先内部体检(时长/距离/赶不赶得上/餐厅满座…约 13 条规矩),不合格自动返工</td><td>critic</td></tr>
<tr><td><b>兜底</b></td><td>AI 出问题时逐级退到更简单但可靠的做法(备用算法/纯规则/固定文案),演示不白屏不翻车</td><td>fallback/drain</td></tr>
</table>
"""

WALKTHROUGH = """
<h2>跟着一次真实对话走一遍 L1-a 大图</h2>
<ol class="walk">
<li>你点场景卡「下午带爸妈出去走走」→ 进入<b>「对话入口」</b></li>
<li><b>「分诊台」</b>听懂:这是<b>第一次提需求</b>(不是闲聊、不是提意见)——内部怎么判的,点它跳子图</li>
<li><b>「需求单」</b>:翻译成结构化清单——下午出发/3 人/长辈同行/别太累</li>
<li><b>三路同时查</b>:查景点、查餐厅、查你的偏好档案(并行,省时间)</li>
<li><b>「排方案」</b>:AI 按需求单起草行程蓝图,同时算你最在乎什么(舒适?省钱?)</li>
<li><b>「拼时间轴」</b>:蓝图变成完整方案,用餐时刻自动对齐餐厅真实可订的时段</li>
<li><b>「质检」</b>:发现餐厅那站 17:00 已满座 → 走<b>「把毛病告诉 AI 重写」</b>返工</li>
<li>二稿过检 → <b>「定稿」</b>:方案立刻推到你页面上(讲解文案随后才到,不让你干等)</li>
<li><b>「写讲解」</b>:补一段开场白、每站的调整按钮、以及没做到之处的诚实说明</li>
<li>你说「太远了」→ 这次「分诊台」判定是<b>对方案提意见</b> → 走「合并意见」重排,之前的需求和你点过的按钮都不丢</li>
<li>你点某站的「换一家」按钮 → 走右侧近道<b>「换菜引擎」</b>:只动这一站,其他站纹丝不动</li>
<li>满意了,点「确认并预约」→ <b>「执行订单」</b>真正订座买票,行程记进你的偏好档案。结束。</li>
</ol>
"""


def build(standalone_out: Path | None = None) -> None:
    anchors = json.loads((HERE / "anchors.json").read_text(encoding="utf-8"))
    base = anchors.get("_base_commit", "?")
    sections = []
    for name in ORDER:
        mmd = (HERE / f"{name}.mmd").read_text(encoding="utf-8")
        q = anchors.get(name, {}).get("question", "")
        sections.append(
            f'<div class="sect" id="{name}"><h2>{html.escape(TITLES.get(name, name))}</h2>'
            f'<p class="note">这张图回答:{html.escape(q)} · 源文件 <code>docs/map/{name}.mmd</code>(锚点表在其头部注释)</p>'
            f'<div class="diagram"><pre class="mermaid">\n{html.escape(mmd)}\n</pre></div>'
            f'<p class="back"><a href="#top">↑ 回到全景</a></p></div>'
        )
    body = INTRO + sections[0] + WALKTHROUGH + "\n".join(sections[1:])
    page = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>晌午局 · 系统地图</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 24px; background: #fafafa; }}
 h1 {{ font-size: 20px; }} h2 {{ font-size: 16px; margin-top: 36px; border-left: 4px solid #f90; padding-left: 8px; }}
 .note {{ color: #444; font-size: 14px; line-height: 1.8; max-width: 900px; }}
 .diagram {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-top: 12px; overflow-x: auto; }}
 .err {{ color: #c00; white-space: pre-wrap; font-family: monospace; }}
 table.gloss {{ border-collapse: collapse; font-size: 14px; max-width: 900px; }}
 table.gloss th, table.gloss td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; line-height: 1.6; }}
 table.gloss th {{ background: #f5f5f5; }}
 table.gloss td:last-child {{ color: #999; font-family: monospace; font-size: 12px; white-space: nowrap; }}
 ol.walk {{ max-width: 900px; line-height: 2.0; font-size: 14px; }}
 .back {{ font-size: 12px; }} code {{ background: #eee; padding: 1px 4px; border-radius: 3px; }}
</style>
<script src="vendor/mermaid11.min.js"></script>
</head>
<body id="top">
<h1>晌午局 · 系统地图</h1>
<p class="note">读法:先看 L0 → L1-a 全景(蓝色可点节点跳子图)→ 走读一遍 → 按需下钻 L2/L3。
每框第一行人话职责,括号内代码名;虚线=旁路/回写;❌=已知断链。基于 commit {base};
锚点与导航同源于 <code>anchors.json</code>,校验: <code>python scripts/check_map_anchors.py</code>。本页离线可开。</p>
{body}
<script>
mermaid.initialize({{ startOnLoad: false, theme: "neutral", securityLevel: "loose", flowchart: {{ useMaxWidth: false }} }});
(async () => {{
  const fails = [];
  for (const d of document.querySelectorAll("div.sect")) {{
    try {{ await mermaid.run({{ nodes: d.querySelectorAll(".mermaid") }}); }}
    catch (e) {{ fails.push(d.id + ": " + (e && e.message ? e.message : e)); }}
  }}
  if (fails.length) {{
    const div = document.createElement("div");
    div.className = "err";
    div.textContent = "以下子图渲染失败:\\n" + fails.join("\\n");
    document.body.prepend(div);
  }}
}})();
</script>
</body>
</html>"""
    (HERE / "index.html").write_text(page, encoding="utf-8")
    print("index.html written")
    if standalone_out is not None:
        lib = (HERE / "vendor" / "mermaid11.min.js").read_text(encoding="utf-8")
        standalone = page.replace('<script src="vendor/mermaid11.min.js"></script>', "<script>" + lib + "</script>")
        standalone_out.write_text(standalone, encoding="utf-8")
        print(f"standalone written: {standalone_out}")


if __name__ == "__main__":
    out = None
    if len(sys.argv) > 2 and sys.argv[1] == "--standalone":
        out = Path(sys.argv[2])
    build(out)
