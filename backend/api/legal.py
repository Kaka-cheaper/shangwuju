"""法务文本（Phase 0.22；docs/legal/*.md 直读）。

占位草案，真上线前需法务审核。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(tags=["运营辅助"])


def _read_legal_doc(filename: str) -> str:
    """读 docs/legal/ 下的 markdown 文件。打包到 docker 时 mock_data 同卷会一起 copy。"""
    backend_root = Path(__file__).resolve().parent.parent

    candidates = [
        backend_root.parent / "docs" / "legal" / filename,
        Path("/app/docs/legal") / filename,  # docker 容器内
        Path("/docs/legal") / filename,  # docker 容器内备选
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(
        status_code=404,
        detail=f"法务文档 {filename} 未就位（docs/legal/）",
    )


@router.get("/legal/terms", summary="用户协议（占位草案）")
def legal_terms() -> Response:
    """用户协议（占位草案；真上线前需律师审核）。"""
    content = _read_legal_doc("terms-of-service.md")
    return Response(content=content, media_type="text/markdown; charset=utf-8")


@router.get("/legal/privacy", summary="隐私政策（占位草案）")
def legal_privacy() -> Response:
    """隐私政策（占位草案；真上线前需律师审核）。"""
    content = _read_legal_doc("privacy-policy.md")
    return Response(content=content, media_type="text/markdown; charset=utf-8")
