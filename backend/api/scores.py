"""得分记录与趋势（需要登录，数据按用户隔离）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import config, db
from ..services import score_service

router = APIRouter(prefix="/api/scores", tags=["scores"])


class ScoreIn(BaseModel):
    """录入一次成绩：选择题给答对个数，综合题给每题得分/满分。"""

    paper_year: int
    practice_date: str | None = None
    choice: dict[str, int] = Field(default_factory=dict)          # {"ds": 9, "co": 8, "os": 7, "cn": 6}
    subjective: list[dict] = Field(default_factory=list)          # [{"qno":41,"score":8,"full":10}, ...]
    note: str = ""


def _user_id(request: Request) -> int:
    user = getattr(request.state, "user", None)
    if user is None:  # 中间件已拦过，这里只是兜底
        raise HTTPException(401, "请先登录")
    return int(user["id"])


@router.get("/schema")
def schema(year: int | None = None):
    """录入表单结构：题组区间、每题分值、综合题默认满分。"""
    return config.score_form_schema(year)


@router.get("")
def list_scores(request: Request):
    with db.get_conn() as conn:
        return score_service.list_records(conn, _user_id(request))


@router.post("")
def create_score(request: Request, payload: ScoreIn):
    with db.get_conn() as conn:
        try:
            row = score_service.save_record(conn, _user_id(request), payload.model_dump())
        except score_service.ScoreError as exc:
            raise HTTPException(400, str(exc)) from None
        return score_service.record_out(row)


@router.put("/{record_id}")
def update_score(request: Request, record_id: int, payload: ScoreIn):
    with db.get_conn() as conn:
        try:
            row = score_service.save_record(conn, _user_id(request), payload.model_dump(), record_id=record_id)
        except score_service.ScoreError as exc:
            raise HTTPException(400, str(exc)) from None
        return score_service.record_out(row)


@router.delete("/{record_id}")
def delete_score(request: Request, record_id: int):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM exam_records WHERE id=? AND user_id=?", (record_id, _user_id(request))
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "记录不存在")
    return {"ok": True}


@router.get("/trend")
def trend(
    request: Request,
    x_axis: str = Query("practice_date", pattern="^(practice_date|paper_year)$"),
    aggregate: str = Query("latest", pattern="^(latest|avg|max)$"),
):
    """折线图数据：模块用百分比（左轴），总分用绝对分（右轴）。"""
    with db.get_conn() as conn:
        return score_service.trend(conn, _user_id(request), x_axis=x_axis, aggregate=aggregate)
