"""管理员专用的小接口（都要 X-Admin-Key，见 backend/auth.py 的默认拒绝规则）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, db
from ..services import user_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SettingsIn(BaseModel):
    allow_register: bool | None = None


class UserIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=24)
    password: str = Field(..., min_length=config.MIN_PASSWORD_LEN, max_length=128)
    display_name: str = Field("", max_length=32)
    is_admin: bool = False


class UserPatch(BaseModel):
    is_admin: bool


def _admin_identity(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    return {
        "via": getattr(request.state, "admin_via", "key"),
        "user": user_service.public_user(user) if user is not None else None,
        "admin_users": sorted(config.ADMIN_USERS),
    }


@router.get("/verify")
def verify(request: Request):
    """后台用它校验登录态：管理员账号或兜底密钥都算通过。"""
    return {"ok": True, **_admin_identity(request)}


@router.get("/settings")
def get_settings():
    with db.get_conn() as conn:
        return {"allow_register": db.allow_register(conn), "user_count": user_service.user_count(conn)}


@router.post("/settings")
def update_settings(payload: SettingsIn):
    with db.get_conn() as conn:
        if payload.allow_register is not None:
            db.set_setting(conn, "allow_register", "1" if payload.allow_register else "0")
        return {"allow_register": db.allow_register(conn), "user_count": user_service.user_count(conn)}


@router.get("/users")
def list_users():
    """用户列表（含是否管理员、各人记录数）。管理员自己也是这个表里的普通账号。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM exam_records r WHERE r.user_id = u.id) AS record_count
               FROM users u ORDER BY u.is_admin DESC, u.id"""
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "username": r["username"],
            "display_name": r["display_name"] or r["username"],
            "is_admin": bool(r["is_admin"]),
            "created_at": r["created_at"],
            "record_count": int(r["record_count"]),
        }
        for r in rows
    ]


@router.post("/users")
def create_user(payload: UserIn):
    """关掉自助注册后，管理员用这个开账号；也可以直接建成管理员。"""
    with db.get_conn() as conn:
        try:
            user = user_service.create_user(conn, payload.username, payload.password, payload.display_name)
        except user_service.AuthError as exc:
            raise HTTPException(400, str(exc)) from None
        if payload.is_admin:
            user_service.set_admin(conn, int(user["id"]), True)
        row = conn.execute("SELECT * FROM users WHERE id=?", (int(user["id"]),)).fetchone()
        return user_service.public_user(row)


@router.patch("/users/{user_id}")
def patch_user(request: Request, user_id: int, payload: UserPatch):
    """调整某人的管理员标记。"""
    identity = _admin_identity(request)
    me = identity["user"]
    if (
        not payload.is_admin
        and identity["via"] == "user"
        and me is not None
        and int(me["id"]) == user_id
    ):
        raise HTTPException(400, "不能取消自己的管理员（换别的管理员操作，或用兜底密钥登录）")
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "用户不存在")
        user_service.set_admin(conn, user_id, payload.is_admin)
        updated = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return user_service.public_user(updated)


@router.delete("/users/{user_id}")
def delete_user(user_id: int):
    """删用户（连带他的得分记录和登录态）。"""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "用户不存在")
    return {"ok": True}
