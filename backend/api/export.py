"""导出错题本 Word。"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config, db
from ..schemas import ExportOut, ExportRequest
from ..services import layout, word_builder
from . import common

router = APIRouter(prefix="/api", tags=["export"])

# 一次性下载票：手机端把 Word 交给**系统浏览器**下载时，浏览器里没有 WebView 的登录 Cookie，
# 直接开 /download 会 401；把会话 token 塞进 URL 又会留在浏览器历史里。
# 所以先用登录态换一张 2 分钟、只能用一次的票，再用票去下载。
DOWNLOAD_TICKET_TTL = 120.0
_download_tickets: dict[str, tuple[int, float]] = {}  # ticket -> (export_id, 过期时间戳)


def _purge_tickets(now: float) -> None:
    for key in [k for k, (_, expires) in _download_tickets.items() if expires < now]:
        _download_tickets.pop(key, None)


def _take_ticket(ticket: str, export_id: int) -> None:
    """核销一张票（一次性的，用完即删）。"""
    entry = _download_tickets.pop(ticket, None)
    if entry is None or entry[0] != export_id or entry[1] < time.time():
        raise HTTPException(401, "下载链接已失效，请回页面重新点一次下载")


def _fetch_questions(req: ExportRequest) -> list[dict]:
    if not req.question_ids and not req.paper_ids:
        raise HTTPException(400, "请至少勾选一道题")
    sql = "SELECT q.*, p.year AS year FROM questions q JOIN papers p ON p.id = q.paper_id WHERE "
    params: list = []
    if req.question_ids:
        sql += f"q.id IN ({','.join('?' * len(req.question_ids))})"
        params += list(req.question_ids)
    else:
        sql += f"q.paper_id IN ({','.join('?' * len(req.paper_ids))})"
        params += list(req.paper_ids)
    sql += " ORDER BY p.year, q.order_no, q.question_no"
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        raise HTTPException(404, "勾选的题目不存在")
    return [common.question_out(r, int(r["year"])) for r in rows]


def _page_paths(paper_ids: set[int]) -> dict[int, dict[int, Path]]:
    if not paper_ids:
        return {}
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT paper_id, page_no, image_path FROM pages "
            f"WHERE paper_id IN ({','.join('?' * len(paper_ids))})",
            list(paper_ids),
        ).fetchall()
    out: dict[int, dict[int, Path]] = {}
    for row in rows:
        out.setdefault(int(row["paper_id"]), {})[int(row["page_no"])] = common.abs_path(row["image_path"])
    return out


def _unique_path(filename: str) -> Path:
    target = config.EXPORT_DIR / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, 100):
        candidate = config.EXPORT_DIR / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return config.EXPORT_DIR / f"{stem}_{db.now().replace(':', '')}{suffix}"


def _title(questions: list[dict]) -> str:
    years = sorted({int(q["year"]) for q in questions if q.get("year")})
    if len(years) == 1:
        return f"{years[0]} 年 408 真题错题本"
    if years:
        return f"{years[0]}-{years[-1]} 年 408 真题错题本"
    return "408 错题本"


@router.post("/export", response_model=ExportOut)
def export_docx(req: ExportRequest):
    """按勾选的题目生成 Word（AGENTS.md 2.4 / 7.5 的排版规则见 services/layout.py）。"""
    config.ensure_dirs()
    questions = _fetch_questions(req)
    entries = layout.plan(
        questions,
        image_width_cm=req.image_width_cm,
        with_caption=req.with_caption,
        note_lines=max(0, min(20, req.note_lines)),
    )
    if not entries:
        raise HTTPException(400, "没有可导出的题目内容")

    word_builder.materialize_pieces(
        entries,
        _page_paths({int(q["paper_id"]) for q in questions}),
        config.CROPS_DIR,
    )

    filename = word_builder.export_filename()
    target = _unique_path(filename)
    stats = word_builder.build(
        entries,
        target,
        image_width_cm=req.image_width_cm,
        with_caption=req.with_caption,
        note_lines=max(0, min(20, req.note_lines)),
        title=_title(questions),
    )
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO exports (paper_ids, question_ids, filename, file_path, options_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                json.dumps(sorted({int(q["paper_id"]) for q in questions})),
                json.dumps([int(q["id"]) for q in questions]),
                target.name,
                config.rel_path(target),
                json.dumps(req.model_dump(), ensure_ascii=False),
                db.now(),
            ),
        )
        export_id = int(cur.lastrowid)

    return ExportOut(
        filename=target.name,
        download_url=f"/api/exports/{export_id}/download",
        question_count=len(questions),
        page_count_estimate=layout.estimate_pages(entries),
    )


@router.get("/exports")
def list_exports():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM exports ORDER BY id DESC LIMIT 200").fetchall()
    return [
        {
            "id": int(r["id"]),
            "filename": r["filename"],
            "created_at": r["created_at"],
            "question_count": len(json.loads(r["question_ids"] or "[]")),
            "download_url": f"/api/exports/{int(r['id'])}/download",
        }
        for r in rows
    ]


@router.post("/exports/{export_id}/ticket")
def export_ticket(export_id: int):
    """发一张一次性下载票，给手机端交给系统浏览器下载用（登录态必需）。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM exports WHERE id=?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    now = time.time()
    _purge_tickets(now)
    ticket = secrets.token_urlsafe(24)
    _download_tickets[ticket] = (export_id, now + DOWNLOAD_TICKET_TTL)
    return {"url": f"/api/exports/{export_id}/download?ticket={ticket}", "expires_in": int(DOWNLOAD_TICKET_TTL)}


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, ticket: str | None = None):
    if ticket:
        _take_ticket(ticket, export_id)  # 没票的话，中间件已经拦过登录态了
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    path = common.abs_path(row["file_path"])
    if not path.exists():
        raise HTTPException(404, "文件已被删除，请重新导出")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=row["filename"],
    )


@router.delete("/exports/{export_id}")
def delete_export(export_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "导出记录不存在")
        conn.execute("DELETE FROM exports WHERE id=?", (export_id,))
    path = common.abs_path(row["file_path"])
    path.unlink(missing_ok=True)
    return {"ok": True}
