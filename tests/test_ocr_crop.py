"""OCR 题号识别 + 裁剪工具的单元测试（不加载 OCR 模型，秒级跑完）。

跑法：.venv\\Scripts\\python tests\\test_ocr_crop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend import config  # noqa: E402
from backend.services import cropper, ocr_question as ocr  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def mark(qno, page_no, y0, x0=90, conf=0.95, kind="main", label="", width=1785):
    return ocr.QuestionMark(
        qno=qno, page_no=page_no, y0=y0, y1=y0 + 40, x0=x0, x1=x0 + 60,
        text=f"{qno}.", confidence=conf, kind=kind, label=label,
    )


def test_question_number_regex() -> None:
    print("题号正则（AGENTS.md 11 节列的各种误识别）")
    cases = {
        "6. 下列关于": 6,
        "07. 设某": 7,
        "6。下列关于": 6,
        "6、下列说法": 6,
        "6·下列说法": 6,
        "43. (8分) 设某计算机": 43,
        "(6) 下列说法": None,   # 括号是小题号，不是大题号
        "1) 请计算": None,
        "A. 数据的逻辑结构": None,
        "2009 年真题": None,
        "12.5 的浮点数": 12,     # 小数会被误判成题号，靠单调递增 + x 位置过滤
    }
    for text, expected in cases.items():
        got = ocr.parse_question_number(text)
        check(f"parse_question_number({text!r}) -> {expected}", got == expected, f"got={got}")


def test_sub_label_regex() -> None:
    print("小问标号正则")
    cases = {"(1) 请计算": "(1)", "1）请计算": "(1)", "（12）说明": "(12)", "1. 请计算": None, "A. 选项": None}
    for text, expected in cases.items():
        got = ocr.parse_sub_label(text)
        check(f"parse_sub_label({text!r}) -> {expected}", got == expected, f"got={got}")


def test_filter_main_marks() -> None:
    print("题号过滤：左侧列 + 单调递增（干掉图表数字误识别）")
    widths = {1: 1785, 2: 1785}
    marks = [
        mark(1, 1, 200),
        mark(2, 1, 800),
        mark(12, 1, 900, x0=1400),   # 右半页的数字 -> 位置过滤
        mark(3, 1, 1400),
        mark(2, 2, 300),             # 重复/倒退 -> 单调过滤
        mark(4, 2, 500),
        mark(5, 2, 900, conf=0.4),   # 置信度太低
        mark(5, 2, 1500),
    ]
    kept = ocr.filter_main_marks(marks, widths)
    check("保留 [1,2,3,4,5]", [m.qno for m in kept] == [1, 2, 3, 4, 5], str([m.qno for m in kept]))

    grouped = ocr.group_sub_marks(
        [mark(0, 2, 600, kind="sub", label="(1)"), mark(0, 2, 1200, kind="sub", label="(2)")],
        kept,
    )
    check("小问归到第 4 题", grouped.get(4) is not None and len(grouped[4]) == 2, str({k: len(v) for k, v in grouped.items()}))


def test_ink_helpers() -> None:
    print("裁剪工具：墨迹检测 / 空白回收 / 内容横向范围")
    gray = np.full((600, 1000), 255, dtype=np.uint8)
    gray[100:300, 50:900] = 0        # 一段正文
    gray[500:520, 50:900] = 0        # 页脚
    check("正文区间有墨迹", cropper.has_ink(gray, 60, 320))
    check("空白区间无墨迹", not cropper.has_ink(gray, 340, 480))
    check("页脚区间有墨迹", cropper.has_ink(gray, 480, 560))

    y1 = cropper.trim_trailing_blank(gray, 100, 480)
    check("尾部空白被回收", 300 <= y1 <= 330, f"y1={y1}")
    check("不裁掉内容", y1 > 300)

    x0, x1 = cropper.content_x_range({1: gray})
    check("内容横向范围", x0 <= 50 and x1 >= 900, f"{x0}~{x1}")

    page = cropper.PageImage(1, Path("nope.png"), 1000, 600)
    top, bottom = cropper.content_bounds(page)
    check("页眉页脚裁切", top == int(600 * config.HEADER_TRIM_RATIO) and bottom == 600 - int(600 * config.FOOTER_TRIM_RATIO), f"{top}~{bottom}")


def test_subject_rules() -> None:
    print("408 卷面结构映射（AGENTS.md 12.1 的满分分配）")
    expect = {
        1: ("ds", "choice", 2.0),
        11: ("ds", "choice", 2.0),
        12: ("co", "choice", 2.0),
        23: ("os", "choice", 2.0),
        33: ("cn", "choice", 2.0),
        40: ("cn", "choice", 2.0),
        41: ("ds", "subjective", None),
        44: ("co", "subjective", None),
        46: ("os", "subjective", None),
        47: ("cn", "subjective", None),
    }
    for qno, want in expect.items():
        got = config.subject_of(qno)
        check(f"第 {qno} 题 -> {want}", got == want, f"got={got}")
    check("满分合计 150", sum(config.MODULE_FULL_SCORE[k] for k in ("ds", "co", "os", "cn")) == 150)


if __name__ == "__main__":
    test_question_number_regex()
    test_sub_label_regex()
    test_filter_main_marks()
    test_ink_helpers()
    test_subject_rules()
    print("\n全部通过" if ok else "\n存在失败项")
    raise SystemExit(0 if ok else 1)
