"""真题 PDF 上传 / 列表 / 详情 / 页面图 / 重新处理。"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, db, tasks
from ..services import pipeline
from . import common

router = APIRouter(prefix="/api/papers", tags=["papers"])

MAX_UPLOAD_MB = 200


@router.post("")
async def upload_paper(
    year: int = Form(..., description="真题年份，如 2009"),
    file: UploadFile = File(..., description="图片型真题 PDF"),
    title: str = Form(""),
    auto_process: bool = Form(True, description="上传后立即渲染+OCR+切题"),
):
    if year < 1990 or year > 2100:
        raise HTTPException(400, "年份不合法")
    head = await file.read(5)
    if head[:4] != b"%PDF":
        raise HTTPException(400, "只接受 PDF 文件")
    await file.seek(0)

    config.ensure_dirs()
    stamp = db.now().replace("-", "").replace(":", "").replace(" ", "_")
    dest = config.UPLOAD_DIR / f"{year}_{stamp}.pdf"
    size = 0
    with dest.open("wb") as fp:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                fp.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过 {MAX_UPLOAD_MB}MB")
            fp.write(chunk)

    rel = str(dest.relative_to(config.ROOT_DIR)).replace("\\", "/")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO papers (year, title, pdf_path, status, dpi, created_at) VALUES (?,?,?,?,?,?)",
            (
                year,
                title or f"{year} 年 408 真题",
                rel,
                "uploaded",
                72.0 * config.RENDER_ZOOM,
                db.now(),
            ),
        )
        paper_id = int(cur.lastrowid)

    if auto_process:
        pipeline.set_status(paper_id, "rendering", 1, "已排队…")
        tasks.submit(pipeline.process_paper, paper_id)

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    return common.paper_out(row)


@router.get("")
def list_papers():
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM questions q WHERE q.paper_id = p.id) AS qn
               FROM papers p ORDER BY p.year DESC, p.id DESC"""
        ).fetchall()
    return [common.paper_out(r, int(r["qn"])) for r in rows]


@router.get("/{paper_id}")
def paper_detail(paper_id: int):
    with db.get_conn() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if paper is None:
            raise HTTPException(404, "真题不存在")
        pages = conn.execute(
            "SELECT * FROM pages WHERE paper_id=? ORDER BY page_no", (paper_id,)
        ).fetchall()
        questions = conn.execute(
            "SELECT * FROM questions WHERE paper_id=? ORDER BY order_no, question_no", (paper_id,)
        ).fetchall()
    data = common.paper_out(paper, len(questions))
    data["pages"] = [common.page_out(p) for p in pages]
    data["questions"] = [common.question_out(q, int(paper["year"])) for q in questions]
    return data


@router.get("/{paper_id}/pages/{page_no}")
def get_page_image(paper_id: int, page_no: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT image_path FROM pages WHERE paper_id=? AND page_no=?", (paper_id, page_no)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在")
    path = common.abs_path(row["image_path"])
    if not path.exists():
        raise HTTPException(404, "页面图丢失，请重新处理")
    return FileResponse(path, media_type="image/png")


@router.post("/{paper_id}/reprocess")
def reprocess(paper_id: int):
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone() is None:
            raise HTTPException(404, "真题不存在")
    pipeline.set_status(paper_id, "rendering", 1, "已排队…")
    tasks.submit(pipeline.process_paper, paper_id)
    return {"ok": True, "paper_id": paper_id}


@router.delete("/{paper_id}")
def delete_paper(paper_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT pdf_path FROM papers WHERE id=?", (paper_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "真题不存在")
        conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    for folder in (config.PAGES_DIR, config.CROPS_DIR):
        target = folder / str(paper_id)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    pdf = common.abs_path(row["pdf_path"])
    if pdf.exists() and pdf.parent == Path(config.UPLOAD_DIR):
        pdf.unlink(missing_ok=True)
    return {"ok": True}
