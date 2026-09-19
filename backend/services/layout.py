"""7.5 layout：把题目分配到 Word 页面。

规则（AGENTS.md 2.4 / 7.5）：
1. 选择题、主观题都连续排，不强制单独起页；
2. 优先保证同一道题不跨页：当前页剩余高度放不下整题就换页；
3. 整题高度超过一页时，才按小问拆成多张图，每个小问同样"放不下就换页"；
4. 跨页题的多张图片视为一个整体参与判断，中间不加分页符。

估算基于"图片按 image_width_cm 等比缩放后的显示高度"，与 word_builder 的排版一致，
所以换页判断足够准；最终仍建议导成 PDF 复查（AGENTS.md 11 节）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import config

PT_TO_CM = 2.54 / 72.0


@dataclass
class Piece:
    """Word 里连续插入的一张裁剪图。"""

    page_no: int
    y0: int
    y1: int
    path: str  # 已有裁剪图路径；按小问拆出来的段落为空，导出时现裁
    height_cm: float


@dataclass
class Segment:
    pieces: list[Piece]
    label: str | None = None

    @property
    def height_cm(self) -> float:
        gap = config.SPACING_PT_PER_GAP * PT_TO_CM
        return sum(p.height_cm + gap for p in self.pieces)


@dataclass
class Entry:
    """一个排版单元：要么一整道题，要么拆分后的一个小问块。"""

    question: dict
    pieces: list[Piece] = field(default_factory=list)
    sub_label: str | None = None
    new_page: bool = False

    @property
    def total_height_cm(self) -> float:
        gap = config.SPACING_PT_PER_GAP * PT_TO_CM
        return sum(p.height_cm + gap for p in self.pieces)


def _content_width_px(question: dict) -> int:
    bbox = question.get("bbox") or {}
    x0 = int(bbox.get("x0") or 0)
    x1 = int(bbox.get("x1") or 0)
    return max(1, x1 - x0)


def piece_height_cm(question: dict, height_px: int, image_width_cm: float) -> float:
    """等比缩放到 image_width_cm 后的显示高度（cm）。"""
    return max(0.0, height_px * image_width_cm / _content_width_px(question))


def _blocks(question: dict) -> list[dict]:
    return sorted(question.get("bbox", {}).get("blocks") or [], key=lambda b: (int(b["page_no"]), int(b["y0"])))


def block_pieces(question: dict, image_width_cm: float) -> list[Piece]:
    """整题直接贴已裁好的块（每题 1 张，跨页题 2 张以上）。"""
    pieces = []
    for block in _blocks(question):
        y0, y1 = int(block["y0"]), int(block["y1"])
        pieces.append(
            Piece(
                page_no=int(block["page_no"]),
                y0=y0,
                y1=y1,
                path=str(block.get("path") or ""),
                height_cm=piece_height_cm(question, y1 - y0, image_width_cm),
            )
        )
    return pieces


def segments(question: dict, image_width_cm: float) -> list[Segment]:
    """按小问把题目切成若干段（只用于整题放不进一页的情况）。"""
    subs = (question.get("bbox") or {}).get("sub_marks") or []
    out: list[Segment] = []
    for block in _blocks(question):
        page_no = int(block["page_no"])
        b0, b1 = int(block["y0"]), int(block["y1"])
        cuts = sorted(
            (int(s["y0"]), str(s.get("label") or ""))
            for s in subs
            if int(s["page_no"]) == page_no and b0 + 5 <= int(s["y0"]) <= b1 - 5
        )
        edges: list[tuple[int, str | None]] = [(b0, None)]
        edges += [(y, label or None) for y, label in cuts]
        edges.append((b1, None))
        for i in range(len(edges) - 1):
            y0, label = edges[i]
            y1 = edges[i + 1][0]
            if y1 - y0 < 10:
                continue
            is_whole_block = y0 == b0 and y1 == b1
            out.append(
                Segment(
                    pieces=[
                        Piece(
                            page_no=page_no,
                            y0=y0,
                            y1=y1,
                            # 整块时直接用已裁好的图，避免重复生成冗余文件
                            path=str(block.get("path") or "") if is_whole_block else "",
                            height_cm=piece_height_cm(question, y1 - y0, image_width_cm),
                        )
                    ],
                    label=label,
                )
            )
    return out


def plan(
    questions: list[dict],
    *,
    image_width_cm: float | None = None,
    with_caption: bool = False,
    note_lines: int = 0,
) -> list[Entry]:
    """生成排版方案。questions 需包含 bbox / image_paths，按 order_no 升序。"""
    image_width_cm = float(image_width_cm or config.IMAGE_WIDTH_CM)
    page_h = config.CONTENT_H_CM * config.LAYOUT_SAFETY_RATIO
    caption_h = config.CAPTION_HEIGHT_CM if with_caption else 0.0
    extra = caption_h + note_lines * config.NOTE_LINE_CM

    entries: list[Entry] = []
    used = 0.0

    def emit(entry: Entry, height: float) -> None:
        nonlocal used
        if used > 0 and used + height > page_h:
            entry.new_page = True  # 放不下就换页，而不是插入分页符空段
            used = 0.0
        entries.append(entry)
        used = page_h if height > page_h else used + height

    for question in questions:
        blocks = block_pieces(question, image_width_cm)
        if not blocks:
            continue
        total = sum(p.height_cm + config.SPACING_PT_PER_GAP * PT_TO_CM for p in blocks) + extra
        if total <= page_h:
            emit(Entry(question=question, pieces=blocks), total)  # 整题不跨页
            continue
        for segment in segments(question, image_width_cm):
            emit(
                Entry(question=question, pieces=segment.pieces, sub_label=segment.label),
                segment.height_cm + caption_h,
            )
    return entries


def estimate_pages(entries: list[Entry]) -> int:
    """估算总页数（仅用于前端提示，非精确值）。"""
    return sum(1 for e in entries if e.new_page) + 1
