"""7.2 ocr_question：从整页 PNG 里识别题号，输出题号的像素坐标。

只用 RapidOCR（onnxruntime + PP-OCRv4 mobile 模型，约 15MB），
不引入 PaddleOCR/PaddlePaddle（GB 级依赖）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .. import config

# 题号：`6.` `07、` `6。` `6·` …（AGENTS.md 11 节提到 OCR 会把 . 认成 。）
MAIN_RE = re.compile(r"^\s*[（(\[【]?\s*(\d{1,2})\s*[.、．。·・,，:：]\s*")
# 小问：`(1)` `1)` `（2）`；不匹配 `1.`
SUB_RE = re.compile(r"^\s*[（(]?\s*(\d{1,2})\s*[)）]\s*")


@dataclass
class OcrLine:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class QuestionMark:
    """识别到的题号标记。坐标均为页面 PNG 像素坐标。"""

    qno: int
    page_no: int
    y0: float
    y1: float
    x0: float
    x1: float
    text: str
    confidence: float
    kind: str = "main"  # main=大题号, sub=小问
    label: str = ""

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class OcrPageResult:
    page_no: int
    lines: list[OcrLine] = field(default_factory=list)


@lru_cache(maxsize=1)
def get_engine():
    """RapidOCR 引擎较贵（加载 onnx 模型），进程内只初始化一次。"""
    from rapidocr_onnxruntime import RapidOCR

    kwargs: dict = {"use_angle_cls": False}
    return RapidOCR(**kwargs)


def _to_lines(raw) -> list[OcrLine]:
    lines: list[OcrLine] = []
    for item in raw or []:
        try:
            box, text, score = item[0], item[1], float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        lines.append(OcrLine(str(text), score, min(xs), min(ys), max(xs), max(ys)))
    return lines


def ocr_page(image_path: str | Path) -> OcrPageResult:
    """对单页做 OCR，返回全部文本行。"""
    engine = get_engine()
    raw, _elapse = engine(str(image_path))
    page_no = int(re.findall(r"page_(\d+)", Path(image_path).stem)[0]) if "page_" in Path(image_path).stem else 0
    return OcrPageResult(page_no=page_no, lines=_to_lines(raw))


def parse_question_number(text: str) -> int | None:
    """从 OCR 文本里提取大题号；不是题号返回 None。"""
    m = MAIN_RE.match(text or "")
    if not m:
        return None
    return int(m.group(1))


def parse_sub_label(text: str) -> str | None:
    """提取小问标号，返回 `(1)` 这样的归一化文本。"""
    m = SUB_RE.match(text or "")
    if not m:
        return None
    return f"({int(m.group(1))})"


def detect_marks(
    pages: list[tuple[int, Path, int, int]],
    progress=None,
) -> list[QuestionMark]:
    """对每页 OCR 并收集候选标记。

    pages: [(page_no, image_path, width, height), ...]
    progress: 可选回调 progress(done_pages, total_pages)，用于上报进度。
    返回：全部候选（含小问），未做去重/过滤。
    """
    marks: list[QuestionMark] = []
    total = len(pages)
    for index, (page_no, path, width, _height) in enumerate(pages, start=1):
        result = ocr_page(path)
        if progress:
            progress(index, total)
        for line in result.lines:
            if line.score < config.OCR_TEXT_SCORE_MIN:
                continue
            qno = parse_question_number(line.text)
            if qno is not None and config.QNO_MIN <= qno <= config.QNO_MAX:
                marks.append(
                    QuestionMark(
                        qno=qno,
                        page_no=page_no,
                        y0=line.y0,
                        y1=line.y1,
                        x0=line.x0,
                        x1=line.x1,
                        text=line.text,
                        confidence=line.score,
                        kind="main",
                    )
                )
                continue
            label = parse_sub_label(line.text)
            # 小问只认左侧列的，正文里的 `1)` 参考编号会被 x 比例过滤掉
            if label and line.x0 < width * config.NUMBER_X_RATIO:
                marks.append(
                    QuestionMark(
                        qno=0,
                        page_no=page_no,
                        y0=line.y0,
                        y1=line.y1,
                        x0=line.x0,
                        x1=line.x1,
                        text=line.text,
                        confidence=line.score,
                        kind="sub",
                        label=label,
                    )
                )
    return marks


def filter_main_marks(
    marks: list[QuestionMark],
    page_widths: dict[int, int],
) -> list[QuestionMark]:
    """过滤大题号：左侧列 + 置信度 + 全卷题号单调递增。

    单调递增是关键：OCR 会把图表里的数字、年份、公式编号认成题号，
    但真题题号一定是自上而下递增的，靠这个约束能干掉绝大部分误识别，
    同时允许中间漏识别（跳过某个题号不会连带丢弃后面的）。
    """
    main = [m for m in marks if m.kind == "main" and m.confidence >= config.OCR_MIN_CONFIDENCE]
    main.sort(key=lambda m: (m.page_no, m.y0))

    kept: list[QuestionMark] = []
    last_qno = 0
    for mark in main:
        width = page_widths.get(mark.page_no, 0)
        if width and mark.x0 > width * config.NUMBER_X_RATIO:
            continue  # 右半页的数字，不是题号
        if mark.qno <= last_qno:
            continue  # 非递增，判为误识别
        kept.append(mark)
        last_qno = mark.qno
    return kept


def group_sub_marks(
    marks: list[QuestionMark],
    main_marks: list[QuestionMark],
) -> dict[int, list[QuestionMark]]:
    """把小题号归到所属大题的 y 区间里（跨页题会落到续页块上）。"""
    subs = [m for m in marks if m.kind == "sub"]
    grouped: dict[int, list[QuestionMark]] = {}
    if not main_marks:
        return grouped
    for mark in main_marks:
        # 大题区间 = 本题号 y 到下一个大题号 y（按页顺序）
        nxt = _next_main(main_marks, mark)
        bucket: list[QuestionMark] = []
        for sub in subs:
            if sub.page_no < mark.page_no:
                continue
            if nxt and sub.page_no > nxt.page_no:
                continue
            if sub.page_no == mark.page_no and sub.y0 < mark.y0 - 1:
                continue
            if nxt and sub.page_no == nxt.page_no and sub.y0 > nxt.y0 - 1:
                continue
            bucket.append(sub)
        if bucket:
            bucket.sort(key=lambda m: (m.page_no, m.y0))
            grouped[mark.qno] = bucket
    return grouped


def _next_main(main_marks: list[QuestionMark], mark: QuestionMark) -> QuestionMark | None:
    for i, m in enumerate(main_marks):
        if m is mark and i + 1 < len(main_marks):
            return main_marks[i + 1]
    return None
