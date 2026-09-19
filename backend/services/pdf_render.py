"""7.1 pdf_render：把图片型 PDF 每页渲染成 PNG。

坐标体系说明（全项目统一）：
    页面 PNG 的像素坐标 = PDF 点坐标 * zoom，且原点在左上角。
    bbox / 题号 y 坐标一律存"页面 PNG 像素"，前端叠加层与裁剪都用同一套，
    不做二次换算，避免 DPI 换算带来的错位。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:  # PyMuPDF 1.24+ 推荐 import pymupdf，旧的 fitz 名字已弃用
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from .. import config


@dataclass(frozen=True)
class PageImage:
    page_no: int  # 1-based，与 PDF 页码一致
    path: Path
    width: int
    height: int


def render_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    zoom: float | None = None,
    grayscale: bool | None = None,
) -> list[PageImage]:
    """渲染 PDF 每一页到 out_dir/page_{n}.png。"""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = config.RENDER_ZOOM if zoom is None else zoom
    grayscale = config.RENDER_GRAYSCALE if grayscale is None else grayscale
    colorspace = fitz.csGRAY if grayscale else fitz.csRGB

    pages: list[PageImage] = []
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，无法渲染")
        matrix = fitz.Matrix(zoom, zoom)
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, colorspace=colorspace, alpha=False)
            path = out_dir / f"page_{index}.png"
            pix.save(path)
            pages.append(PageImage(index, path, pix.width, pix.height))
    if not pages:
        raise ValueError("PDF 没有任何页面")
    return pages


def page_size(pdf_path: str | Path) -> tuple[int, float]:
    """返回 (页数, 渲染用 DPI)。"""
    with fitz.open(pdf_path) as doc:
        return doc.page_count, 72.0 * config.RENDER_ZOOM
