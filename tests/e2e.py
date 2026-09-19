"""端到端自测：不经过 HTTP，直接跑 渲染 -> OCR -> 切题 -> 排版 -> 生成 Word。

跑法：
    .venv\\Scripts\\python tests\\e2e.py
数据写在 data/_selftest/，不污染正式库。
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

# Windows 控制台默认 GBK，中文/符号容易炸，这里统一成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / "data" / "_selftest"
os.environ["ZC_DATA_DIR"] = str(TEST_DATA)

from backend import config, db  # noqa: E402
from backend.services import layout, pipeline, word_builder  # noqa: E402
from tests.make_sample_pdf import build as build_sample  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")


def montage(items: list[tuple[str, Path]], out: Path, width: int = 640) -> Path:
    """把裁剪图拼成一张联系表，方便肉眼看切得对不对。"""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    tiles = []
    for label, path in items:
        with Image.open(path) as im:
            ratio = width / im.width
            tiles.append((label, im.convert("L").resize((width, max(1, int(im.height * ratio))))))
    total = sum(t.height + 22 for _, t in tiles)
    sheet = Image.new("L", (width, total), 255)
    draw = ImageDraw.Draw(sheet)
    y = 0
    for label, tile in tiles:
        draw.text((4, y + 4), label, font=font, fill=0)
        y += 22
        sheet.paste(tile, (0, y))
        draw.line((0, y - 1, width, y - 1), fill=128)
        y += tile.height
    sheet.save(out)
    return out


def main() -> int:
    if TEST_DATA.exists():
        import shutil

        shutil.rmtree(TEST_DATA, ignore_errors=True)
    config.ensure_dirs()
    db.init_db()

    print("1) 生成图片型 PDF 样本")
    pdf_path = build_sample(TEST_DATA / "uploads" / "sample_2009.pdf")
    check("样本 PDF 生成", pdf_path.exists(), f"{pdf_path.stat().st_size} bytes")

    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO papers (year, title, pdf_path, status, dpi, created_at) VALUES (?,?,?,?,?,?)",
            (
                2009,
                "2009 年 408 真题（自测样本）",
                str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
                "uploaded",
                72.0 * config.RENDER_ZOOM,
                db.now(),
            ),
        )
        paper_id = int(cur.lastrowid)

    print("2) 跑流水线（渲染 -> OCR -> 切题）")
    pipeline.process_paper(paper_id)

    with db.get_conn() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        rows = conn.execute(
            "SELECT * FROM questions WHERE paper_id=? ORDER BY order_no", (paper_id,)
        ).fetchall()
    print(f"   status={paper['status']}  progress={paper['progress']}  msg={paper['message']}")
    check("流水线成功", paper["status"] == "split", paper["message"])
    if paper["status"] != "split":
        return 1

    questions = [db.question_out(r) for r in rows]
    print("3) 识别结果")
    for q in questions:
        blocks = ", ".join(f"p{b['page_no']}:{b['y0']}~{b['y1']}" for b in q["bbox"]["blocks"])
        subs = len(q["bbox"].get("sub_marks") or [])
        print(f"   第 {q['question_no']:>2} 题 [{q['subject']}/{q['type']}] 块[{blocks}] 小问={subs}")

    qnos = [q["question_no"] for q in questions]
    check("识别到 1~5 题", qnos == [1, 2, 3, 4, 5], str(qnos))
    q3 = next((q for q in questions if q["question_no"] == 3), None)
    check("第 3 题为跨页题（2 块）", bool(q3) and len(q3["bbox"]["blocks"]) == 2,
          str([b["page_no"] for b in q3["bbox"]["blocks"]]) if q3 else "")
    q4 = next((q for q in questions if q["question_no"] == 4), None)
    check("第 4 题识别出小问", bool(q4) and len(q4["bbox"].get("sub_marks") or []) >= 2,
          str([s["label"] for s in (q4["bbox"].get("sub_marks") or [])]) if q4 else "")
    check("跨页题图片文件都存在", all((config.ROOT_DIR / p).exists() for q in questions for p in q["image_paths"]))

    sheet = montage(
        [(f"q{q['question_no']} p{b['page_no']}", config.ROOT_DIR / b["path"])
         for q in questions for b in q["bbox"]["blocks"]],
        TEST_DATA / "crops_montage.png",
    )
    print(f"   联系表: {sheet}")

    print("4) 排版 + 生成 Word")
    entries = layout.plan(questions, with_caption=False, note_lines=0)
    for e in entries:
        print(f"   第 {e.question['question_no']} 题 图片{len(e.pieces)}张 "
              f"高{e.total_height_cm:.1f}cm 换页={e.new_page}")
    with db.get_conn() as conn:
        page_rows = conn.execute(
            "SELECT page_no, image_path FROM pages WHERE paper_id=?", (paper_id,)
        ).fetchall()
    page_paths = {
        paper_id: {int(r["page_no"]): (config.ROOT_DIR / r["image_path"]) for r in page_rows}
    }
    word_builder.materialize_pieces(entries, page_paths, config.CROPS_DIR, rel_to=config.ROOT_DIR)
    out = config.EXPORT_DIR / word_builder.export_filename()
    stats = word_builder.build(entries, out, with_caption=False, note_lines=0, title="2009 年 408 真题错题本")
    print(f"   {out.name}  {out.stat().st_size} bytes  images={stats['image_count']}")
    check("docx 生成", out.exists() and out.stat().st_size > 5000)
    check("文件名符合 408错题本_YYYY-MM-DD_HHMM.docx", out.name.startswith("408错题本_") and out.suffix == ".docx")

    with zipfile.ZipFile(out) as zf:
        document = zf.read("word/document.xml").decode("utf-8")
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    drawings = document.count("<w:drawing>")
    page_breaks = document.count("w:pageBreakBefore")
    keep_next = document.count("w:keepNext")
    planned_breaks = sum(1 for e in entries if e.new_page)
    print(f"   drawings={drawings} media={len(media)} pageBreakBefore={page_breaks} keepNext={keep_next}")
    check("Word 里图片数与排版一致", drawings == stats["image_count"] and drawings >= 6, f"{drawings}")
    check("换页次数与排版方案一致", page_breaks == planned_breaks, f"{page_breaks} vs {planned_breaks}")
    check("跨页题图片保持在一起(keepNext)", keep_next >= 1, str(keep_next))

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
