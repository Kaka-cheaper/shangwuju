"""verify_legacy_frozen —— 验 spec agent-directory-restructure R3：legacy/ FROZEN 标记完整。

检查 backend/agent/legacy/ 下所有 .py 文件（除 __init__.py）头部 20 行内
含 `# FROZEN: 详见 AGENTS.md §3.3.1` 注释。

被 CI / 防再犯使用——任何 PR 把 legacy/ 模块 FROZEN 标记删掉都会被拦下。

运行：
    cd backend && .venv/Scripts/python.exe scripts/verify_legacy_frozen.py
"""

from __future__ import annotations

import sys
from pathlib import Path

LEGACY_DIR = Path(__file__).resolve().parent.parent / "agent" / "legacy"
MARKER = "# FROZEN: 详见 AGENTS.md §3.3.1"


def main() -> int:
    if not LEGACY_DIR.exists():
        print(f"[FAIL] legacy 目录不存在：{LEGACY_DIR}")
        return 1

    missing: list[str] = []
    py_files = [
        f for f in LEGACY_DIR.rglob("*.py") if f.name != "__init__.py"
    ]

    for py_file in py_files:
        head = py_file.read_text(encoding="utf-8").splitlines()[:20]
        if not any(MARKER in line for line in head):
            missing.append(str(py_file.relative_to(LEGACY_DIR.parent.parent)))

    if missing:
        print("[FAIL] FROZEN 标记缺失：")
        for m in missing:
            print(f"  - {m}")
        return 1

    print(f"[PASS] {len(py_files)} 个 legacy 模块全部含 FROZEN 标记")
    return 0


if __name__ == "__main__":
    sys.exit(main())
