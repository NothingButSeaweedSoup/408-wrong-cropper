"""排版规则单元测试（AGENTS.md 2.4 / 7.5 的换页与拆分规则）。

跑法：.venv\\Scripts\\python tests\\test_layout.py
高度换算：块高 1600px -> 图片宽 16cm 时显示高 16/1600*1600 = 16cm，
所以下面用 `px = cm * 100` 直接构造题目。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend import config  # noqa: E402
from backend.services import layout  # noqa: E402

WIDTH_PX = 1600  # 正文宽，对应 16cm
ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def question(qno: int, blocks_cm: list[tuple[int, float]], sub_cm: list[tuple[int, float]] = ()) -> dict:
    """blocks_cm: [(page_no, 高度cm)]；sub_cm: [(page_no, 相对块顶部的 cm 位置)]"""
    blocks = []
    for page_no, height_cm in blocks_cm:
        blocks.append({"page_no": page_no, "y0": 0, "y1": int(height_cm * 100), "path": f"q{qno}_p{page_no}.png"})
    subs = [
        {"page_no": page_no, "y0": int(offset * 100), "label": f"({i})"}
        for i, (page_no, offset) in enumerate(sub_cm, start=1)
    ]
    return {
        "id": qno,
        "paper_id": 1,
        "year": 2009,
        "order_no": qno,
        "question_no": qno,
        "image_paths": [b["path"] for b in blocks],
        "bbox": {"blocks": blocks, "sub_marks": subs, "x0": 0, "x1": WIDTH_PX},
    }


def main() -> int:
    page_h = config.CONTENT_H_CM * config.LAYOUT_SAFETY_RATIO
    print(f"可用高度 = {config.CONTENT_H_CM:.1f}cm x 安全系数 {config.LAYOUT_SAFETY_RATIO} = {page_h:.2f}cm")

    print("A) 两道 15cm 的题：第二题换页，保证一题不跨页")
    entries = layout.plan([question(1, [(1, 15)]), question(2, [(1, 15)])])
    check("两题两单元", len(entries) == 2, str(len(entries)))
    check("第一题不换页，第二题换页", [e.new_page for e in entries] == [False, True], str([e.new_page for e in entries]))

    print("B) 三道 9cm 的题：前两题同页，第三题换页")
    entries = layout.plan([question(i, [(1, 9)]) for i in (1, 2, 3)])
    check("换页标志 [F,F,T]", [e.new_page for e in entries] == [False, False, True], str([e.new_page for e in entries]))

    print("C) 单题 40cm 超过一页 + 3 个小问：按小问拆成 4 段")
    entries = layout.plan([question(41, [(1, 40)], [(1, 10), (1, 20), (1, 30)])])
    check("拆成 4 段", len(entries) == 4, str(len(entries)))
    check("每段约 10cm", all(abs(e.total_height_cm - (10 + config.SPACING_PT_PER_GAP * 2.54 / 72)) < 0.01 for e in entries),
          str([round(e.total_height_cm, 2) for e in entries]))
    check("第 2 段的小问标签", entries[1].sub_label == "(1)", str(entries[1].sub_label))
    check("第 3 段换页（装不下了）", [e.new_page for e in entries] == [False, False, True, False],
          str([e.new_page for e in entries]))
    check("拆分后用的是现裁（path 为空）", all(p.path == "" for e in entries for p in e.pieces))

    print("D) 单题 40cm 超过一页但没识别到小问：整题一块，不硬拆")
    entries = layout.plan([question(42, [(1, 40)])])
    check("只有 1 个单元", len(entries) == 1, str(len(entries)))
    check("保留整块（path 复用已裁图片）", entries[0].pieces[0].path == "q42_p1.png", entries[0].pieces[0].path)

    print("E) 跨页题（两块）当成一个整体：一起换页、不拆开")
    entries = layout.plan([question(1, [(1, 5)]), question(43, [(1, 18), (2, 3)])])
    check("跨页题是 1 个单元 2 张图", len(entries) == 2 and len(entries[1].pieces) == 2, str([len(e.pieces) for e in entries]))
    check("跨页题整体换页", entries[1].new_page is True)
    check("跨页题块按页序", [p.page_no for p in entries[1].pieces] == [1, 2], str([p.page_no for p in entries[1].pieces]))

    print("F) 刚好放得下就不换页（24cm + 前题 0.2cm 间距）")
    entries = layout.plan([question(1, [(1, 24)])])
    check("不换页", entries[0].new_page is False)

    print("G) 笔记空行会参与高度估算，从而触发换页")
    entries = layout.plan([question(1, [(1, 20)])] * 2, note_lines=5)
    check("加了 5 行留白后第二题换页", entries[1].new_page is True, str([e.new_page for e in entries]))

    print("\n全部通过" if ok else "\n存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
