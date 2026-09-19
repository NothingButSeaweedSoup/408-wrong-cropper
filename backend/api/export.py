"""导出错题本 Word。

页面不做「导出记录」了（用户明确要求去掉）：生成的 docx 只作为"下载凭据"留着 ——
下载链接要按 id 找文件，所以 exports 表还在，但不再对外提供列表/删除接口。
表里的行会**自动只留最近 KEEP_EXPORTS 条**（连同磁盘文件），不会越积越多。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, db
from ..schemas import ExportOut, ExportRequest
from ..services import layout, word_builder
from . import common

router = APIRouter(prefix="/api", tags=["export"])

# 磁盘上最多留多少份导出的 Word（超出就把最老的删掉：页面没有删除入口了，得自己看着点）
KEEP_EXPORTS = 30

# 临时下载链接：手机端把 URL 复制出来、粘到浏览器里下载（WebView 里的下载最不靠谱的一环）。
# 15 分钟有效，**窗口内可重复访问**——用户可能第一次点失败、或者粘到另一台设备上再下一次。
DOWNLOAD_TICKET_TTL = 900.0
_download_tickets: dict[str, tuple[int, float]] = {}  # ticket -> (export_id, 过期时间戳)


def _purge_tickets(now: float) -> None:
    for key in [k for k, (_, expires) in _download_tickets.items() if expires < now]:
        _download_tickets.pop(key, None)


def _check_ticket(ticket: str, export_id: int) -> None:
    """校验票（不消耗：15 分钟内可以重复下载同一份）。"""
    entry = _download_tickets.get(ticket)
    if entry is None or entry[0] != export_id or entry[1] < time.time():
        raise HTTPException(401, "下载链接已失效（15 分钟），请回页面重新点一次导出")


def _owner_id(request: Request) -> int | None:
    user = getattr(request.state, "user", None)
    return int(user["id"]) if user else None


def _check_owner(row, request: Request) -> None:
    """老数据（user_id 为空）谁登录都能下；新数据只有生成它的账号能下。"""
    owner = row["user_id"]
    if owner is None:
        return
    if _owner_id(request) != int(owner):
        raise HTTPException(404, "导出记录不存在")


def _prune_exports(conn, keep: int = KEEP_EXPORTS) -> int:
    """只留最近 keep 条导出（含磁盘文件），返回删了几条。"""
    rows = conn.execute(
        "SELECT id, file_path FROM exports ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,)
    ).fetchall()
    for row in rows:
        conn.execute("DELETE FROM exports WHERE id=?", (int(row["id"]),))
        common.abs_path(row["file_path"]).unlink(missing_ok=True)
    return len(rows)


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
def export_docx(req: ExportRequest, request: Request):
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
            """INSERT INTO exports (user_id, paper_ids, question_ids, filename, file_path, options_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                _owner_id(request),
                json.dumps(sorted({int(q["paper_id"]) for q in questions})),
                json.dumps([int(q["id"]) for q in questions]),
                target.name,
                config.rel_path(target),
                json.dumps(req.model_dump(), ensure_ascii=False),
                db.now(),
            ),
        )
        export_id = int(cur.lastrowid)
        _prune_exports(conn)  # 页面没有删除入口了，这里自动只留最近 KEEP_EXPORTS 份

    return ExportOut(
        filename=target.name,
        download_url=f"/api/exports/{export_id}/download",
        question_count=len(questions),
        page_count_estimate=layout.estimate_pages(entries),
    )


@router.post("/exports/{export_id}/ticket")
def export_ticket(export_id: int, request: Request):
    """发一张临时下载链接（默认 15 分钟）：手机端复制出来粘到浏览器里下载用。

    为什么要票：外部浏览器没有 WebView 的登录 Cookie，直接开 `/download` 会 401；
    把会话 token 塞进 URL 又会留在浏览器历史里。票绑在导出 id 上、只认这个文件，
    过期自动作废（存在进程内存里，重启即失效）。
    """
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, user_id FROM exports WHERE id=?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    _check_owner(row, request)
    now = time.time()
    _purge_tickets(now)
    ticket = secrets.token_urlsafe(24)
    _download_tickets[ticket] = (export_id, now + DOWNLOAD_TICKET_TTL)
    return {"url": f"/api/exports/{export_id}/download?ticket={ticket}", "expires_in": int(DOWNLOAD_TICKET_TTL)}


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, request: Request, ticket: str | None = None):
    if ticket:
        # 拿票下载时没有登录态（外部浏览器），归属在发票那一步已经核对过了
        _check_ticket(ticket, export_id)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    if not ticket:
        _check_owner(row, request)
    path = common.abs_path(row["file_path"])
    if not path.exists():
        raise HTTPException(404, "文件已被删除，请重新导出")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=row["filename"],
    )
