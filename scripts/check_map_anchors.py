#!/usr/bin/env python3
"""check_map_anchors —— 系统地图(docs/map)的锚点校验器 + 过期提醒器。

anchors.json 是「图 ↔ 代码」映射的单一真相源(index.html 的点击导航与本脚本
共用同一份数据,防止导航和校验各自漂移)。本脚本提供两种模式:

1. 默认(无参数):**存在性校验**——每张图的 .mmd 文件存在、每条 watch 路径
   存在。路径消失(改名/删除/搬家)= 该图指着不存在的代码,机械可查的过期信号。
   退出码 0=全部通过,1=有失效锚点。

2. --touched <文件路径...>:**过期提醒**——给定一批改动过的文件(通常来自
   `git diff --name-only`),打印哪些图的 watch 面被命中,提醒"这些图可能过期"。
   永远退出 0(提醒型,不阻断——阻断型钩子会逼人敷衍更新,毁掉机制公信力,
   见系统地图计划的更新机制三层防线设计)。

用法:
    python scripts/check_map_anchors.py
    python scripts/check_map_anchors.py --touched $(git diff --name-only HEAD~1)

不负责:语义漂移检测(图的内容与代码行为是否一致)——那靠"行为语义变更的
commit 同批更新对应 .mmd 与其 base commit 戳"的人工纪律,机器只能查代理信号。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP_DIR = REPO / "docs" / "map"
ANCHORS = MAP_DIR / "anchors.json"


def load() -> dict:
    return json.loads(ANCHORS.read_text(encoding="utf-8"))


def check_existence() -> int:
    data = load()
    failures: list[str] = []
    for name, meta in data.items():
        if name.startswith("_"):
            continue
        mmd = MAP_DIR / f"{name}.mmd"
        if not mmd.exists():
            failures.append(f"[图缺失] {mmd.relative_to(REPO)}")
        for w in meta.get("watch", []):
            if not (REPO / w).exists():
                failures.append(f"[锚点失效] {name} → {w} 不存在(改名/删除/搬家?)")
    if failures:
        print("check_map_anchors: 发现失效锚点——对应的图指着不存在的代码,需要更新:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"check_map_anchors: OK({sum(1 for k in data if not k.startswith('_'))} 张图,全部锚点存在)")
    return 0


def remind_touched(touched: list[str]) -> int:
    data = load()
    touched_norm = [t.replace("\\", "/").strip() for t in touched if t.strip()]
    hits: dict[str, list[str]] = {}
    for name, meta in data.items():
        if name.startswith("_"):
            continue
        for w in meta.get("watch", []):
            for t in touched_norm:
                if t == w or t.startswith(w.rstrip("/") + "/"):
                    hits.setdefault(name, []).append(t)
    if hits:
        print("check_map_anchors[提醒]: 以下系统地图子图的监视面被本批改动命中,行为语义若有变化请同批更新 .mmd 与 base commit 戳:")
        for name, files in sorted(hits.items()):
            print(f"  {name}.mmd  ←  {', '.join(sorted(set(files))[:5])}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--touched":
        sys.exit(remind_touched(sys.argv[2:]))
    sys.exit(check_existence())
