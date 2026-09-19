"""7.3 cropper：按题号 y 坐标切题 + 跨页合并 + 小问标定。

坐标全部使用"页面 PNG 像素坐标"。裁剪时按整卷统一的内容 x 范围切开，
保证每题图片的物理缩放比例一致（AGENTS.md 7.4：图片宽度统一 16cm）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import config
from . import ocr_question
from .ocr_question import QuestionMark
from .pdf_render import PageImage


@dataclass
class Block:
    """一道题在某一页上的纵向区间。"""

    page_no: int
    y0: int
    y1: int
    path: str = ""  # 相对项目根目录的裁剪图路径

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    def as_dict(self) -> dict:
        return {"page_no": self.page_no, "y0": self.y0, "y1": self.y1, "path": self.path}


# ---------------------------------------------------------------- 图像工具
def load_gray(path: str | Path) -> np.ndarray:
    """读页面图并转灰度 ndarray（用于墨迹检测，不落盘）。"""
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def content_x_range(grays: dict[int, np.ndarray], pad: int = 12) -> tuple[int, int]:
    """整卷统一的正文横向范围（取所有页墨迹列的并集）。

    统一范围而不是每题各自紧裁，是为了让所有题目图片保持同一缩放比，
    贴到 Word 里字号才一致。
    """
    left, right = None, None
    for gray in grays.values():
        dark = gray < 200
        cols = np.where(dark.any(axis=0))[0]
        if cols.size == 0:
            continue
        left = int(cols[0]) if left is None else min(left, int(cols[0]))
        right = int(cols[-1]) if right is None else max(right, int(cols[-1]))
    if left is None or right is None or right - left < 100:
        any_gray = next(iter(grays.values()))
        return 0, int(any_gray.shape[1])
    width = int(next(iter(grays.values())).shape[1])
    return max(0, left - pad), min(width, right + pad)


def has_ink(gray: np.ndarray, y_from: int, y_to: int) -> bool:
    """判断 [y_from, y_to) 区间是否存在成片墨迹（排除零星噪点）。"""
    y_from = max(0, int(y_from))
    y_to = min(int(gray.shape[0]), int(y_to))
    if y_to - y_from < 5:
        return False
    region = gray[y_from:y_to] < 128
    rows = (region.mean(axis=1) > config.INK_ROW_RATIO).sum()
    return bool(rows >= config.INK_MIN_ROWS)


def trim_trailing_blank(
    gray: np.ndarray,
    y0: int,
    y1: int,
    *,
    min_keep: int = 60,
    threshold: int = 200,
    ratio: float = 0.004,
) -> int:
    """把块底部连续的空白行切掉，只保留一点白边。

    最后一题按 AGENTS.md 5.3 要"裁到页底"，但页底往往是大片空白，
    贴进 Word 会白占半页，所以只在**确认是空白行**时才回收，
    不会切掉任何有内容的部分。
    """
    region = gray[max(0, y0) : int(y1)] < threshold
    if region.size == 0:
        return int(y1)
    rows = region.mean(axis=1)
    ink = np.where(rows > ratio)[0]
    if ink.size == 0:
        return int(y1)
    last_ink = max(0, y0) + int(ink[-1])
    return int(min(y1, max(last_ink + config.PAD_BOTTOM_PX, y0 + min_keep)))


def crop_block(
    page_path: str | Path,
    y0: int,
    y1: int,
    out_path: str | Path,
    *,
    x0: int = 0,
    x1: int | None = None,
) -> tuple[int, int]:
    """把页面图的一段裁出来存成 PNG，返回 (宽, 高)。"""
    page_path, out_path = Path(page_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(page_path) as im:
        w, h = im.size
        x0 = max(0, min(int(x0), w - 1))
        x1 = w if x1 is None else max(x0 + 1, min(int(x1), w))
        y0 = max(0, min(int(y0), h - 1))
        y1 = h if y1 is None else max(y0 + 1, min(int(y1), h))
        box = im.crop((x0, y0, x1, y1))
        box.save(out_path, optimize=True)
        return box.size


# ---------------------------------------------------------------- 切题主流程
def content_bounds(page: PageImage) -> tuple[int, int]:
    """页面的正文上下边界（去掉页眉页脚）。"""
    top = int(page.height * config.HEADER_TRIM_RATIO)
    bottom = page.height - int(page.height * config.FOOTER_TRIM_RATIO)
    return top, bottom


def build_blocks(
    pages: list[PageImage],
    main_marks: list[QuestionMark],
    grays: dict[int, np.ndarray],
) -> dict[int, list[Block]]:
    """按题号 y 坐标切块，并合并跨页题的续页块。"""
    by_page: dict[int, PageImage] = {p.page_no: p for p in pages}
    page_order = [p.page_no for p in pages]
    marks_by_page: dict[int, list[QuestionMark]] = {}
    for mark in main_marks:
        marks_by_page.setdefault(mark.page_no, []).append(mark)
    for marks in marks_by_page.values():
        marks.sort(key=lambda m: m.y0)

    result: dict[int, list[Block]] = {}
    for idx, mark in enumerate(main_marks):
        page = by_page[mark.page_no]
        top_lim, bottom_lim = content_bounds(page)
        y0 = max(top_lim, int(mark.y0) - config.PAD_TOP_PX)

        same_page = marks_by_page[mark.page_no]
        pos = same_page.index(mark)
        if pos + 1 < len(same_page):
            y1 = int(same_page[pos + 1].y0) - config.PAD_BOTTOM_PX
        else:
            y1 = bottom_lim
        y1 = min(y1, bottom_lim)
        if y1 <= y0 + 10:
            y1 = min(y0 + 10, page.height)

        blocks = [Block(mark.page_no, y0, y1)]

        # ---- 跨页续块：本题是所在页最后一题，且下一页题号之前还有内容
        if pos + 1 == len(same_page):
            nxt = main_marks[idx + 1] if idx + 1 < len(main_marks) else None
            for pno in page_order:
                if pno <= mark.page_no or pno > (nxt.page_no if nxt else page_order[-1]):
                    continue
                p = by_page[pno]
                p_top, p_bottom = content_bounds(p)
                stop = p_bottom
                if nxt and nxt.page_no == pno:
                    stop = min(int(nxt.y0) - config.PAD_BOTTOM_PX, p_bottom)
                if stop <= p_top + 10:
                    break
                if has_ink(grays[pno], p_top, stop):
                    blocks.append(Block(pno, p_top, stop))
                else:
                    break  # 续页没有内容，说明不是跨页题
        result[mark.qno] = [
            Block(b.page_no, b.y0, trim_trailing_blank(grays[b.page_no], b.y0, b.y1))
            if b.y1 - b.y0 > 60
            else b
            for b in blocks
        ]
    return result


def coalesce_blocks(blocks: list[dict], gap: int | None = None) -> list[dict]:
    """把同一页上首尾相接（或重叠）的块合成一块。

    人工合并相邻两题、或把切分出来的两半合回去时用得上，
    否则同一页会留下两个断开的碎块、Word 里也多一张多余的图。
    """
    if gap is None:
        gap = config.PAD_TOP_PX + config.PAD_BOTTOM_PX + 10
    out: list[dict] = []
    for block in sorted((dict(b) for b in blocks), key=lambda b: (int(b["page_no"]), int(b["y0"]))):
        if out:
            last = out[-1]
            if int(block["page_no"]) == int(last["page_no"]) and int(block["y0"]) <= int(last["y1"]) + gap:
                last["y1"] = max(int(last["y1"]), int(block["y1"]))
                continue
        out.append(block)
    return out


def sub_marks_for(blocks: list[Block], sub_marks: list[QuestionMark]) -> list[dict]:
    """把小题号映射到各个块内的绝对 y 坐标，供排版时拆分。"""
    out: list[dict] = []
    for block in blocks:
        for sub in sub_marks:
            if sub.page_no != block.page_no:
                continue
            if block.y0 + 5 <= sub.y0 <= block.y1 - 5:
                out.append({"page_no": block.page_no, "y0": int(sub.y0), "label": sub.label})
    out.sort(key=lambda s: (s["page_no"], s["y0"]))
    return out


def split_paper(
    year: int,
    paper_dir: Path,
    pages: list[PageImage],
    main_marks: list[QuestionMark],
    sub_marks: list[QuestionMark],
) -> list[dict]:
    """把识别结果切成题目图片，返回待入库的题目列表。"""
    grays = {p.page_no: load_gray(p.path) for p in pages}
    x0, x1 = content_x_range(grays)
    blocks_by_qno = build_blocks(pages, main_marks, grays)
    mark_text = {m.qno: m.text for m in main_marks}
    paper_dir.mkdir(parents=True, exist_ok=True)

    questions: list[dict] = []
    for qno, blocks in blocks_by_qno.items():
        for i, block in enumerate(blocks, start=1):
            out = paper_dir / f"{year}_q{qno}_p{i}.png"
            crop_block(
                next(p.path for p in pages if p.page_no == block.page_no),
                block.y0,
                block.y1,
                out,
                x0=x0,
                x1=x1,
            )
            block.path = config.rel_path(out)

        own = sub_marks_for(blocks, sub_marks)
        subject, qtype, score = config.subject_of(qno)
        # 分值以卷面印的「（8分）」为准，各年综合题分布不同；读不到才落到默认值。
        ocr_score = ocr_question.parse_score(mark_text.get(qno, ""))
        score_source = "default"
        if ocr_score is not None:
            score, score_source = ocr_score, "ocr"
        questions.append(
            {
                "question_no": qno,
                "subject": subject,
                "type": qtype,
                "score": score,
                "score_source": score_source,
                "order_no": qno,
                "image_paths": [b.path for b in blocks],
                "bbox": {
                    "blocks": [b.as_dict() for b in blocks],
                    "sub_marks": own,
                    "x0": x0,
                    "x1": x1,
                },
            }
        )
    questions.sort(key=lambda q: q["order_no"])
    return questions


def recrop_question(
    year: int,
    paper_dir: Path,
    page_paths: dict[int, Path],
    question: dict,
) -> dict:
    """人工校正后按新的 bbox 重新裁剪，返回更新后的 image_paths / bbox。"""
    blocks = question["bbox"].get("blocks", [])
    paper_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, block in enumerate(sorted(blocks, key=lambda b: b["page_no"]), start=1):
        page_path = page_paths.get(int(block["page_no"]))
        if page_path is None:
            continue
        out = paper_dir / f"{year}_q{question['question_no']}_p{i}.png"
        crop_block(
            page_path,
            int(block["y0"]),
            int(block["y1"]),
            out,
            x0=int(question["bbox"].get("x0") or 0),
            x1=question["bbox"].get("x1"),
        )
        block["path"] = config.rel_path(out)
        paths.append(block["path"])
    question["image_paths"] = paths
    return question
