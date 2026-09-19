"""鉴权：登录态（Cookie 或 Bearer token）与管理员判断。

三档权限：
1. **公开**：`/api/health`、`/api/meta`、`/api/auth/status`、登录与注册。
2. **需要登录**（任何账号）：题目库与题目图片、`/api/catalog`、生成/下载 Word、导出历史、得分记录、`/api/auth/me`。
   登录态从 **Cookie**（`zc_token`，浏览器里 `<img>`/下载链接靠它）或 `Authorization: Bearer`（安卓/脚本）取。
3. **需要管理员**：其余所有 `/api/*`（上传真题、切题校正、删除、用户管理）。
   管理员 = 用户名在 `backend/.env` 的 `ZC_ADMIN_USERS` 里（或 `users.is_admin=1`）；
   也可用 `X-Admin-Key`（`ZC_ADMIN_KEY`）兜底，方便脚本和"还没有管理员账号"时引导。

已登录但不是管理员 → 403（明确告诉前端"换个账号"）。
**默认拒绝**：没在白名单里的 /api/* 一律要管理员，以后新增接口忘了标注也不会漏。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config, db
from .services import user_service

# 完全公开
PUBLIC_EXACT = {"/api/health", "/api/meta", "/api/auth/status"}
# 公开的 方法+路径
PUBLIC_ROUTES = {("POST", "/api/auth/login"), ("POST", "/api/auth/register")}
# 需要登录（不要求管理员）的路径前缀
LOGIN_PREFIXES = (
    "/api/catalog",
    "/api/export",  # 同时覆盖 /api/export 与 /api/exports
    "/api/scores",
    "/api/auth/me",
    "/api/auth/logout",
)


def is_public(method: str, path: str, query: Mapping[str, str] | None = None) -> bool:
    if path in PUBLIC_EXACT:
        return True
    if (method, path) in PUBLIC_ROUTES:
        return True
    # 带一次性票的导出下载：手机端把 Word 交给系统浏览器时没有登录 Cookie，
    # 这里放行，票由 backend/api/export.py 自己校验（2 分钟、一次、绑导出 id）。
    if (
        method == "GET"
        and query
        and query.get("ticket")
        and path.startswith("/api/exports/")
        and path.endswith("/download")
    ):
        return True
    return False


def needs_login(method: str, path: str) -> bool:
    """哪些接口"登录即可"。

    注意 `/api/questions` 要按方法区分：**读**题目（列表/详情/题目图）登录即可，
    但 **改/删**题目（人工校正）属于管理操作，必须管理员 —— 只按前缀判断会漏权限。
    """
    if path.startswith(LOGIN_PREFIXES):
        return True
    if path.startswith("/api/questions"):
        return method == "GET"
    return False


def token_from_request(request: Request) -> str:
    """Cookie 优先（浏览器），其次 Authorization 头（安卓/脚本）。"""
    cookie = request.cookies.get(config.TOKEN_COOKIE)
    if cookie:
        return cookie
    return user_service.bearer_token(request.headers.get("authorization"))


def key_matches(provided: str | None) -> bool:
    if not provided or not config.ADMIN_KEY:
        return False
    return secrets.compare_digest(str(provided), config.ADMIN_KEY)


def provided_key(request: Request) -> str:
    return request.headers.get(config.ADMIN_KEY_HEADER) or request.query_params.get("key") or ""


def install_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and not is_public(request.method, path, request.query_params) and path != "/api/health":
            token = token_from_request(request)
            user = None
            if token:
                with db.get_conn() as conn:
                    user = user_service.user_by_token(conn, token)

            if needs_login(request.method, path):
                if user is None:
                    return JSONResponse({"detail": "请先登录"}, status_code=401)
                request.state.user = user
            elif user is not None:
                if not user_service.is_admin(user):
                    return JSONResponse(
                        {
                            "detail": "当前账号不是管理员，"
                            "请在 backend/.env 的 ZC_ADMIN_USERS 里加上这个用户名后重启后端"
                        },
                        status_code=403,
                    )
                request.state.user = user
                request.state.admin_via = "user"
            elif key_matches(provided_key(request)):
                request.state.admin_via = "key"
            else:
                return JSONResponse(
                    {"detail": f"需要管理员登录（或用请求头 {config.ADMIN_KEY_HEADER}）"},
                    status_code=401,
                )
        return await call_next(request)
