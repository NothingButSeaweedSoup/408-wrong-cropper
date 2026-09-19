"""FastAPI 入口。

启动（端口默认 18100，见 config.SERVER_PORT）：
    .venv\\Scripts\\python.exe -m backend
    .venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 18100

- 用户端 H5：/             （公开）
- 管理后台：  /admin/       （SPA 自带密钥输入，密钥在 backend/.env）
- 接口文档：  /docs
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, config, db
from .api import admin, auth as auth_api, export, papers, questions, scores
from .services import user_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    config.WEB_DIR.mkdir(parents=True, exist_ok=True)
    key = config.ensure_admin_key()
    with db.get_conn() as conn:
        user_service.cleanup_expired(conn)
        admins = user_service.sync_admin_users(conn)
        users = user_service.user_count(conn)
    port = config.SERVER_PORT
    urls = config.access_urls()
    print("=" * 70)
    print("  408 错题助手已启动")
    print(f"  用户端   {urls[0]}      （本机）")
    if len(urls) > 1:
        print(f"  局域网   {urls[1]}   ← 手机 / 平板 / 安卓 App 填这个")
    else:
        print("  局域网   没查到本机 IP，手机可能连不上（可跑 python -m backend --print-ip 复查）")
    if Path("/.dockerenv").exists():  # 容器里：上面那些是容器内网地址，别直接抄
        print("  容器内   上面是容器自己的地址；宿主机/手机请用 http://<服务器IP>:18100（compose 里映射的端口）")
    print(f"  管理后台 http://127.0.0.1:{port}/admin/")
    if config.ADMIN_USERS:
        print(f"  管理员账号：{', '.join(sorted(config.ADMIN_USERS))}（backend/.env 的 ZC_ADMIN_USERS）")
        if admins["missing"]:
            print(f"  还没注册：{', '.join(admins['missing'])} —— 注册该用户名后自动成为管理员")
    else:
        print("  还没指定管理员账号：把用户名写进 backend/.env 的 ZC_ADMIN_USERS 后重启")
        print("  （实在进不去时，可用下面的兜底密钥登录后台，先去建/指定管理员）")
    print(f"  兜底密钥 {key}（也可写在 ZC_ADMIN_KEY，脚本与首次引导用）")
    print(f"  已注册用户 {users} 个 ｜ 密钥与名单文件 {config.ENV_FILE}")
    print("=" * 70)
    yield


app = FastAPI(title="408 错题助手", version=__version__, lifespan=lifespan)

# 本地工具：管理后台开发态跑在 5173，直接放开跨域。
# 允许 X-Admin-Key 头，否则前端带密钥的预检会失败。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 管理端鉴权（默认拒绝，公开接口白名单见 backend/auth.py）
auth.install_guard(app)


@app.middleware("http")
async def no_stale_static(request, call_next):
    """静态资源（用户端 H5 / 管理后台）强制每次回源校验。

    用户端是无构建的：改了 `web/*.js` 刷新一下就该生效。但 StaticFiles 不带 `Cache-Control`，
    浏览器会按 `Last-Modified` 做**启发式缓存**，结果文件改了、用户浏览器还在跑旧脚本
    （踩过：录入表单的总分明明改好了，用户看到的还是旧的）。加 `no-cache` 后浏览器每次都会带
    `If-None-Match` 来问一句：没变就 304（几字节），变了就拿到新的。
    """
    response = await call_next(request)
    if request.method == "GET" and not request.url.path.startswith("/api") and response.status_code == 200:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.include_router(papers.router)
app.include_router(questions.router)
app.include_router(export.router)
app.include_router(auth_api.router)
app.include_router(scores.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "db": str(config.DB_PATH), "port": config.SERVER_PORT}


@app.get("/api/meta")
def meta():
    """前端需要的常量，避免两端各写一份。"""
    return {
        "version": __version__,
        "subjects": config.SUBJECT_NAMES,
        "subject_rules": [
            {"from": lo, "to": hi, "subject": s, "type": t, "score": sc}
            for lo, hi, s, t, sc in config.SUBJECT_RULES
        ],
        "module_full_score": config.MODULE_FULL_SCORE,
        "qno_range": [config.QNO_MIN, config.QNO_MAX],
        "default_image_width_cm": config.IMAGE_WIDTH_CM,
        "content_height_cm": round(config.CONTENT_H_CM, 2),
        "render_dpi": round(72.0 * config.RENDER_ZOOM, 1),
    }


@app.get("/api/catalog")
def catalog():
    """用户端要的轻量目录：按年份聚合的题量（真题列表本身属管理端）。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT p.year AS year,
                      COUNT(DISTINCT p.id) AS papers,
                      COUNT(q.id)         AS questions
               FROM papers p LEFT JOIN questions q ON q.paper_id = p.id
               GROUP BY p.year ORDER BY p.year DESC"""
        ).fetchall()
    return {
        "years": [
            {"year": int(r["year"]), "papers": int(r["papers"]), "questions": int(r["questions"])}
            for r in rows
        ]
    }


# 静态资源放最后挂载：先匹配 /api/*，再兜底到静态文件
if config.ADMIN_DIST_DIR.exists():
    app.mount("/admin", StaticFiles(directory=config.ADMIN_DIST_DIR, html=True), name="admin")


# 用户端是无构建的（改 web/*.js 直接生效），没有构建产物哈希可用。
# 光靠 no-cache 还不够：已经缓存过的旧副本（尤其是安卓 WebView）不一定肯回源，
# 结果是"服务端明明改了，用户看到的还是旧表单"。所以首页把 js/css 的 URL 带上文件 mtime，
# 文件一改 URL 就变，浏览器只能重新拉。
_ASSET_RE = re.compile(r'(href|src)="/([^"?]+\.(?:js|css))"')


def stamp_static_urls(html: str) -> str:
    def replace(match: re.Match) -> str:
        attr, name = match.group(1), match.group(2)
        try:
            stamp = int((config.WEB_DIR / name).stat().st_mtime)
        except OSError:
            return match.group(0)
        return f'{attr}="/{name}?v={stamp}"'

    return _ASSET_RE.sub(replace, html)


@app.get("/", include_in_schema=False)
def web_index():
    html = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(stamp_static_urls(html), headers={"Cache-Control": "no-cache, must-revalidate"})


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
