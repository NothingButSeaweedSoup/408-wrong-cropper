"""题目导入流水线：渲染 -> OCR 题号 -> 切题 -> 入库。

放在 services 里而不是 api 里：接口只负责提交任务，流水线可被脚本/测试直接调用。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config, db
from . import cropper, ocr_question, pdf_render


def abs_path(rel: str | Path) -> Path:
    """把库里存的相对路径还原成绝对路径。"""
    path = Path(rel)
    return path if path.is_absolute() else config.ROOT_DIR / path


def set_status(paper_id: int, status: str, progress: int, message: str = "") -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE papers SET status=?, progress=?, message=? WHERE id=?",
            (status, int(progress), message, paper_id),
        )


def process_paper(paper_id: int) -> None:
    """完整跑一遍导入流程。任何异常都写成 failed 状态，方便前端看到原因。"""
    with db.get_conn() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if paper is None:
        return

    year = int(paper["year"])
    try:
        set_status(paper_id, "rendering", 5, "渲染 PDF 页面…")
        page_dir = config.PAGES_DIR / str(paper_id)
        pages = pdf_render.render_pdf(abs_path(paper["pdf_path"]), page_dir)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM pages WHERE paper_id=?", (paper_id,))
            conn.executemany(
                "INSERT INTO pages (paper_id, page_no, image_path, width, height) VALUES (?,?,?,?,?)",
                [
                    (
                        paper_id,
                        p.page_no,
                        str(p.path.relative_to(config.ROOT_DIR)).replace("\\", "/"),
                        p.width,
                        p.height,
                    )
                    for p in pages
                ],
            )
            conn.execute("UPDATE papers SET page_count=? WHERE id=?", (len(pages), paper_id))

        set_status(paper_id, "ocr", 20, f"OCR 识别题号（共 {len(pages)} 页）…")

        def on_page(done: int, total: int) -> None:
            set_status(paper_id, "ocr", 20 + int(45 * done / max(1, total)), f"OCR {done}/{total} 页…")

        marks = ocr_question.detect_marks(
            [(p.page_no, p.path, p.width, p.height) for p in pages], progress=on_page
        )
        widths = {p.page_no: p.width for p in pages}
        main_marks = ocr_question.filter_main_marks(marks, widths)
        grouped = ocr_question.group_sub_marks(marks, main_marks)
        sub_marks = [m for bucket in grouped.values() for m in bucket]
        if not main_marks:
            set_status(paper_id, "failed", 0, "未识别到任何题号，请检查 PDF 是否清晰或调整识别参数")
            return

        set_status(paper_id, "splitting", 70, f"识别到 {len(main_marks)} 个题号，正在裁剪…")
        questions = cropper.split_paper(
            year,
            config.CROPS_DIR / str(paper_id),
            pages,
            main_marks,
            sub_marks,
            rel_to=config.ROOT_DIR,
        )

        with db.get_conn() as conn:
            conn.execute("DELETE FROM questions WHERE paper_id=?", (paper_id,))
            conn.executemany(
                """INSERT INTO questions
                   (paper_id, question_no, subject, type, score, parent_id, order_no,
                    bbox_json, image_paths, source, created_at)
                   VALUES (?,?,?,?,?,NULL,?,?,?,'auto',?)""",
                [
                    (
                        paper_id,
                        q["question_no"],
                        q["subject"],
                        q["type"],
                        q["score"],
                        q["order_no"],
                        json.dumps(
                            {
                                "blocks": q["bbox"]["blocks"],
                                "sub_marks": q["bbox"]["sub_marks"],
                                "x0": q["bbox"]["x0"],
                                "x1": q["bbox"]["x1"],
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(q["image_paths"], ensure_ascii=False),                        db.now(),
                    )
                    for q in questions
                ],
            )
        set_status(
            paper_id,
            "split",
            100,
            f"完成：识别 {len(main_marks)} 个题号，切出 {len(questions)} 道题",
        )
    except Exception as exc:  # noqa: BLE001 —— 兜底写库，前端轮询能看到原因
        set_status(paper_id, "failed", 0, f"处理失败：{exc}")
        raise
