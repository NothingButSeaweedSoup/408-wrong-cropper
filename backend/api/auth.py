"""用户注册 / 登录 / 登录态查询。

登录成功后同时给两样东西：
- 响应体里的 `token`（前端存 localStorage，用 `Authorization: Bearer` 发；安卓/脚本也用这个）
- `HttpOnly` 的会话 Cookie（浏览器里 `<img src="/api/questions/.../images/0">`
  和 Word 下载链接没法带自定义头，只能靠 Cookie 自动携带）

管理员后台也走这套账号登录；用户名在 backend/.env 的 ZC_ADMIN_USERS 里即为管理员。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import config, db
from ..auth import token_from_request
from ..services import user_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=24)
    password: str = Field(..., min_length=config.MIN_PASSWORD_LEN, max_length=128)
    display_name: str = Field("", max_length=32)


class LoginIn(BaseModel):
    username: str
    password: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.TOKEN_COOKIE,
        value=token,
        max_age=config.TOKEN_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _session_payload(conn, user_row, response: Response) -> dict:
    token_info = user_service.issue_token(conn, int(user_row["id"]))
    _set_cookie(response, token_info["token"])
    return {"user": user_service.public_user(user_row), **token_info}


@router.get("/me")
def me(request: Request):
    """带登录态调用返回用户信息；没登录返回 401（前端据此决定显示登录页）。"""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "请先登录")
    with db.get_conn() as conn:
        allow_register = db.allow_register(conn)
    return {"user": user_service.public_user(user), "allow_register": allow_register}


@router.get("/status")
def status():
    """公开：前端判断要不要显示"注册"入口。"""
    with db.get_conn() as conn:
        return {
            "allow_register": db.allow_register(conn),
            "user_count": user_service.user_count(conn),
        }


@router.post("/register")
def register(payload: RegisterIn, response: Response):
    with db.get_conn() as conn:
        if not db.allow_register(conn):
            raise HTTPException(403, "管理员已经关闭注册，请找管理员开账号")
        try:
            user = user_service.create_user(conn, payload.username, payload.password, payload.display_name)
        except user_service.AuthError as exc:
            raise HTTPException(400, str(exc)) from None
        return _session_payload(conn, user, response)


@router.post("/login")
def login(payload: LoginIn, response: Response):
    with db.get_conn() as conn:
        try:
            user = user_service.authenticate(conn, payload.username, payload.password)
        except user_service.AuthError as exc:
            raise HTTPException(401, str(exc)) from None
        return _session_payload(conn, user, response)


@router.post("/logout")
def logout(request: Request, response: Response):
    with db.get_conn() as conn:
        user_service.revoke(conn, token_from_request(request))
    response.delete_cookie(config.TOKEN_COOKIE, path="/")
    return {"ok": True}
