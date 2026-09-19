"""题目的人工校正：改边界 / 改题号 / 合并 / 拆分 / 重裁。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import config, db
from ..schemas import MergeRequest, QuestionPatch, SplitRequest
from ..services import cropper
from . import common

router = APIRouter(prefix="/api", tags=["questions"])

MIN_BLOCK_PX = 20  # 块的最小高度，防止拖拽成 0 高度


def _paper_row(conn, paper_id: int):
    row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "真题不存在")
    return row


def _question_row(conn, qid: int):
    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if row is None:
        raise HTTPException(404, "题目不存在")
    return row


def _page_paths(paper_id: int) -> dict[int, Path]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, image_path FROM pages WHERE paper_id=?", (paper_id,)
        ).fetchall()
    return {int(r["page_no"]): common.abs_path(r["image_path"]) for r in rows}


def _renumber(conn, paper_id: int) -> None:
    """按 (题号, 原顺序) 把 order_no 重排成 1..N，合并/拆分后调用。"""
    rows = conn.execute(
        "SELECT id FROM questions WHERE paper_id=? ORDER BY question_no, order_no, id", (paper_id,)
    ).fetchall()
    for index, row in enumerate(rows, start=1):
        conn.execute("UPDATE questions SET order_no=? WHERE id=?", (index, int(row["id"])))


def _recrop(conn, paper_row, question_row) -> None:
    """按当前 bbox 重新裁剪图片并写库。"""
    data = db.question_out(question_row)
    data["year"] = int(paper_row["year"])
    cropper.recrop_question(
        int(paper_row["year"]),
        config.CROPS_DIR / str(int(paper_row["id"])),
        _page_paths(int(paper_row["id"])),
        data,
    )
    conn.execute(
        "UPDATE questions SET bbox_json=?, image_paths=? WHERE id=?",
        (
            json.dumps(
                {
                    "blocks": data["bbox"]["blocks"],
                    "sub_marks": data["bbox"].get("sub_marks", []),
                    "x0": data["bbox"].get("x0", 0),
                    "x1": data["bbox"].get("x1"),
                },
                ensure_ascii=False,
            ),
            json.dumps(data["image_paths"], ensure_ascii=False),
            int(question_row["id"]),
        ),
    )


# ---------------------------------------------------------------- 查询
@router.get("/questions")
def list_questions(
    paper_id: int | None = None,
    year: int | None = None,
    subject: str | None = Query(None, pattern="^(ds|co|os|cn)$"),
    type: str | None = Query(None, pattern="^(choice|subjective)$"),
    keyword: str | None = None,
):
    sql = (
        "SELECT q.*, p.year AS year FROM questions q JOIN papers p ON p.id = q.paper_id WHERE 1=1"
    )
    params: list = []
    if paper_id:
        sql += " AND q.paper_id=?"
        params.append(paper_id)
    if year:
        sql += " AND p.year=?"
        params.append(year)
    if subject:
        sql += " AND q.subject=?"
        params.append(subject)
    if type:
        sql += " AND q.type=?"
        params.append(type)
    if keyword:
        sql += " AND (p.title LIKE ? OR CAST(p.year AS TEXT) LIKE ? OR CAST(q.question_no AS TEXT) LIKE ?)"
        params += [f"%{keyword}%"] * 3
    sql += " ORDER BY p.year DESC, q.order_no"
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [common.question_out(r, int(r["year"])) for r in rows]


@router.get("/questions/{qid}")
def get_question(qid: int):
    with db.get_conn() as conn:
        row = _question_row(conn, qid)
        paper = _paper_row(conn, int(row["paper_id"]))
    return common.question_out(row, int(paper["year"]))


@router.get("/questions/{qid}/images/{index}")
def get_question_image(qid: int, index: int):
    with db.get_conn() as conn:
        row = _question_row(conn, qid)
    paths = db.question_out(row)["image_paths"]
    if index < 0 or index >= len(paths):
        raise HTTPException(404, "图片不存在")
    path = common.abs_path(paths[index])
    if not path.exists():
        raise HTTPException(404, "图片文件丢失，请重新裁剪")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------- 校正
@router.patch("/questions/{qid}")
def patch_question(qid: int, patch: QuestionPatch):
    with db.get_conn() as conn:
        row = _question_row(conn, qid)
        paper = _paper_row(conn, int(row["paper_id"]))
        fields: list[str] = []
        params: list = []
        if patch.question_no is not None:
            if not config.QNO_MIN <= patch.question_no <= config.QNO_MAX:
                raise HTTPException(400, f"题号需在 {config.QNO_MIN}~{config.QNO_MAX} 之间")
            fields += ["question_no=?"]
            params.append(patch.question_no)
        if patch.subject is not None:
            fields += ["subject=?"]
            params.append(patch.subject)
        if patch.type is not None:
            fields += ["type=?"]
            params.append(patch.type)
        if patch.score is not None:
            fields += ["score=?"]
            params.append(patch.score)
        if fields:
            conn.execute(f"UPDATE questions SET {', '.join(fields)} WHERE id=?", (*params, qid))

        if patch.block:
            row = _question_row(conn, qid)
            data = db.question_out(row)
            page_no = int(patch.block.get("page_no") or 0)
            found = False
            for block in data["bbox"].get("blocks", []):
                if int(block["page_no"]) != page_no:
                    continue
                y0 = int(patch.block.get("y0", block["y0"]))
                y1 = int(patch.block.get("y1", block["y1"]))
                if y1 - y0 < MIN_BLOCK_PX:
                    raise HTTPException(400, "块高度太小")
                block["y0"], block["y1"] = min(y0, y1), max(y0, y1)
                found = True
            if not found:
                raise HTTPException(400, f"该题在第 {page_no} 页没有题块")
            conn.execute(
                "UPDATE questions SET bbox_json=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "blocks": data["bbox"]["blocks"],
                            "sub_marks": data["bbox"].get("sub_marks", []),
                            "x0": data["bbox"].get("x0", 0),
                            "x1": data["bbox"].get("x1"),
                        },
                        ensure_ascii=False,
                    ),
                    qid,
                ),
            )
            _recrop(conn, paper, _question_row(conn, qid))

        conn.execute("UPDATE questions SET source='manual' WHERE id=?", (qid,))
        row = _question_row(conn, qid)
    return common.question_out(row, int(paper["year"]))


@router.delete("/questions/{qid}")
def delete_question(qid: int):
    with db.get_conn() as conn:
        row = _question_row(conn, qid)
        paper_id = int(row["paper_id"])
        conn.execute("DELETE FROM questions WHERE id=?", (qid,))
        _renumber(conn, paper_id)
    return {"ok": True}


@router.post("/papers/{paper_id}/questions/split")
def split_question(paper_id: int, req: SplitRequest):
    """在指定页面的 y 处把一道题切开，下半部分成为一道新题（复用同一题号）。"""
    with db.get_conn() as conn:
        paper = _paper_row(conn, paper_id)
        rows = conn.execute(
            "SELECT * FROM questions WHERE paper_id=? ORDER BY order_no", (paper_id,)
        ).fetchall()
        target = None
        for row in rows:
            data = db.question_out(row)
            for block in data["bbox"].get("blocks", []):
                if int(block["page_no"]) == req.page_no and block["y0"] + 5 <= req.y <= block["y1"] - 5:
                    target = (row, data, block)
                    break
            if target:
                break
        if target is None:
            raise HTTPException(404, "该位置不在任何题块内")

        row, data, block = target
        blocks = sorted(data["bbox"].get("blocks", []), key=lambda b: int(b["page_no"]))
        index = blocks.index(block)
        upper = [dict(b) for b in blocks[:index]] + [
            {**block, "y1": int(req.y), "path": ""}
        ]
        lower = [{**block, "y0": int(req.y), "path": ""}] + [dict(b) for b in blocks[index + 1 :]]

        conn.execute(
            "UPDATE questions SET bbox_json=?, order_no=? WHERE id=?",
            (_blocks_json(upper, data), float(row["order_no"]) + 0.5, int(row["id"])),
        )
        cur = conn.execute(
            """INSERT INTO questions
               (paper_id, question_no, subject, type, score, parent_id, order_no,
                bbox_json, image_paths, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,'[]','manual',?)""",
            (
                paper_id,
                int(row["question_no"]),
                row["subject"],
                row["type"],
                row["score"],
                row["parent_id"],
                float(row["order_no"]) + 0.6,
                _blocks_json(lower, data),
                db.now(),
            ),
        )
        new_id = int(cur.lastrowid)
        lower_row = _question_row(conn, new_id)
        _recrop(conn, paper, _question_row(conn, int(row["id"])))
        _recrop(conn, paper, lower_row)
        _renumber(conn, paper_id)
        out = common.question_out(_question_row(conn, new_id), int(paper["year"]))
    return {"ok": True, "new_question": out}


@router.post("/papers/{paper_id}/questions/merge")
def merge_questions(paper_id: int, req: MergeRequest):
    """把 drop_id 的题块并到 keep_id 上（一般是"误切开"，合并回一道题）。"""
    if req.keep_id == req.drop_id:
        raise HTTPException(400, "不能合并自身")
    with db.get_conn() as conn:
        paper = _paper_row(conn, paper_id)
        keep = _question_row(conn, req.keep_id)
        drop = _question_row(conn, req.drop_id)
        if int(keep["paper_id"]) != paper_id or int(drop["paper_id"]) != paper_id:
            raise HTTPException(400, "题目不属于该真题")
        keep_data = db.question_out(keep)
        drop_data = db.question_out(drop)
        blocks = cropper.coalesce_blocks(
            keep_data["bbox"].get("blocks", []) + drop_data["bbox"].get("blocks", [])
        )
        subs = sorted(
            keep_data["bbox"].get("sub_marks", []) + drop_data["bbox"].get("sub_marks", []),
            key=lambda s: (int(s["page_no"]), int(s["y0"])),
        )
        merged = {
            "blocks": blocks,
            "sub_marks": subs,
            "x0": keep_data["bbox"].get("x0", 0),
            "x1": keep_data["bbox"].get("x1"),
        }
        conn.execute(
            """UPDATE questions SET question_no=?, bbox_json=?, source='manual' WHERE id=?""",
            (min(int(keep["question_no"]), int(drop["question_no"])), json.dumps(merged, ensure_ascii=False), req.keep_id),
        )
        conn.execute("DELETE FROM questions WHERE id=?", (req.drop_id,))
        _recrop(conn, paper, _question_row(conn, req.keep_id))
        _renumber(conn, paper_id)
        out = common.question_out(_question_row(conn, req.keep_id), int(paper["year"]))
    return {"ok": True, "question": out}


@router.post("/papers/{paper_id}/recrop")
def recrop_paper(paper_id: int):
    """整卷重裁（手工改过图或换过裁剪参数后用）。"""
    with db.get_conn() as conn:
        paper = _paper_row(conn, paper_id)
        rows = conn.execute("SELECT * FROM questions WHERE paper_id=?", (paper_id,)).fetchall()
        for row in rows:
            _recrop(conn, paper, row)
    return {"ok": True, "count": len(rows)}


def _blocks_json(blocks: list[dict], data: dict) -> str:
    return json.dumps(
        {
            "blocks": blocks,
            "sub_marks": data["bbox"].get("sub_marks", []),
            "x0": data["bbox"].get("x0", 0),
            "x1": data["bbox"].get("x1"),
        },
        ensure_ascii=False,
    )
